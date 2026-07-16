from __future__ import annotations

from pathlib import Path
from typing import Type

from .boards import Board
from .model import Net, Part, PinRef
from .solution_file import SavedChip, SavedSolution


class Solution:
    board: Type[Board] | Board
    name = "Untitled"

    def __init__(self) -> None:
        board = self.board
        self.board = board() if isinstance(board, type) else board
        self.parts: list[Part] = []
        self.nets: list[Net] = []
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def place(self, part: Part, at: tuple[int, int], name: str | None = None) -> Part:
        part.x, part.y = at
        if name is not None:
            part.name = name
        self.parts.append(part)
        return part

    def connect(self, a: PinRef, b: PinRef, name: str | None = None) -> Net:
        net = Net(a, b, name=name)
        self.nets.append(net)
        return net

    def to_saved_solution(self) -> SavedSolution:
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

