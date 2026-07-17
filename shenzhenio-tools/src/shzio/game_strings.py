from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .game_metadata import DEFAULT_OUTPUT as DEFAULT_METADATA, load_game_metadata


FORMAT_NAME = "shzio-game-strings"
FORMAT_VERSION = 1
DEFAULT_OUTPUT = DEFAULT_METADATA.with_name("game-strings.json")
UINT32_MASK = 0xFFFFFFFF
MAX_DECOMPRESSED_SIZE = 16 * 1024 * 1024


class GameStringError(RuntimeError):
    pass


@dataclass(frozen=True)
class StringTableProfile:
    name: str = "shenzhen-smartassembly-v4"
    resource_header_size: int = 4
    record_header_size: int = 7
    index_key_delta: int = 0x75A1911B
    metadata_xor: int = 0xD69837F5
    length_mask: int = 0x0FFFFFFF
    compressed_mask: int = 0x80000000
    single_byte_mask: int = 0x40000000
    interned_mask: int = 0x20000000


DEFAULT_PROFILE = StringTableProfile()


@dataclass(frozen=True)
class StringReference:
    identifier: int
    category: str | None
    type_name: str | None
    method_name: str | None
    method_token: str | None
    il_offset: int


@dataclass(frozen=True)
class DecodedString:
    identifier: int
    offset: int
    encoded_length: int
    compressed: bool
    single_byte: bool
    interned: bool
    value: str


@dataclass(frozen=True)
class StringTableKeys:
    index_xor: int
    decoder: int


class GameStringTable:
    def __init__(
        self,
        resource: bytes,
        keys: StringTableKeys,
        profile: StringTableProfile = DEFAULT_PROFILE,
    ) -> None:
        self.resource = resource
        self.keys = keys
        self.profile = profile

    def decode(self, identifier: int) -> DecodedString:
        profile = self.profile
        offset = (identifier & UINT32_MASK) ^ self.keys.index_xor
        if offset < profile.resource_header_size:
            raise GameStringError(f"string {identifier} points into the resource header")
        if offset + profile.record_header_size > len(self.resource):
            raise GameStringError(f"string {identifier} record header is out of bounds")

        header = bytes(
            self.resource[offset + index]
            ^ ((self.keys.decoder >> (index * 8)) & 0xFF)
            for index in range(3)
        )
        encrypted_metadata = struct.unpack_from("<I", self.resource, offset + 3)[0]
        metadata = (
            encrypted_metadata
            ^ offset
            ^ profile.metadata_xor
            ^ self.keys.decoder
        ) & UINT32_MASK
        encoded_length = metadata & profile.length_mask
        payload_start = offset + profile.record_header_size
        payload_end = payload_start + encoded_length
        if payload_end > len(self.resource):
            raise GameStringError(
                f"string {identifier} payload ends at {payload_end}, beyond resource size {len(self.resource)}"
            )

        payload = _transform_payload(header, self.resource[payload_start:payload_end])
        compressed = bool(metadata & profile.compressed_mask)
        single_byte = bool(metadata & profile.single_byte_mask)
        interned = bool(metadata & profile.interned_mask)
        if compressed:
            payload = _decompress_payload(payload)

        try:
            if single_byte:
                value = "".join(chr(byte) for byte in payload)
            else:
                value = payload.decode("utf-16le")
        except UnicodeDecodeError as exc:
            raise GameStringError(f"string {identifier} has invalid UTF-16LE data: {exc}") from exc

        return DecodedString(
            identifier=identifier,
            offset=offset,
            encoded_length=encoded_length,
            compressed=compressed,
            single_byte=single_byte,
            interned=interned,
            value=value,
        )


def find_string_references(metadata: dict[str, Any]) -> list[StringReference]:
    decoder_tokens = {
        entry.get("metadata_token")
        for entry in metadata.get("disassembly", [])
        if entry.get("category") == "string_decoder_candidate"
    }
    decoder_tokens.discard(None)
    if len(decoder_tokens) != 1:
        raise GameStringError(
            f"expected one string decoder candidate, found {len(decoder_tokens)}"
        )

    references: list[StringReference] = []
    for entry in metadata.get("disassembly", []):
        body = entry.get("body")
        if not isinstance(body, dict):
            continue
        instructions = body.get("instructions")
        if not isinstance(instructions, list):
            continue
        for previous, instruction in zip(instructions, instructions[1:]):
            if instruction.get("opcode") not in {"call", "callvirt"}:
                continue
            operand = instruction.get("operand")
            if not isinstance(operand, dict) or operand.get("token") not in decoder_tokens:
                continue
            identifier = _integer_constant(previous)
            if identifier is None:
                raise GameStringError(
                    f"decoder call at {entry.get('metadata_token')}:{instruction.get('offset')} "
                    "is not preceded by an integer constant"
                )
            references.append(
                StringReference(
                    identifier=identifier,
                    category=entry.get("category"),
                    type_name=entry.get("type"),
                    method_name=entry.get("method"),
                    method_token=entry.get("metadata_token"),
                    il_offset=int(instruction.get("offset", 0)),
                )
            )
    if not references:
        raise GameStringError("no calls to the string decoder were found")
    return references


