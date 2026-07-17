from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .game_metadata import DEFAULT_OUTPUT as DEFAULT_METADATA, load_game_metadata
from .game_strings import DEFAULT_OUTPUT as DEFAULT_STRINGS
from .manual_parts import MANUAL_PARTS, ManualPartSpec


FORMAT_NAME = "shzio-chip-catalog"
FORMAT_VERSION = 1
DEFAULT_OUTPUT = DEFAULT_METADATA.with_name("chip-catalog.json")


class ChipCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChipCatalogProfile:
    display_name_field: str = "0x040008A4"
    type_name_field: str = "0x040008A5"
    price_field: str = "0x040008A6"
    size_field: str = "0x040008A7"
    unlock_field: str = "0x040008B8"
    pin_kind_field: str = "0x040008C1"
    pin_label_field: str = "0x040008C2"
    pin_register_field: str = "0x040008C3"


DEFAULT_PROFILE = ChipCatalogProfile()


def build_chip_catalog(
    metadata: dict[str, Any],
    strings: dict[str, Any],
    profile: ChipCatalogProfile = DEFAULT_PROFILE,
) -> dict[str, Any]:
    string_values = {
        row["id"]: row["value"]
        for row in strings.get("strings", [])
        if isinstance(row, dict) and isinstance(row.get("id"), int)
    }
    if not string_values:
        raise ChipCatalogError("decoded string report contains no strings")

    chip_type = _type(metadata, "ChipType")
    chip_types = _type(metadata, "ChipTypes")
    register_type = _type(metadata, "Register")
    chip_field_types = {
        field["metadata_token"]: field["type"] for field in chip_type.get("fields", [])
    }
    static_chip_fields = {
        field["metadata_token"]
        for field in chip_types.get("fields", [])
        if field.get("type") == "ChipType"
    }
    register_names = {
        field["constant"]: field["name"].lower()
        for field in register_type.get("fields", [])
        if isinstance(field.get("constant"), int) and field.get("name") != "NULL"
    }

    initializer = _initializer(metadata, "ChipTypes")
    segments = _split_chip_segments(initializer, static_chip_fields)
    if len(segments) != len(static_chip_fields):
        raise ChipCatalogError(
            f"split {len(segments)} ChipType records for {len(static_chip_fields)} static fields"
        )

    raw_chips = [
        _parse_chip_segment(segment, string_values, chip_field_types, profile)
        for segment in segments
    ]
    pin_kind_names = _infer_pin_kind_names(raw_chips, register_names)

    chips = []
    for raw in raw_chips:
        width, height = raw["size"]
        pin_indexes = sorted(
            set(raw["pin_kinds"]) | set(raw["pin_labels"]) | set(raw["pin_registers"])
        )
        generated_counts: dict[str, int] = {}
        pins = []
        for index in pin_indexes:
            kind_value = raw["pin_kinds"].get(index)
            kind = pin_kind_names.get(kind_value, f"unknown-{kind_value}")
            register_value = raw["pin_registers"].get(index)
            register = register_names.get(register_value)
            explicit_label = raw["pin_labels"].get(index)
            name = explicit_label or register
            name_source = "game-label" if explicit_label is not None else "game-register"
            if name is None:
                prefix = "x" if kind == "xbus" else "p" if kind == "simple" else "pin"
                sequence = generated_counts.get(prefix, 0)
                generated_counts[prefix] = sequence + 1
                name = f"{prefix}{sequence}"
                name_source = "generated"
            side, side_offset = _pin_slot(index, height)
            pins.append(
                {
                    "index": index,
                    "name": name,
                    "name_source": name_source,
                    "official_name": None,
                    "kind": kind,
                    "kind_value": kind_value,
                    "register": register,
                    "register_value": register_value,
                    "side": side,
                    "side_offset": side_offset,
                    "direction": "unknown",
                }
            )

        price_raw = raw["price_raw"]
        chip = {
            "static_field_token": raw["static_field_token"],
            "name": raw["name"],
            "type": raw["type"],
            "price_raw": price_raw,
            "price": price_raw // 100 if price_raw % 100 == 0 else price_raw / 100,
            "size": [width, height],
            "unlock": raw["unlock"],
            "pins": pins,
            "scalar_fields": raw["scalar_fields"],
            "construction": raw.get("factory", "direct"),
            "custom_key": raw.get("custom_key"),
            "il_range": raw["il_range"],
            "manual": None,
        }
        manual = MANUAL_PARTS.get(chip["name"])
        if manual is not None:
            _apply_manual_spec(chip, manual)
        chips.append(chip)

    source = metadata.get("source", {})
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "source": {
            "game_exe": source.get("path"),
            "game_sha256": source.get("sha256"),
            "module_version_id": source.get("module_version_id"),
            "metadata_format": metadata.get("format"),
            "strings_format": strings.get("format"),
        },
        "pin_kind_values": {str(key): value for key, value in sorted(pin_kind_names.items())},
        "summary": {
            "chip_count": len(chips),
            "named_chip_count": sum(chip["name"] is not None for chip in chips),
            "typed_chip_count": sum(chip["type"] is not None for chip in chips),
            "manual_verified_chip_count": sum(chip["manual"] is not None for chip in chips),
        },
        "chips": chips,
    }


