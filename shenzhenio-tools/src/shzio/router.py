from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count, islice, permutations
from typing import Iterable

from .boards import Board
from .geometry import trace_blocked_cells
from .model import Net, Part, PinRef
from .physical import (
    bridge_origins,
    endpoint_for_pin_ref,
    endpoints_for_design,
    trace_components,
)
from .traces import DOWN, EXISTS, LEFT, RIGHT, UP, TraceGrid


CARDINAL_STEPS = (
    (-1, 0, LEFT, RIGHT),
    (1, 0, RIGHT, LEFT),
    (0, -1, DOWN, UP),
    (0, 1, UP, DOWN),
)


class RoutingError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoutedNet:
    pins: tuple[PinRef, ...]
    cells: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class RoutingResult:
    traces: TraceGrid
    nets: tuple[RoutedNet, ...]
    strategy: str = "ordered"
    iterations: int = 0


@dataclass
class _LogicalNet:
    pins: list[PinRef]
    source_index: int
    route_hints: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _NegotiatedRoute:
    cells: frozenset[tuple[int, int]]
    edges: frozenset[tuple[tuple[int, int], tuple[int, int]]]


class DeterministicRouter:
    def __init__(
        self,
        max_order_attempts: int = 720,
        max_negotiation_iterations: int = 64,
    ) -> None:
        if max_order_attempts < 1:
            raise ValueError("max_order_attempts must be positive")
        if max_negotiation_iterations < 1:
            raise ValueError("max_negotiation_iterations must be positive")
        self.max_order_attempts = max_order_attempts
        self.max_negotiation_iterations = max_negotiation_iterations

    def route(self, board: Board, parts: list[Part], nets: list[Net]) -> RoutingResult:
        if not nets:
            return RoutingResult(
                board.traces.copy()
                if board.traces is not None
                else TraceGrid.blank(board.width, board.height),
                (),
            )
        if not board.routable_cells:
            raise RoutingError(f"board {board.puzzle_id} has no routable-cell model")

        logical_nets = _logical_nets(nets)
        bridge_edges = _bridge_edges(bridge_origins(board, parts))
        footprint_obstacles = trace_blocked_cells([*parts, *board.fixed_parts])
        initial_masks = (
            board.traces.nonempty_cells() if board.traces is not None else {}
        )
        initial_components = _initial_trace_components(
            board,
            logical_nets,
            initial_masks,
            bridge_edges,
        )
        initial_under_parts = set(initial_masks) & footprint_obstacles
        if initial_under_parts:
            raise RoutingError(
                f"initial traces pass through part footprints: {sorted(initial_under_parts)}"
            )
        contacts = {
            (endpoint.x, endpoint.y)
            for endpoint in endpoints_for_design(board, parts)
        }
        last_error: RoutingError | None = None
        for order in _route_orders(logical_nets, self.max_order_attempts):
            try:
                return self._route_in_order(
                    board,
                    order,
                    contacts,
                    initial_masks,
                    initial_components,
                    bridge_edges,
                    footprint_obstacles,
                )
            except RoutingError as exc:
                last_error = exc
        assert last_error is not None
        try:
            return self._route_negotiated(
                board,
                tuple(logical_nets),
                contacts,
                initial_masks,
                initial_components,
                bridge_edges,
                footprint_obstacles,
            )
        except RoutingError as negotiated_error:
            raise RoutingError(
                f"could not route {len(logical_nets)} logical nets on "
                f"{board.puzzle_id}; ordered search: {last_error}; "
                f"negotiated search: {negotiated_error}"
            ) from negotiated_error

    def _route_in_order(
        self,
        board: Board,
        logical_nets: tuple[_LogicalNet, ...],
        contacts: set[tuple[int, int]],
        initial_masks: dict[tuple[int, int], int],
        initial_components: dict[int | None, tuple[frozenset[tuple[int, int]], ...]],
        bridge_edges: dict[tuple[int, int], tuple[int, int]],
        footprint_obstacles: frozenset[tuple[int, int]],
    ) -> RoutingResult:
        masks = initial_masks.copy()
        occupied = set(initial_masks)
        routed: dict[int, RoutedNet] = {}

        for logical_net in logical_nets:
            endpoint_cells = _validate_logical_net(board, logical_net, contacts)
            unique_endpoints = tuple(dict.fromkeys(endpoint_cells))

            seed_components = initial_components.get(logical_net.source_index, ())
            own_seed_cells = set().union(*seed_components) if seed_components else set()
            occupied.difference_update(own_seed_cells)
            if seed_components:
                tree = set(seed_components[0])
                pending = [min(component) for component in seed_components[1:]]
                pending.extend(logical_net.route_hints)
                pending.extend(
                    endpoint for endpoint in unique_endpoints if endpoint not in tree
                )
            else:
                tree = {unique_endpoints[0]}
                pending = [*logical_net.route_hints, *unique_endpoints[1:]]
            pending = list(dict.fromkeys(cell for cell in pending if cell not in tree))
            edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
            blocked_contacts = contacts - set(unique_endpoints)
            for endpoint in pending:
                component = next(
                    (item for item in seed_components if endpoint in item),
                    frozenset({endpoint}),
                )
                path = _lee_path(
                    start=endpoint,
                    targets=tree,
                    allowed=board.routable_cells,
                    blocked=occupied | blocked_contacts,
                    footprint_obstacles=footprint_obstacles,
                    bridge_edges=bridge_edges,
                )
                for a, b in zip(path, path[1:]):
                    edges.add((a, b))
                tree.update(path)
                tree.update(component)

            if tree & occupied:
                raise RoutingError(
                    f"net {_pin_labels(logical_net.pins)} intersects an earlier net"
                )
            occupied.update(tree)
            for cell in tree:
                masks[cell] = masks.get(cell, 0) | EXISTS
            for a, b in edges:
                _connect_masks(masks, a, b, bridge_edges)
            routed[logical_net.source_index] = RoutedNet(
                tuple(logical_net.pins), frozenset(tree)
            )

        return RoutingResult(
            traces=TraceGrid.from_masks(board.width, board.height, masks),
            nets=tuple(routed[index] for index in sorted(routed)),
        )

    def _route_negotiated(
        self,
        board: Board,
        logical_nets: tuple[_LogicalNet, ...],
        contacts: set[tuple[int, int]],
        initial_masks: dict[tuple[int, int], int],
        initial_components: dict[
            int | None, tuple[frozenset[tuple[int, int]], ...]
        ],
        bridge_edges: dict[tuple[int, int], tuple[int, int]],
        footprint_obstacles: frozenset[tuple[int, int]],
    ) -> RoutingResult:
        routes: dict[int, _NegotiatedRoute] = {}
        usage: Counter[tuple[int, int]] = Counter()
        history: Counter[tuple[int, int]] = Counter()
        base_order = tuple(logical_nets)

        for iteration in range(1, self.max_negotiation_iterations + 1):
            shift = (iteration - 1) % len(base_order)
            order = base_order[shift:] + base_order[:shift]
            present_penalty = 1 + (iteration - 1) * 4

            for logical_net in order:
                previous = routes.pop(logical_net.source_index, None)
                if previous is not None:
                    _update_usage(usage, previous.cells, -1)
                route = _route_weighted_net(
                    board=board,
                    logical_net=logical_net,
                    contacts=contacts,
                    initial_masks=initial_masks,
                    initial_components=initial_components,
                    bridge_edges=bridge_edges,
                    footprint_obstacles=footprint_obstacles,
                    usage=usage,
                    history=history,
                    present_penalty=present_penalty,
                )
                routes[logical_net.source_index] = route
                _update_usage(usage, route.cells, 1)

            congested = {
                cell: used for cell, used in usage.items() if used > 1
            }
            if not congested:
                return _build_negotiated_result(
                    board,
                    logical_nets,
                    routes,
                    initial_masks,
                    bridge_edges,
                    iteration,
                )
            for cell, used in congested.items():
                history[cell] += (used - 1) * 4

        worst = sorted(
            ((used, cell) for cell, used in usage.items() if used > 1),
            reverse=True,
        )[:8]
        raise RoutingError(
            f"congestion remained after {self.max_negotiation_iterations} "
            f"iterations at {worst}"
        )


