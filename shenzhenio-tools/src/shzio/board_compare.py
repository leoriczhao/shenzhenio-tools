from __future__ import annotations

from typing import Any

from .boards import Board
from .model import PinDirection, PinKind


TERMINAL_KIND = {
    "Digital": PinKind.SIMPLE,
    "Analog": PinKind.SIMPLE,
    "Audio": PinKind.SIMPLE,
    "XBus": PinKind.XBUS,
    "NonBlockingXBus": PinKind.XBUS,
}

# Puzzle terminal direction describes the circuit's role. BoardPort direction
# describes the attached device, so the electrical direction is complementary.
TERMINAL_DEVICE_DIRECTION = {
    "Input": PinDirection.OUTPUT,
    "Output": PinDirection.INPUT,
}


def compare_board_to_puzzle(board: Board, puzzle: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    _compare(checks, "puzzle_id", puzzle.get("id"), board.puzzle_id)
    canvas_size = puzzle.get("canvas_size")
    if isinstance(canvas_size, list) and len(canvas_size) == 2:
        _compare(
            checks,
            "canvas_size",
            canvas_size,
            [board.width, board.height],
        )
    else:
        _unresolved(checks, "canvas_size", "The puzzle canvas size is unavailable.")

    extracted_types = sorted(
        chip.get("chip_type") for chip in puzzle.get("provided_chips", [])
    )
    model_types = sorted(part.type_name for part in board.fixed_parts)
    _compare(checks, "provided_chip_types", extracted_types, model_types)
    _compare(
        checks,
        "provided_chip_positions",
        [chip.get("position_raw") for chip in puzzle.get("provided_chips", [])],
        [[part.x, part.y] for part in board.fixed_parts],
    )
    _compare(
        checks,
        "provided_chip_rotations",
        [
            bool(chip.get("boolean_fields", {}).get("0x0400087D", False))
            for chip in puzzle.get("provided_chips", [])
        ],
        [part.rotated for part in board.fixed_parts],
    )

    bound_terminal_names = {
        link.get("terminal_name")
        for chip in puzzle.get("provided_chips", [])
        for link in chip.get("terminal_pin_links", [])
    }
    external_terminals = {
        terminal["name"]: terminal
        for terminal in puzzle.get("terminals", [])
        if terminal.get("name") not in bound_terminal_names
    }
    _compare(
        checks,
        "external_terminal_names",
        sorted(external_terminals),
        sorted(board.ports),
    )

    for name in sorted(set(external_terminals) & set(board.ports)):
        terminal = external_terminals[name]
        port = board.ports[name]
        extracted_kind = TERMINAL_KIND.get(terminal.get("type"))
        extracted_direction = TERMINAL_DEVICE_DIRECTION.get(terminal.get("direction"))
        _compare(
            checks,
            f"port.{name}.kind",
            extracted_kind.value if extracted_kind else None,
            port.kind.value,
        )
        _compare(
            checks,
            f"port.{name}.device_direction",
            extracted_direction.value if extracted_direction else None,
            port.direction.value,
        )
        _compare(
            checks,
            f"port.{name}.nonblocking",
            terminal.get("type") == "NonBlockingXBus"
            or bool(terminal.get("nonblocking_flag")),
            port.nonblocking,
        )
        _compare(
            checks,
            f"port.{name}.contact",
            terminal.get("position_raw"),
            [port.x, port.y],
        )
    _unresolved(
        checks,
        "board_tiles_and_trace_grid",
        "Board tile semantics are decoded, but the hand-written Board does not model its tile layer.",
    )

    counts = {
        status: sum(check["status"] == status for check in checks)
        for status in ("match", "mismatch", "unresolved")
    }
    return {
        "puzzle_id": board.puzzle_id,
        "status": "mismatch" if counts["mismatch"] else "partial-match",
        "summary": counts,
        "checks": checks,
    }


def _compare(
    checks: list[dict[str, Any]], field: str, extracted: Any, model: Any
) -> None:
    checks.append(
        {
            "field": field,
            "status": "match" if extracted == model else "mismatch",
            "extracted": extracted,
            "model": model,
        }
    )


def _unresolved(checks: list[dict[str, Any]], field: str, reason: str) -> None:
    checks.append({"field": field, "status": "unresolved", "reason": reason})
