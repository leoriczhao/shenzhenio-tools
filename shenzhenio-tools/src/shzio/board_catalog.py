from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .chip_catalog import DEFAULT_OUTPUT as DEFAULT_CHIPS
from .puzzle_catalog import DEFAULT_OUTPUT as DEFAULT_PUZZLES


FORMAT_NAME = "shzio-board-catalog"
FORMAT_VERSION = 2
DEFAULT_OUTPUT = DEFAULT_PUZZLES.with_name("board-catalog.json")
PROVIDED_ROTATION_FIELD = "0x0400087D"
ROUTABLE_TEXTURE_INDEXES = frozenset({1, 9})
PLACEMENT_MARGIN = (1, 1)


class BoardCatalogError(RuntimeError):
    pass


class ElectricalKind(str, Enum):
    SIMPLE = "simple"
    XBUS = "xbus"
    DISPLAY = "display"


class DeviceDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True)
class BoardTileSpec:
    x: int
    y: int
    texture_index: int
    quarter_turns: int
    flip_flags: int


@dataclass(frozen=True)
class BoardPinSpec:
    index: int
    name: str
    kind: ElectricalKind
    contact: tuple[int, int]


@dataclass(frozen=True)
class BoardPartSpec:
    index: int
    chip_type: str
    chip_name: str
    position: tuple[int, int]
    rotated: bool
    pins: tuple[BoardPinSpec, ...]


@dataclass(frozen=True)
class TerminalBinding:
    provided_part_index: int
    chip_type: str
    pin_index: int
    pin_name: str


@dataclass(frozen=True)
class BoardTerminalSpec:
    index: int
    name: str
    terminal_type: str
    electrical_kind: ElectricalKind
    device_direction: DeviceDirection
    position: tuple[int, int]
    contact: tuple[int, int]
    nonblocking: bool
    binding: TerminalBinding | None


@dataclass(frozen=True)
class BoardSpec:
    puzzle_id: str
    canvas_size: tuple[int, int]
    tile_grid_size: tuple[int, int]
    board_variant_value: int
    placement_origin: tuple[int, int]
    placement_size: tuple[int, int]
    routable_cells: frozenset[tuple[int, int]]
    tiles: tuple[BoardTileSpec, ...]
    provided_parts: tuple[BoardPartSpec, ...]
    terminals: tuple[BoardTerminalSpec, ...]
    initial_traces: tuple[tuple[int, int, int], ...]

    def is_routable(self, position: tuple[int, int]) -> bool:
        return position in self.routable_cells

    def contains_footprint(
        self, position: tuple[int, int], size: tuple[int, int]
    ) -> bool:
        x, y = position
        width, height = size
        origin_x, origin_y = self.placement_origin
        placement_width, placement_height = self.placement_size
        return (
            width >= 0
            and height >= 0
            and x >= origin_x
            and y >= origin_y
            and x + width <= origin_x + placement_width
            and y + height <= origin_y + placement_height
        )

    def terminal(self, name: str) -> BoardTerminalSpec:
        matches = [terminal for terminal in self.terminals if terminal.name == name]
        if len(matches) != 1:
            raise BoardCatalogError(
                f"expected one terminal {name!r} on {self.puzzle_id}, found {len(matches)}"
            )
        return matches[0]


TERMINAL_ELECTRICAL_KINDS = {
    "Digital": ElectricalKind.SIMPLE,
    "Analog": ElectricalKind.SIMPLE,
    "Audio": ElectricalKind.SIMPLE,
    "XBus": ElectricalKind.XBUS,
    "NonBlockingXBus": ElectricalKind.XBUS,
    "Display": ElectricalKind.DISPLAY,
}

# Puzzle direction describes the circuit's role. This is the direction of the
# attached board device as seen by the user's circuit.
TERMINAL_DEVICE_DIRECTIONS = {
    "Input": DeviceDirection.OUTPUT,
    "Output": DeviceDirection.INPUT,
}


