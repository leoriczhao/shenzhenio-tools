from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chip_catalog import DEFAULT_OUTPUT as DEFAULT_CHIPS
from .game_metadata import DEFAULT_OUTPUT as DEFAULT_METADATA, load_game_metadata
from .game_strings import DEFAULT_OUTPUT as DEFAULT_STRINGS


FORMAT_NAME = "shzio-puzzle-catalog"
FORMAT_VERSION = 1
DEFAULT_OUTPUT = DEFAULT_METADATA.with_name("puzzle-catalog.json")


class PuzzleCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class PuzzleCatalogProfile:
    puzzle_id_field: str = "0x04000A7E"
    test_delegate_field: str = "0x04000A88"
    terminals_field: str = "0x04000A89"
    provided_chips_field: str = "0x04000A8A"
    initial_traces_field: str = "0x04000A8B"
    board_variant_field: str = "0x04000A8C"
    board_data_field: str = "0x04000A8E"
    canvas_width: int = 22
    canvas_height: int = 14
    board_tuple_width: int = 3


DEFAULT_PROFILE = PuzzleCatalogProfile()


def build_puzzle_catalog(
    metadata: dict[str, Any],
    strings: dict[str, Any],
    chips: dict[str, Any],
    profile: PuzzleCatalogProfile = DEFAULT_PROFILE,
) -> dict[str, Any]:
    string_values = {
        row["id"]: row["value"]
        for row in strings.get("strings", [])
        if isinstance(row, dict)
        and isinstance(row.get("id"), int)
        and isinstance(row.get("value"), str)
    }
    if not string_values:
        raise PuzzleCatalogError("decoded string report contains no strings")

    puzzle_type = _type(metadata, "Puzzle")
    puzzles_type = _type(metadata, "Puzzles")
    terminal_type = _type(metadata, "Terminal")
    provided_pin_type = _type(metadata, "PuzzleProvidedChipTerminalPin")

    puzzle_fields = {
        field["metadata_token"]: field for field in puzzle_type.get("fields", [])
    }
    static_puzzle_fields = {
        field["metadata_token"]
        for field in puzzles_type.get("fields", [])
        if field.get("type") == "Puzzle"
    }
    terminal_enum_names = _enum_names(metadata, "TerminalType")
    direction_enum_names = _enum_names(metadata, "TerminalDirection")

    provided_array_type = puzzle_fields[profile.provided_chips_field]["type"]
    if not provided_array_type.endswith("[]"):
        raise PuzzleCatalogError("Puzzle provided-chip field is not an array")
    provided_type_name = provided_array_type[:-2]
    provided_type = _type(metadata, provided_type_name)
    provided_fields = {
        field["metadata_token"]: field for field in provided_type.get("fields", [])
    }
    provided_chip_field = _one_field_token(provided_fields, "ChipType")
    provided_position_field = _one_field_token(provided_fields, "Index2")
    provided_pin_array_field = _one_field_token(
        provided_fields, "PuzzleProvidedChipTerminalPin[]"
    )
    provided_boolean_fields = [
        token for token, field in provided_fields.items() if field.get("type") == "System.Boolean"
    ]
    provided_string_fields = [
        token for token, field in provided_fields.items() if field.get("type") == "System.String"
    ]

    chip_by_token = {
        chip["static_field_token"]: chip
        for chip in chips.get("chips", [])
        if isinstance(chip, dict) and isinstance(chip.get("static_field_token"), str)
    }
    initialized_data = {
        row["metadata_token"]: row
        for row in metadata.get("initialized_data_fields", [])
        if isinstance(row, dict) and isinstance(row.get("metadata_token"), str)
    }

    initializer = _initializer(metadata, "Puzzles")
    segments = _split_puzzle_segments(initializer, static_puzzle_fields)
    if len(segments) != len(static_puzzle_fields):
        raise PuzzleCatalogError(
            f"split {len(segments)} Puzzle records for {len(static_puzzle_fields)} static fields"
        )

    records = []
    for segment in segments:
        puzzle_id = _string_field(segment, profile.puzzle_id_field, string_values)
        terminals = _parse_terminals(
            segment,
            profile.terminals_field,
            terminal_type["full_name"],
            string_values,
            terminal_enum_names,
            direction_enum_names,
        )
        try:
            provided_chips = _parse_provided_chips(
                segment,
                profile.provided_chips_field,
                provided_type_name,
                provided_chip_field,
                provided_position_field,
                provided_pin_array_field,
                provided_boolean_fields,
                provided_string_fields,
                provided_pin_type["full_name"],
                string_values,
                chip_by_token,
            )
        except PuzzleCatalogError as exc:
            raise PuzzleCatalogError(f"Puzzle {puzzle_id}: {exc}") from exc
        final_operand = segment[-1].get("operand")
        if not isinstance(final_operand, dict):
            raise PuzzleCatalogError(f"Puzzle {puzzle_id} has no static field target")

        records.append(
            {
                "id": puzzle_id,
                "static_field_token": final_operand.get("token"),
                "canvas_size": [profile.canvas_width, profile.canvas_height],
                "board_variant_value": _integer_field(
                    segment, profile.board_variant_field, default=0
                ),
                "test_method": _delegate_method(segment, profile.test_delegate_field),
                "terminals": terminals,
                "provided_chips": provided_chips,
                "initial_traces": _parse_initial_traces(
                    segment, profile.initial_traces_field
                ),
                "board_data": _parse_board_data(
                    segment,
                    profile.board_data_field,
                    initialized_data,
                    profile.canvas_width,
                    profile.board_tuple_width,
                ),
                "scalar_fields": _scalar_fields(segment, puzzle_fields),
                "il_range": [segment[0]["offset"], segment[-1]["offset"]],
            }
        )

    ids = [record["id"] for record in records]
    duplicates = sorted({puzzle_id for puzzle_id in ids if ids.count(puzzle_id) > 1})
    if duplicates:
        raise PuzzleCatalogError(f"duplicate puzzle ids: {duplicates}")

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
            "chips_format": chips.get("format"),
        },
        "terminal_type_values": {
            str(value): name for value, name in sorted(terminal_enum_names.items())
        },
        "terminal_direction_values": {
            str(value): name for value, name in sorted(direction_enum_names.items())
        },
        "summary": {
            "puzzle_count": len(records),
            "terminal_count": sum(len(record["terminals"]) for record in records),
            "provided_chip_count": sum(
                len(record["provided_chips"]) for record in records
            ),
            "initial_trace_count": sum(
                len(record["initial_traces"]) for record in records
            ),
            "decoded_board_count": sum(
                record["board_data"] is not None
                and record["board_data"]["encoding"] == "raw-int32-tuples"
                for record in records
            ),
        },
        "puzzles": records,
    }


