from __future__ import annotations

import unittest

from shzio import CircuitSimulator, MC4000, Solution
from shzio.boards import Board
from shzio.checker import check_solution
from shzio.model import BoardPort, PinDirection, PinKind
from shzio.networks import (
    CircuitPinIO,
    NetworkError,
    SimpleNetwork,
    SimpleNode,
    SimplePinMode,
    XBusNetwork,
    XBusNode,
    XBusNodeState,
)
from shzio.simulator import SimulationBudgetExceeded, SimulationBuildError
from shzio.vm import StepStatus


def blank_board(*ports: BoardPort) -> Board:
    return Board(
        puzzle_id="SimTest",
        width=12,
        height=8,
        placement_origin=(1, 1),
        placement_size=(10, 6),
        ports={port.name: port for port in ports},
    )


class TwoChipSignal(Solution):
    board = blank_board()
    auto_route = False

    def build(self) -> None:
        self.producer = self.place(MC4000("producer"), at=(1, 1))
        self.consumer = self.place(MC4000("consumer"), at=(5, 1))
        self.connect(self.producer.p0, self.consumer.p0)

        producer_program = self.producer.program()
        producer_program.mov(100, self.producer.p0)
        producer_program.slp(1)
        producer_program.mov(0, self.producer.p0)
        producer_program.slp(1)

        consumer_program = self.consumer.program()
        consumer_program.nop()
        consumer_program.mov(self.consumer.p0, self.consumer.acc)
        consumer_program.slp(1)


def port_solution() -> Solution:
    sensor = BoardPort(
        name="sensor",
        pin_name="output",
        kind=PinKind.SIMPLE,
        direction=PinDirection.OUTPUT,
    )
    actuator = BoardPort(
        name="actuator",
        pin_name="input",
        kind=PinKind.SIMPLE,
        direction=PinDirection.INPUT,
    )

    class PortLoopback(Solution):
        board = blank_board(sensor, actuator)
        auto_route = False

        def build(self) -> None:
            cpu = self.place(MC4000("cpu"), at=(3, 2))
            self.connect(self.board.sensor.output, cpu.p0)
            self.connect(cpu.p1, self.board.actuator.input)
            program = cpu.program()
            program.mov(cpu.p0, cpu.acc)
            program.mov(cpu.acc, cpu.p1)
            program.slp(1)

    return PortLoopback()


class SimpleNetworkTests(unittest.TestCase):
    def test_network_uses_highest_other_output_and_clamps_levels(self) -> None:
        network = SimpleNetwork("signals")
        first = SimpleNode(1, "first", "p0")
        second = SimpleNode(2, "second", "p0")
        third = SimpleNode(3, "third", "p0")
        for node in (first, second, third):
            network.add_node(node)

        first.drive(-20)
        second.drive(40)
        third.drive(150)

        self.assertEqual(100, network.level)
        self.assertEqual(100, first.sample())
        self.assertEqual(100, second.sample())
        self.assertEqual(40, third.sample())

    def test_reading_simple_pin_clears_its_previous_output_mode(self) -> None:
        network = SimpleNetwork("mode")
        reader = SimpleNode(1, "reader", "p0")
        source = SimpleNode(2, "source", "output")
        network.add_node(reader)
        network.add_node(source)
        reader.drive(100)
        source.drive(35)

        self.assertEqual(35, reader.read())
        self.assertEqual(SimplePinMode.INPUT, reader.mode)
        self.assertEqual(0, reader.transmitted_value)
        self.assertEqual(35, network.level)

    def test_network_rejects_duplicate_and_same_device_nodes(self) -> None:
        network = SimpleNetwork("invalid")
        first = SimpleNode(1, "cpu", "p0")
        network.add_node(first)
        with self.assertRaisesRegex(NetworkError, "already in network"):
            network.add_node(first)
        with self.assertRaisesRegex(NetworkError, "connected to itself"):
            network.add_node(SimpleNode(1, "cpu", "p1"))

    def test_circuit_pin_io_keeps_unconnected_xbus_blocked_and_simple_immediate(self) -> None:
        cpu = MC4000("cpu")
        pin_io = CircuitPinIO(cpu)

        self.assertTrue(pin_io.try_write("p0", 999))
        self.assertEqual(100, pin_io.node("p0").transmitted_value)
        self.assertEqual(0, pin_io.try_read("p0").value)
        self.assertEqual(SimplePinMode.INPUT, pin_io.node("p0").mode)
        self.assertFalse(pin_io.try_read("x0").ready)
        self.assertFalse(pin_io.try_write("x1", 1))


