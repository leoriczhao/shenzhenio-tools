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
    placement_origin: tuple[int, int] = (1, 1)
    placement_size: tuple[int, int] = (20, 12)
    routable_cells: frozenset[tuple[int, int]] = field(default_factory=frozenset)
    traces: TraceGrid | None = None

    def __post_init__(self) -> None:
        if self.traces is None:
            self.traces = TraceGrid.blank(self.width, self.height)

    def port(self, name: str) -> BoardPort:
        try:
            return self.ports[name]
        except KeyError as exc:
            raise AttributeError(f"board {self.puzzle_id} has no port {name!r}") from exc

    def is_routable(self, position: tuple[int, int]) -> bool:
        return position in self.routable_cells

    def contains_footprint(
        self, position: tuple[int, int], size: tuple[int, int]
    ) -> bool:
        x, y = position
        width, height = size
        origin_x, origin_y = self.placement_origin
        placement_width, placement_height = self.placement_size
        return (
            width >= 0
            and height >= 0
            and x >= origin_x
            and y >= origin_y
            and x + width <= origin_x + placement_width
            and y + height <= origin_y + placement_height
        )


class Sz035(Board):
    def __init__(self) -> None:
        radio = Radio("radio")
        radio.x = 6
        radio.y = 4
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
                    y=5,
                    label="蜂鸣器",
                )
            },
            routable_cells=frozenset(
                (x, y) for y in range(3, 8) for x in range(6, 16)
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