def build_board_catalog(
    puzzles: dict[str, Any], chips: dict[str, Any]
) -> dict[str, Any]:
    _validate_source_hash(puzzles, chips)
    chip_by_token = {
        chip["static_field_token"]: chip
        for chip in chips.get("chips", [])
        if isinstance(chip, dict) and isinstance(chip.get("static_field_token"), str)
    }
    boards = [
        _normalize_board(puzzle, chip_by_token)
        for puzzle in puzzles.get("puzzles", [])
        if isinstance(puzzle, dict)
    ]
    unresolved_contacts = sum(
        terminal.get("contact") is None
        for board in boards
        for terminal in board["terminals"]
    )
    if unresolved_contacts:
        raise BoardCatalogError(
            f"failed to resolve {unresolved_contacts} terminal contact positions"
        )

    source = puzzles.get("source", {})
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "source": {
            "game_exe": source.get("game_exe"),
            "game_sha256": source.get("game_sha256"),
            "module_version_id": source.get("module_version_id"),
            "puzzles_format": puzzles.get("format"),
            "chips_format": chips.get("format"),
        },
        "coordinate_model": {
            "space": "solution-canvas",
            "origin": "bottom-left",
            "stored_positions": "used-without-transform",
            "provided_rotation": "180-degrees",
            "provided_rotation_field": PROVIDED_ROTATION_FIELD,
            "method_tokens": ["0x06000A02", "0x06000A31", "0x06000F39"],
        },
        "tile_model": {
            "tuple_fields": [
                "texture_index",
                "quarter_turns_clockwise",
                "flip_flags",
            ],
            "flip_flag_bits": {"horizontal": 1, "vertical": 2},
            "method_tokens": ["0x06000287", "0x06000260"],
        },
        "routing_model": {
            "routable_texture_indexes": sorted(ROUTABLE_TEXTURE_INDEXES),
            "trace_exists_bit": 16,
            "trace_direction_bits": {
                "right": 1,
                "up": 2,
                "left": 4,
                "down": 8,
            },
            "saved_row_order": "highest-y-first",
            "method_tokens": ["0x06000287", "0x06000A35", "0x06000F3E", "0x06000F42"],
        },
        "placement_model": {
            "canvas_margin": list(PLACEMENT_MARGIN),
            "ordinary_overlap_allowed": False,
            "bridge_terminal_overlap": "terminal-at-bridge-origin-plus-0,1",
            "method_token": "0x06000A35",
        },
        "summary": {
            "board_count": len(boards),
            "tile_count": sum(len(board["tiles"]) for board in boards),
            "routable_cell_count": sum(
                len(board["routable_cells"]) for board in boards
            ),
            "terminal_count": sum(len(board["terminals"]) for board in boards),
            "provided_part_count": sum(
                len(board["provided_parts"]) for board in boards
            ),
            "unresolved_contact_count": unresolved_contacts,
        },
        "boards": boards,
    }


def write_board_catalog(
    puzzles_path: str | Path = DEFAULT_PUZZLES,
    chips_path: str | Path = DEFAULT_CHIPS,
    output: str | Path = DEFAULT_OUTPUT,
) -> tuple[Path, dict[str, Any]]:
    puzzles = _read_report(puzzles_path, "shzio-puzzle-catalog", "puzzle catalog")
    chips = _read_report(chips_path, "shzio-chip-catalog", "chip catalog")
    payload = build_board_catalog(puzzles, chips)
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path, payload


