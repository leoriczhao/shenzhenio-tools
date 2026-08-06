from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, TYPE_CHECKING

from .model import Part, PinKind, PinRef, RegisterRef

if TYPE_CHECKING:
    from collections.abc import Iterator


MIN_VALUE = -999
MAX_VALUE = 999

_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_REGISTER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class ProgramError(ValueError):
    def __init__(self, message: str, line_number: int | None = None) -> None:
        self.message = message
        self.line_number = line_number
        prefix = f"line {line_number}: " if line_number is not None else ""
        super().__init__(prefix + message)


class Condition(str, Enum):
    ALWAYS = ""
    POSITIVE = "+"
    NEGATIVE = "-"


@dataclass(frozen=True)
class Immediate:
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            raise TypeError("immediate values must be integers")
        if not MIN_VALUE <= self.value <= MAX_VALUE:
            raise ValueError(
                f"immediate value {self.value} is outside {MIN_VALUE}..{MAX_VALUE}"
            )

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class RegisterOperand:
    name: str

    def __post_init__(self) -> None:
        if not _REGISTER_RE.fullmatch(self.name):
            raise ValueError(f"invalid register name {self.name!r}")

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class LabelOperand:
    name: str

    def __post_init__(self) -> None:
        _validate_label(self.name)

    def __str__(self) -> str:
        return self.name


Operand = Immediate | RegisterOperand | LabelOperand


class OperandRole(str, Enum):
    VALUE = "value"
    REGISTER = "register"
    PIN = "pin"
    LABEL = "label"


INSTRUCTION_SIGNATURES: dict[str, tuple[OperandRole, ...]] = {
    "nop": (),
    "mov": (OperandRole.VALUE, OperandRole.REGISTER),
    "jmp": (OperandRole.LABEL,),
    "slp": (OperandRole.VALUE,),
    "slx": (OperandRole.PIN,),
    "add": (OperandRole.VALUE,),
    "sub": (OperandRole.VALUE,),
    "mul": (OperandRole.VALUE,),
    "not": (),
    "dgt": (OperandRole.VALUE,),
    "dst": (OperandRole.VALUE, OperandRole.VALUE),
    "teq": (OperandRole.VALUE, OperandRole.VALUE),
    "tgt": (OperandRole.VALUE, OperandRole.VALUE),
    "tlt": (OperandRole.VALUE, OperandRole.VALUE),
    "tcp": (OperandRole.VALUE, OperandRole.VALUE),
    "gen": (OperandRole.PIN, OperandRole.VALUE, OperandRole.VALUE),
}


@dataclass(frozen=True)
class Instruction:
    opcode: str
    operands: tuple[Operand, ...] = ()
    condition: Condition = Condition.ALWAYS

    def __post_init__(self) -> None:
        opcode = self.opcode.lower()
        object.__setattr__(self, "opcode", opcode)
        try:
            signature = INSTRUCTION_SIGNATURES[opcode]
        except KeyError as exc:
            raise ValueError(f"unknown instruction {opcode!r}") from exc
        if len(self.operands) != len(signature):
            raise ValueError(
                f"{opcode} expects {len(signature)} operands, got {len(self.operands)}"
            )
        for index, (operand, role) in enumerate(zip(self.operands, signature), 1):
            if role == OperandRole.VALUE and not isinstance(
                operand, (Immediate, RegisterOperand)
            ):
                raise TypeError(f"{opcode} operand {index} must be a value")
            if role in {OperandRole.REGISTER, OperandRole.PIN} and not isinstance(
                operand, RegisterOperand
            ):
                raise TypeError(f"{opcode} operand {index} must be a register")
            if role == OperandRole.LABEL and not isinstance(operand, LabelOperand):
                raise TypeError(f"{opcode} operand {index} must be a label")

    def render(self) -> str:
        body = " ".join((self.opcode, *(str(item) for item in self.operands)))
        return f"{self.condition.value} {body}" if self.condition.value else f"  {body}"