def infer_string_table_keys(
    resource: bytes,
    identifiers: Iterable[int],
    profile: StringTableProfile = DEFAULT_PROFILE,
) -> StringTableKeys:
    unique_ids = sorted({identifier & UINT32_MASK for identifier in identifiers})
    if not unique_ids:
        raise GameStringError("at least one string identifier is required")
    if len(resource) < profile.resource_header_size + profile.record_header_size:
        raise GameStringError("string resource is too small")

    anchor = unique_ids[0]
    candidates: list[StringTableKeys] = []
    last_record_start = len(resource) - profile.record_header_size
    for anchor_offset in range(profile.resource_header_size, last_record_start + 1):
        index_key = anchor ^ anchor_offset
        decoder_key = index_key ^ profile.index_key_delta
        valid = True
        for identifier in unique_ids:
            offset = identifier ^ index_key
            if offset < profile.resource_header_size or offset > last_record_start:
                valid = False
                break
            encrypted_metadata = struct.unpack_from("<I", resource, offset + 3)[0]
            metadata = (
                encrypted_metadata
                ^ offset
                ^ profile.metadata_xor
                ^ decoder_key
            ) & UINT32_MASK
            encoded_length = metadata & profile.length_mask
            if offset + profile.record_header_size + encoded_length > len(resource):
                valid = False
                break
        if valid:
            candidates.append(StringTableKeys(index_key, decoder_key))

    if not candidates:
        raise GameStringError("no string-table key satisfies all record bounds")

    decoded_candidates: list[StringTableKeys] = []
    for keys in candidates:
        table = GameStringTable(resource, keys, profile)
        try:
            for identifier in unique_ids:
                table.decode(identifier)
        except (GameStringError, IndexError, struct.error):
            continue
        decoded_candidates.append(keys)

    if len(decoded_candidates) != 1:
        raise GameStringError(
            "string-table key inference is ambiguous: "
            f"{len(decoded_candidates)} of {len(candidates)} structural candidates decode successfully"
        )
    return decoded_candidates[0]


def decode_game_strings(
    metadata: dict[str, Any],
    *,
    metadata_path: str | Path | None = None,
    profile: StringTableProfile = DEFAULT_PROFILE,
) -> dict[str, Any]:
    references = find_string_references(metadata)
    resource_name, resource = _read_string_resource(metadata)
    keys = infer_string_table_keys(
        resource,
        (reference.identifier for reference in references),
        profile,
    )
    table = GameStringTable(resource, keys, profile)

    uses: dict[int, list[StringReference]] = {}
    for reference in references:
        uses.setdefault(reference.identifier, []).append(reference)

    strings = []
    for identifier in sorted(uses, key=lambda value: value & UINT32_MASK):
        decoded = table.decode(identifier)
        strings.append(
            {
                "id": identifier,
                "id_hex": f"0x{identifier & UINT32_MASK:08X}",
                "offset": decoded.offset,
                "encoded_length": decoded.encoded_length,
                "flags": {
                    "compressed": decoded.compressed,
                    "single_byte": decoded.single_byte,
                    "interned": decoded.interned,
                },
                "value": decoded.value,
                "uses": [
                    {
                        "category": reference.category,
                        "type": reference.type_name,
                        "method": reference.method_name,
                        "method_token": reference.method_token,
                        "il_offset": reference.il_offset,
                        "il_offset_hex": f"IL_{reference.il_offset:04X}",
                    }
                    for reference in uses[identifier]
                ],
            }
        )

    source = metadata.get("source", {})
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "source": {
            "metadata_path": str(Path(metadata_path).resolve()) if metadata_path else None,
            "game_exe": source.get("path"),
            "game_sha256": source.get("sha256"),
            "module_version_id": source.get("module_version_id"),
            "resource_name": resource_name,
        },
        "profile": {
            "name": profile.name,
            "resource_header_size": profile.resource_header_size,
            "record_header_size": profile.record_header_size,
            "index_key_delta": f"0x{profile.index_key_delta:08X}",
            "metadata_xor": f"0x{profile.metadata_xor:08X}",
        },
        "keys": {
            "index_xor": f"0x{keys.index_xor:08X}",
            "decoder": f"0x{keys.decoder:08X}",
            "inferred_from_all_records": True,
        },
        "summary": {
            "reference_count": len(references),
            "unique_id_count": len(uses),
            "decoded_count": len(strings),
        },
        "strings": strings,
    }


