from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManualPinSpec:
    index: int
    alias: str
    official_name: str | None
    kind: str
    side: str
    side_offset: int
    direction: str = "bidirectional"


@dataclass(frozen=True)
class ManualPartSpec:
    name: str
    expected_type_name: str
    expected_game_price: int
    expected_size: tuple[int, int]
    pins: tuple[ManualPinSpec, ...]
    pdf_page: int
    program_lines: int | None = None
    internal_registers: tuple[str, ...] = ()
    behavior_family: str | None = None
    direction_model: str | None = None
    notes: tuple[str, ...] = ()

    def source_pages(self) -> list[dict[str, str | int]]:
        return [
            {
                "file": "SHENZHEN IO Manual (English).pdf",
                "pdf_page": self.pdf_page,
            },
            {
                "file": "SHENZHEN IO Manual (Chinese).pdf",
                "pdf_page": self.pdf_page,
            },
        ]


MANUAL_PARTS = {
    "MC4000": ManualPartSpec(
        name="MC4000",
        expected_type_name="UC4",
        expected_game_price=3,
        expected_size=(3, 2),
        program_lines=9,
        internal_registers=("acc",),
        behavior_family="microcontroller",
        pdf_page=18,
        pins=(
            ManualPinSpec(0, "x0", "x0", "xbus", "left", 0),
            ManualPinSpec(1, "p0", "p0", "simple", "left", 1),
            ManualPinSpec(2, "x1", "x1", "xbus", "right", 1),
            ManualPinSpec(3, "p1", "p1", "simple", "right", 0),
        ),
    ),
    "MC6000": ManualPartSpec(
        name="MC6000",
        expected_type_name="UC6",
        expected_game_price=5,
        expected_size=(3, 3),
        program_lines=14,
        internal_registers=("acc", "dat"),
        behavior_family="microcontroller",
        pdf_page=19,
        pins=(
            ManualPinSpec(0, "x0", "x0", "xbus", "left", 0),
            ManualPinSpec(1, "x1", "x1", "xbus", "left", 1),
            ManualPinSpec(2, "p0", "p0", "simple", "left", 2),
            ManualPinSpec(3, "x2", "x2", "xbus", "right", 2),
            ManualPinSpec(4, "x3", "x3", "xbus", "right", 1),
            ManualPinSpec(5, "p1", "p1", "simple", "right", 0),
        ),
    ),
    "DX300": ManualPartSpec(
        name="DX300",
        expected_type_name="DX3",
        expected_game_price=1,
        expected_size=(2, 3),
        behavior_family="three-digit-io-expander",
        direction_model="device-wide",
        pdf_page=20,
        pins=(
            ManualPinSpec(0, "x0", None, "xbus", "left", 0),
            ManualPinSpec(1, "x1", None, "xbus", "left", 1),
            ManualPinSpec(2, "x2", None, "xbus", "left", 2),
            ManualPinSpec(3, "p0", "p0", "simple", "right", 2),
            ManualPinSpec(4, "p1", "p1", "simple", "right", 1),
            ManualPinSpec(5, "p2", "p2", "simple", "right", 0),
        ),
        notes=(
            "The three XBus contacts are equivalent and are not individually named in the manual.",
            "Reading or writing any XBus contact transfers the three simple-pin digits together.",
            "The whole device changes between input and output mode as one unit.",
        ),
    ),
}