class XBusNetworkTests(unittest.TestCase):
    def test_active_writer_and_reader_complete_one_rendezvous(self) -> None:
        network = XBusNetwork("bus")
        writer = XBusNode(1, "writer", "x0")
        reader = XBusNode(2, "reader", "x0")
        network.add_node(writer)
        network.add_node(reader)

        self.assertFalse(writer.try_write(123))
        self.assertFalse(reader.try_read().ready)
        transfer = network.propagate()

        self.assertIsNotNone(transfer)
        self.assertEqual(123, transfer.value)
        self.assertTrue(writer.try_write(123))
        received = reader.try_read()
        self.assertTrue(received.ready)
        self.assertEqual(123, received.value)
        self.assertEqual(XBusNodeState.IDLE, writer.state)
        self.assertEqual(XBusNodeState.IDLE, reader.state)

    def test_all_passive_nodes_do_not_transfer_without_active_operation(self) -> None:
        network = XBusNetwork("passive")
        source = XBusNode(1, "source", "output")
        sink = XBusNode(2, "sink", "input")
        network.add_node(source)
        network.add_node(sink)
        source.configure_passive_transmitter()
        sink.configure_passive_receiver()
        source.enqueue(77)

        self.assertIsNone(network.propagate())
        self.assertIsNone(sink.dequeue())

    def test_active_reader_can_receive_from_passive_transmitter(self) -> None:
        network = XBusNetwork("input")
        source = XBusNode(1, "source", "output")
        reader = XBusNode(2, "cpu", "x0")
        network.add_node(source)
        network.add_node(reader)
        source.configure_passive_transmitter(empty_value=-999)

        self.assertFalse(reader.try_read().ready)
        empty_transfer = network.propagate()
        self.assertEqual(-999, empty_transfer.value)
        self.assertEqual(-999, reader.try_read().value)

        source.enqueue(41)
        self.assertFalse(reader.try_read().ready)
        queued_transfer = network.propagate()
        self.assertEqual(41, queued_transfer.value)
        self.assertEqual(41, reader.try_read().value)

    def test_active_writer_can_send_to_passive_receiver(self) -> None:
        network = XBusNetwork("output")
        writer = XBusNode(1, "cpu", "x0")
        sink = XBusNode(2, "sink", "input")
        network.add_node(writer)
        network.add_node(sink)
        sink.configure_passive_receiver()

        self.assertFalse(writer.try_write(9999))
        transfer = network.propagate()

        self.assertEqual(999, transfer.value)
        self.assertTrue(writer.try_write(999))
        self.assertEqual(999, sink.dequeue())

    def test_multiple_active_writers_use_stable_node_order(self) -> None:
        network = XBusNetwork("arbitration")
        first = XBusNode(1, "first", "x0")
        second = XBusNode(2, "second", "x0")
        reader = XBusNode(3, "reader", "x0")
        for node in (first, second, reader):
            network.add_node(node)
        first.try_write(10)
        second.try_write(20)
        reader.try_read()

        transfer = network.propagate()

        self.assertEqual("first.x0", transfer.transmitter)
        self.assertEqual(10, transfer.value)
        self.assertEqual(10, reader.try_read().value)
        self.assertTrue(first.try_write(10))
        self.assertFalse(second.try_write(20))


