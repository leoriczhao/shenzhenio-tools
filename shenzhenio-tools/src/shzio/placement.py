from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Iterable, Mapping

from .boards import Board
from .geometry import (
    board_terminal_cells,
    footprint_cells,
    footprint_size,
    part_overlap_allowed,
    port_overlap_allowed,
)
from .model import Net, Part, PinRef
from .physical import endpoint_for_pin_ref
from .router import RoutingError, RoutingResult, route_nets
from .traces import DOWN, LEFT, RIGHT, UP


class PlacementError(ValueError):
    pass


class PlacementSearchExhausted(PlacementError):
    pass


@dataclass(frozen=True, order=True)
class PlacementScore:
    trace_cells: int
    bends: int
    footprint_span: int


@dataclass(frozen=True)
class PlacedPart:
    name: str
    x: int
    y: int
    rotated: bool


@dataclass(frozen=True)
class PlacementResult:
    placements: tuple[PlacedPart, ...]
    score: PlacementScore
    routing: RoutingResult | None
    explored_states: int
    evaluated_layouts: int
    exhaustive: bool
    elapsed_seconds: float


@dataclass(frozen=True)
class _Candidate:
    x: int
    y: int
    rotated: bool
    estimate: int


def place_parts(
    board: Board,
    parts: list[Part],
    nets: list[Net],
    *,
    orientation_options: Mapping[int, Iterable[bool]] | None = None,
    max_states: int = 50_000,
    timeout_seconds: float | None = None,
) -> PlacementResult:
    if max_states < 1:
        raise ValueError("max_states must be positive")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    started = monotonic()
    originals = {id(part): (part.x, part.y, part.rotated) for part in parts}
    unplaced = [part for part in parts if part.x is None or part.y is None]
    if any((part.x is None) != (part.y is None) for part in parts):
        raise PlacementError("part coordinates must be both set or both omitted")

    connected_pins = _connected_pins(nets)
    unplaced_ids = {id(part) for part in unplaced}
    fixed_parts = [
        *board.fixed_parts,
        *(part for part in parts if id(part) not in unplaced_ids),
    ]
    terminal_cells = board_terminal_cells(board)
    _validate_fixed_parts(board, fixed_parts, terminal_cells)
    candidates = {
        id(part): _candidate_positions(
            board,
            part,
            fixed_parts,
            terminal_cells,
            connected_pins.get(id(part), frozenset()),
            nets,
            orientation_options,
        )
        for part in unplaced
    }
    for part in unplaced:
        if not candidates[id(part)]:
            _restore(parts, originals)
            raise PlacementError(f"part {part.name} has no legal placement candidates")

    part_index = {id(part): index for index, part in enumerate(parts)}
    net_degree = {
        id(part): sum(
            1
            for net in nets
            if net.a.owner is part or net.b.owner is part
        )
        for part in unplaced
    }
    search_parts = sorted(
        unplaced,
        key=lambda part: (
            len(candidates[id(part)]),
            -net_degree[id(part)],
            part_index[id(part)],
        ),
    )

    explored_states = 0
    evaluated_layouts = 0
    stopped = False
    best: tuple[
        PlacementScore,
        tuple[tuple[int, int, bool], ...],
        RoutingResult | None,
    ] | None = None

    def over_budget() -> bool:
        nonlocal stopped
        if explored_states >= max_states:
            stopped = True
            return True
        if timeout_seconds is not None and monotonic() - started >= timeout_seconds:
            stopped = True
            return True
        return False

    def visit(index: int) -> None:
        nonlocal explored_states, evaluated_layouts, best
        if index == len(search_parts):
            evaluated_layouts += 1
            try:
                routing = route_nets(board, parts, nets) if nets else None
            except RoutingError:
                return
            score = _score_layout(board, parts, routing)
            key = tuple(
                (part.x or 0, part.y or 0, part.rotated)
                for part in parts
            )
            if best is None or (score, key) < (best[0], best[1]):
                best = score, key, routing
            return
        if over_budget():
            return

        part = search_parts[index]
        for candidate in candidates[id(part)]:
            if over_budget():
                return
            explored_states += 1
            if not _candidate_fits_current(
                part,
                candidate,
                [*board.fixed_parts, *parts],
                terminal_cells,
            ):
                continue
            part.x, part.y, part.rotated = candidate.x, candidate.y, candidate.rotated
            visit(index + 1)
            part.x, part.y, part.rotated = originals[id(part)]

    if unplaced:
        visit(0)
    else:
        try:
            routing = route_nets(board, parts, nets) if nets else None
        except RoutingError as exc:
            raise PlacementError(f"fixed placement is not routable: {exc}") from exc
        best = _score_layout(board, parts, routing), tuple(
            (part.x or 0, part.y or 0, part.rotated) for part in parts
        ), routing
        evaluated_layouts = 1

    if best is None:
        _restore(parts, originals)
        if stopped:
            raise PlacementSearchExhausted(
                f"no routable placement found within {max_states} search states"
            )
        raise PlacementError("no routable placement exists for the current constraints")

    for part, (x, y, rotated) in zip(parts, best[1]):
        part.x, part.y, part.rotated = x, y, rotated
    return PlacementResult(
        placements=tuple(
            PlacedPart(part.name or part.type_name, part.x or 0, part.y or 0, part.rotated)
            for part in parts
        ),
        score=best[0],
        routing=best[2],
        explored_states=explored_states,
        evaluated_layouts=evaluated_layouts,
        exhaustive=not stopped,
        elapsed_seconds=monotonic() - started,
    )


