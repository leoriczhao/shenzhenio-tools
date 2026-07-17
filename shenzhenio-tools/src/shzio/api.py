from __future__ import annotations

from pathlib import Path
from typing import Iterable, Type

from .boards import Board, board_from_id
from .model import Net, Part, PinRef
from .router import RoutingResult, route_nets
from .solution_file import SavedChip, SavedSolution


class Solution:
    board: Type[Board] | Board | str
    name = "Untitled"
    auto_route = True

    def __init__(self) -> None:
        board = self.board
        if isinstance(board, str):
            self.board = board_from_id(board)
        else:
            self.board = board() if isinstance(board, type) else board
        self.parts: list[Part] = []
        self.nets: list[Net] = []
        self.routing_result: RoutingResult | None = None
        self.build()
        if self.auto_route and self.nets:
            self.route()

    def build(self) -> None:
        raise NotImplementedError

    def place(self, part: Part, at: tuple[int, int], name: str | None = None) -> Part:
        part.x, part.y = at
        if name is not None:
            part.name = name
        self.parts.append(part)
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
