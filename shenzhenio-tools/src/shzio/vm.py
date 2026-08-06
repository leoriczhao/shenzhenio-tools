from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .model import Part, PinKind
from .program import (
    MAX_VALUE,
    MIN_VALUE,
    Condition,
    Immediate,
    Instruction,
    LabelOperand,
    Program,
    RegisterOperand,
    parse_program,
    validate_program_for_part,
)


class VMError(RuntimeError):
    pass


@dataclass(frozen=True)
class PinRead:
    ready: bool
    value: int = 0


class PinIO(Protocol):
    def can_read(self, pin_name: str) -> bool: ...

    def try_read(self, pin_name: str) -> PinRead: ...

    def try_write(self, pin_name: str, value: int) -> bool: ...


class MemoryPinIO:
    """Deterministic standalone I/O used by VM unit tests and small probes.

    It is not a network simulator. Simple pins are immediate levels; XBus input
    values are queued and XBus output writes are captured when enabled.
    """

    def __init__(self, pin_kinds: dict[str, PinKind]) -> None:
        self.pin_kinds = dict(pin_kinds)
        self.simple_inputs: dict[str, int] = defaultdict(int)
        self.simple_outputs: dict[str, int] = {}
        self.xbus_inputs: dict[str, deque[int]] = {
            name: deque() for name, kind in pin_kinds.items() if kind == PinKind.XBUS
        }
        self.xbus_outputs: dict[str, list[int]] = {
            name: [] for name, kind in pin_kinds.items() if kind == PinKind.XBUS
        }
        self.xbus_write_ready: dict[str, bool] = defaultdict(lambda: True)

    def set_simple_input(self, pin_name: str, value: int) -> None:
        self._require_kind(pin_name, PinKind.SIMPLE)
        self.simple_inputs[pin_name] = _clamp(value, 0, 100)

    def push_xbus_input(self, pin_name: str, value: int) -> None:
        self._require_kind(pin_name, PinKind.XBUS)
        self.xbus_inputs[pin_name].append(_clamp(value, MIN_VALUE, MAX_VALUE))

    def set_xbus_write_ready(self, pin_name: str, ready: bool) -> None:
        self._require_kind(pin_name, PinKind.XBUS)
        self.xbus_write_ready[pin_name] = ready

    def can_read(self, pin_name: str) -> bool:
        kind = self._kind(pin_name)
        if kind == PinKind.SIMPLE:
            return True
        if kind == PinKind.XBUS:
            return bool(self.xbus_inputs[pin_name])
        return False

    def try_read(self, pin_name: str) -> PinRead:
        kind = self._kind(pin_name)
        if kind == PinKind.SIMPLE:
            self.simple_outputs.pop(pin_name, None)
            return PinRead(True, self.simple_inputs[pin_name])
        if kind == PinKind.XBUS:
            queue = self.xbus_inputs[pin_name]
            return PinRead(True, queue.popleft()) if queue else PinRead(False)
        return PinRead(False)

    def try_write(self, pin_name: str, value: int) -> bool:
        kind = self._kind(pin_name)
        if kind == PinKind.SIMPLE:
            self.simple_outputs[pin_name] = _clamp(value, 0, 100)
            return True
        if kind == PinKind.XBUS:
            if not self.xbus_write_ready[pin_name]:
                return False
            self.xbus_outputs[pin_name].append(_clamp(value, MIN_VALUE, MAX_VALUE))
            return True
        return False

    def _kind(self, pin_name: str) -> PinKind:
        try:
            return self.pin_kinds[pin_name]
        except KeyError as exc:
            raise VMError(f"unknown pin {pin_name!r}") from exc

    def _require_kind(self, pin_name: str, expected: PinKind) -> None:
        actual = self._kind(pin_name)
        if actual != expected:
            raise VMError(
                f"pin {pin_name!r} is {actual.value}, expected {expected.value}"
            )


class StepStatus(str, Enum):
    EXECUTED = "executed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    SLEEPING = "sleeping"
    IDLE = "idle"


@dataclass(frozen=True)
class StepResult:
    status: StepStatus
    pc_before: int
    pc_after: int
    instruction: Instruction | None
    power_used: int = 0


@dataclass
class _SleepState:
    remaining: int


@dataclass
class _PulseState:
    pin_name: str
    phase: str
    remaining: int
    low_duration: int