def write_chip_catalog(
    metadata_path: str | Path = DEFAULT_METADATA,
    strings_path: str | Path = DEFAULT_STRINGS,
    output: str | Path = DEFAULT_OUTPUT,
) -> tuple[Path, dict[str, Any]]:
    metadata_file = Path(metadata_path).resolve()
    strings_file = Path(strings_path).resolve()
    metadata = load_game_metadata(metadata_file)
    try:
        strings = json.loads(strings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChipCatalogError(f"failed to read {strings_file}: {exc}") from exc
    if strings.get("format") != "shzio-game-strings":
        raise ChipCatalogError(f"unexpected strings format {strings.get('format')!r}")

    payload = build_chip_catalog(metadata, strings)
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path, payload


def _type(metadata: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [entry for entry in metadata.get("types", []) if entry.get("full_name") == name]
    if len(matches) != 1:
        raise ChipCatalogError(f"expected one detailed type {name!r}, found {len(matches)}")
    return matches[0]


def _initializer(metadata: dict[str, Any], type_name: str) -> list[dict[str, Any]]:
    matches = [
        entry
        for entry in metadata.get("disassembly", [])
        if entry.get("category") == "initializer"
        and entry.get("type") == type_name
        and isinstance(entry.get("body"), dict)
    ]
    if len(matches) != 1:
        raise ChipCatalogError(f"expected one initializer for {type_name}, found {len(matches)}")
    instructions = matches[0]["body"].get("instructions")
    if not isinstance(instructions, list):
        raise ChipCatalogError(f"initializer for {type_name} has no instructions")
    return instructions


def _split_chip_segments(
    instructions: list[dict[str, Any]],
    static_chip_fields: set[str],
) -> list[list[dict[str, Any]]]:
    segments = []
    start = 0
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "stsfld"
            and isinstance(operand, dict)
            and operand.get("token") in static_chip_fields
        ):
            segments.append(instructions[start : index + 1])
            start = index + 1
    return segments


def _parse_chip_segment(
    instructions: list[dict[str, Any]],
    strings: dict[int, str],
    chip_field_types: dict[str, str],
    profile: ChipCatalogProfile,
) -> dict[str, Any]:
    final_operand = instructions[-1].get("operand")
    if not isinstance(final_operand, dict):
        raise ChipCatalogError("ChipType segment has no final field operand")

    name = _string_field(
        instructions,
        profile.display_name_field,
        strings,
        required=False,
    )
    type_name = _string_field(
        instructions,
        profile.type_name_field,
        strings,
        required=False,
    )
    if name is None and type_name is None:
        factory_chip = _parse_factory_segment(instructions, strings, chip_field_types)
        if factory_chip is not None:
            factory_chip["static_field_token"] = final_operand["token"]
            factory_chip["il_range"] = [instructions[0]["offset"], instructions[-1]["offset"]]
            return factory_chip
        raise ChipCatalogError("ChipType record has no decoded name or type")
    name = name or type_name
    type_name = type_name or name
    price = _integer_field(instructions, profile.price_field, default=0)
    size = _index2_field(instructions, profile.size_field)
    unlock = _string_field(instructions, profile.unlock_field, strings, required=False)
    pin_kinds = _integer_dictionary(instructions, profile.pin_kind_field)
    pin_labels = _string_dictionary(instructions, profile.pin_label_field, strings)
    pin_registers = _integer_dictionary(instructions, profile.pin_register_field)

    scalar_fields = []
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if instruction.get("opcode") != "stfld" or not isinstance(operand, dict):
            continue
        token = operand.get("token")
        if token not in chip_field_types or index == 0:
            continue
        value = _integer_constant(instructions[index - 1])
        if value is None:
            continue
        scalar_fields.append(
            {
                "field_token": token,
                "field_type": chip_field_types[token],
                "value": value,
            }
        )

    return {
        "static_field_token": final_operand["token"],
        "name": name,
        "type": type_name,
        "price_raw": price,
        "size": size,
        "unlock": unlock,
        "pin_kinds": pin_kinds,
        "pin_labels": pin_labels,
        "pin_registers": pin_registers,
        "scalar_fields": scalar_fields,
        "il_range": [instructions[0]["offset"], instructions[-1]["offset"]],
    }


def _string_field(
    instructions: list[dict[str, Any]],
    field_token: str,
    strings: dict[int, str],
    *,
    required: bool = True,
) -> str | None:
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "stfld"
            and isinstance(operand, dict)
            and operand.get("token") == field_token
        ):
            try:
                identifier = _nearest_string_id(instructions, index)
            except ChipCatalogError:
                if not required:
                    return None
                raise
            try:
                return strings[identifier]
            except KeyError as exc:
                raise ChipCatalogError(f"decoded value for string id {identifier} is missing") from exc
    if required:
        raise ChipCatalogError(f"required string field {field_token} is not assigned")
    return None