def write_game_strings(
    metadata_path: str | Path = DEFAULT_METADATA,
    output: str | Path = DEFAULT_OUTPUT,
) -> tuple[Path, dict[str, Any]]:
    resolved_metadata = Path(metadata_path).resolve()
    metadata = load_game_metadata(resolved_metadata)
    payload = decode_game_strings(metadata, metadata_path=resolved_metadata)
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path, payload


def _read_string_resource(metadata: dict[str, Any]) -> tuple[str, bytes]:
    resources = [
        resource
        for resource in metadata.get("manifest_resources", [])
        if isinstance(resource, dict) and isinstance(resource.get("data_base64"), str)
    ]
    if len(resources) != 1:
        raise GameStringError(
            f"expected one embedded resource with data, found {len(resources)}"
        )
    resource = resources[0]
    try:
        data = base64.b64decode(resource["data_base64"], validate=True)
    except ValueError as exc:
        raise GameStringError("embedded resource is not valid base64") from exc
    return str(resource.get("name", "")), data


def _integer_constant(instruction: dict[str, Any]) -> int | None:
    opcode = instruction.get("opcode")
    if opcode in {"ldc.i4", "ldc.i4.s"}:
        operand = instruction.get("operand")
        return operand if isinstance(operand, int) else None
    if not isinstance(opcode, str) or not opcode.startswith("ldc.i4."):
        return None
    suffix = opcode.removeprefix("ldc.i4.")
    if suffix == "m1":
        return -1
    return int(suffix) if suffix.isdigit() else None


def _transform_payload(header: bytes, payload: bytes) -> bytes:
    if len(header) != 3:
        raise GameStringError("record transform requires a three-byte header")
    output = bytearray(payload)
    seed = header[1]
    rolling = ((len(payload) + 11) ^ (seed + 7)) & 0xFF
    state = (header[0] | (header[2] << 8)) + (rolling << 3)
    random_word = 0
    for index, encrypted in enumerate(payload):
        if index % 2 == 0:
            state = (state * 214013 + 2531011) & UINT32_MASK
            random_word = (state >> 16) & 0xFFFF
        random_byte = random_word & 0xFF
        random_word >>= 8
        output[index] = encrypted ^ seed ^ ((rolling + 3) & 0xFF) ^ random_byte
        rolling = encrypted
    return bytes(output)


def _decompress_payload(payload: bytes) -> bytes:
    if len(payload) < 4:
        raise GameStringError("compressed string payload has no size header")
    output_size = payload[2] | (payload[0] << 16) | (payload[3] << 8) | (payload[1] << 24)
    if output_size > MAX_DECOMPRESSED_SIZE:
        raise GameStringError(f"compressed string expands to unreasonable size {output_size}")
    return _inflate_lz(payload, 4, output_size)


def _inflate_lz(source: bytes, source_offset: int, output_size: int) -> bytes:
    output = bytearray(output_size)
    output_offset = 0
    control = 0
    mask = 128
    while output_offset < output_size:
        mask <<= 1
        if mask == 256:
            mask = 1
            if source_offset >= len(source):
                raise GameStringError("compressed string ended before a control byte")
            control = source[source_offset]
            source_offset += 1

        if control & mask:
            if source_offset + 2 > len(source):
                raise GameStringError("compressed string ended inside a back-reference")
            count = (source[source_offset] >> 2) + 3
            distance = ((source[source_offset] << 8) | source[source_offset + 1]) & 1023
            source_offset += 2
            copy_offset = output_offset - distance
            if copy_offset < 0 or copy_offset >= output_offset:
                raise GameStringError(f"invalid compressed back-reference distance {distance}")
            while count >= 0 and output_offset < output_size:
                output[output_offset] = output[copy_offset]
                output_offset += 1
                copy_offset += 1
                count -= 1
        else:
            if source_offset >= len(source):
                raise GameStringError("compressed string ended inside a literal")
            output[output_offset] = source[source_offset]
            output_offset += 1
            source_offset += 1
    return bytes(output)