class MicrocontrollerVM:
    def __init__(
        self,
        part: Part,
        program: Program | None = None,
        pin_io: PinIO | None = None,
    ) -> None:
        self.part = part
        self.program = program or part.program_ir or parse_program(part.code_lines)
        validate_program_for_part(self.program, part)
        if "acc" not in part.spec.registers:
            raise VMError(f"{part.type_name} is not an MCxxxx microcontroller")

        self._instruction_lines = self.program.instruction_lines
        self._labels = self.program.labels
        self.pin_io = pin_io or MemoryPinIO(
            {name: pin.kind for name, pin in part.spec.pins.items()}
        )
        self.registers = {name: 0 for name in part.spec.registers}
        self.pc = 0
        self.condition_code: int | None = None
        self.power_used = 0
        self.instructions_executed = 0
        self._operand_cache: dict[int, int] = {}
        self._sleep: _SleepState | None = None
        self._pulse: _PulseState | None = None

    @property
    def sleeping(self) -> bool:
        return self._sleep is not None or self._pulse is not None

    @property
    def current_instruction(self) -> Instruction | None:
        if not self._instruction_lines:
            return None
        return self._instruction_lines[self.pc].instruction

    def read_register(self, name: str) -> int:
        if name == "null":
            return 0
        try:
            return self.registers[name]
        except KeyError as exc:
            raise VMError(f"unknown internal register {name!r}") from exc

    def write_register(self, name: str, value: int) -> None:
        if name == "null":
            return
        if name not in self.registers:
            raise VMError(f"unknown internal register {name!r}")
        self.registers[name] = _clamp(value, MIN_VALUE, MAX_VALUE)

    def step(self) -> StepResult:
        if not self._instruction_lines:
            return StepResult(StepStatus.IDLE, 0, 0, None)
        instruction = self.current_instruction
        assert instruction is not None
        pc_before = self.pc

        if self.sleeping:
            return StepResult(
                StepStatus.SLEEPING, pc_before, self.pc, instruction
            )

        if not self._condition_enabled(instruction.condition):
            self._finish_instruction()
            return StepResult(
                StepStatus.SKIPPED, pc_before, self.pc, instruction
            )

        status = self._execute(instruction)
        power = 0
        if status in {StepStatus.EXECUTED, StepStatus.SLEEPING}:
            self.power_used += 1
            self.instructions_executed += 1
            power = 1
        return StepResult(status, pc_before, self.pc, instruction, power)

    def run_until_blocked(self, max_steps: int = 10_000) -> list[StepResult]:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        results: list[StepResult] = []
        for _ in range(max_steps):
            result = self.step()
            results.append(result)
            if result.status in {
                StepStatus.BLOCKED,
                StepStatus.SLEEPING,
                StepStatus.IDLE,
            }:
                return results
        raise VMError(f"execution did not block or sleep within {max_steps} steps")

    def advance_time(self, time_units: int = 1) -> None:
        if time_units < 0:
            raise ValueError("time_units cannot be negative")
        remaining = time_units

        if self._sleep is not None:
            self._sleep.remaining -= remaining
            if self._sleep.remaining <= 0:
                self._sleep = None
                self._finish_instruction()
            return

        while self._pulse is not None:
            pulse = self._pulse
            if pulse.remaining > remaining:
                pulse.remaining -= remaining
                return
            remaining -= max(pulse.remaining, 0)
            if pulse.phase == "high":
                if not self.pin_io.try_write(pulse.pin_name, 0):
                    raise VMError("simple output unexpectedly blocked during gen")
                if pulse.low_duration > 0:
                    pulse.phase = "low"
                    pulse.remaining = pulse.low_duration
                    if remaining == 0:
                        return
                    continue
            self._pulse = None
            self._finish_instruction()
            return

    def _execute(self, instruction: Instruction) -> StepStatus:
        opcode = instruction.opcode
        if opcode == "nop":
            return self._complete()
        if opcode == "mov":
            source = self._try_value(instruction, 0)
            if source is None:
                return StepStatus.BLOCKED
            destination = instruction.operands[1]
            assert isinstance(destination, RegisterOperand)
            if not self._try_write(destination.name, source):
                return StepStatus.BLOCKED
            return self._complete()
        if opcode == "jmp":
            target = instruction.operands[0]
            assert isinstance(target, LabelOperand)
            self._operand_cache.clear()
            self.pc = self._labels[target.name]
            return StepStatus.EXECUTED
        if opcode == "slp":
            duration = self._try_value(instruction, 0)
            if duration is None:
                return StepStatus.BLOCKED
            if duration > 0:
                self._operand_cache.clear()
                self._sleep = _SleepState(duration)
                return StepStatus.SLEEPING
            return self._complete()
        if opcode == "slx":
            pin = instruction.operands[0]
            assert isinstance(pin, RegisterOperand)
            if not self.pin_io.can_read(pin.name):
                return StepStatus.BLOCKED
            return self._complete()
        if opcode in {"add", "sub", "mul"}:
            value = self._try_value(instruction, 0)
            if value is None:
                return StepStatus.BLOCKED
            acc = self.registers["acc"]
            if opcode == "add":
                acc += value
            elif opcode == "sub":
                acc -= value
            else:
                acc *= value
            self.write_register("acc", acc)
            return self._complete()
        if opcode == "not":
            self.write_register("acc", 100 if self.registers["acc"] == 0 else 0)
            return self._complete()
        if opcode == "dgt":
            digit_number = self._try_value(instruction, 0)
            if digit_number is None:
                return StepStatus.BLOCKED
            value = 0
            if 0 <= digit_number <= 2:
                shifted = _trunc_div(self.registers["acc"], 10**digit_number)
                value = _trunc_mod(shifted, 10)
            self.write_register("acc", value)
            return self._complete()
        if opcode == "dst":
            digit_number = self._try_value(instruction, 0)
            if digit_number is None:
                return StepStatus.BLOCKED
            digit_source = self._try_value(instruction, 1)
            if digit_source is None:
                return StepStatus.BLOCKED
            acc = self.registers["acc"]
            if 0 <= digit_number <= 2:
                digit_value = _trunc_mod(digit_source, 10)
                if digit_value < 0:
                    acc = -acc
                place = 10**digit_number
                old_digit = _trunc_mod(_trunc_div(acc, place), 10) * place
                acc = acc - old_digit + digit_value * place
            self.write_register("acc", acc)
            return self._complete()
        if opcode in {"teq", "tgt", "tlt", "tcp"}:
            left = self._try_value(instruction, 0)
            if left is None:
                return StepStatus.BLOCKED
            right = self._try_value(instruction, 1)
            if right is None:
                return StepStatus.BLOCKED
            if opcode == "teq":
                self.condition_code = 1 if left == right else -1
            elif opcode == "tgt":
                self.condition_code = 1 if left > right else -1
            elif opcode == "tlt":
                self.condition_code = 1 if left < right else -1
            else:
                self.condition_code = (left > right) - (left < right)
            return self._complete()
        if opcode == "gen":
            pin = instruction.operands[0]
            assert isinstance(pin, RegisterOperand)
            high_duration = self._try_value(instruction, 1)
            if high_duration is None:
                return StepStatus.BLOCKED
            low_duration = self._try_value(instruction, 2)
            if low_duration is None:
                return StepStatus.BLOCKED
            if not self.pin_io.try_write(pin.name, 100):
                return StepStatus.BLOCKED
            self._operand_cache.clear()
            if high_duration > 0:
                self._pulse = _PulseState(
                    pin.name, "high", high_duration, max(low_duration, 0)
                )
                return StepStatus.SLEEPING
            if not self.pin_io.try_write(pin.name, 0):
                raise VMError("simple output unexpectedly blocked during gen")
            if low_duration > 0:
                self._pulse = _PulseState(pin.name, "low", low_duration, 0)
                return StepStatus.SLEEPING
            self._finish_instruction()
            return StepStatus.EXECUTED
        raise VMError(f"unimplemented instruction {opcode!r}")

    def _try_value(self, instruction: Instruction, index: int) -> int | None:
        if index in self._operand_cache:
            return self._operand_cache[index]
        operand = instruction.operands[index]
        if isinstance(operand, Immediate):
            value = operand.value
        elif isinstance(operand, RegisterOperand):
            read = self._try_read(operand.name)
            if not read.ready:
                return None
            value = read.value
        else:
            raise VMError(f"operand {operand!r} is not readable")
        self._operand_cache[index] = value
        return value

    def _try_read(self, name: str) -> PinRead:
        if name == "null":
            return PinRead(True, 0)
        if name in self.registers:
            return PinRead(True, self.registers[name])
        if name in self.part.spec.pins:
            return self.pin_io.try_read(name)
        raise VMError(f"unknown register {name!r}")

    def _try_write(self, name: str, value: int) -> bool:
        if name == "null":
            return True
        if name in self.registers:
            self.write_register(name, value)
            return True
        if name in self.part.spec.pins:
            return self.pin_io.try_write(name, value)
        raise VMError(f"unknown register {name!r}")

    def _condition_enabled(self, condition: Condition) -> bool:
        if condition == Condition.ALWAYS:
            return True
        if condition == Condition.POSITIVE:
            return self.condition_code == 1
        return self.condition_code == -1

    def _complete(self) -> StepStatus:
        self._finish_instruction()
        return StepStatus.EXECUTED

    def _finish_instruction(self) -> None:
        self._operand_cache.clear()
        if self._instruction_lines:
            self.pc = (self.pc + 1) % len(self._instruction_lines)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)


def _trunc_div(left: int, right: int) -> int:
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


def _trunc_mod(left: int, right: int) -> int:
    return left - _trunc_div(left, right) * right