def load_board_catalog(path: str | Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    return _read_report(path, FORMAT_NAME, "board catalog")


def find_board(payload: dict[str, Any], puzzle_id: str) -> dict[str, Any]:
    matches = [
        board for board in payload.get("boards", []) if board.get("id") == puzzle_id
    ]
    if len(matches) != 1:
        raise BoardCatalogError(
            f"expected one board {puzzle_id!r}, found {len(matches)}"
        )
    return matches[0]


def load_board_spec(
    puzzle_id: str, path: str | Path = DEFAULT_OUTPUT
) -> BoardSpec:
    return board_spec_from_record(find_board(load_board_catalog(path), puzzle_id))


def board_spec_from_record(record: dict[str, Any]) -> BoardSpec:
    parts = tuple(
        BoardPartSpec(
            index=part["index"],
            chip_type=part["chip_type"],
            chip_name=part["chip_name"],
            position=tuple(part["position"]),
            rotated=part["rotated"],
            pins=tuple(
                BoardPinSpec(
                    index=pin["index"],
                    name=pin["name"],
                    kind=ElectricalKind(pin["kind"]),
                    contact=tuple(pin["contact"]),
                )
                for pin in part["pins"]
            ),
        )
        for part in record["provided_parts"]
    )
    terminals = tuple(
        BoardTerminalSpec(
            index=terminal["index"],
            name=terminal["name"],
            terminal_type=terminal["terminal_type"],
            electrical_kind=ElectricalKind(terminal["electrical_kind"]),
            device_direction=DeviceDirection(terminal["device_direction"]),
            position=tuple(terminal["position"]),
            contact=tuple(terminal["contact"]),
            nonblocking=terminal["nonblocking"],
            binding=TerminalBinding(**terminal["binding"])
            if terminal["binding"] is not None
            else None,
        )
        for terminal in record["terminals"]
    )
    return BoardSpec(
        puzzle_id=record["id"],
        canvas_size=tuple(record["canvas_size"]),
        tile_grid_size=tuple(record["tile_grid_size"]),
        board_variant_value=record["board_variant_value"],
        placement_origin=tuple(record["placement_origin"]),
        placement_size=tuple(record["placement_size"]),
        routable_cells=frozenset(tuple(cell) for cell in record["routable_cells"]),
        tiles=tuple(
            BoardTileSpec(
                x=tile["position"][0],
                y=tile["position"][1],
                texture_index=tile["texture_index"],
                quarter_turns=tile["quarter_turns"],
                flip_flags=tile["flip_flags"],
            )
            for tile in record["tiles"]
        ),
        provided_parts=parts,
        terminals=terminals,
        initial_traces=tuple(
            (trace["position"][0], trace["position"][1], trace["trace_value"])
            for trace in record["initial_traces"]
        ),
    )


def _normalize_board(
    puzzle: dict[str, Any], chip_by_token: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    puzzle_id = puzzle.get("id")
    board_data = puzzle.get("board_data")
    if not isinstance(board_data, dict) or board_data.get("encoding") != "raw-int32-tuples":
        raise BoardCatalogError(f"Puzzle {puzzle_id}: decoded board data is unavailable")
    tuple_semantics = board_data.get("tuple_semantics", {})
    if tuple_semantics.get("fields") != [
        "texture_index",
        "quarter_turns_clockwise",
        "flip_flags",
    ] or tuple_semantics.get("flip_flag_bits") != {
        "horizontal": 1,
        "vertical": 2,
    }:
        raise BoardCatalogError(
            f"Puzzle {puzzle_id}: board tuple semantics are unavailable or incompatible"
        )

    provided_parts = []
    link_by_terminal: dict[str, dict[str, Any]] = {}
    for raw_part in puzzle.get("provided_chips", []):
        token = raw_part.get("chip_static_field_token")
        chip = chip_by_token.get(token)
        if chip is None:
            raise BoardCatalogError(
                f"Puzzle {puzzle_id}: unknown provided chip token {token!r}"
            )
        position = _coordinate(raw_part.get("position_raw"), "provided chip position")
        rotated = bool(
            raw_part.get("boolean_fields", {}).get(PROVIDED_ROTATION_FIELD, False)
        )
        pins = []
        pins_by_index = {}
        for raw_pin in chip.get("pins", []):
            offset_key = "rotated_contact_offset" if rotated else "contact_offset"
            offset = _coordinate(raw_pin.get(offset_key), f"chip pin {raw_pin.get('index')}")
            pin = {
                "index": raw_pin["index"],
                "name": raw_pin["name"],
                "kind": raw_pin["kind"],
                "contact": [position[0] + offset[0], position[1] + offset[1]],
            }
            pins.append(pin)
            pins_by_index[pin["index"]] = pin
        part = {
            "index": raw_part["array_index"],
            "chip_static_field_token": token,
            "chip_type": chip["type"],
            "chip_name": chip["name"],
            "position": list(position),
            "rotated": rotated,
            "pins": pins,
            "terminal_links": [],
        }
        for link in raw_part.get("terminal_pin_links", []):
            terminal_name = link["terminal_name"]
            pin_index = link["pin_index"]
            pin = pins_by_index.get(pin_index)
            if pin is None:
                raise BoardCatalogError(
                    f"Puzzle {puzzle_id}: {chip['type']} has no linked pin {pin_index}"
                )
            if terminal_name in link_by_terminal:
                raise BoardCatalogError(
                    f"Puzzle {puzzle_id}: terminal {terminal_name!r} is linked twice"
                )
            binding = {
                "provided_part_index": part["index"],
                "chip_type": chip["type"],
                "pin_index": pin_index,
                "pin_name": pin["name"],
            }
            part["terminal_links"].append(
                {
                    "terminal_name": terminal_name,
                    "pin_index": pin_index,
                    "pin_name": pin["name"],
                    "contact": pin["contact"],
                }
            )
            link_by_terminal[terminal_name] = {
                "binding": binding,
                "contact": pin["contact"],
            }
        provided_parts.append(part)

    terminals = []
    terminal_names = set()
    for raw_terminal in puzzle.get("terminals", []):
        name = raw_terminal["name"]
        if name in terminal_names:
            raise BoardCatalogError(f"Puzzle {puzzle_id}: duplicate terminal {name!r}")
        terminal_names.add(name)
        terminal_type = raw_terminal["type"]
        direction = raw_terminal["direction"]
        try:
            electrical_kind = TERMINAL_ELECTRICAL_KINDS[terminal_type]
            device_direction = TERMINAL_DEVICE_DIRECTIONS[direction]
        except KeyError as exc:
            raise BoardCatalogError(
                f"Puzzle {puzzle_id}: unknown terminal model {exc.args[0]!r}"
            ) from exc
        position = _coordinate(raw_terminal.get("position_raw"), "terminal position")
        linked = link_by_terminal.get(name)
        terminals.append(
            {
                "index": raw_terminal["array_index"],
                "name": name,
                "terminal_type": terminal_type,
                "electrical_kind": electrical_kind.value,
                "puzzle_direction": direction,
                "device_direction": device_direction.value,
                "position": list(position),
                "contact": linked["contact"] if linked else list(position),
                "nonblocking": terminal_type == "NonBlockingXBus"
                or bool(raw_terminal.get("nonblocking_flag")),
                "binding": linked["binding"] if linked else None,
            }
        )
    missing_links = sorted(set(link_by_terminal) - terminal_names)
    if missing_links:
        raise BoardCatalogError(
            f"Puzzle {puzzle_id}: links reference missing terminals {missing_links}"
        )

    tiles = []
    routable_cells = []
    for y, row in enumerate(board_data.get("cells", [])):
        for x, values in enumerate(row):
            if not isinstance(values, list) or len(values) != 3:
                raise BoardCatalogError(
                    f"Puzzle {puzzle_id}: invalid tile tuple at ({x}, {y})"
                )
            texture_index, quarter_turns, flip_flags = values
            if texture_index < 0:
                continue
            tiles.append(
                {
                    "position": [x, y],
                    "texture_index": texture_index,
                    "quarter_turns": quarter_turns,
                    "flip_flags": flip_flags,
                }
            )
            if texture_index in ROUTABLE_TEXTURE_INDEXES:
                routable_cells.append([x, y])

    canvas_size = _coordinate(puzzle.get("canvas_size"), "canvas size")
    placement_size = [
        canvas_size[0] - 2 * PLACEMENT_MARGIN[0],
        canvas_size[1] - 2 * PLACEMENT_MARGIN[1],
    ]
    if placement_size[0] < 0 or placement_size[1] < 0:
        raise BoardCatalogError(f"Puzzle {puzzle_id}: canvas is smaller than placement margin")

    return {
        "id": puzzle_id,
        "canvas_size": list(canvas_size),
        "tile_grid_size": [board_data["tile_width"], board_data["tile_height"]],
        "board_variant_value": puzzle.get("board_variant_value"),
        "placement_origin": list(PLACEMENT_MARGIN),
        "placement_size": placement_size,
        "routable_cells": routable_cells,
        "tiles": tiles,
        "provided_parts": provided_parts,
        "terminals": terminals,
        "initial_traces": [
            {
                "position": trace["position_raw"],
                "trace_value": trace["trace_value"],
            }
            for trace in puzzle.get("initial_traces", [])
        ],
    }


def _coordinate(value: Any, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(item, int) for item in value)
    ):
        raise BoardCatalogError(f"invalid {label}: {value!r}")
    return value[0], value[1]


def _read_report(
    path: str | Path, expected_format: str, label: str
) -> dict[str, Any]:
    source = Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoardCatalogError(f"failed to read {label} {source}: {exc}") from exc
    if payload.get("format") != expected_format:
        raise BoardCatalogError(
            f"unexpected {label} format {payload.get('format')!r}"
        )
    return payload


def _validate_source_hash(puzzles: dict[str, Any], chips: dict[str, Any]) -> None:
    puzzle_hash = puzzles.get("source", {}).get("game_sha256")
    chip_hash = chips.get("source", {}).get("game_sha256")
    if puzzle_hash and chip_hash and puzzle_hash != chip_hash:
        raise BoardCatalogError(
            "puzzle and chip catalogs were extracted from different game executables"
        )