def write_puzzle_catalog(
    metadata_path: str | Path = DEFAULT_METADATA,
    strings_path: str | Path = DEFAULT_STRINGS,
    chips_path: str | Path = DEFAULT_CHIPS,
    output: str | Path = DEFAULT_OUTPUT,
) -> tuple[Path, dict[str, Any]]:
    metadata_file = Path(metadata_path).resolve()
    metadata = load_game_metadata(metadata_file)
    strings = _read_report(strings_path, "shzio-game-strings", "strings")
    chips = _read_report(chips_path, "shzio-chip-catalog", "chip catalog")
    _validate_source_hash(metadata, strings, "strings")
    _validate_source_hash(metadata, chips, "chip catalog")

    payload = build_puzzle_catalog(metadata, strings, chips)
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path, payload


def load_puzzle_catalog(path: str | Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    return _read_report(path, FORMAT_NAME, "puzzle catalog")


def find_puzzle(payload: dict[str, Any], puzzle_id: str) -> dict[str, Any]:
    matches = [
        puzzle for puzzle in payload.get("puzzles", []) if puzzle.get("id") == puzzle_id
    ]
    if len(matches) != 1:
        raise PuzzleCatalogError(
            f"expected one puzzle {puzzle_id!r}, found {len(matches)}"
        )
    return matches[0]


def _type(metadata: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [entry for entry in metadata.get("types", []) if entry.get("full_name") == name]
    if len(matches) != 1:
        raise PuzzleCatalogError(f"expected one detailed type {name!r}, found {len(matches)}")
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
        raise PuzzleCatalogError(
            f"expected one initializer for {type_name}, found {len(matches)}"
        )
    instructions = matches[0]["body"].get("instructions")
    if not isinstance(instructions, list):
        raise PuzzleCatalogError(f"initializer for {type_name} has no instructions")
    return instructions


def _split_puzzle_segments(
    instructions: list[dict[str, Any]], static_puzzle_fields: set[str]
) -> list[list[dict[str, Any]]]:
    segments = []
    start = None
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "newobj"
            and isinstance(operand, dict)
            and operand.get("declaring_type") == "Puzzle"
        ):
            start = index
        if (
            start is not None
            and instruction.get("opcode") == "stsfld"
            and isinstance(operand, dict)
            and operand.get("token") in static_puzzle_fields
        ):
            segments.append(instructions[start : index + 1])
            start = None
    return segments


def _enum_names(metadata: dict[str, Any], type_name: str) -> dict[int, str]:
    enum_type = _type(metadata, type_name)
    values = {
        field["constant"]: field["name"]
        for field in enum_type.get("fields", [])
        if isinstance(field.get("constant"), int) and field.get("name") != "value__"
    }
    if not values:
        raise PuzzleCatalogError(f"enum {type_name} has no literal values")
    return values


def _one_field_token(fields: dict[str, dict[str, Any]], field_type: str) -> str:
    matches = [token for token, field in fields.items() if field.get("type") == field_type]
    if len(matches) != 1:
        raise PuzzleCatalogError(
            f"expected one field of type {field_type!r}, found {len(matches)}"
        )
    return matches[0]


def _string_field(
    instructions: list[dict[str, Any]], field_token: str, strings: dict[int, str]
) -> str:
    for index, instruction in enumerate(instructions):
        if _field_token(instruction) != field_token:
            continue
        identifiers = _string_ids(instructions, max(0, index - 8), index, strings)
        if not identifiers:
            break
        return strings[identifiers[-1]]
    raise PuzzleCatalogError(f"required string field {field_token} is not assigned")


def _parse_terminals(
    instructions: list[dict[str, Any]],
    field_token: str,
    terminal_type_name: str,
    strings: dict[int, str],
    terminal_types: dict[int, str],
    directions: dict[int, str],
) -> list[dict[str, Any]]:
    field_end = _field_assignment_index(instructions, field_token)
    if field_end is None:
        return []
    array_start = _nearest_newarr_type(instructions, field_end, terminal_type_name)
    if array_start is None:
        raise PuzzleCatalogError("Terminal[] assignment has no newarr")
    expected_count = _integer_constant(instructions[array_start - 1])

    terminals = []
    item_start = array_start + 1
    for index in range(array_start + 1, field_end):
        instruction = instructions[index]
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") != "newobj"
            or not isinstance(operand, dict)
            or operand.get("declaring_type") != terminal_type_name
        ):
            continue
        values = _trailing_integer_constants(instructions, index)
        if len(values) != 5:
            raise PuzzleCatalogError(
                f"Terminal constructor at IL {instruction.get('offset')} has {len(values)} integer arguments"
            )
        type_value, direction_value, x, y, nonblocking = values
        identifiers = _string_ids(instructions, item_start, index, strings)
        if not identifiers:
            raise PuzzleCatalogError(
                f"Terminal constructor at IL {instruction.get('offset')} has no decoded name"
            )
        array_index = _array_item_index(instructions, item_start, index)
        terminals.append(
            {
                "array_index": array_index,
                "name": strings[identifiers[0]],
                "localization_context": strings[identifiers[1]]
                if len(identifiers) > 1
                else None,
                "type": terminal_types.get(type_value, f"unknown-{type_value}"),
                "type_value": type_value,
                "direction": directions.get(direction_value, f"unknown-{direction_value}"),
                "direction_value": direction_value,
                "position_raw": [x, y],
                "nonblocking_flag": bool(nonblocking),
            }
        )
        item_start = index + 1

    if expected_count is not None and len(terminals) != expected_count:
        raise PuzzleCatalogError(
            f"Terminal[] declares {expected_count} items but parsed {len(terminals)}"
        )
    return terminals


