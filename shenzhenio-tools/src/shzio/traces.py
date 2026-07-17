from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

RIGHT = 0x1
UP = 0x2
LEFT = 0x4
DOWN = 0x8
EXISTS = 0x10
DIRECTION_MASK = RIGHT | UP | LEFT | DOWN

HEX_DIGITS = "0123456789ABCDEF"

CHAR_TO_MASK = {".": 0}
CHAR_TO_MASK.update(
    {char: EXISTS | direction_mask for direction_mask, char in enumerate(HEX_DIGITS)}
)


@dataclass
class TraceGrid:
    rows: list[str]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("trace grid cannot be empty")
        width = len(self.rows[0])
        if any(len(row) != width for row in self.rows):
            raise ValueError("all trace rows must have the same width")
        unknown = sorted({ch for row in self.rows for ch in row if ch not in CHAR_TO_MASK})
        if unknown:
            raise ValueError(f"unknown trace characters: {unknown}")

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)

    def mask_at(self, x: int, y: int) -> int:
        self._check_coordinate(x, y)
        return CHAR_TO_MASK[self.rows[self.height - 1 - y][x]]

    def char_at(self, x: int, y: int) -> str:
        self._check_coordinate(x, y)
        return self.rows[self.height - 1 - y][x]

    def set_mask(self, x: int, y: int, mask: int) -> None:
        self._check_coordinate(x, y)
        row_index = self.height - 1 - y
        row = self.rows[row_index]
        self.rows[row_index] = row[:x] + encode_mask(mask) + row[x + 1 :]

    def copy(self) -> "TraceGrid":
        return TraceGrid(self.rows.copy())

    def nonempty_cells(self) -> dict[tuple[int, int], int]:
        return {
            (x, y): self.mask_at(x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.mask_at(x, y) != 0
        }

    def _check_coordinate(self, x: int, y: int) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            raise IndexError(f"trace coordinate ({x}, {y}) is outside {self.width}x{self.height}")

    @classmethod
    def blank(cls, width: int = 22, height: int = 14) -> "TraceGrid":
        return cls(["." * width for _ in range(height)])

    @classmethod
    def from_masks(
        cls,
        width: int,
        height: int,
        masks: Mapping[tuple[int, int], int],
    ) -> "TraceGrid":
        grid = cls.blank(width, height)
        for (x, y), mask in masks.items():
            grid.set_mask(x, y, mask)
        return grid

    def as_text(self) -> str:
        return "\n".join(self.rows)


def encode_mask(mask: int) -> str:
    if mask == 0:
        return "."
    if mask < 0 or mask > (EXISTS | DIRECTION_MASK):
        raise ValueError(f"invalid trace mask {mask}")
    return HEX_DIGITS[mask & DIRECTION_MASK]


def decode_char(ch: str) -> int:
    try:
        return CHAR_TO_MASK[ch]
    except KeyError as exc:
        raise ValueError(f"invalid trace char {ch!r}") from exc
