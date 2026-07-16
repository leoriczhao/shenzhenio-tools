from __future__ import annotations

from dataclasses import dataclass

RIGHT = 0x1
UP = 0x2
LEFT = 0x4
DOWN = 0x8

MASK_TO_CHAR = {
    0x0: ".",
    0x1: "1",
    0x2: "2",
    0x3: "3",
    0x4: "4",
    0x5: "5",
    0x6: "6",
    0x7: "7",
    0x8: "8",
    0x9: "9",
    0xA: "A",
    0xB: "B",
    0xC: "C",
    0xD: "D",
    0xE: "E",
    0xF: "F",
}

CHAR_TO_MASK = {char: mask for mask, char in MASK_TO_CHAR.items()}
CHAR_TO_MASK["0"] = 0


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
        return CHAR_TO_MASK[self.rows[y][x]]

    def char_at(self, x: int, y: int) -> str:
        return self.rows[y][x]

    @classmethod
    def blank(cls, width: int = 22, height: int = 14) -> "TraceGrid":
        return cls(["." * width for _ in range(height)])

    def as_text(self) -> str:
        return "\n".join(self.rows)


def encode_mask(mask: int) -> str:
    try:
        return MASK_TO_CHAR[mask]
    except KeyError as exc:
        raise ValueError(f"invalid trace mask {mask}") from exc


def decode_char(ch: str) -> int:
    try:
        return CHAR_TO_MASK[ch]
    except KeyError as exc:
        raise ValueError(f"invalid trace char {ch!r}") from exc