def _parse_provided_chips(
    instructions: list[dict[str, Any]],
    field_token: str,
    provided_type_name: str,
    chip_field: str,
    position_field: str,
    pin_array_field: str,
    boolean_fields: list[str],
    string_fields: list[str],
    provided_pin_type_name: str,
    strings: dict[int, str],
    chip_by_token: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    field_end = _field_assignment_index(instructions, field_token)
    if field_end is None:
        return []
    array_start = _nearest_newarr_type(instructions, field_end, provided_type_name)
    if array_start is None:
        raise PuzzleCatalogError("provided-chip assignment has no newarr")
    expected_count = _integer_constant(instructions[array_start - 1])

    starts = []
    for index in range(array_start + 1, field_end):
        operand = instructions[index].get("operand")
        if (
            instructions[index].get("opcode") == "newobj"
            and isinstance(operand, dict)
            and operand.get("declaring_type") == provided_type_name
        ):
            starts.append(index)

    provided = []
    for item_number, start in enumerate(starts):
        end = starts[item_number + 1] if item_number + 1 < len(starts) else field_end
        chunk = instructions[start:end]
        static_chip_token = _loaded_static_field(chunk, chip_field)
        chip = chip_by_token.get(static_chip_token)
        position = _index2_assigned_to(chunk, position_field)
        pin_links = _parse_provided_pin_links(chunk, provided_pin_type_name, strings)
        provided.append(
            {
                "array_index": item_number,
                "chip_static_field_token": static_chip_token,
                "chip_name": chip.get("name") if chip else None,
                "chip_type": chip.get("type") if chip else None,
                "position_raw": list(position),
                "boolean_fields": {
                    token: bool(_integer_field(chunk, token, default=0))
                    for token in boolean_fields
                },
                "name": _optional_string_assignment(chunk, string_fields, strings),
                "terminal_pin_links": pin_links,
                "pin_array_field_token": pin_array_field,
            }
        )

    if expected_count is not None and len(provided) != expected_count:
        raise PuzzleCatalogError(
            f"provided-chip array declares {expected_count} items but parsed {len(provided)}"
        )
    return provided


def _parse_provided_pin_links(
    instructions: list[dict[str, Any]],
    pin_type_name: str,
    strings: dict[int, str],
) -> list[dict[str, Any]]:
    links = []
    item_start = 0
    for index, instruction in enumerate(instructions):
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") != "newobj"
            or not isinstance(operand, dict)
            or operand.get("declaring_type") != pin_type_name
        ):
            continue
        pin_index = _integer_constant(instructions[index - 1]) if index else None
        identifiers = _string_ids(instructions, item_start, index, strings)
        if pin_index is None or not identifiers:
            raise PuzzleCatalogError(
                f"provided terminal-pin constructor at IL {instruction.get('offset')} is incomplete"
            )
        links.append(
            {
                "terminal_name": strings[identifiers[-1]],
                "pin_index": pin_index,
            }
        )
        item_start = index + 1
    return links