def _candidate_positions(
    board: Board,
    part: Part,
    fixed_parts: list[Part],
    terminal_cells: frozenset[tuple[int, int]],
    connected_pin_names: frozenset[str],
    nets: list[Net],
    orientation_options: Mapping[int, Iterable[bool]] | None,
) -> tuple[_Candidate, ...]:
    raw_orientations = (
        orientation_options.get(id(part), (part.rotated,))
        if orientation_options is not None
        else (part.rotated,)
    )
    orientations = tuple(dict.fromkeys(bool(value) for value in raw_orientations))
    if not orientations:
        raise PlacementError(f"part {part.name} has no allowed orientations")

    origin_x, origin_y = board.placement_origin
    placement_width, placement_height = board.placement_size
    result = []
    for rotated in orientations:
        width, height = footprint_size(part, rotated)
        max_x = origin_x + placement_width - width
        max_y = origin_y + placement_height - height
        for y in range(origin_y, max_y + 1):
            for x in range(origin_x, max_x + 1):
                candidate = _Candidate(
                    x=x,
                    y=y,
                    rotated=rotated,
                    estimate=_wire_estimate(part, (x, y), rotated, nets),
                )
                if not _candidate_fits_current(
                    part, candidate, fixed_parts, terminal_cells
                ):
                    continue
                contacts = _candidate_pin_contacts(part, candidate)
                if any(
                    contacts.get(name) not in board.routable_cells
                    for name in connected_pin_names
                ):
                    continue
                result.append(candidate)
    return tuple(
        sorted(
            result,
            key=lambda item: (item.estimate, item.y, item.x, item.rotated),
        )
    )


def _validate_fixed_parts(
    board: Board,
    parts: list[Part],
    terminal_cells: frozenset[tuple[int, int]],
) -> None:
    checked: list[Part] = []
    for part in parts:
        assert part.x is not None and part.y is not None
        if not board.contains_footprint(
            (part.x, part.y), footprint_size(part)
        ):
            raise PlacementError(
                f"fixed part {part.name} is outside the placement range"
            )
        cells = footprint_cells(part)
        for terminal in cells & terminal_cells:
            if not port_overlap_allowed(part, terminal):
                raise PlacementError(
                    f"fixed part {part.name} overlaps board terminal at {terminal}"
                )
        for other in checked:
            if cells & footprint_cells(other) and not part_overlap_allowed(
                part, other
            ):
                raise PlacementError(
                    f"fixed parts {part.name} and {other.name} overlap"
                )
        checked.append(part)