def route_nets(board: Board, parts: list[Part], nets: list[Net]) -> RoutingResult:
    return DeterministicRouter().route(board, parts, nets)


def _route_weighted_net(
    *,
    board: Board,
    logical_net: _LogicalNet,
    contacts: set[tuple[int, int]],
    initial_masks: dict[tuple[int, int], int],
    initial_components: dict[
        int | None, tuple[frozenset[tuple[int, int]], ...]
    ],
    bridge_edges: dict[tuple[int, int], tuple[int, int]],
    footprint_obstacles: frozenset[tuple[int, int]],
    usage: Counter[tuple[int, int]],
    history: Counter[tuple[int, int]],
    present_penalty: int,
) -> _NegotiatedRoute:
    endpoint_cells = _validate_logical_net(board, logical_net, contacts)
    unique_endpoints = tuple(dict.fromkeys(endpoint_cells))
    seed_components = initial_components.get(logical_net.source_index, ())
    own_seed_cells = set().union(*seed_components) if seed_components else set()
    hard_blocked = (
        footprint_obstacles
        | (contacts - set(unique_endpoints))
        | (set(initial_masks) - own_seed_cells)
    )

    if seed_components:
        tree = set(seed_components[0])
        pending = [min(component) for component in seed_components[1:]]
        pending.extend(logical_net.route_hints)
        pending.extend(
            endpoint for endpoint in unique_endpoints if endpoint not in tree
        )
    else:
        tree = {unique_endpoints[0]}
        pending = [*logical_net.route_hints, *unique_endpoints[1:]]
    pending = list(dict.fromkeys(cell for cell in pending if cell not in tree))
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    for endpoint in pending:
        component = next(
            (item for item in seed_components if endpoint in item),
            frozenset({endpoint}),
        )
        path = _weighted_path(
            start=endpoint,
            targets=tree,
            allowed=board.routable_cells,
            hard_blocked=hard_blocked,
            bridge_edges=bridge_edges,
            usage=usage,
            history=history,
            present_penalty=present_penalty,
        )
        edges.update(zip(path, path[1:]))
        tree.update(path)
        tree.update(component)

    return _NegotiatedRoute(frozenset(tree), frozenset(edges))


