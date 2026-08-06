from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from .model import Part, PinKind
from .vm import PinIO, PinRead, VMError


class NetworkError(ValueError):
    pass


class SimplePinMode(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


class XBusNodeState(str, Enum):
    IDLE = "idle"
    TRANSMITTING = "transmitting"
    RECEIVING = "receiving"
    PASSIVE_TRANSMITTING = "passive-transmitting"
    PASSIVE_RECEIVING = "passive-receiving"


@dataclass(eq=False)
class SimpleNode:
    owner_key: int
    device_name: str
    pin_name: str
    mode: SimplePinMode = SimplePinMode.INPUT
    output_value: int = 0
    network: "SimpleNetwork | None" = None

    @property
    def label(self) -> str:
        return f"{self.device_name}.{self.pin_name}"

    @property
    def transmitted_value(self) -> int:
        return self.output_value if self.mode == SimplePinMode.OUTPUT else 0

    def drive(self, value: int) -> None:
        self.mode = SimplePinMode.OUTPUT
        self.output_value = _clamp_simple(value)

    def read(self) -> int:
        self.mode = SimplePinMode.INPUT
        self.output_value = 0
        return self.sample()

    def sample(self) -> int:
        if self.network is None:
            return 0
        return self.network.value_for(self)


class SimpleNetwork:
    """Continuous 0..100 network with provisional max-driver composition."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.nodes: list[SimpleNode] = []

    @property
    def level(self) -> int:
        return max((node.transmitted_value for node in self.nodes), default=0)

    def add_node(self, node: SimpleNode) -> None:
        if node in self.nodes or node.network is self:
            raise NetworkError(f"node {node.label} is already in network {self.name}")
        if node.network is not None:
            raise NetworkError(
                f"node {node.label} is already in network {node.network.name}"
            )
        same_owner = next(
            (existing for existing in self.nodes if existing.owner_key == node.owner_key),
            None,
        )
        if same_owner is not None:
            raise NetworkError(
                f"device {node.device_name} is connected to itself through "
                f"{same_owner.pin_name} and {node.pin_name}"
            )
        self.nodes.append(node)
        node.network = self

    def value_for(self, receiving_node: SimpleNode) -> int:
        if receiving_node not in self.nodes:
            raise NetworkError(
                f"node {receiving_node.label} is not in network {self.name}"
            )
        return max(
            (
                node.transmitted_value
                for node in self.nodes
                if node is not receiving_node
            ),
            default=0,
        )


@dataclass(frozen=True)
class XBusTransfer:
    network_name: str
    transmitter: str
    receiver: str
    value: int


@dataclass(eq=False)
class XBusNode:
    owner_key: int
    device_name: str
    pin_name: str
    state: XBusNodeState = XBusNodeState.IDLE
    network: "XBusNetwork | None" = None

    def __post_init__(self) -> None:
        self._pending_value: int | None = None
        self._write_completed = False
        self._received_value: int | None = None
        self._outbound: deque[int] = deque()
        self._inbound: deque[int] = deque()
        self._empty_value: int | None = None

    @property
    def label(self) -> str:
        return f"{self.device_name}.{self.pin_name}"

    @property
    def activity_version(self) -> int:
        return self.network.activity_version if self.network is not None else 0

    def configure_passive_transmitter(self, empty_value: int | None = None) -> None:
        self._require_state(XBusNodeState.IDLE)
        self.state = XBusNodeState.PASSIVE_TRANSMITTING
        self._empty_value = (
            _clamp_xbus(empty_value) if empty_value is not None else None
        )
        self._touch()

    def configure_passive_receiver(self) -> None:
        self._require_state(XBusNodeState.IDLE)
        self.state = XBusNodeState.PASSIVE_RECEIVING
        self._touch()

    def enqueue(self, value: int) -> None:
        if self.state != XBusNodeState.PASSIVE_TRANSMITTING:
            raise NetworkError(f"node {self.label} is not a passive transmitter")
        self._outbound.append(_clamp_xbus(value))
        self._touch()

    def dequeue(self) -> int | None:
        if self.state != XBusNodeState.PASSIVE_RECEIVING:
            raise NetworkError(f"node {self.label} is not a passive receiver")
        return self._inbound.popleft() if self._inbound else None

    def can_read(self) -> bool:
        return self.network is not None and self.network.has_transmitter_for(self)

    def try_read(self) -> PinRead:
        if self.state == XBusNodeState.IDLE:
            self.state = XBusNodeState.RECEIVING
            self._touch()
            return PinRead(False)
        if self.state != XBusNodeState.RECEIVING:
            raise NetworkError(
                f"node {self.label} cannot start an active read while {self.state.value}"
            )
        if self._received_value is None:
            return PinRead(False)
        value = self._received_value
        self._received_value = None
        self.state = XBusNodeState.IDLE
        self._touch()
        return PinRead(True, value)

    def try_write(self, value: int) -> bool:
        value = _clamp_xbus(value)
        if self.state == XBusNodeState.IDLE:
            self.state = XBusNodeState.TRANSMITTING
            self._pending_value = value
            self._write_completed = False
            self._touch()
            return False
        if self.state != XBusNodeState.TRANSMITTING:
            raise NetworkError(
                f"node {self.label} cannot start an active write while {self.state.value}"
            )
        if self._pending_value != value:
            raise NetworkError(
                f"node {self.label} changed pending XBus value from "
                f"{self._pending_value} to {value}"
            )
        if not self._write_completed:
            return False
        self._pending_value = None
        self._write_completed = False
        self.state = XBusNodeState.IDLE
        self._touch()
        return True

    def peek_transmitted_value(self) -> int | None:
        if self.state == XBusNodeState.TRANSMITTING:
            return self._pending_value
        if self.state == XBusNodeState.PASSIVE_TRANSMITTING:
            if self._outbound:
                return self._outbound[0]
            return self._empty_value
        return None

    def accept_value(self, value: int) -> bool:
        value = _clamp_xbus(value)
        if self.state == XBusNodeState.RECEIVING:
            if self._received_value is not None:
                return False
            self._received_value = value
            self._touch()
            return True
        if self.state == XBusNodeState.PASSIVE_RECEIVING:
            self._inbound.append(value)
            self._touch()
            return True
        return False

    def mark_transmitted(self) -> None:
        if self.state == XBusNodeState.TRANSMITTING:
            self._write_completed = True
            self._touch()
            return
        if self.state == XBusNodeState.PASSIVE_TRANSMITTING:
            if self._outbound:
                self._outbound.popleft()
            self._touch()
            return
        raise NetworkError(f"node {self.label} has no XBus value to mark transmitted")

    def _require_state(self, expected: XBusNodeState) -> None:
        if self.state != expected:
            raise NetworkError(
                f"node {self.label} is {self.state.value}, expected {expected.value}"
            )

    def _touch(self) -> None:
        if self.network is not None:
            self.network.touch()


class XBusNetwork:
    """Synchronous one-packet rendezvous network with deterministic arbitration."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.nodes: list[XBusNode] = []
        self.activity_version = 0

    def touch(self) -> None:
        self.activity_version += 1

    def add_node(self, node: XBusNode) -> None:
        if node in self.nodes or node.network is self:
            raise NetworkError(f"node {node.label} is already in network {self.name}")
        if node.network is not None:
            raise NetworkError(
                f"node {node.label} is already in network {node.network.name}"
            )
        same_owner = next(
            (existing for existing in self.nodes if existing.owner_key == node.owner_key),
            None,
        )
        if same_owner is not None:
            raise NetworkError(
                f"device {node.device_name} is connected to itself through "
                f"{same_owner.pin_name} and {node.pin_name}"
            )
        self.nodes.append(node)
        node.network = self
        self.touch()

    def has_transmitter_for(self, receiving_node: XBusNode) -> bool:
        return any(
            node is not receiving_node and node.peek_transmitted_value() is not None
            for node in self.nodes
        )

    def propagate(self) -> XBusTransfer | None:
        transmitters = [
            node for node in self.nodes if node.peek_transmitted_value() is not None
        ]
        receivers = [
            node
            for node in self.nodes
            if node.state
            in {XBusNodeState.RECEIVING, XBusNodeState.PASSIVE_RECEIVING}
        ]
        if not transmitters or not receivers:
            return None

        active_transmitter = next(
            (
                node
                for node in transmitters
                if node.state == XBusNodeState.TRANSMITTING
            ),
            None,
        )
        if active_transmitter is not None:
            transmitter = active_transmitter
            receiver = receivers[0]
        else:
            active_receiver = next(
                (
                    node
                    for node in receivers
                    if node.state == XBusNodeState.RECEIVING
                ),
                None,
            )
            if active_receiver is None:
                return None
            transmitter = transmitters[0]
            receiver = active_receiver

        if transmitter is receiver:
            return None
        value = transmitter.peek_transmitted_value()
        assert value is not None
        if not receiver.accept_value(value):
            return None
        transmitter.mark_transmitted()
        return XBusTransfer(self.name, transmitter.label, receiver.label, value)


class CircuitPinIO(PinIO):
    """MCxxxx pin adapter backed by circuit-level Simple and XBus nodes."""

    def __init__(
        self,
        part: Part,
        simple_nodes: dict[str, SimpleNode] | None = None,
        xbus_nodes: dict[str, XBusNode] | None = None,
    ) -> None:
        self.part = part
        provided_simple = simple_nodes or {}
        provided_xbus = xbus_nodes or {}
        self.simple_nodes: dict[str, SimpleNode] = {}
        self.xbus_nodes: dict[str, XBusNode] = {}
        for name, spec in part.spec.pins.items():
            if spec.kind == PinKind.SIMPLE:
                self.simple_nodes[name] = provided_simple.get(name) or SimpleNode(
                    owner_key=id(part),
                    device_name=part.name or part.type_name,
                    pin_name=name,
                )
            elif spec.kind == PinKind.XBUS:
                self.xbus_nodes[name] = provided_xbus.get(name) or XBusNode(
                    owner_key=id(part),
                    device_name=part.name or part.type_name,
                    pin_name=name,
                )

    def can_read(self, pin_name: str) -> bool:
        kind = self._kind(pin_name)
        if kind == PinKind.SIMPLE:
            return True
        if kind == PinKind.XBUS:
            return self.xbus_nodes[pin_name].can_read()
        return False

    def try_read(self, pin_name: str) -> PinRead:
        kind = self._kind(pin_name)
        if kind == PinKind.SIMPLE:
            return PinRead(True, self.simple_nodes[pin_name].read())
        if kind == PinKind.XBUS:
            return self.xbus_nodes[pin_name].try_read()
        raise VMError(f"display pin {pin_name!r} cannot be read by an MCxxxx VM")

    def try_write(self, pin_name: str, value: int) -> bool:
        kind = self._kind(pin_name)
        if kind == PinKind.SIMPLE:
            self.simple_nodes[pin_name].drive(value)
            return True
        if kind == PinKind.XBUS:
            return self.xbus_nodes[pin_name].try_write(value)
        raise VMError(f"display pin {pin_name!r} cannot be written by an MCxxxx VM")

    def node(self, pin_name: str) -> SimpleNode:
        try:
            return self.simple_nodes[pin_name]
        except KeyError as exc:
            raise VMError(f"pin {pin_name!r} is not a simple pin") from exc

    def xbus_node(self, pin_name: str) -> XBusNode:
        try:
            return self.xbus_nodes[pin_name]
        except KeyError as exc:
            raise VMError(f"pin {pin_name!r} is not an XBus pin") from exc

    def _kind(self, pin_name: str) -> PinKind:
        try:
            return self.part.spec.pins[pin_name].kind
        except KeyError as exc:
            raise VMError(f"unknown pin {pin_name!r}") from exc


def _clamp_simple(value: int) -> int:
    return min(max(value, 0), 100)


def _clamp_xbus(value: int) -> int:
    return min(max(value, -999), 999)