class CircuitSimulatorTests(unittest.TestCase):
    def test_two_microcontrollers_exchange_continuous_simple_levels(self) -> None:
        solution = TwoChipSignal()
        simulator = CircuitSimulator(solution)

        high_tick = simulator.tick()
        self.assertEqual(100, simulator.machine("consumer").read_register("acc"))
        self.assertEqual(5, high_tick.power_used)
        self.assertEqual(3, high_tick.rounds)
        self.assertEqual({"simple-0": 100}, high_tick.simple_levels)
        self.assertEqual(("producer", "consumer"), high_tick.sleeping)

        low_tick = simulator.tick()
        self.assertEqual(0, simulator.machine("consumer").read_register("acc"))
        self.assertEqual(5, low_tick.power_used)
        self.assertEqual({"simple-0": 0}, low_tick.simple_levels)
        self.assertEqual(2, simulator.time)

    def test_board_ports_drive_and_sample_solution_networks(self) -> None:
        simulator = CircuitSimulator(port_solution())
        simulator.drive_port("sensor", 73)

        report = simulator.tick()

        self.assertEqual(73, simulator.machine("cpu").read_register("acc"))
        self.assertEqual(73, simulator.read_port("actuator"))
        self.assertEqual(3, report.power_used)
        self.assertEqual({"simple-0": 73, "simple-1": 73}, report.simple_levels)

    def test_board_port_direction_is_enforced(self) -> None:
        simulator = CircuitSimulator(port_solution())
        with self.assertRaisesRegex(SimulationBuildError, "cannot drive"):
            simulator.drive_port("actuator", 100)
        with self.assertRaisesRegex(SimulationBuildError, "cannot sample"):
            simulator.read_port("sensor")

    def test_self_connected_part_is_rejected_during_network_build(self) -> None:
        class SelfConnected(Solution):
            board = blank_board()
            auto_route = False

            def build(self) -> None:
                cpu = self.place(MC4000("cpu"), at=(2, 2))
                self.connect(cpu.p0, cpu.p1)

        solution = SelfConnected()
        diagnostics = "\n".join(str(item) for item in check_solution(solution))
        self.assertIn("connects cpu to itself through p0 and p1", diagnostics)
        with self.assertRaisesRegex(SimulationBuildError, "connected to itself"):
            CircuitSimulator(solution)

    def test_gen_report_captures_level_before_time_advance(self) -> None:
        actuator = BoardPort(
            name="actuator",
            pin_name="input",
            kind=PinKind.SIMPLE,
            direction=PinDirection.INPUT,
        )

        class PulseGenerator(Solution):
            board = blank_board(actuator)
            auto_route = False

            def build(self) -> None:
                cpu = self.place(MC4000("cpu"), at=(2, 2))
                self.connect(cpu.p1, self.board.actuator.input)
                cpu.program().gen(cpu.p1, 1, 1)

        simulator = CircuitSimulator(PulseGenerator())

        high = simulator.tick()
        self.assertEqual({"simple-0": 100}, high.simple_levels)
        self.assertEqual(1, high.power_used)
        self.assertEqual(0, simulator.read_port("actuator"))

        low = simulator.tick()
        self.assertEqual({"simple-0": 0}, low.simple_levels)
        self.assertEqual(0, low.power_used)

    def test_unconnected_xbus_is_reported_as_deadlock(self) -> None:
        class BlockedXBus(Solution):
            board = blank_board()
            auto_route = False

            def build(self) -> None:
                cpu = self.place(MC4000("cpu"), at=(2, 2))
                cpu.program().mov(1, cpu.x0)

        simulator = CircuitSimulator(BlockedXBus())
        report = simulator.tick()

        self.assertTrue(report.deadlocked)
        self.assertEqual(("cpu",), report.blocked)
        self.assertEqual(0, report.power_used)
        self.assertEqual(StepStatus.BLOCKED, report.steps[-1].result.status)

    def test_two_microcontrollers_complete_xbus_rendezvous(self) -> None:
        class XBusPair(Solution):
            board = blank_board()
            auto_route = False

            def build(self) -> None:
                sender = self.place(MC4000("sender"), at=(1, 1))
                receiver = self.place(MC4000("receiver"), at=(5, 1))
                self.connect(sender.x0, receiver.x0)
                sender_program = sender.program()
                sender_program.mov(42, sender.x0)
                sender_program.slp(1)
                receiver_program = receiver.program()
                receiver_program.mov(receiver.x0, receiver.acc)
                receiver_program.slp(1)

        simulator = CircuitSimulator(XBusPair())
        report = simulator.tick()

        self.assertFalse(report.deadlocked)
        self.assertEqual(("sender", "receiver"), report.sleeping)
        self.assertEqual(4, report.power_used)
        self.assertEqual(42, simulator.machine("receiver").read_register("acc"))
        self.assertEqual(1, len(report.xbus_transfers))
        self.assertEqual(42, report.xbus_transfers[0].value)

    def test_slx_observes_waiting_writer_without_consuming_packet(self) -> None:
        class SleepUntilData(Solution):
            board = blank_board()
            auto_route = False

            def build(self) -> None:
                sender = self.place(MC4000("sender"), at=(1, 1))
                receiver = self.place(MC4000("receiver"), at=(5, 1))
                self.connect(sender.x0, receiver.x0)
                sender_program = sender.program()
                sender_program.nop()
                sender_program.mov(88, sender.x0)
                sender_program.slp(1)
                receiver_program = receiver.program()
                receiver_program.slx(receiver.x0)
                receiver_program.mov(receiver.x0, receiver.acc)
                receiver_program.slp(1)

        simulator = CircuitSimulator(SleepUntilData())
        report = simulator.tick()

        self.assertEqual(88, simulator.machine("receiver").read_register("acc"))
        self.assertEqual(1, len(report.xbus_transfers))
        self.assertEqual(88, report.xbus_transfers[0].value)
        self.assertEqual(6, report.power_used)

    def test_external_xbus_sink_passively_receives_cpu_packet(self) -> None:
        telemetry = BoardPort(
            name="telemetry",
            pin_name="input",
            kind=PinKind.XBUS,
            direction=PinDirection.INPUT,
        )

        class XBusOutput(Solution):
            board = blank_board(telemetry)
            auto_route = False

            def build(self) -> None:
                cpu = self.place(MC4000("cpu"), at=(2, 2))
                self.connect(cpu.x0, self.board.telemetry.input)
                program = cpu.program()
                program.mov(55, cpu.x0)
                program.slp(1)

        simulator = CircuitSimulator(XBusOutput())
        with self.assertRaisesRegex(SimulationBuildError, "cannot drive"):
            simulator.drive_xbus_port("telemetry", 9)
        report = simulator.tick()

        self.assertEqual(55, simulator.read_xbus_port("telemetry"))
        self.assertEqual(2, report.power_used)
        self.assertEqual("telemetry.input", report.xbus_transfers[0].receiver)

    def test_zero_time_infinite_loop_hits_diagnostic_budget(self) -> None:
        class BusyLoop(Solution):
            board = blank_board()
            auto_route = False

            def build(self) -> None:
                cpu = self.place(MC4000("cpu"), at=(2, 2))
                cpu.program().nop()

        simulator = CircuitSimulator(BusyLoop(), max_rounds_per_tick=3)
        with self.assertRaisesRegex(
            SimulationBudgetExceeded, "tick 0 exceeded round budget 3; cpu:pc=0:nop"
        ):
            simulator.tick()

    def test_sleep_on_last_allowed_round_does_not_exceed_budget(self) -> None:
        class ThreeInstructions(Solution):
            board = blank_board()
            auto_route = False

            def build(self) -> None:
                cpu = self.place(MC4000("cpu"), at=(2, 2))
                program = cpu.program()
                program.nop()
                program.nop()
                program.slp(1)

        report = CircuitSimulator(
            ThreeInstructions(), max_rounds_per_tick=3
        ).tick()

        self.assertEqual(3, report.rounds)
        self.assertEqual(("cpu",), report.sleeping)

    def test_empty_program_is_idle_not_deadlocked(self) -> None:
        class EmptyProgram(Solution):
            board = blank_board()
            auto_route = False

            def build(self) -> None:
                self.place(MC4000("cpu"), at=(2, 2))

        report = CircuitSimulator(EmptyProgram()).tick()

        self.assertFalse(report.deadlocked)
        self.assertEqual(("cpu",), report.idle)
        self.assertEqual(0, report.power_used)


if __name__ == "__main__":
    unittest.main()
