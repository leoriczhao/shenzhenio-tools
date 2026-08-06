from __future__ import annotations

from dataclasses import dataclass

from .api import Solution
from .model import PinDirection, PinKind, PinRef
from .networks import (
    CircuitPinIO,
    NetworkError,
    SimpleNetwork,
    SimpleNode,
    XBusNetwork,
    XBusNode,
    XBusNodeState,
    XBusTransfer,
)
from .vm import MicrocontrollerVM, StepResult, StepStatus, VMError


class SimulationBuildError(ValueError):
    pass


class SimulationBudgetExceeded(VMError):
    pass


@dataclass(frozen=True)
class MachineStep:
    machine_name: str
    round_index: int
    result: StepResult


@dataclass(frozen=True)
class TickReport:
    tick: int
    rounds: int
    steps: tuple[MachineStep, ...]
    power_used: int
    blocked: tuple[str, ...]
    sleeping: tuple[str, ...]
    idle: tuple[str, ...]
    deadlocked: bool
    simple_levels: dict[str, int]
    xbus_transfers: tuple[XBusTransfer, ...]


class CircuitSimulator:
    def __init__(
        self,
        solution: Solution,
        *,
        max_rounds_per_tick: int = 10_000,
        max_steps_per_tick: int = 100_000,
    ) -> None:
        if max_rounds_per_tick <= 0:
            raise ValueError("max_rounds_per_tick must be positive")
        if max_steps_per_tick <= 0:
            raise ValueError("max_steps_per_tick must be positive")
        self.solution = solution
        self.max_rounds_per_tick = max_rounds_per_tick
        self.max_steps_per_tick = max_steps_per_tick
        self.time = 0

        self.simple_networks: list[SimpleNetwork] = []
        self.xbus_networks: list[XBusNetwork] = []
        self._endpoint_nodes: dict[tuple[int, str], SimpleNode] = {}
        self._endpoint_xbus_nodes: dict[tuple[int, str], XBusNode] = {}
        self._board_nodes: dict[str, SimpleNode] = {}
        self._external_xbus_nodes: dict[str, XBusNode] = {}
        self.pin_ios: dict[int, CircuitPinIO] = {}
        self.machines: list[MicrocontrollerVM] = []
        self._machines_by_name: dict[str, MicrocontrollerVM] = {}
        owners = [
            *solution.parts,
            *solution.board.fixed_parts,
            *solution.board.ports.values(),
        ]
        self._owner_rank = {id(owner): index for index, owner in enumerate(owners)}

        self._build_simple_networks()
        self._build_xbus_networks()
        self._build_board_nodes()
        self._build_external_xbus_nodes()
        self._build_machines()

    def machine(self, name: str) -> MicrocontrollerVM:
        try:
            return self._machines_by_name[name]
        except KeyError as exc:
            raise KeyError(f"no simulated microcontroller named {name!r}") from exc

    def drive_port(self, name: str, value: int) -> None:
        port = self.solution.board.port(name)
        if port.kind != PinKind.SIMPLE:
            raise SimulationBuildError(f"board port {name!r} is not simple I/O")
        if port.direction not in {PinDirection.OUTPUT, PinDirection.BIDIRECTIONAL}:
            raise SimulationBuildError(
                f"board port {name!r} is an input to the external device and cannot drive the circuit"
            )
        self._board_nodes[name].drive(value)

    def read_port(self, name: str) -> int:
        port = self.solution.board.port(name)
        if port.kind != PinKind.SIMPLE:
            raise SimulationBuildError(f"board port {name!r} is not simple I/O")
        if port.direction not in {PinDirection.INPUT, PinDirection.BIDIRECTIONAL}:
            raise SimulationBuildError(
                f"board port {name!r} is an output from the external device and cannot sample the circuit"
            )
        return self._board_nodes[name].sample()

    def drive_xbus_port(self, name: str, value: int) -> None:
        try:
            node = self._external_xbus_nodes[name]
        except KeyError as exc:
            raise SimulationBuildError(f"no external XBus port named {name!r}") from exc
        if node.state != XBusNodeState.PASSIVE_TRANSMITTING:
            raise SimulationBuildError(
                f"external XBus port {name!r} receives circuit output and cannot drive the circuit"
            )
        node.enqueue(value)

    def read_xbus_port(self, name: str) -> int | None:
        try:
            node = self._external_xbus_nodes[name]
        except KeyError as exc:
            raise SimulationBuildError(f"no external XBus port named {name!r}") from exc
        if node.state != XBusNodeState.PASSIVE_RECEIVING:
            raise SimulationBuildError(
                f"external XBus port {name!r} drives circuit input and cannot be sampled"
            )
        return node.dequeue()

    def drive_input(self, name: str, value: int) -> None:
        if name in self._external_xbus_nodes:
            self.drive_xbus_port(name, value)
            return
        self.drive_port(name, value)

    def tick(self) -> TickReport:
        power_before = sum(machine.power_used for machine in self.machines)
        events: list[MachineStep] = []
        last_status: dict[str, StepStatus] = {}
        rounds = 0
        step_count = 0
        xbus_transfers: list[XBusTransfer] = []

        while True:
            runnable = [
                machine
                for machine in self.machines
                if not machine.sleeping
                and last_status.get(machine.part.name or machine.part.type_name)
                != StepStatus.IDLE
            ]
            if not runnable:
                break
            if rounds >= self.max_rounds_per_tick:
                raise self._budget_error("round", self.max_rounds_per_tick)
            round_index = rounds + 1
            progressed = False
            xbus_version_before = sum(
                network.activity_version for network in self.xbus_networks
            )

            for machine in runnable:
                name = machine.part.name or machine.part.type_name
                result = machine.step()
                step_count += 1
                if step_count > self.max_steps_per_tick:
                    raise self._budget_error("step", self.max_steps_per_tick)
                events.append(MachineStep(name, round_index, result))
                last_status[name] = result.status
                if result.status in {
                    StepStatus.EXECUTED,
                    StepStatus.SKIPPED,
                    StepStatus.SLEEPING,
                }:
                    progressed = True

            for network in self.xbus_networks:
                transfer = network.propagate()
                if transfer is not None:
                    xbus_transfers.append(transfer)
            if sum(
                network.activity_version for network in self.xbus_networks
            ) != xbus_version_before:
                progressed = True

            rounds = round_index
            if not progressed:
                break

        blocked = tuple(
            name
            for name in self._machine_names()
            if last_status.get(name) == StepStatus.BLOCKED
        )
        sleeping = tuple(
            name
            for name in self._machine_names()
            if self._machines_by_name[name].sleeping
        )
        idle = tuple(
            name
            for name in self._machine_names()
            if last_status.get(name) == StepStatus.IDLE
        )
        active_names = set(self._machine_names()) - set(idle)
        deadlocked = bool(active_names) and set(blocked) == active_names and not sleeping
        levels = {network.name: network.level for network in self.simple_networks}

        for machine in self.machines:
            machine.advance_time(1)

        power_after = sum(machine.power_used for machine in self.machines)
        report = TickReport(
            tick=self.time,
            rounds=rounds,
            steps=tuple(events),
            power_used=power_after - power_before,
            blocked=blocked,
            sleeping=sleeping,
            idle=idle,
            deadlocked=deadlocked,
            simple_levels=levels,
            xbus_transfers=tuple(xbus_transfers),
        )
        self.time += 1
        return report

    def run(self, ticks: int) -> list[TickReport]:
        if ticks < 0:
            raise ValueError("ticks cannot be negative")
        return [self.tick() for _ in range(ticks)]

    def _build_simple_networks(self) -> None:
        for index, group in enumerate(self._logical_groups(PinKind.SIMPLE)):
            network = SimpleNetwork(f"simple-{index}")
            for ref in group:
                key = (id(ref.owner), ref.name)
                node = SimpleNode(
                    owner_key=id(ref.owner),
                    device_name=ref.owner.name,
                    pin_name=ref.name,
                )
                try:
                    network.add_node(node)
                except NetworkError as exc:
                    raise SimulationBuildError(str(exc)) from exc
                self._endpoint_nodes[key] = node
            self.simple_networks.append(network)

    def _build_xbus_networks(self) -> None:
        for index, group in enumerate(self._logical_groups(PinKind.XBUS)):
            network = XBusNetwork(f"xbus-{index}")
            for ref in group:
                key = (id(ref.owner), ref.name)
                node = XBusNode(
                    owner_key=id(ref.owner),
                    device_name=ref.owner.name,
                    pin_name=ref.name,
                )
                try:
                    network.add_node(node)
                except NetworkError as exc:
                    raise SimulationBuildError(str(exc)) from exc
                self._endpoint_xbus_nodes[key] = node
            self.xbus_networks.append(network)

    def _build_board_nodes(self) -> None:
        for name, port in self.solution.board.ports.items():
            if port.kind != PinKind.SIMPLE:
                continue
            key = (id(port), port.pin_name)
            node = self._endpoint_nodes.get(key) or SimpleNode(
                owner_key=id(port),
                device_name=port.name,
                pin_name=port.pin_name,
            )
            if port.direction == PinDirection.OUTPUT:
                node.drive(0)
            self._board_nodes[name] = node

    def _build_external_xbus_nodes(self) -> None:
        for name, port in self.solution.board.ports.items():
            if port.kind != PinKind.XBUS:
                continue
            key = (id(port), port.pin_name)
            node = self._endpoint_xbus_nodes.get(key) or XBusNode(
                owner_key=id(port),
                device_name=port.name,
                pin_name=port.pin_name,
            )
            self._configure_external_xbus_node(
                name,
                node,
                port.direction,
                port.nonblocking,
                port.empty_value,
            )

        spec = self.solution.board.spec
        if spec is None:
            return
        for terminal in spec.terminals:
            if terminal.electrical_kind.value != PinKind.XBUS.value:
                continue
            if terminal.binding is None:
                continue
            try:
                ref = self.solution.board.terminal_bindings[terminal.name]
            except KeyError as exc:
                raise SimulationBuildError(
                    f"board terminal {terminal.name!r} has no runtime binding"
                ) from exc
            key = (id(ref.owner), ref.name)
            node = self._endpoint_xbus_nodes.get(key) or XBusNode(
                owner_key=id(ref.owner),
                device_name=ref.owner.name,
                pin_name=ref.name,
            )
            direction = PinDirection(terminal.device_direction.value)
            self._configure_external_xbus_node(
                terminal.name,
                node,
                direction,
                terminal.nonblocking,
                None,
            )

    def _build_machines(self) -> None:
        for part in self.solution.parts:
            if "acc" not in part.spec.registers:
                continue
            name = part.name or part.type_name
            if name in self._machines_by_name:
                raise SimulationBuildError(
                    f"multiple programmable parts are named {name!r}"
                )
            simple_nodes = {
                pin_name: node
                for pin_name in part.spec.pins
                if (node := self._endpoint_nodes.get((id(part), pin_name))) is not None
            }
            xbus_nodes = {
                pin_name: node
                for pin_name in part.spec.pins
                if (
                    node := self._endpoint_xbus_nodes.get((id(part), pin_name))
                )
                is not None
            }
            pin_io = CircuitPinIO(part, simple_nodes, xbus_nodes)
            try:
                machine = MicrocontrollerVM(part, pin_io=pin_io)
            except (ValueError, VMError) as exc:
                raise SimulationBuildError(
                    f"cannot create VM for {name}: {exc}"
                ) from exc
            self.pin_ios[id(part)] = pin_io
            self.machines.append(machine)
            self._machines_by_name[name] = machine

    def _machine_names(self) -> tuple[str, ...]:
        return tuple(
            machine.part.name or machine.part.type_name for machine in self.machines
        )

    def _budget_error(self, kind: str, limit: int) -> SimulationBudgetExceeded:
        states = ", ".join(
            f"{machine.part.name}:pc={machine.pc}:"
            f"{machine.current_instruction.opcode if machine.current_instruction else 'idle'}"
            for machine in self.machines
        )
        return SimulationBudgetExceeded(
            f"tick {self.time} exceeded {kind} budget {limit}; {states}"
        )

    def _logical_groups(self, kind: PinKind) -> list[list[PinRef]]:
        parents: dict[tuple[int, str], tuple[int, str]] = {}
        refs: dict[tuple[int, str], PinRef] = {}

        def add(ref: PinRef) -> tuple[int, str]:
            key = (id(ref.owner), ref.name)
            parents.setdefault(key, key)
            refs.setdefault(key, ref)
            return key

        def find(key: tuple[int, str]) -> tuple[int, str]:
            root = key
            while parents[root] != root:
                root = parents[root]
            while parents[key] != key:
                parent = parents[key]
                parents[key] = root
                key = parent
            return root

        def union(left: tuple[int, str], right: tuple[int, str]) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        for net in self.solution.nets:
            if net.kind != kind:
                continue
            union(add(net.a), add(net.b))

        groups: dict[tuple[int, str], list[PinRef]] = {}
        for key, ref in refs.items():
            groups.setdefault(find(key), []).append(ref)
        normalized = [
            sorted(group, key=self._endpoint_sort_key) for group in groups.values()
        ]
        return sorted(
            normalized,
            key=lambda group: min(self._endpoint_sort_key(ref) for ref in group),
        )

    def _configure_external_xbus_node(
        self,
        name: str,
        node: XBusNode,
        direction: PinDirection,
        nonblocking: bool,
        empty_value: int | None,
    ) -> None:
        existing = next(
            (alias for alias, candidate in self._external_xbus_nodes.items() if candidate is node),
            None,
        )
        if existing is None:
            if direction == PinDirection.OUTPUT:
                fallback = empty_value
                if fallback is None and nonblocking:
                    fallback = -999
                node.configure_passive_transmitter(fallback)
            elif direction == PinDirection.INPUT:
                node.configure_passive_receiver()
            else:
                raise SimulationBuildError(
                    f"external XBus port {name!r} has unsupported bidirectional direction"
                )
        self._external_xbus_nodes[name] = node

    def _endpoint_sort_key(self, ref: PinRef) -> tuple[int, str, str]:
        return (
            self._owner_rank.get(id(ref.owner), len(self._owner_rank)),
            ref.owner.name,
            ref.name,
        )
