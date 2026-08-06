from __future__ import annotations

from dataclasses import dataclass

from .api import Solution
from .geometry import (
    footprint_cells,
    footprint_size,
    part_overlap_allowed,
    port_overlap_allowed,
    trace_blocked_cells,
)
from .model import Part, PinKind
from .physical import DIRS, analyze_physical_nets, find_net_for_pin
from .program import (
    ProgramError,
    RegisterOperand,
    parse_program,
    validate_program_for_part,
)


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
    diagnostics.extend(_check_traces(solution))
    diagnostics.extend(_check_physical_nets(solution))
    return diagnostics


def _check_nets(solution: Solution) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for net in solution.nets:
        if net.a.owner is net.b.owner:
            diagnostics.append(
                Diagnostic(
                    "error",
                    f"net {net.name or ''} connects {net.a.owner.name} to itself "
                    f"through {net.a.name} and {net.b.name}",
                )
            )
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
    min_x, min_y = solution.board.placement_origin
    placement_width, placement_height = solution.board.placement_size
    max_x = min_x + placement_width
    max_y = min_y + placement_height
    terminal_cells = {
        (port.x, port.y): port
        for port in solution.board.ports.values()
        if port.kind != PinKind.DISPLAY and port.x is not None and port.y is not None
    }
    for part in all_parts:
        if part.x is None or part.y is None:
            diagnostics.append(Diagnostic("error", f"part {part.name} has no placement"))
            continue
        if not solution.board.contains_footprint(
            (part.x, part.y), footprint_size(part)
        ):
            diagnostics.append(
                Diagnostic(
                    "error",
                    f"part {part.name} footprint at ({part.x}, {part.y}) is outside "
                    f"placement range ({min_x}, {min_y})..({max_x}, {max_y})",
                )
            )
        for pos in footprint_cells(part):
                if pos in occupied and not _bridge_terminal_overlap_allowed(
                    part, occupied[pos]
                ):
                    diagnostics.append(Diagnostic("error", f"part {part.name} overlaps {occupied[pos].name} at {pos}"))
                terminal = terminal_cells.get(pos)
                if terminal is not None and not _bridge_port_overlap_allowed(
                    part, pos
                ):
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            f"part {part.name} overlaps board terminal {terminal.name} at {pos}",
                        )
                    )
                occupied[pos] = part
    return diagnostics


def _bridge_terminal_overlap_allowed(a: Part, b: Part) -> bool:
    return part_overlap_allowed(a, b)


def _bridge_port_overlap_allowed(part: Part, position: tuple[int, int]) -> bool:
    return port_overlap_allowed(part, position)


def _check_programs(solution: Solution) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    connected = {(id(net.a.owner), net.a.name) for net in solution.nets}
    connected.update((id(net.b.owner), net.b.name) for net in solution.nets)

    for part in solution.parts:
        try:
            program = part.program_ir or parse_program(part.code_lines)
            validate_program_for_part(program, part)
        except ProgramError as exc:
            diagnostics.append(
                Diagnostic("error", f"{part.name} program: {exc}")
            )
            continue

        referenced_pins = {
            operand.name
            for instruction in program.instructions
            for operand in instruction.operands
            if isinstance(operand, RegisterOperand)
            and operand.name in part.spec.pins
        }
        for pin_name in sorted(referenced_pins):
            if (id(part), pin_name) not in connected:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        f"{part.name}.{pin_name} is referenced but not connected in API netlist",
                    )
                )
    return diagnostics


def _check_traces(solution: Solution) -> list[Diagnostic]:
    grid = solution.board.traces
    if grid is None:
        return [Diagnostic("error", "board has no trace grid")]

    diagnostics: list[Diagnostic] = []
    blocked_by_parts = trace_blocked_cells(
        [*solution.parts, *solution.board.fixed_parts]
    )
    reported_edges: set[tuple[tuple[int, int], int]] = set()
    for (x, y), mask in grid.nonempty_cells().items():
        if (x, y) in blocked_by_parts:
            diagnostics.append(
                Diagnostic("error", f"trace at ({x}, {y}) passes through a part footprint")
            )
        if solution.board.routable_cells and (x, y) not in solution.board.routable_cells:
            diagnostics.append(
                Diagnostic("error", f"trace at ({x}, {y}) is outside the routable board area")
            )
        for bit, (dx, dy, opposite) in DIRS.items():
            if not (mask & bit) or ((x, y), bit) in reported_edges:
                continue
            nx, ny = x + dx, y + dy
            reported_edges.add(((x, y), bit))
            reported_edges.add(((nx, ny), opposite))
            if nx < 0 or ny < 0 or nx >= grid.width or ny >= grid.height:
                diagnostics.append(
                    Diagnostic("error", f"trace at ({x}, {y}) points outside the canvas")
                )
                continue
            if not (grid.mask_at(nx, ny) & opposite):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        f"trace edge ({x}, {y}) -> ({nx}, {ny}) has no reciprocal direction bit",
                    )
                )
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