def _integer_field(
    instructions: list[dict[str, Any]],
    field_token: str,
    *,
    default: int | None = None,
) -> int:
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "stfld"
            and isinstance(operand, dict)
            and operand.get("token") == field_token
            and index > 0
        ):
            value = _integer_constant(instructions[index - 1])
            if value is not None:
                return value
    if default is not None:
        return default
    raise ChipCatalogError(f"required integer field {field_token} is not assigned")


def _index2_field(instructions: list[dict[str, Any]], field_token: str) -> tuple[int, int]:
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "stfld"
            and isinstance(operand, dict)
            and operand.get("token") == field_token
        ):
            for constructor_index in range(index - 1, max(-1, index - 6), -1):
                constructor = instructions[constructor_index]
                constructor_operand = constructor.get("operand")
                if (
                    constructor.get("opcode") == "newobj"
                    and isinstance(constructor_operand, dict)
                    and constructor_operand.get("declaring_type") == "Index2"
                ):
                    width = _integer_constant(instructions[constructor_index - 2])
                    height = _integer_constant(instructions[constructor_index - 1])
                    if width is not None and height is not None:
                        return width, height
    raise ChipCatalogError(f"required Index2 field {field_token} is not assigned")


def _integer_dictionary(
    instructions: list[dict[str, Any]], field_token: str
) -> dict[int, int]:
    start, end = _dictionary_range(instructions, field_token)
    if start is None:
        return {}
    values = {}
    for index in range(start, end):
        instruction = instructions[index]
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "callvirt"
            and isinstance(operand, dict)
            and index >= 2
        ):
            key = _integer_constant(instructions[index - 2])
            value = _integer_constant(instructions[index - 1])
            if key is not None and value is not None:
                values[key] = value
    return values


def _string_dictionary(
    instructions: list[dict[str, Any]],
    field_token: str,
    strings: dict[int, str],
) -> dict[int, str]:
    start, end = _dictionary_range(instructions, field_token)
    if start is None:
        return {}
    values = {}
    for index in range(start, end):
        instruction = instructions[index]
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "callvirt"
            and isinstance(operand, dict)
            and operand.get("name") == "Add"
            and index >= 3
        ):
            key = _integer_constant(instructions[index - 3])
            identifier = _integer_constant(instructions[index - 2])
            if (
                key is not None
                and identifier is not None
                and instructions[index - 1].get("opcode") == "call"
                and identifier in strings
            ):
                values[key] = strings[identifier]
    return values


def _dictionary_range(
    instructions: list[dict[str, Any]], field_token: str
) -> tuple[int | None, int | None]:
    for end, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "stfld"
            and isinstance(operand, dict)
            and operand.get("token") == field_token
        ):
            for start in range(end - 1, -1, -1):
                if instructions[start].get("opcode") == "newobj":
                    return start, end
    return None, None


def _nearest_string_id(instructions: list[dict[str, Any]], end: int) -> int:
    for index in range(end - 1, max(-1, end - 8), -1):
        operand = instructions[index].get("operand")
        if instructions[index].get("opcode") == "call" and isinstance(operand, dict):
            if index > 0:
                identifier = _integer_constant(instructions[index - 1])
                if identifier is not None:
                    return identifier
    raise ChipCatalogError("could not locate the string id assigned to a field")


def _integer_constant(instruction: dict[str, Any]) -> int | None:
    opcode = instruction.get("opcode")
    if opcode in {"ldc.i4", "ldc.i4.s"}:
        value = instruction.get("operand")
        return value if isinstance(value, int) else None
    if not isinstance(opcode, str) or not opcode.startswith("ldc.i4."):
        return None
    suffix = opcode.removeprefix("ldc.i4.")
    if suffix == "m1":
        return -1
    return int(suffix) if suffix.isdigit() else None


def _infer_pin_kind_names(
    chips: list[dict[str, Any]], register_names: dict[int, str]
) -> dict[int, str]:
    observations: dict[int, set[str]] = {}
    for chip in chips:
        for index, kind_value in chip["pin_kinds"].items():
            register = register_names.get(chip["pin_registers"].get(index))
            if register is None:
                continue
            if register.startswith("x"):
                observations.setdefault(kind_value, set()).add("xbus")
            elif register.startswith("p"):
                observations.setdefault(kind_value, set()).add("simple")
    conflicts = {key: values for key, values in observations.items() if len(values) != 1}
    if conflicts:
        raise ChipCatalogError(f"conflicting pin-kind observations: {conflicts}")
    return {key: next(iter(values)) for key, values in observations.items()}


