from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Label:
    name: str

    def __str__(self) -> str:
        return self.name


class ConditionalProgramProxy:
    def __init__(self, builder: "ProgramBuilder", prefix: str) -> None:
        self.builder = builder
        self.prefix = prefix

    def __getattr__(self, name: str):
        target = getattr(self.builder, name)

        def wrapped(*args: Any) -> "ProgramBuilder":
            return target(*args, prefix=self.prefix)

        return wrapped


class ProgramBuilder:
    def __init__(self, part) -> None:
        self.part = part
        self.lines: list[str] = []
        self.plus = ConditionalProgramProxy(self, "+")
        self.minus = ConditionalProgramProxy(self, "-")

    def emit(self, opcode: str, *operands: Any, prefix: str = "") -> "ProgramBuilder":
        pieces = [opcode]
        pieces.extend(_operand_text(op) for op in operands)
        indent = "" if prefix else "  "
        self.lines.append(f"{prefix} {opcode} {' '.join(pieces[1:])}".rstrip() if prefix else f"{indent}{' '.join(pieces)}")
        self.part.set_code(self.lines)
        return self

    def raw(self, line: str) -> "ProgramBuilder":
        self.lines.append(line)
        self.part.set_code(self.lines)
        return self

    def label(self, name: str) -> Label:
        return Label(name)

    def mark(self, label: Label | str) -> "ProgramBuilder":
        name = str(label)
        self.lines.append(f"{name}:")
        self.part.set_code(self.lines)
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


def _operand_text(value: Any) -> str:
    return str(value)

