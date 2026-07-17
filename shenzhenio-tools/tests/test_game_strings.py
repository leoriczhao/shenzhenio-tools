from __future__ import annotations

import base64
import struct
import unittest

from shzio.game_strings import (
    DEFAULT_PROFILE,
    GameStringTable,
    StringTableKeys,
    _inflate_lz,
    decode_game_strings,
    infer_string_table_keys,
)


INDEX_KEY = 0x12345678
DECODER_KEY = INDEX_KEY ^ DEFAULT_PROFILE.index_key_delta


def encode_payload(header: bytes, payload: bytes) -> bytes:
    output = bytearray(len(payload))
    seed = header[1]
    rolling = ((len(payload) + 11) ^ (seed + 7)) & 0xFF
    state = (header[0] | (header[2] << 8)) + (rolling << 3)
    random_word = 0
    for index, plain in enumerate(payload):
        if index % 2 == 0:
            state = (state * 214013 + 2531011) & 0xFFFFFFFF
            random_word = (state >> 16) & 0xFFFF
        random_byte = random_word & 0xFF
        random_word >>= 8
        encrypted = plain ^ seed ^ ((rolling + 3) & 0xFF) ^ random_byte
        output[index] = encrypted
        rolling = encrypted
    return bytes(output)


def write_record(resource: bytearray, offset: int, value: str, *, single_byte: bool) -> int:
    payload = value.encode("latin1") if single_byte else value.encode("utf-16le")
    header = bytes((offset & 0xFF, (offset * 3) & 0xFF, (offset * 7) & 0xFF))
    encrypted_header = bytes(
        byte ^ ((DECODER_KEY >> (index * 8)) & 0xFF)
        for index, byte in enumerate(header)
    )
    flags = DEFAULT_PROFILE.single_byte_mask if single_byte else 0
    metadata = flags | len(payload)
    encrypted_metadata = (
        metadata ^ offset ^ DEFAULT_PROFILE.metadata_xor ^ DECODER_KEY
    ) & 0xFFFFFFFF
    record = encrypted_header + struct.pack("<I", encrypted_metadata) + encode_payload(header, payload)
    resource[offset : offset + len(record)] = record
    return offset ^ INDEX_KEY


def make_fixture() -> tuple[bytes, list[int], list[str]]:
    resource = bytearray([0xA5] * 512)
    offsets = [20, 90, 220, 350]
    values = ["MC4000", "DX300", "terminal", "power"]
    identifiers = [
        write_record(resource, offset, value, single_byte=index != 1)
        for index, (offset, value) in enumerate(zip(offsets, values))
    ]
    return bytes(resource), identifiers, values


class GameStringTests(unittest.TestCase):
    def test_infers_keys_and_decodes_records(self) -> None:
        resource, identifiers, values = make_fixture()

        keys = infer_string_table_keys(resource, identifiers)
        table = GameStringTable(resource, keys)

        self.assertEqual(StringTableKeys(INDEX_KEY, DECODER_KEY), keys)
        self.assertEqual(values, [table.decode(identifier).value for identifier in identifiers])

    def test_decodes_metadata_report_with_call_sites(self) -> None:
        resource, identifiers, values = make_fixture()
        instructions = []
        for offset, identifier in enumerate(identifiers):
            instructions.extend(
                [
                    {"offset": offset * 6, "opcode": "ldc.i4", "operand": identifier},
                    {
                        "offset": offset * 6 + 5,
                        "opcode": "call",
                        "operand": {"kind": "method", "token": "0x06000008"},
                    },
                ]
            )
        metadata = {
            "source": {
                "path": "Shenzhen.exe",
                "sha256": "a" * 64,
                "module_version_id": "fixture",
            },
            "disassembly": [
                {
                    "category": "string_decoder_candidate",
                    "metadata_token": "0x06000008",
                    "body": {"instructions": []},
                },
                {
                    "category": "initializer",
                    "type": "ChipTypes",
                    "method": "Initialize",
                    "metadata_token": "0x0600094C",
                    "body": {"instructions": instructions},
                },
            ],
            "manifest_resources": [
                {
                    "name": "fixture",
                    "data_base64": base64.b64encode(resource).decode("ascii"),
                }
            ],
        }

        report = decode_game_strings(metadata)

        self.assertEqual(4, report["summary"]["decoded_count"])
        self.assertEqual("0x12345678", report["keys"]["index_xor"])
        self.assertEqual(sorted(values), sorted(row["value"] for row in report["strings"]))
        self.assertEqual("ChipTypes", report["strings"][0]["uses"][0]["type"])

    def test_inflates_overlapping_back_reference(self) -> None:
        compressed = b"\x10ABCD\x00\x04"

        self.assertEqual(b"ABCDABCD", _inflate_lz(compressed, 0, 8))


if __name__ == "__main__":
    unittest.main()