@dataclass(frozen=True)
class ProgramLine:
    label: str | None = None
    instruction: Instruction | None = None
    comment: str | None = None
    source_line: int | None = None

    def __post_init__(self) -> None:
        if self.label is not None:
            _validate_label(self.label)

    def render(self) -> str:
        pieces: list[str] = []
        if self.label is not None:
            pieces.append(f"{self.label}:")
        if self.instruction is not None:
            instruction = self.instruction.render()
            if pieces:
                instruction = instruction.lstrip()
            pieces.append(instruction)
        text = " ".join(pieces)
        if self.comment is not None:
            separator = "  " if text else ""
            text += f"{separator}# {self.comment}" if self.comment else f"{separator}#"
        return text


@dataclass(frozen=True)
class Program:
    lines: tuple[ProgramLine, ...]

    @property
    def instructions(self) -> tuple[Instruction, ...]:
        return tuple(
            line.instruction for line in self.lines if line.instruction is not None
        )

    @property
    def instruction_lines(self) -> tuple[ProgramLine, ...]:
        return tuple(line for line in self.lines if line.instruction is not None)

    @property
    def instruction_count(self) -> int:
        return sum(line.instruction is not None for line in self.lines)

    @property
    def labels(self) -> dict[str, int]:
        labels: dict[str, int] = {}
        instruction_index = 0
        for line in self.lines:
            if line.label is not None:
                labels[line.label] = instruction_index
            if line.instruction is not None:
                instruction_index += 1
        return labels

    def render_lines(self) -> list[str]:
        return [line.render() for line in self.lines]

    def __iter__(self) -> Iterator[ProgramLine]:
        return iter(self.lines)


@dataclass(frozen=True)
class Label:
    name: str

    def __post_init__(self) -> None:
        _validate_label(self.name)

    def __str__(self) -> str:
        return self.name


def parse_program(lines: Iterable[str]) -> Program:
    parsed = tuple(
        _parse_program_line(raw, line_number)
        for line_number, raw in enumerate(lines, 1)
    )
    program = Program(parsed)
    validate_program_structure(program)
    return program


def validate_program_structure(program: Program) -> None:
    labels: dict[str, ProgramLine] = {}
    for line in program.lines:
        if line.label is None:
            continue
        if line.label in labels:
            raise ProgramError(f"duplicate label {line.label!r}", line.source_line)
        labels[line.label] = line

    for line in program.instruction_lines:
        instruction = line.instruction
        assert instruction is not None
        if instruction.opcode != "jmp":
            continue
        target = instruction.operands[0]
        assert isinstance(target, LabelOperand)
        if target.name not in labels:
            raise ProgramError(
                f"jump references undefined label {target.name!r}", line.source_line
            )
        if program.labels[target.name] >= program.instruction_count:
            raise ProgramError(
                f"label {target.name!r} does not precede an instruction",
                labels[target.name].source_line,
            )


def validate_program_for_part(program: Program, part: Part) -> None:
    validate_program_structure(program)
    if (
        part.spec.max_code_lines is not None
        and program.instruction_count > part.spec.max_code_lines
    ):
        raise ProgramError(
            f"{part.name} has {program.instruction_count} instructions; "
            f"{part.type_name} limit is {part.spec.max_code_lines}"
        )

    available = {"null", *part.spec.registers, *part.spec.pins}
    for line in program.instruction_lines:
        instruction = line.instruction
        assert instruction is not None
        for operand in instruction.operands:
            if isinstance(operand, RegisterOperand) and operand.name not in available:
                raise ProgramError(
                    f"{part.name} references unavailable register {operand.name!r}",
                    line.source_line,
                )

        if instruction.opcode == "slx":
            _validate_pin_operand(part, line, PinKind.XBUS)
        elif instruction.opcode == "gen":
            _validate_pin_operand(part, line, PinKind.SIMPLE)