def _candidate_fits_current(
    part: Part,
    candidate: _Candidate,
    placed_parts: list[Part],
    terminal_cells: frozenset[tuple[int, int]],
) -> bool:
    own_cells = footprint_cells(
        part,
        position=(candidate.x, candidate.y),
        rotated=candidate.rotated,
    )
    for terminal in own_cells & terminal_cells:
        if not port_overlap_allowed(
            part, terminal, part_position=(candidate.x, candidate.y)
        ):
            return False
    original = part.x, part.y, part.rotated
    part.x, part.y, part.rotated = candidate.x, candidate.y, candidate.rotated
    try:
        for other in placed_parts:
            if other is part or other.x is None or other.y is None:
                continue
            if own_cells & footprint_cells(other) and not part_overlap_allowed(
                part, other
            ):
                return False
    finally:
        part.x, part.y, part.rotated = original
    return True


def _candidate_pin_contacts(
    part: Part, candidate: _Candidate
) -> dict[str, tuple[int, int]]:
    cells = {}
    for name, pin in part.spec.pins.items():
        dx = pin.rotated_contact_dx if candidate.rotated else pin.contact_dx
        dy = pin.rotated_contact_dy if candidate.rotated else pin.contact_dy
        if dx is not None and dy is not None:
            cells[name] = candidate.x + dx, candidate.y + dy
    return cells


def _connected_pins(nets: list[Net]) -> dict[int, frozenset[str]]:
    grouped: dict[int, set[str]] = {}
    for net in nets:
        for pin in (net.a, net.b):
            if isinstance(pin.owner, Part):
                grouped.setdefault(id(pin.owner), set()).add(pin.name)
    return {key: frozenset(value) for key, value in grouped.items()}


def _wire_estimate(
    part: Part,
    position: tuple[int, int],
    rotated: bool,
    nets: list[Net],
) -> int:
    candidate = _Candidate(position[0], position[1], rotated, 0)
    contacts = _candidate_pin_contacts(part, candidate)
    estimate = 0
    for net in nets:
        own: PinRef | None = None
        other: PinRef | None = None
        if net.a.owner is part:
            own, other = net.a, net.b
        elif net.b.owner is part:
            own, other = net.b, net.a
        if own is None or other is None or own.name not in contacts:
            continue
        endpoint = endpoint_for_pin_ref(other)
        if endpoint is None:
            continue
        x, y = contacts[own.name]
        estimate += abs(x - endpoint.x) + abs(y - endpoint.y)
    return estimate


def _score_layout(
    board: Board,
    parts: list[Part],
    routing: RoutingResult | None,
) -> PlacementScore:
    if routing is None:
        trace_cells = 0
        bends = 0
    else:
        initial = (
            set(board.traces.nonempty_cells())
            if board.traces is not None
            else set()
        )
        routed = routing.traces.nonempty_cells()
        trace_cells = len(set(routed) - initial)
        bends = sum(
            1
            for cell, mask in routed.items()
            if cell not in initial
            and bool(mask & (LEFT | RIGHT))
            and bool(mask & (UP | DOWN))
        )
    occupied = set().union(*(footprint_cells(part) for part in parts)) if parts else set()
    if occupied:
        xs = [cell[0] for cell in occupied]
        ys = [cell[1] for cell in occupied]
        footprint_span = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
    else:
        footprint_span = 0
    return PlacementScore(trace_cells, bends, footprint_span)


def _restore(
    parts: list[Part],
    originals: Mapping[int, tuple[int | None, int | None, bool]],
) -> None:
    for part in parts:
        part.x, part.y, part.rotated = originals[id(part)]
