from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from .program import Program


class PinKind(str, Enum):
    SIMPLE = "simple"
    XBUS = "xbus"
    DISPLAY = "display"


class PinDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"


class Side(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    BOARD = "board"


@dataclass(frozen=True)
class PinSpec:
    name: str
    kind: PinKind
    side: Side
    offset: int
    index: int | None = None
    direction: PinDirection = PinDirection.BIDIRECTIONAL
    contact_dx: int | None = None
    contact_dy: int | None = None
    rotated_contact_dx: int | None = None
    rotated_contact_dy: int | None = None


@dataclass(frozen=True)
class PartSpec:
    type_name: str
    width: int
    height: int
    cost: int
    max_code_lines: int | None
    registers: tuple[str, ...]
    pins: dict[str, PinSpec]
    chip_kind_value: int | None = None


@dataclass(frozen=True)
class RegisterRef:
    owner: "Part"
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class PinRef:
    owner: "Connectable"
    name: str
    spec: PinSpec

    @property
    def kind(self) -> PinKind:
        return self.spec.kind

    @property
    def direction(self) -> PinDirection:
        return self.spec.direction

    def __str__(self) -> str:
        return self.name


class Connectable:
    name: str


@dataclass
class Part(Connectable):
    spec: PartSpec
    name: str | None = None
    x: int | None = None
    y: int | None = None
    rotated: bool = False
    provided: bool = False
    code_lines: list[str] = field(default_factory=list)
    program_ir: "Program | None" = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.name is None:
            self.name = self.spec.type_name.lower()

    def __getattr__(self, name: str) -> PinRef | RegisterRef:
        spec = self.__dict__.get("spec")
        if spec is not None:
            if name in spec.pins:
                return self.pin(name)
            if name in spec.registers:
                return self.register(name)
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    @property
    def type_name(self) -> str:
        return self.spec.type_name

    def pin(self, name: str) -> PinRef:
        try:
            spec = self.spec.pins[name]
        except KeyError as exc:
            raise AttributeError(f"{self.type_name} has no pin {name!r}") from exc
        return PinRef(self, name, spec)

    def register(self, name: str) -> RegisterRef:
        if name not in self.spec.registers:
            raise AttributeError(f"{self.type_name} has no register {name!r}")
        return RegisterRef(self, name)

    @property
    def acc(self) -> RegisterRef:
        return self.register("acc")

    @property
    def dat(self) -> RegisterRef:
        return self.register("dat")

    @property
    def p0(self) -> PinRef:
        return self.pin("p0")

    @property
    def p1(self) -> PinRef:
        return self.pin("p1")

    @property
    def x0(self) -> PinRef:
        return self.pin("x0")

    @property
    def x1(self) -> PinRef:
        return self.pin("x1")

    @property
    def x2(self) -> PinRef:
        return self.pin("x2")

    @property
    def x3(self) -> PinRef:
        return self.pin("x3")

    def program(self) -> "ProgramBuilder":
        from .program import ProgramBuilder

        return ProgramBuilder(self)

    def set_code(self, lines: Iterable[str]) -> None:
        self.code_lines = list(lines)
        self.program_ir = None

    def set_program(self, program: "Program") -> None:
        self.program_ir = program
        self.code_lines = program.render_lines()


@dataclass
class BoardPort(Connectable):
    name: str
    pin_name: str
    kind: PinKind
    direction: PinDirection
    x: int | None = None
    y: int | None = None
    label: str | None = None
    nonblocking: bool = False
    empty_value: int | None = None

    @property
    def input(self) -> PinRef:
        return self.pin()

    @property
    def output(self) -> PinRef:
        return self.pin()

    def pin(self) -> PinRef:
        return PinRef(
            self,
            self.pin_name,
            PinSpec(
                name=self.pin_name,
                kind=self.kind,
                side=Side.BOARD,
                offset=0,
                direction=self.direction,
            ),
        )


@dataclass
class Net:
    a: PinRef
    b: PinRef
    name: str | None = None
    route_hints: tuple[tuple[int, int], ...] = ()

    @property
    def kind(self) -> PinKind:
        if self.a.kind != self.b.kind:
            raise ValueError(f"net mixes {self.a.kind} and {self.b.kind}")
        return self.a.kind
