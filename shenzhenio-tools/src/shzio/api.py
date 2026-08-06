from __future__ import annotations

from pathlib import Path
from typing import Iterable, Type

from .boards import Board, board_from_id
from .model import Net, Part, PinRef
from .placement import PlacementResult, place_parts
from .router import RoutingResult, route_nets
from .solution_file import SavedChip, SavedSolution


class Solution:
    board: Type[Board] | Board | str
    name = "Untitled"
    auto_layout = True
    auto_route = True
    placement_max_states = 50_000
    placement_timeout_seconds: float | None = None

    def __init__(self) -> None:
        board = self.board
        if isinstance(board, str):
            self.board = board_from_id(board)
        else:
            self.board = board() if isinstance(board, type) else board
        self.parts: list[Part] = []
        self.nets: list[Net] = []
        self.routing_result: RoutingResult | None = None
        self.placement_result: PlacementResult | None = None
        self._placement_orientations: dict[int, tuple[bool, ...]] = {}
        self.build()
        if self.auto_layout and any(
            part.x is None or part.y is None for part in self.parts
        ):
            self.layout(
                max_states=self.placement_max_states,
                timeout_seconds=self.placement_timeout_seconds,
            )
        if self.auto_route and self.nets and self.routing_result is None:
            self.route()

    def build(self) -> None:
        raise NotImplementedError

    def place(
        self,
        part: Part,
        at: tuple[int, int] | None = None,
        name: str | None = None,
        *,
        rotate: bool | None = None,
        allow_rotate: bool = False,
    ) -> Part:
        if at is not None:
            if (
                not isinstance(at, (tuple, list))
                or len(at) != 2
                or not all(isinstance(value, int) for value in at)
            ):
                raise ValueError("placement must be an (x, y) integer pair")
            part.x, part.y = at
        if rotate is not None:
            if not isinstance(rotate, bool):
                raise ValueError("rotate must be a boolean")
            part.rotated = rotate
        if at is not None and allow_rotate:
            raise ValueError("allow_rotate is only valid for automatic placement")
        if name is not None:
            part.name = name
        self.parts.append(part)
        self._placement_orientations[id(part)] = (
            tuple(dict.fromkeys((part.rotated, not part.rotated)))
            if allow_rotate
            else (part.rotated,)
        )
        return part

    def connect(
        self,
        a: PinRef,
        b: PinRef,
        name: str | None = None,
        via: Iterable[tuple[int, int]] = (),
    ) -> Net:
        route_hints = []
        for position in via:
            if (
                not isinstance(position, (tuple, list))
                or len(position) != 2
                or not all(isinstance(value, int) for value in position)
            ):
                raise ValueError("route hints must be (x, y) integer pairs")
            route_hints.append((position[0], position[1]))
        net = Net(a, b, name=name, route_hints=tuple(route_hints))
        self.nets.append(net)
        return net

    def route(self) -> RoutingResult:
        result = route_nets(self.board, self.parts, self.nets)
        self.board.traces = result.traces
        self.routing_result = result
        return result

    def layout(
        self,
        *,
        max_states: int = 50_000,
        timeout_seconds: float | None = None,
    ) -> PlacementResult:
        result = place_parts(
            self.board,
            self.parts,
            self.nets,
            orientation_options=self._placement_orientations,
            max_states=max_states,
            timeout_seconds=timeout_seconds,
        )
        self.placement_result = result
        if result.routing is not None:
            self.board.traces = result.routing.traces
            self.routing_result = result.routing
        return result

    def to_saved_solution(self) -> SavedSolution:
        if self.board.traces is None:
            raise ValueError(f"board {self.board.puzzle_id} has no trace grid")
        chips = [_chip_from_part(part) for part in [*self.parts, *self.board.fixed_parts]]
        return SavedSolution(
            name=self.name,
            puzzle=self.board.puzzle_id,
            traces=self.board.traces,
            chips=chips,
        )

    def write(self, path: str | Path) -> None:
        self.to_saved_solution().write(path)


def _chip_from_part(part: Part) -> SavedChip:
    if part.x is None or part.y is None:
        raise ValueError(f"part {part.name} has no placement")
    return SavedChip(
        type_name=part.type_name,
        x=part.x,
        y=part.y,
        rotated=part.rotated,
        provided=part.provided,
        code_lines=part.code_lines,
    )
