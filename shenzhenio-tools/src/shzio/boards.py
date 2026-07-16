from __future__ import annotations

from dataclasses import dataclass, field

from .model import BoardPort, Part, PinDirection, PinKind
from .parts import Radio
from .traces import TraceGrid


@dataclass
class Board:
    puzzle_id: str
    width: int = 22
    height: int = 14
    fixed_parts: list[Part] = field(default_factory=list)
    ports: dict[str, BoardPort] = field(default_factory=dict)
    traces: TraceGrid | None = None

    def __post_init__(self) -> None:
        if self.traces is None:
            self.traces = TraceGrid.blank(self.width, self.height)

    def port(self, name: str) -> BoardPort:
        try:
            return self.ports[name]
        except KeyError as exc:
            raise AttributeError(f"board {self.puzzle_id} has no port {name!r}") from exc


class Sz035(Board):
    def __init__(self) -> None:
        radio = Radio("radio")
        radio.x = 7
        radio.y = 5
        radio.provided = True

        super().__init__(
            puzzle_id="Sz035",
            fixed_parts=[radio],
            ports={
                "buzzer": BoardPort(
                    name="buzzer",
                    pin_name="input",
                    kind=PinKind.SIMPLE,
                    direction=PinDirection.INPUT,
                    x=15,
                    y=8,
                    label="蜂音器",
                )
            },
            traces=TraceGrid(
                [
                    "......................",
                    "......................",
                    "......................",
                    "......................",
                    "......................",
                    "......................",
                    "........1C............",
                    ".........354.15C......",
                    "...............2......",
                    "......................",
                    "......................",
                    "......................",
                    "......................",
                    "......................",
                ]
            ),
        )
        self.radio = radio
        self.buzzer = self.ports["buzzer"]


BOARD_CLASSES = {
    "Sz035": Sz035,
}


def board_from_id(puzzle_id: str) -> Board:
    try:
        cls = BOARD_CLASSES[puzzle_id]
    except KeyError as exc:
        raise KeyError(f"unknown board {puzzle_id!r}") from exc
    return cls()
