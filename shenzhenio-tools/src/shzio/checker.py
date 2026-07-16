from __future__ import annotations

import re
from dataclasses import dataclass

from .api import Solution
from .model import Part, PinKind
from .physical import analyze_physical_nets, find_net_for_pin

PIN_TOKEN_RE = re.compile(r"\b([px][0-3])\b")


@dataclass
class Diagnostic:
    level: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


def check_solution(solution: Solution) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_nets(solution))
    diagnostics.extend(_check_parts(solution))
    diagnostics.extend(_check_programs(solution))
    diagnostics.extend(_check_physical_nets(solution))
    return diagnostics


def _check_nets(solution: Solution) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for net in solution.nets:
        if net.a.kind != net.b.kind:
            diagnostics.append(
                Diagnostic(
                    "error",
                    f"net {net.name or ''} mixes {net.a.owner.name}.{net.a.name} ({net.a.kind.value}) "
                    f"and {net.b.owner.name}.{net.b.name} ({net.b.kind.value})",
                )
            )
    return diagnostics


def _check_parts(solution: Solution) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    all_parts = [*solution.parts, *solution.board.fixed_parts]
    occupied: dict[tuple[int, int], Part] = {}
    for part in all_parts:
        if part.x is None or part.y is None:
            diagnostics.append(Diagnostic("error", f"part {part.name} has no placement"))
            continue
        if part.x < 0 or part.y < 0:
            diagnostics.append(Diagnostic("error", f"part {part.name} has negative placement ({part.x}, {part.y})"))
        for dx in range(part.spec.width):
            for dy in range(part.spec.height):
                pos = (part.x + dx, part.y + dy)
                if pos in occupied:
                    diagnostics.append(Diagnostic("error", f"part {part.name} overlaps {occupied[pos].name} at {pos}"))
                occupied[pos] = part
    return diagnostics


def _check_programs(solution: Solution) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    connected = {(id(net.a.owner), net.a.name) for net in solution.nets}
    connected.update((id(net.b.owner), net.b.name) for net in solution.nets)

    for part in solution.parts:
        max_lines = part.spec.max_code_lines
        instruction_lines = _instruction_lines(part.code_lines)
        if max_lines is not None and len(instruction_lines) > max_lines:
            diagnostics.append(
                Diagnostic("error", f"{part.name} has {len(instruction_lines)} instruction lines; {part.type_name} limit is {max_lines}")
            )
        for line in instruction_lines:
            for pin_name in PIN_TOKEN_RE.findall(line):
                if pin_name not in part.spec.pins:
                    diagnostics.append(Diagnostic("error", f"{part.name} references missing pin {pin_name}"))
                    continue
                if (id(part), pin_name) not in connected:
                    diagnostics.append(Diagnostic("warning", f"{part.name}.{pin_name} is referenced but not connected in API netlist"))
                if pin_name.startswith("p") and part.spec.pins[pin_name].kind != PinKind.SIMPLE:
                    diagnostics.append(Diagnostic("error", f"{part.name}.{pin_name} is not a simple pin"))
                if pin_name.startswith("x") and part.spec.pins[pin_name].kind != PinKind.XBUS:
                    diagnostics.append(Diagnostic("error", f"{part.name}.{pin_name} is not an XBus pin"))
    return diagnostics


def _check_physical_nets(solution: Solution) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    physical_nets = analyze_physical_nets(solution.board, solution.parts)
    for net in solution.nets:
        a_net = find_net_for_pin(physical_nets, net.a)
        b_net = find_net_for_pin(physical_nets, net.b)
        if a_net is None:
            diagnostics.append(Diagnostic("error", f"{net.a.owner.name}.{net.a.name} has no physical trace connection"))
            continue
        if b_net is None:
            diagnostics.append(Diagnostic("error", f"{net.b.owner.name}.{net.b.name} has no physical trace connection"))
            continue
        if a_net is not b_net:
            a_seen = ", ".join(endpoint.label for endpoint in a_net.endpoints) or "no endpoints"
            b_seen = ", ".join(endpoint.label for endpoint in b_net.endpoints) or "no endpoints"
            diagnostics.append(
                Diagnostic(
                    "error",
                    f"API net {net.a.owner.name}.{net.a.name} <-> {net.b.owner.name}.{net.b.name} "
                    f"does not match physical traces; seen [{a_seen}] and [{b_seen}]",
                )
            )

    for physical_net in physical_nets:
        kinds = {endpoint.kind for endpoint in physical_net.endpoints}
        if len(kinds) > 1:
            endpoints = ", ".join(endpoint.label for endpoint in physical_net.endpoints)
            diagnostics.append(Diagnostic("error", f"physical trace mixes pin kinds: {endpoints}"))
    return diagnostics


def _instruction_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":"):
            continue
        out.append(line)
    return out