def _parse_initial_traces(
    instructions: list[dict[str, Any]], field_token: str
) -> list[dict[str, Any]]:
    field_end = _field_assignment_index(instructions, field_token)
    if field_end is None:
        return []
    dictionary_start = None
    for index in range(field_end - 1, -1, -1):
        instruction = instructions[index]
        operand = instruction.get("operand")
        declaring_type = operand.get("declaring_type") if isinstance(operand, dict) else None
        if (
            instruction.get("opcode") == "newobj"
            and isinstance(declaring_type, str)
            and "Dictionary`2" in declaring_type
            and "Index2" in declaring_type
            and "Trace" in declaring_type
        ):
            dictionary_start = index
            break
    if dictionary_start is None:
        raise PuzzleCatalogError("initial-trace dictionary has no constructor")

    traces = []
    for index in range(dictionary_start + 1, field_end):
        instruction = instructions[index]
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") != "callvirt"
            or not isinstance(operand, dict)
            or operand.get("name") != "Add"
        ):
            continue
        index2 = _nearest_newobj_type(instructions, dictionary_start, index, "Index2")
        if index2 is None or index2 < 2:
            continue
        x = _integer_constant(instructions[index2 - 2])
        y = _integer_constant(instructions[index2 - 1])
        trace_value = _integer_constant(instructions[index - 1])
        if x is not None and y is not None and trace_value is not None:
            traces.append({"position_raw": [x, y], "trace_value": trace_value})
    return traces


