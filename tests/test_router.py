from __future__ import annotations

import unittest

from shzio import Bridge, MC6000
from shzio.api import Solution
from shzio.boards import Board, Sz035
from shzio.model import BoardPort, Net, Part, PartSpec, PinDirection, PinKind
from shzio.physical import analyze_physical_nets
from shzio.router import DeterministicRouter, RoutingError, route_nets
from shzio.traces import LEFT, RIGHT, TraceGrid


class VirtualRealityBuzzer(Solution):
    board = Sz035

    def build(self) -> None:
        self.cpu = self.place(MC6000("cpu"), at=(11, 5))
        self.connect(self.board.radio.rx, self.cpu.x0, name="radio_to_cpu")
        self.connect(self.cpu.p1, self.board.buzzer.input, name="cpu_to_buzzer")


def _congestion_board(
    include_bypass: bool,
) -> tuple[Board, dict[str, BoardPort]]:
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
            ("right", 5, 2),
            ("bottom", 3, 1),
            ("top", 3, 3),
        )
    }
    ys = range(5) if include_bypass else range(1, 4)
    return (
        Board(
            puzzle_id="negotiated-congestion",
            width=7,
            height=5,
            placement_size=(5, 3),
            ports=ports,
            routable_cells=frozenset((x, y) for x in range(1, 6) for y in ys),
        ),
        ports,
    )


def _all_orders_congestion_board() -> tuple[Board, dict[str, BoardPort]]:
    points = ((6, 6), (0, 3), (0, 0), (6, 1), (5, 6), (0, 4))
    ports = {
        f"p{index}": BoardPort(
            name=f"p{index}",
            pin_name="pin",
            kind=PinKind.SIMPLE,
            direction=PinDirection.BIDIRECTIONAL,
            x=x,
            y=y,
        )
        for index, (x, y) in enumerate(points)
    }
    cells = frozenset(
        {
            (0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6),
            (1, 0), (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6),
            (2, 0), (2, 2), (2, 3), (2, 4), (2, 5), (2, 6),
            (3, 1), (3, 4), (3, 5), (3, 6),
            (4, 1), (4, 2), (4, 3), (4, 4), (4, 5), (4, 6),
            (5, 0), (5, 1), (5, 3), (5, 5), (5, 6),
            (6, 1), (6, 5), (6, 6),
        }
    )
    return (
        Board(
            puzzle_id="all-orders-congestion",
            width=7,
            height=7,
            ports=ports,
            placement_origin=(0, 0),
            placement_size=(7, 7),
            routable_cells=cells,
        ),
        ports,
    )


class RouterTests(unittest.TestCase):
    def test_negotiation_succeeds_when_every_greedy_net_order_fails(self) -> None:
        board, ports = _all_orders_congestion_board()
        nets = [
            Net(ports["p0"].pin(), ports["p1"].pin()),
            Net(ports["p2"].pin(), ports["p3"].pin()),
            Net(ports["p4"].pin(), ports["p5"].pin()),
        ]

        result = DeterministicRouter(max_order_attempts=720).route(
            board, [], nets
        )

        self.assertEqual("negotiated", result.strategy)
        self.assertEqual(3, result.iterations)
        self.assertTrue(result.nets[0].cells.isdisjoint(result.nets[1].cells))
        self.assertTrue(result.nets[0].cells.isdisjoint(result.nets[2].cells))
        self.assertTrue(result.nets[1].cells.isdisjoint(result.nets[2].cells))
        board.traces = result.traces
        physical = {
            frozenset(endpoint.label for endpoint in net.endpoints)
            for net in analyze_physical_nets(board, [])
            if net.endpoints
        }
        self.assertEqual(
            {
                frozenset({"p0.pin", "p1.pin"}),
                frozenset({"p2.pin", "p3.pin"}),
                frozenset({"p4.pin", "p5.pin"}),
            },
            physical,
        )

    def test_negotiated_congestion_reroutes_an_earlier_net(self) -> None:
        board, ports = _congestion_board(include_bypass=True)
        nets = [
            Net(ports["left"].pin(), ports["right"].pin()),
            Net(ports["bottom"].pin(), ports["top"].pin()),
        ]
        router = DeterministicRouter(max_order_attempts=1)

        first = router.route(board, [], nets)
        second_board, second_ports = _congestion_board(True)
        second = router.route(
            second_board,
            [],
            [
                Net(second_ports["left"].pin(), second_ports["right"].pin()),
                Net(second_ports["bottom"].pin(), second_ports["top"].pin()),
            ],
        )

        self.assertEqual("negotiated", first.strategy)
        self.assertGreaterEqual(first.iterations, 2)
        self.assertTrue(first.nets[0].cells.isdisjoint(first.nets[1].cells))
        self.assertTrue(
            any(y in {0, 4} for _, y in first.nets[0].cells),
            "the flexible horizontal net should use the outer bypass",
        )
        self.assertEqual(first.traces.rows, second.traces.rows)
        self.assertEqual(first.iterations, second.iterations)

    def test_negotiated_congestion_reports_a_physically_impossible_crossing(self) -> None:
        board, ports = _congestion_board(include_bypass=False)
        nets = [
            Net(ports["left"].pin(), ports["right"].pin()),
            Net(ports["bottom"].pin(), ports["top"].pin()),
        ]

        with self.assertRaisesRegex(RoutingError, "congestion remained"):
            DeterministicRouter(
                max_order_attempts=1,
                max_negotiation_iterations=3,
            ).route(board, [], nets)

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

    def test_part_footprint_blocks_trace_routing(self) -> None:
        ports = {
            name: BoardPort(
                name=name,
                pin_name="pin",
                kind=PinKind.SIMPLE,
                direction=PinDirection.BIDIRECTIONAL,
                x=x,
                y=2,
            )
            for name, x in (("left", 1), ("right", 3))
        }
        board = Board(
            puzzle_id="footprint-obstacle",
            width=5,
            height=5,
            placement_size=(3, 3),
            ports=ports,
            routable_cells=frozenset({(1, 2), (2, 2), (3, 2)}),
        )
        blocker = Part(
            PartSpec("BLOCK", 1, 1, 0, None, (), {}),
            name="blocker",
            x=2,
            y=2,
        )

        with self.assertRaisesRegex(RoutingError, "no legal trace path"):
            route_nets(
                board,
                [blocker],
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