class ConditionalProgramProxy:
    def __init__(self, builder: "ProgramBuilder", condition: Condition) -> None:
        self.builder = builder
        self.condition = condition

    def __getattr__(self, name: str):
        target = getattr(self.builder, name)

        def wrapped(*args: Any) -> "ProgramBuilder":
            return target(*args, prefix=self.condition.value)

        return wrapped


class ProgramBuilder:
    def __init__(self, part: Part) -> None:
        self.part = part
        self._lines: list[ProgramLine] = []
        self.plus = ConditionalProgramProxy(self, Condition.POSITIVE)
        self.minus = ConditionalProgramProxy(self, Condition.NEGATIVE)

    @property
    def program(self) -> Program:
        return Program(tuple(self._lines))

    @property
    def lines(self) -> list[str]:
        return self.program.render_lines()

    def emit(self, opcode: str, *operands: Any, prefix: str = "") -> "ProgramBuilder":
        try:
            condition = Condition(prefix)
        except ValueError as exc:
            raise ValueError(f"invalid conditional prefix {prefix!r}") from exc
        opcode = opcode.lower()
        try:
            signature = INSTRUCTION_SIGNATURES[opcode]
        except KeyError as exc:
            raise ValueError(f"unknown instruction {opcode!r}") from exc
        if len(operands) != len(signature):
            raise ValueError(
                f"{opcode} expects {len(signature)} operands, got {len(operands)}"
            )
        typed_operands = tuple(
            _coerce_operand(value, role)
            for value, role in zip(operands, signature)
        )
        self._lines.append(
            ProgramLine(
                instruction=Instruction(opcode, typed_operands, condition),
                source_line=len(self._lines) + 1,
            )
        )
        self._sync_part()
        return self

    def raw(self, line: str) -> "ProgramBuilder":
        parsed = _parse_program_line(line, len(self._lines) + 1)
        if parsed.label is not None and any(
            item.label == parsed.label for item in self._lines
        ):
            raise ProgramError(f"duplicate label {parsed.label!r}", parsed.source_line)
        self._lines.append(parsed)
        self._sync_part()
        return self

    def label(self, name: str) -> Label:
        return Label(name)

    def mark(self, label: Label | str) -> "ProgramBuilder":
        name = str(label)
        _validate_label(name)
        if any(item.label == name for item in self._lines):
            raise ProgramError(f"duplicate label {name!r}", len(self._lines) + 1)
        self._lines.append(ProgramLine(label=name, source_line=len(self._lines) + 1))
        self._sync_part()
        return self

    def nop(self, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("nop", prefix=prefix)

    def mov(self, src: Any, dst: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("mov", src, dst, prefix=prefix)

    def jmp(self, label: Label | str, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("jmp", label, prefix=prefix)

    def slp(self, value: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("slp", value, prefix=prefix)

    def slx(self, pin: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("slx", pin, prefix=prefix)

    def teq(self, a: Any, b: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("teq", a, b, prefix=prefix)

    def tgt(self, a: Any, b: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("tgt", a, b, prefix=prefix)

    def tlt(self, a: Any, b: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("tlt", a, b, prefix=prefix)

    def tcp(self, a: Any, b: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("tcp", a, b, prefix=prefix)

    def add(self, value: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("add", value, prefix=prefix)

    def sub(self, value: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("sub", value, prefix=prefix)

    def mul(self, value: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("mul", value, prefix=prefix)

    def not_(self, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("not", prefix=prefix)

    def dgt(self, value: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("dgt", value, prefix=prefix)

    def dst(self, digit: Any, value: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("dst", digit, value, prefix=prefix)

    def gen(self, pin: Any, on_time: Any, off_time: Any, *, prefix: str = "") -> "ProgramBuilder":
        return self.emit("gen", pin, on_time, off_time, prefix=prefix)

    def _sync_part(self) -> None:
        self.part.set_program(self.program)


def _parse_program_line(raw: str, line_number: int) -> ProgramLine:
    code, marker, comment = raw.partition("#")
    comment_value = comment.strip() if marker else None
    remaining = code.strip()
    label: str | None = None

    if ":" in remaining:
        candidate, suffix = remaining.split(":", 1)
        candidate = candidate.strip()
        if not candidate or not _LABEL_RE.fullmatch(candidate):
            raise ProgramError(f"invalid label {candidate!r}", line_number)
        label = candidate
        remaining = suffix.strip()

    if not remaining:
        return ProgramLine(
            label=label,
            comment=comment_value,
            source_line=line_number,
        )

    condition = Condition.ALWAYS
    if remaining[0] in "+-":
        condition = Condition(remaining[0])
        remaining = remaining[1:].strip()
        if not remaining:
            raise ProgramError("conditional prefix has no instruction", line_number)

    tokens = remaining.split()
    opcode = tokens[0].lower()
    try:
        signature = INSTRUCTION_SIGNATURES[opcode]
    except KeyError as exc:
        raise ProgramError(f"unknown instruction {opcode!r}", line_number) from exc
    if len(tokens) - 1 != len(signature):
        raise ProgramError(
            f"{opcode} expects {len(signature)} operands, got {len(tokens) - 1}",
            line_number,
        )
    try:
        operands = tuple(
            _parse_operand(token, role)
            for token, role in zip(tokens[1:], signature)
        )
        instruction = Instruction(opcode, operands, condition)
    except (TypeError, ValueError) as exc:
        raise ProgramError(str(exc), line_number) from exc
    return ProgramLine(
        label=label,
        instruction=instruction,
        comment=comment_value,
        source_line=line_number,
    )


def _parse_operand(token: str, role: OperandRole) -> Operand:
    if role == OperandRole.LABEL:
        return LabelOperand(token)
    if role in {OperandRole.REGISTER, OperandRole.PIN}:
        return RegisterOperand(token.lower())
    try:
        value = int(token, 10)
    except ValueError:
        return RegisterOperand(token.lower())
    return Immediate(value)


def _coerce_operand(value: Any, role: OperandRole) -> Operand:
    if role == OperandRole.LABEL:
        return LabelOperand(str(value))
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid instruction operands")
    if isinstance(value, int):
        if role in {OperandRole.REGISTER, OperandRole.PIN}:
            raise TypeError(f"{role.value} operand cannot be an integer")
        return Immediate(value)
    if isinstance(value, (RegisterRef, PinRef)):
        if role == OperandRole.LABEL:
            raise TypeError("label operand cannot be a register")
        return RegisterOperand(value.name)
    if isinstance(value, str):
        if role == OperandRole.LABEL:
            return LabelOperand(value)
        if role == OperandRole.VALUE:
            try:
                immediate = int(value, 10)
            except ValueError:
                pass
            else:
                return Immediate(immediate)
        return RegisterOperand(value.lower())
    if isinstance(value, Label):
        if role != OperandRole.LABEL:
            raise TypeError("label cannot be used as a value")
        return LabelOperand(value.name)
    raise TypeError(f"unsupported instruction operand {value!r}")


def _validate_label(name: str) -> None:
    if not _LABEL_RE.fullmatch(name):
        raise ValueError(
            f"invalid label {name!r}; labels must start with a letter and contain "
            "only letters, digits, and underscores"
        )


def _validate_pin_operand(
    part: Part,
    line: ProgramLine,
    required_kind: PinKind,
) -> None:
    instruction = line.instruction
    assert instruction is not None
    operand = instruction.operands[0]
    assert isinstance(operand, RegisterOperand)
    pin = part.spec.pins.get(operand.name)
    if pin is None:
        raise ProgramError(
            f"{instruction.opcode} requires a pin, got {operand.name!r}",
            line.source_line,
        )
    if pin.kind != required_kind:
        raise ProgramError(
            f"{instruction.opcode} requires a {required_kind.value} pin; "
            f"{part.name}.{operand.name} is {pin.kind.value}",
            line.source_line,
        )
