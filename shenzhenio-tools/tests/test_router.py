from __future__ import annotations

import unittest

from shzio import MC6000
from shzio.api import Solution
from shzio.boards import Board, Sz035
from shzio.model import BoardPort, Net, PinDirection, PinKind
from shzio.router import RoutingError, route_nets


class VirtualRealityBuzzer(Solution):
    board = Sz035

    def build(self) -> None:
        self.cpu = self.place(MC6000("cpu"), at=(11, 5))
        self.connect(self.board.radio.rx, self.cpu.x0, name="radio_to_cpu")
        self.connect(self.cpu.p1, self.board.buzzer.input, name="cpu_to_buzzer")


class RouterTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