def _validate_logical_net(
    board: Board,
    logical_net: _LogicalNet,
    contacts: set[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    endpoints = tuple(_pin_coordinate(pin) for pin in logical_net.pins)
    unique_endpoints = set(endpoints)
    for cell in endpoints:
        if cell not in board.routable_cells:
            raise RoutingError(
                f"endpoint {_pin_labels(logical_net.pins)} at {cell} is not routable"
            )
    for hint in logical_net.route_hints:
        if hint not in board.routable_cells:
            raise RoutingError(
                f"route hint {hint} for {_pin_labels(logical_net.pins)} "
                "is not routable"
            )
        if hint in contacts and hint not in unique_endpoints:
            raise RoutingError(
                f"route hint {hint} for {_pin_labels(logical_net.pins)} "
                "is an unrelated pin contact"
            )
    return endpoints


def _weighted_path(
    *,
    start: tuple[int, int],
    targets: set[tuple[int, int]],
    allowed: frozenset[tuple[int, int]],
    hard_blocked: frozenset[tuple[int, int]] | set[tuple[int, int]],
    bridge_edges: dict[tuple[int, int], tuple[int, int]],
    usage: Counter[tuple[int, int]],
    history: Counter[tuple[int, int]],
    present_penalty: int,
) -> list[tuple[int, int]]:
    if start in targets:
        return [start]
    sequence = count()
    distances = {start: 0}
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue: list[tuple[int, int, int, tuple[int, int]]] = [
        (0, 0, next(sequence), start)
    ]

    while queue:
        cost, hops, _, current = heappop(queue)
        if cost != distances.get(current):
            continue
        if current in targets:
            return _reconstruct_path(parent, current)
        for neighbor in _route_neighbors(current, bridge_edges):
            if neighbor not in allowed or neighbor in hard_blocked:
                continue
            step_cost = (
                1
                + usage.get(neighbor, 0) * present_penalty
                + history.get(neighbor, 0)
            )
            candidate_cost = cost + step_cost
            if candidate_cost >= distances.get(neighbor, 1 << 60):
                continue
            distances[neighbor] = candidate_cost
            parent[neighbor] = current
            heappush(
                queue,
                (candidate_cost, hops + 1, next(sequence), neighbor),
            )

    raise RoutingError(
        f"no legal negotiated path from {start} to the existing net tree"
    )


def _route_neighbors(
    cell: tuple[int, int],
    bridge_edges: dict[tuple[int, int], tuple[int, int]],
) -> Iterable[tuple[int, int]]:
    x, y = cell
    for dx, dy, _, _ in CARDINAL_STEPS:
        yield x + dx, y + dy
    bridge_neighbor = bridge_edges.get(cell)
    if bridge_neighbor is not None:
        yield bridge_neighbor


def _update_usage(
    usage: Counter[tuple[int, int]],
    cells: Iterable[tuple[int, int]],
    delta: int,
) -> None:
    for cell in cells:
        usage[cell] += delta
        if usage[cell] == 0:
            del usage[cell]


def _build_negotiated_result(
    board: Board,
    logical_nets: tuple[_LogicalNet, ...],
    routes: dict[int, _NegotiatedRoute],
    initial_masks: dict[tuple[int, int], int],
    bridge_edges: dict[tuple[int, int], tuple[int, int]],
    iterations: int,
) -> RoutingResult:
    masks = initial_masks.copy()
    routed = []
    for logical_net in sorted(logical_nets, key=lambda item: item.source_index):
        route = routes[logical_net.source_index]
        for cell in route.cells:
            masks[cell] = masks.get(cell, 0) | EXISTS
        for a, b in route.edges:
            _connect_masks(masks, a, b, bridge_edges)
        routed.append(RoutedNet(tuple(logical_net.pins), route.cells))
    return RoutingResult(
        traces=TraceGrid.from_masks(board.width, board.height, masks),
        nets=tuple(routed),
        strategy="negotiated",
        iterations=iterations,
    )


def _logical_nets(nets: list[Net]) -> list[_LogicalNet]:
    parent: dict[tuple[int, str], tuple[int, str]] = {}
    pins: dict[tuple[int, str], PinRef] = {}
    first_seen: dict[tuple[int, str], int] = {}

    def key(pin: PinRef) -> tuple[int, str]:
        return id(pin.owner), pin.name

    def find(item: tuple[int, str]) -> tuple[int, str]:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: tuple[int, str], b: tuple[int, str]) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for index, net in enumerate(nets):
        a = key(net.a)
        b = key(net.b)
        pins.setdefault(a, net.a)
        pins.setdefault(b, net.b)
        first_seen.setdefault(a, index)
        first_seen.setdefault(b, index)
        union(a, b)

    grouped: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for item in pins:
        grouped.setdefault(find(item), []).append(item)

    logical_nets = [
        _LogicalNet(
            pins=[pins[item] for item in sorted(items, key=first_seen.__getitem__)],
            source_index=min(first_seen[item] for item in items),
            route_hints=(),
        )
        for items in grouped.values()
    ]
    logical_nets.sort(key=lambda item: item.source_index)
    by_pin = {
        (id(pin.owner), pin.name): logical_net
        for logical_net in logical_nets
        for pin in logical_net.pins
    }
    hints: dict[int, list[tuple[int, int]]] = {
        logical_net.source_index: [] for logical_net in logical_nets
    }
    for net in nets:
        logical_net = by_pin[(id(net.a.owner), net.a.name)]
        hints[logical_net.source_index].extend(net.route_hints)
    for logical_net in logical_nets:
        logical_net.route_hints = tuple(
            dict.fromkeys(hints[logical_net.source_index])
        )
    return logical_nets


def _initial_trace_components(
    board: Board,
    logical_nets: list[_LogicalNet],
    initial_masks: dict[tuple[int, int], int],
    bridge_edges: dict[tuple[int, int], tuple[int, int]],
) -> dict[int | None, tuple[frozenset[tuple[int, int]], ...]]:
    if not initial_masks:
        return {}
    outside = set(initial_masks) - board.routable_cells
    if outside:
        raise RoutingError(
            f"board {board.puzzle_id} has initial traces outside the routable area: "
            f"{sorted(outside)}"
        )

    bridge_positions = [
        cell for cell, neighbor in bridge_edges.items() if neighbor[1] > cell[1]
    ]
    component_ids = (
        trace_components(board.traces, bridge_positions)
        if board.traces is not None
        else {}
    )
    cells_by_component: dict[int, set[tuple[int, int]]] = {}
    for cell, component_id in component_ids.items():
        cells_by_component.setdefault(component_id, set()).add(cell)

    endpoint_owners: dict[tuple[int, int], set[int]] = {}
    for logical_net in logical_nets:
        for pin in logical_net.pins:
            endpoint_owners.setdefault(_pin_coordinate(pin), set()).add(
                logical_net.source_index
            )

    grouped: dict[int | None, list[frozenset[tuple[int, int]]]] = {}
    for cells in cells_by_component.values():
        owners = set().union(*(endpoint_owners.get(cell, set()) for cell in cells))
        if len(owners) > 1:
            raise RoutingError(
                f"an initial trace component on {board.puzzle_id} shorts API nets "
                f"{sorted(owners)}"
            )
        owner = next(iter(owners)) if owners else None
        grouped.setdefault(owner, []).append(frozenset(cells))
    return {owner: tuple(components) for owner, components in grouped.items()}


def _route_orders(
    logical_nets: list[_LogicalNet], max_attempts: int
) -> Iterable[tuple[_LogicalNet, ...]]:
    if len(logical_nets) <= 1:
        yield tuple(logical_nets)
        return

    seen: set[tuple[int, ...]] = set()
    emitted = 0

    def emit(order: tuple[_LogicalNet, ...]) -> tuple[_LogicalNet, ...] | None:
        nonlocal emitted
        key = tuple(item.source_index for item in order)
        if key in seen or emitted >= max_attempts:
            return None
        seen.add(key)
        emitted += 1
        return order

    base = tuple(logical_nets)
    for reverse in (False, True):
        candidate = tuple(reversed(base)) if reverse else base
        for shift in range(len(candidate)):
            order = candidate[shift:] + candidate[:shift]
            result = emit(order)
            if result is not None:
                yield result

    if emitted >= max_attempts:
        return
    for order in islice(permutations(logical_nets), max_attempts):
        result = emit(order)
        if result is not None:
            yield result
        if emitted >= max_attempts:
            return


def _lee_path(
    start: tuple[int, int],
    targets: set[tuple[int, int]],
    allowed: frozenset[tuple[int, int]],
    blocked: set[tuple[int, int]],
    footprint_obstacles: frozenset[tuple[int, int]],
    bridge_edges: dict[tuple[int, int], tuple[int, int]],
) -> list[tuple[int, int]]:
    if start in targets:
        return [start]
    queue = deque([start])
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}

    while queue:
        current = queue.popleft()
        cx, cy = current
        for dx, dy, _, _ in CARDINAL_STEPS:
            neighbor = cx + dx, cy + dy
            if neighbor in parent or neighbor not in allowed:
                continue
            if (
                neighbor in footprint_obstacles
                or (neighbor in blocked and neighbor not in targets)
            ):
                continue
            parent[neighbor] = current
            if neighbor in targets:
                return _reconstruct_path(parent, neighbor)
            queue.append(neighbor)
        bridge_neighbor = bridge_edges.get(current)
        if (
            bridge_neighbor is not None
            and bridge_neighbor not in parent
            and bridge_neighbor in allowed
            and bridge_neighbor not in footprint_obstacles
            and (bridge_neighbor not in blocked or bridge_neighbor in targets)
        ):
            parent[bridge_neighbor] = current
            if bridge_neighbor in targets:
                return _reconstruct_path(parent, bridge_neighbor)
            queue.append(bridge_neighbor)

    raise RoutingError(f"no legal trace path from {start} to the existing net tree")


