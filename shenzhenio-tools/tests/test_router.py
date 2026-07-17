from __future__ import annotations

import unittest

from shzio import Bridge, MC6000
from shzio.api import Solution
from shzio.boards import Board, Sz035
from shzio.model import BoardPort, Net, PinDirection, PinKind
from shzio.physical import analyze_physical_nets
from shzio.router import RoutingError, route_nets
from shzio.traces import LEFT, RIGHT, TraceGrid


class VirtualRealityBuzzer(Solution):
    board = Sz035

    def build(self) -> None:
        self.cpu = self.place(MC6000("cpu"), at=(11, 5))
        self.connect(self.board.radio.rx, self.cpu.x0, name="radio_to_cpu")
        self.connect(self.cpu.p1, self.board.buzzer.input, name="cpu_to_buzzer")


class RouterTests(unittest.TestCase):
    def test_route_hint_is_included_in_the_routed_net(self) -> None:
        class HintedBuzzer(Solution):
            board = Sz035

            def build(self) -> None:
                cpu = self.place(MC6000("cpu"), at=(11, 5))
                self.connect(
                    self.board.radio.rx,
                    cpu.x0,
                    via=[(6, 7)],
                )
                self.connect(cpu.p1, self.board.buzzer.input)

        solution = HintedBuzzer()

        self.assertIn((6, 7), solution.routing_result.nets[0].cells)

    def test_routes_sz035_without_using_unconnected_pin_contacts(self) -> None:
        solution = VirtualRealityBuzzer()
        grid = solution.board.traces
        assert grid is not None

        self.assertTrue(grid.nonempty_cells())
        self.assertTrue(
            set(grid.nonempty_cells()).issubset(solution.board.routable_cells)
        )
        for contact in ((11, 5), (11, 6), (13, 5), (13, 6)):
            self.assertEqual(0, grid.mask_at(*contact))

    def test_logical_nets_do_not_share_trace_cells(self) -> None:
        result = VirtualRealityBuzzer().routing_result
        assert result is not None

        self.assertEqual(2, len(result.nets))
        self.assertTrue(result.nets[0].cells.isdisjoint(result.nets[1].cells))

    def test_reports_when_an_unconnected_contact_blocks_the_only_path(self) -> None:
        ports = {
            name: BoardPort(
                name=name,
                pin_name="pin",
                kind=PinKind.SIMPLE,
                direction=PinDirection.BIDIRECTIONAL,
                x=x,
                y=2,
            )
            for name, x in (("left", 1), ("blocker", 2), ("right", 3))
        }
        board = Board(
            puzzle_id="test",
            width=5,
            height=5,
            placement_size=(3, 3),
            ports=ports,
            routable_cells=frozenset({(1, 2), (2, 2), (3, 2)}),
        )

        with self.assertRaisesRegex(RoutingError, "no legal trace path"):
            route_nets(
                board,
                [],
                [Net(ports["left"].pin(), ports["right"].pin())],
            )

    def test_reuses_and_preserves_initial_trace_components(self) -> None:
        ports = {
            name: BoardPort(
                name=name,
                pin_name="pin",
                kind=PinKind.SIMPLE,
                direction=PinDirection.BIDIRECTIONAL,
                x=x,
                y=2,
            )
            for name, x in (("left", 1), ("right", 5))
        }
        board = Board(
            puzzle_id="initial-trace",
            width=7,
            height=5,
            placement_size=(5, 3),
            ports=ports,
            routable_cells=frozenset((x, 2) for x in range(1, 6)),
            traces=TraceGrid.from_masks(
                7,
                5,
                {(1, 2): RIGHT, (2, 2): LEFT},
            ),
        )

        result = route_nets(
            board,
            [],
            [Net(ports["left"].pin(), ports["right"].pin())],
        )
        board.traces = result.traces

        self.assertEqual({(x, 2) for x in range(1, 6)}, set(result.traces.nonempty_cells()))
        endpoint_sets = {
            frozenset(endpoint.label for endpoint in net.endpoints)
            for net in analyze_physical_nets(board, [])
        }
        self.assertIn(frozenset({"left.pin", "right.pin"}), endpoint_sets)

    def test_bridge_routes_one_net_over_another_without_sharing_middle_cell(self) -> None:
        ports = {
            name: BoardPort(
                name=name,
                pin_name="pin",
                kind=PinKind.SIMPLE,
                direction=PinDirection.BIDIRECTIONAL,
                x=x,
                y=y,
            )
            for name, x, y in (
                ("left", 1, 2),
                ("right", 3, 2),
                ("bottom", 2, 1),
                ("top", 2, 3),
            )
        }
        board = Board(
            puzzle_id="bridge-crossing",
            width=5,
            height=5,
            placement_size=(3, 3),
            ports=ports,
            routable_cells=frozenset(
                {(1, 2), (2, 2), (3, 2), (2, 1), (2, 3)}
            ),
        )
        bridge = Bridge()
        bridge.x, bridge.y = 2, 1
        nets = [
            Net(ports["left"].pin(), ports["right"].pin()),
            Net(ports["bottom"].pin(), ports["top"].pin()),
        ]

        result = route_nets(board, [bridge], nets)
        board.traces = result.traces
        physical = {
            frozenset(endpoint.label for endpoint in net.endpoints)
            for net in analyze_physical_nets(board, [bridge])
            if net.endpoints
        }

        self.assertEqual(0, result.traces.mask_at(2, 1) & 0xF)
        self.assertEqual(0, result.traces.mask_at(2, 3) & 0xF)
        self.assertIn(frozenset({"left.pin", "right.pin"}), physical)
        self.assertIn(frozenset({"bottom.pin", "top.pin"}), physical)


if __name__ == "__main__":
    unittest.main()