def _parse_board_data(
    instructions: list[dict[str, Any]],
    field_token: str,
    initialized_data: dict[str, dict[str, Any]],
    width: int,
    tuple_width: int,
) -> dict[str, Any] | None:
    field_end = _field_assignment_index(instructions, field_token)
    if field_end is None:
        return None
    array_start = _nearest_opcode(instructions, field_end, "newarr")
    if array_start is None:
        raise PuzzleCatalogError("board data assignment has no array allocation")
    count = _integer_constant(instructions[array_start - 1])
    data_token = None
    for instruction in instructions[array_start + 1 : field_end]:
        if instruction.get("opcode") != "ldtoken":
            continue
        operand = instruction.get("operand")
        if isinstance(operand, dict):
            data_token = operand.get("token")
            break
    result = {
        "int_count": count,
        "initialized_data_field_token": data_token,
        "encoding": "unavailable",
    }
    data = initialized_data.get(data_token)
    if data is None or not isinstance(data.get("data_base64"), str):
        return result
    if count is None:
        raise PuzzleCatalogError(f"board data field {data_token} has no constant length")

    try:
        raw = base64.b64decode(data["data_base64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise PuzzleCatalogError(f"board data field {data_token} is not valid base64") from exc
    expected_size = count * 4
    if len(raw) != expected_size:
        raise PuzzleCatalogError(
            f"board data field {data_token} has {len(raw)} bytes, expected {expected_size}"
        )
    row_width = width * tuple_width
    if count % row_width != 0:
        raise PuzzleCatalogError(
            f"board data field {data_token} has {count} integers for rows of {row_width}"
        )
    height = count // row_width
    values = struct.unpack(f"<{count}i", raw)
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            start = (y * width + x) * tuple_width
            row.append(list(values[start : start + tuple_width]))
        rows.append(row)

    result.update(
        {
            "encoding": "raw-int32-tuples",
            "tile_width": width,
            "tile_height": height,
            "tuple_width": tuple_width,
            "data_sha256": data.get("sha256"),
            "cells": rows,
        }
    )
    return result


def _delegate_method(
    instructions: list[dict[str, Any]], field_token: str
) -> dict[str, Any] | None:
    field_end = _field_assignment_index(instructions, field_token)
    if field_end is None:
        return None
    for index in range(field_end - 1, -1, -1):
        if instructions[index].get("opcode") != "ldftn":
            continue
        operand = instructions[index].get("operand")
        if isinstance(operand, dict):
            return {
                "metadata_token": operand.get("token"),
                "declaring_type": operand.get("declaring_type"),
                "name": operand.get("name"),
            }
    return None


def _scalar_fields(
    instructions: list[dict[str, Any]], fields: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    values = []
    for index, instruction in enumerate(instructions):
        token = _field_token(instruction)
        if token not in fields or index == 0:
            continue
        value = _integer_constant(instructions[index - 1])
        if value is None:
            continue
        values.append(
            {
                "field_token": token,
                "field_type": fields[token].get("type"),
                "value": value,
            }
        )
    return values


def _integer_field(
    instructions: list[dict[str, Any]], field_token: str, *, default: int
) -> int:
    for index, instruction in enumerate(instructions):
        if _field_token(instruction) != field_token or index == 0:
            continue
        value = _integer_constant(instructions[index - 1])
        if value is not None:
            return value
    return default


def _loaded_static_field(instructions: list[dict[str, Any]], target_field: str) -> str:
    target_index = _field_assignment_index(instructions, target_field)
    if target_index is None:
        raise PuzzleCatalogError(f"provided chip field {target_field} is not assigned")
    for index in range(target_index - 1, -1, -1):
        instruction = instructions[index]
        operand = instruction.get("operand")
        if instruction.get("opcode") == "ldsfld" and isinstance(operand, dict):
            return operand.get("token")
    raise PuzzleCatalogError("provided chip assignment has no static ChipType load")


def _index2_assigned_to(
    instructions: list[dict[str, Any]], target_field: str
) -> tuple[int, int]:
    target_index = _field_assignment_index(instructions, target_field)
    if target_index is None:
        raise PuzzleCatalogError(f"Index2 field {target_field} is not assigned")
    constructor = _nearest_newobj_type(instructions, 0, target_index, "Index2")
    if constructor is None or constructor < 2:
        raise PuzzleCatalogError(f"Index2 field {target_field} has no constructor")
    x = _integer_constant(instructions[constructor - 2])
    y = _integer_constant(instructions[constructor - 1])
    if x is None or y is None:
        raise PuzzleCatalogError(f"Index2 field {target_field} has nonconstant coordinates")
    return x, y


def _optional_string_assignment(
    instructions: list[dict[str, Any]],
    field_tokens: list[str],
    strings: dict[int, str],
) -> str | None:
    for token in field_tokens:
        field_index = _field_assignment_index(instructions, token)
        if field_index is None:
            continue
        identifiers = _string_ids(
            instructions, max(0, field_index - 8), field_index, strings
        )
        if identifiers:
            return strings[identifiers[-1]]
    return None


def _string_ids(
    instructions: list[dict[str, Any]],
    start: int,
    end: int,
    strings: dict[int, str],
) -> list[int]:
    identifiers = []
    for index in range(max(start + 1, 1), end):
        if instructions[index].get("opcode") != "call":
            continue
        identifier = _integer_constant(instructions[index - 1])
        if identifier is not None and identifier in strings:
            identifiers.append(identifier)
    return identifiers


def _trailing_integer_constants(
    instructions: list[dict[str, Any]], end: int
) -> list[int]:
    values = []
    for index in range(end - 1, -1, -1):
        value = _integer_constant(instructions[index])
        if value is None:
            break
        values.append(value)
    return list(reversed(values))


def _array_item_index(
    instructions: list[dict[str, Any]], start: int, end: int
) -> int | None:
    for index in range(start, end - 1):
        if instructions[index].get("opcode") != "dup":
            continue
        value = _integer_constant(instructions[index + 1])
        if value is not None:
            return value
    return None


def _field_assignment_index(
    instructions: list[dict[str, Any]], field_token: str
) -> int | None:
    for index, instruction in enumerate(instructions):
        if _field_token(instruction) == field_token:
            return index
    return None


def _field_token(instruction: dict[str, Any]) -> str | None:
    if instruction.get("opcode") != "stfld":
        return None
    operand = instruction.get("operand")
    return operand.get("token") if isinstance(operand, dict) else None


def _nearest_opcode(
    instructions: list[dict[str, Any]], end: int, opcode: str
) -> int | None:
    for index in range(end - 1, -1, -1):
        if instructions[index].get("opcode") == opcode:
            return index
    return None


def _nearest_newobj_type(
    instructions: list[dict[str, Any]], start: int, end: int, type_name: str
) -> int | None:
    for index in range(end - 1, start - 1, -1):
        instruction = instructions[index]
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "newobj"
            and isinstance(operand, dict)
            and operand.get("declaring_type") == type_name
        ):
            return index
    return None


def _nearest_newarr_type(
    instructions: list[dict[str, Any]], end: int, type_name: str
) -> int | None:
    for index in range(end - 1, -1, -1):
        instruction = instructions[index]
        operand = instruction.get("operand")
        if (
            instruction.get("opcode") == "newarr"
            and isinstance(operand, dict)
            and operand.get("name") == type_name
        ):
            return index
    return None


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


def _read_report(path: str | Path, expected_format: str, description: str) -> dict[str, Any]:
    report_path = Path(path).resolve()
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PuzzleCatalogError(f"failed to read {description} {report_path}: {exc}") from exc
    if payload.get("format") != expected_format:
        raise PuzzleCatalogError(
            f"unexpected {description} format {payload.get('format')!r}"
        )
    return payload


def _validate_source_hash(
    metadata: dict[str, Any], report: dict[str, Any], description: str
) -> None:
    expected = metadata.get("source", {}).get("sha256")
    actual = report.get("source", {}).get("game_sha256")
    if expected != actual:
        raise PuzzleCatalogError(
            f"{description} source hash {actual!r} does not match metadata {expected!r}"
        )