def _reconstruct_path(
    parent: dict[tuple[int, int], tuple[int, int] | None],
    end: tuple[int, int],
) -> list[tuple[int, int]]:
    path = [end]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])
    path.reverse()
    return path


def _connect_masks(
    masks: dict[tuple[int, int], int],
    a: tuple[int, int],
    b: tuple[int, int],
    bridge_edges: dict[tuple[int, int], tuple[int, int]],
) -> None:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    for step_x, step_y, forward, backward in CARDINAL_STEPS:
        if (dx, dy) == (step_x, step_y):
            masks[a] = masks.get(a, EXISTS) | EXISTS | forward
            masks[b] = masks.get(b, EXISTS) | EXISTS | backward
            return
    if bridge_edges.get(a) == b:
        masks[a] = masks.get(a, 0) | EXISTS
        masks[b] = masks.get(b, 0) | EXISTS
        return
    raise RoutingError(f"trace edge {a} -> {b} is not cardinal and adjacent")


def _bridge_edges(
    origins: Iterable[tuple[int, int]],
) -> dict[tuple[int, int], tuple[int, int]]:
    edges = {}
    for x, y in origins:
        edges[(x, y)] = (x, y + 2)
        edges[(x, y + 2)] = (x, y)
    return edges


def _pin_coordinate(pin: PinRef) -> tuple[int, int]:
    endpoint = endpoint_for_pin_ref(pin)
    if endpoint is None:
        raise RoutingError(f"pin {pin.owner.name}.{pin.name} has no physical contact")
    return endpoint.x, endpoint.y


def _pin_labels(pins: Iterable[PinRef]) -> str:
    return ", ".join(f"{pin.owner.name}.{pin.name}" for pin in pins)