def _parse_factory_segment(
    instructions: list[dict[str, Any]],
    strings: dict[int, str],
    chip_field_types: dict[str, str],
) -> dict[str, Any] | None:
    invoke_index = None
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "callvirt"
            and isinstance(operand, dict)
            and operand.get("name") == "Invoke"
        ):
            invoke_index = index
            break
    if invoke_index is None:
        return None

    decoded: list[tuple[int, int]] = []
    for index in range(invoke_index):
        identifier = _integer_constant(instructions[index])
        if identifier is None or identifier not in strings or index + 1 >= invoke_index:
            continue
        next_operand = instructions[index + 1].get("operand")
        if instructions[index + 1].get("opcode") == "call" and isinstance(next_operand, dict):
            decoded.append((index, identifier))
    if len(decoded) < 2:
        raise ChipCatalogError("factory ChipType record does not contain two decoded strings")

    first_string_index = decoded[0][0]
    selector = None
    for index in range(first_string_index - 1, -1, -1):
        selector = _integer_constant(instructions[index])
        if selector is not None:
            break
    if selector is None:
        return {
            "name": strings[decoded[0][1]],
            "type": strings[decoded[1][1]],
            "price_raw": 100,
            "size": (2, 2),
            "unlock": "welcome",
            "pin_kinds": {0: 1, 1: 1, 2: 1, 3: 1},
            "pin_labels": {},
            "pin_registers": {},
            "scalar_fields": [
                {
                    "field_token": "factory",
                    "field_type": chip_field_types.get("0x040008B5", "System.Int32"),
                    "value": 23,
                }
            ],
            "factory": "led",
            "custom_key": None,
        }

    uses_switch_factory = any(
        instruction.get("opcode") == "ldloc.1"
        for instruction in instructions[:invoke_index]
    )
    prefix = "SWITCH" if uses_switch_factory else "DIAL"
    kind_value = 1 if uses_switch_factory else 2
    return {
        "name": strings[decoded[0][1]],
        "type": f"{prefix}{selector}",
        "price_raw": 0,
        "size": (2, 1),
        "unlock": None,
        "pin_kinds": {0: kind_value, 1: kind_value},
        "pin_labels": {},
        "pin_registers": {},
        "scalar_fields": [
            {
                "field_token": "factory",
                "field_type": chip_field_types.get("0x040008B5", "System.Int32"),
                "value": 20 if uses_switch_factory else 19,
            }
        ],
        "factory": prefix.lower(),
        "custom_key": strings[decoded[1][1]],
    }


def _pin_slot(index: int, height: int) -> tuple[str, int]:
    if index < height:
        return "left", index
    if index < height * 2:
        return "right", height * 2 - 1 - index
    return "unknown", index


def _apply_manual_spec(chip: dict[str, Any], spec: ManualPartSpec) -> None:
    mismatches = []
    for field, actual, expected in (
        ("type", chip["type"], spec.expected_type_name),
        ("game price", chip["price"], spec.expected_game_price),
        ("size", tuple(chip["size"]), spec.expected_size),
    ):
        if actual != expected:
            mismatches.append(f"{field}: extracted {actual!r}, reference {expected!r}")

    pins_by_index = {pin["index"]: pin for pin in chip["pins"]}
    expected_indexes = {pin.index for pin in spec.pins}
    if set(pins_by_index) != expected_indexes:
        mismatches.append(
            f"pin indexes: extracted {sorted(pins_by_index)}, reference {sorted(expected_indexes)}"
        )

    for expected in spec.pins:
        actual = pins_by_index.get(expected.index)
        if actual is None:
            continue
        for field, actual_value, expected_value in (
            ("name", actual["name"], expected.alias),
            ("kind", actual["kind"], expected.kind),
            ("side", actual["side"], expected.side),
            ("side_offset", actual["side_offset"], expected.side_offset),
        ):
            if actual_value != expected_value:
                mismatches.append(
                    f"pin {expected.index} {field}: extracted {actual_value!r}, "
                    f"reference {expected_value!r}"
                )
        actual["official_name"] = expected.official_name
        actual["direction"] = expected.direction

    if mismatches:
        details = "; ".join(mismatches)
        raise ChipCatalogError(f"reference validation failed for {spec.name}: {details}")

    chip["manual"] = {
        "program_lines": spec.program_lines,
        "internal_registers": list(spec.internal_registers),
        "behavior_family": spec.behavior_family,
        "direction_model": spec.direction_model,
        "notes": list(spec.notes),
        "sources": spec.source_pages(),
    }
