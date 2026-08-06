from __future__ import annotations

from typing import Iterable, TYPE_CHECKING

from .model import Part, PinKind

if TYPE_CHECKING:
    from .boards import Board


BRIDGE_KIND = 2
TERMINAL_KIND = 1


def footprint_size(part: Part, rotated: bool | None = None) -> tuple[int, int]:
    is_rotated = part.rotated if rotated is None else rotated
    if is_rotated:
        return part.spec.height, part.spec.width
    return part.spec.width, part.spec.height


def footprint_cells(
    part: Part,
    *,
    position: tuple[int, int] | None = None,
    rotated: bool | None = None,
) -> frozenset[tuple[int, int]]:
    if position is None:
        if part.x is None or part.y is None:
            return frozenset()
        position = part.x, part.y
    x, y = position
    width, height = footprint_size(part, rotated)
    return frozenset(
        (x + dx, y + dy)
        for dx in range(width)
        for dy in range(height)
    )


def pin_contact_cells(
    part: Part,
    *,
    position: tuple[int, int] | None = None,
    rotated: bool | None = None,
) -> frozenset[tuple[int, int]]:
    if position is None:
        if part.x is None or part.y is None:
            return frozenset()
        position = part.x, part.y
    is_rotated = part.rotated if rotated is None else rotated
    x, y = position
    contacts = set()
    for pin in part.spec.pins.values():
        dx = pin.rotated_contact_dx if is_rotated else pin.contact_dx
        dy = pin.rotated_contact_dy if is_rotated else pin.contact_dy
        if dx is not None and dy is not None:
            contacts.add((x + dx, y + dy))
    return frozenset(contacts)


def trace_blocked_cells(parts: Iterable[Part]) -> frozenset[tuple[int, int]]:
    blocked = set()
    for part in parts:
        if part.spec.chip_kind_value == BRIDGE_KIND:
            continue
        blocked.update(footprint_cells(part) - pin_contact_cells(part))
    return frozenset(blocked)


def part_overlap_allowed(a: Part, b: Part) -> bool:
    bridge, terminal = (a, b) if a.spec.chip_kind_value == BRIDGE_KIND else (b, a)
    if (
        bridge.spec.chip_kind_value != BRIDGE_KIND
        or terminal.spec.chip_kind_value != TERMINAL_KIND
        or bridge.x is None
        or bridge.y is None
        or terminal.x is None
        or terminal.y is None
    ):
        return False
    return (terminal.x, terminal.y) == (bridge.x, bridge.y + 1)


def port_overlap_allowed(
    part: Part,
    position: tuple[int, int],
    *,
    part_position: tuple[int, int] | None = None,
) -> bool:
    if part.spec.chip_kind_value != BRIDGE_KIND:
        return False
    if part_position is None:
        if part.x is None or part.y is None:
            return False
        part_position = part.x, part.y
    return position == (part_position[0], part_position[1] + 1)


def board_terminal_cells(board: "Board") -> frozenset[tuple[int, int]]:
    return frozenset(
        (port.x, port.y)
        for port in board.ports.values()
        if (
            port.kind != PinKind.DISPLAY
            and port.x is not None
            and port.y is not None
        )
    )
