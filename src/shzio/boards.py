from __future__ import annotations

from dataclasses import dataclass, field

from .board_catalog import (
    BoardCatalogError,
    BoardSpec,
    DeviceDirection,
    ElectricalKind,
    load_board_spec,
)
from .model import BoardPort, Part, PinDirection, PinKind, PinRef
from .parts import Radio, part_from_type
from .traces import TraceGrid


ELECTRICAL_PIN_KINDS = {
    ElectricalKind.SIMPLE: PinKind.SIMPLE,
    ElectricalKind.XBUS: PinKind.XBUS,
    ElectricalKind.DISPLAY: PinKind.DISPLAY,
}

DEVICE_PIN_DIRECTIONS = {
    DeviceDirection.INPUT: PinDirection.INPUT,
    DeviceDirection.OUTPUT: PinDirection.OUTPUT,
}


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
    terminal_bindings: dict[str, PinRef] = field(default_factory=dict)
    traces: TraceGrid | None = None
    spec: BoardSpec | None = None

    def __post_init__(self) -> None:
        if self.traces is None:
            self.traces = TraceGrid.blank(self.width, self.height)

    def port(self, name: str) -> BoardPort:
        try:
            return self.ports[name]
        except KeyError as exc:
            raise AttributeError(f"board {self.puzzle_id} has no port {name!r}") from exc

    def part(self, name: str) -> Part:
        matches = [part for part in self.fixed_parts if part.name == name]
        if len(matches) != 1:
            raise AttributeError(
                f"board {self.puzzle_id} has {len(matches)} fixed parts named {name!r}"
            )
        return matches[0]

    def terminal(self, name: str) -> PinRef:
        if name in self.terminal_bindings:
            return self.terminal_bindings[name]
        return self.port(name).pin()

    def __getattr__(self, name: str) -> BoardPort | Part:
        ports = self.__dict__.get("ports", {})
        if name in ports:
            return ports[name]
        parts = self.__dict__.get("fixed_parts", [])
        matches = [part for part in parts if part.name == name]
        if len(matches) == 1:
            return matches[0]
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

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

    @staticmethod
    def from_spec(spec: BoardSpec) -> "Board":
        fixed_parts: list[Part] = []
        part_by_index: dict[int, Part] = {}
        name_counts: dict[str, int] = {}
        for provided in spec.provided_parts:
            base_name = provided.chip_type.lower()
            sequence = name_counts.get(base_name, 0)
            name_counts[base_name] = sequence + 1
            name = base_name if sequence == 0 else f"{base_name}_{sequence}"
            part = part_from_type(provided.chip_type, name=name)
            part.x, part.y = provided.position
            part.rotated = provided.rotated
            part.provided = True
            fixed_parts.append(part)
            part_by_index[provided.index] = part

        ports: dict[str, BoardPort] = {}
        terminal_bindings = {}
        for terminal in spec.terminals:
            if terminal.binding is not None:
                try:
                    owner = part_by_index[terminal.binding.provided_part_index]
                except KeyError as exc:
                    raise BoardCatalogError(
                        f"{spec.puzzle_id}: terminal {terminal.name!r} references "
                        f"missing provided part {terminal.binding.provided_part_index}"
                    ) from exc
                pin_name = terminal.binding.pin_name
                if pin_name not in owner.spec.pins:
                    indexed = [
                        name
                        for name, pin in owner.spec.pins.items()
                        if pin.index == terminal.binding.pin_index
                    ]
                    if len(indexed) != 1:
                        raise BoardCatalogError(
                            f"{spec.puzzle_id}: cannot bind terminal {terminal.name!r} "
                            f"to {owner.type_name} pin index {terminal.binding.pin_index}"
                        )
                    pin_name = indexed[0]
                terminal_bindings[terminal.name] = owner.pin(pin_name)
                continue
            ports[terminal.name] = BoardPort(
                name=terminal.name,
                pin_name=terminal.device_direction.value,
                kind=ELECTRICAL_PIN_KINDS[terminal.electrical_kind],
                direction=DEVICE_PIN_DIRECTIONS[terminal.device_direction],
                x=terminal.contact[0],
                y=terminal.contact[1],
                label=terminal.name,
                nonblocking=terminal.nonblocking,
            )

        return Board(
            puzzle_id=spec.puzzle_id,
            width=spec.canvas_size[0],
            height=spec.canvas_size[1],
            fixed_parts=fixed_parts,
            ports=ports,
            placement_origin=spec.placement_origin,
            placement_size=spec.placement_size,
            routable_cells=spec.routable_cells,
            terminal_bindings=terminal_bindings,
            traces=TraceGrid.from_masks(
                spec.canvas_size[0],
                spec.canvas_size[1],
                {(x, y): value for x, y, value in spec.initial_traces},
            ),
            spec=spec,
        )


class Sz035(Board):
    def __init__(self) -> None:
        try:
            runtime = Board.from_spec(load_board_spec("Sz035"))
        except BoardCatalogError:
            self._init_fixture()
            return

        super().__init__(
            puzzle_id=runtime.puzzle_id,
            width=runtime.width,
            height=runtime.height,
            fixed_parts=runtime.fixed_parts,
            ports=runtime.ports,
            placement_origin=runtime.placement_origin,
            placement_size=runtime.placement_size,
            routable_cells=runtime.routable_cells,
            terminal_bindings=runtime.terminal_bindings,
            traces=runtime.traces,
            spec=runtime.spec,
        )
        self.radio = self.part("radio")
        self.buzzer = self.port("buzzer")

    def _init_fixture(self) -> None:
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
    if puzzle_id in BOARD_CLASSES:
        return BOARD_CLASSES[puzzle_id]()
    try:
        return Board.from_spec(load_board_spec(puzzle_id))
    except BoardCatalogError as exc:
        raise KeyError(f"unknown board {puzzle_id!r}") from exc
