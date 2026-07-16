from __future__ import annotations

from dataclasses import dataclass, field

from .boards import Board
from .model import BoardPort, Part, PinKind, PinRef
from .traces import DOWN, LEFT, RIGHT, UP, TraceGrid


DIRS = {
    RIGHT: (1, 0, LEFT),
    LEFT: (-1, 0, RIGHT),
    UP: (0, -1, DOWN),
    DOWN: (0, 1, UP),
}


@dataclass
class PhysicalEndpoint:
    owner_name: str
    pin_name: str
    kind: PinKind
    x: int
    y: int

    @property
    def label(self) -> str:
        return f"{self.owner_name}.{self.pin_name}"


@dataclass
class PhysicalNet:
    cells: set[tuple[int, int]] = field(default_factory=set)
    endpoints: list[PhysicalEndpoint] = field(default_factory=list)


def analyze_physical_nets(board: Board, parts: list[Part]) -> list[PhysicalNet]:
    grid = board.traces
    if grid is None:
        return []

    component_by_cell = _trace_components(grid)
    nets: dict[int, PhysicalNet] = {}
    for cell, component_id in component_by_cell.items():
        nets.setdefault(component_id, PhysicalNet()).cells.add(cell)

    for endpoint in _endpoints(board, parts):
        component_id = component_by_cell.get((endpoint.x, endpoint.y))
        if component_id is not None:
            nets.setdefault(component_id, PhysicalNet()).endpoints.append(endpoint)

    return list(nets.values())


def endpoint_for_pin(part: Part, pin_name: str) -> PhysicalEndpoint | None:
    spec = part.spec.pins[pin_name]
    if part.x is None or part.y is None:
        return None
    if spec.contact_dx is None or spec.contact_dy is None:
        return None
    return PhysicalEndpoint(
        owner_name=part.name or part.type_name,
        pin_name=pin_name,
        kind=spec.kind,
        x=part.x + spec.contact_dx,
        y=part.y + spec.contact_dy,
    )


def _endpoints(board: Board, parts: list[Part]) -> list[PhysicalEndpoint]:
    endpoints: list[PhysicalEndpoint] = []
    for part in [*parts, *board.fixed_parts]:
        for pin_name in part.spec.pins:
            endpoint = endpoint_for_pin(part, pin_name)
            if endpoint is not None:
                endpoints.append(endpoint)
    for port in board.ports.values():
        endpoint = _endpoint_for_port(port)
        if endpoint is not None:
            endpoints.append(endpoint)
    return endpoints


def _endpoint_for_port(port: BoardPort) -> PhysicalEndpoint | None:
    if port.x is None or port.y is None:
        return None
    return PhysicalEndpoint(
        owner_name=port.name,
        pin_name=port.pin_name,
        kind=port.kind,
        x=port.x,
        y=port.y,
    )


def _trace_components(grid: TraceGrid) -> dict[tuple[int, int], int]:
    seen: set[tuple[int, int]] = set()
    component_by_cell: dict[tuple[int, int], int] = {}
    next_id = 1

    for y in range(grid.height):
        for x in range(grid.width):
            if grid.mask_at(x, y) == 0 or (x, y) in seen:
                continue
            stack = [(x, y)]
            seen.add((x, y))
            while stack:
                cell = stack.pop()
                component_by_cell[cell] = next_id
                cx, cy = cell
                mask = grid.mask_at(cx, cy)
                for bit, (dx, dy, opposite) in DIRS.items():
                    if not (mask & bit):
                        continue
                    nx, ny = cx + dx, cy + dy
                    if nx < 0 or ny < 0 or nx >= grid.width or ny >= grid.height:
                        continue
                    if not (grid.mask_at(nx, ny) & opposite):
                        continue
                    if (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            next_id += 1

    return component_by_cell


def find_net_for_pin(nets: list[PhysicalNet], pin: PinRef) -> PhysicalNet | None:
    owner_name = pin.owner.name
    for net in nets:
        for endpoint in net.endpoints:
            if endpoint.owner_name == owner_name and endpoint.pin_name == pin.name:
                return net
    return None

