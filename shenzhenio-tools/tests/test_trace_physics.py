from __future__ import annotations

import unittest

from shzio import MC6000
from shzio.boards import Sz035
from shzio.checker import check_solution
from shzio.physical import analyze_physical_nets, endpoint_for_pin
from shzio.traces import DOWN, EXISTS, LEFT, RIGHT, UP, TraceGrid, decode_char, encode_mask
from shzio.api import Solution


class RoutedBuzzer(Solution):
    board = Sz035

    def build(self) -> None:
        cpu = self.place(MC6000("cpu"), at=(11, 5))
        self.connect(self.board.radio.rx, cpu.x0)
        self.connect(cpu.p1, self.board.buzzer.input)


def endpoint_sets(solution: Solution) -> list[frozenset[str]]:
    nets = analyze_physical_nets(solution.board, solution.parts)
    return [
        frozenset(endpoint.label for endpoint in net.endpoints)
        for net in nets
        if net.endpoints
    ]


class TracePhysicsTests(unittest.TestCase):
    def test_trace_character_bitmask_mapping(self) -> None:
        self.assertEqual(decode_char("."), 0)
        self.assertEqual(decode_char("0"), EXISTS)
        self.assertEqual(decode_char("1"), EXISTS | RIGHT)
        self.assertEqual(decode_char("2"), EXISTS | UP)
        self.assertEqual(decode_char("4"), EXISTS | LEFT)
        self.assertEqual(decode_char("8"), EXISTS | DOWN)
        self.assertEqual(encode_mask(RIGHT | UP | LEFT | DOWN), "F")
        self.assertEqual(encode_mask(EXISTS), "0")

    def test_trace_rows_are_serialized_highest_y_first(self) -> None:
        grid = TraceGrid(["1.", ".2"])

        self.assertEqual(EXISTS | RIGHT, grid.mask_at(0, 1))
        self.assertEqual(EXISTS | UP, grid.mask_at(1, 0))
        self.assertEqual("1", grid.char_at(0, 1))

    def test_sz035_good_placement_hits_x0_and_p1(self) -> None:
        solution = RoutedBuzzer()
        labels = endpoint_sets(solution)
        self.assertIn(frozenset({"radio.rx", "cpu.x0"}), labels)
        self.assertIn(frozenset({"cpu.p1", "buzzer.input"}), labels)
        self.assertEqual([], check_solution(solution))

    def test_rotated_chip_uses_rotated_pin_contacts(self) -> None:
        cpu = MC6000("cpu")
        cpu.x = 4
        cpu.y = 5
        cpu.rotated = True

        x0 = endpoint_for_pin(cpu, "x0")
        p1 = endpoint_for_pin(cpu, "p1")

        self.assertEqual((6, 5), (x0.x, x0.y))
        self.assertEqual((4, 5), (p1.x, p1.y))

    def test_checker_rejects_api_net_that_does_not_match_physical_trace(self) -> None:
        class BadPlacement(Solution):
            board = Sz035
            auto_route = False

            def build(self) -> None:
                cpu = self.place(MC6000("cpu"), at=(11, 4))
                self.connect(self.board.radio.rx, cpu.x0)
                self.connect(cpu.p1, self.board.buzzer.input)

        diagnostics = check_solution(BadPlacement())
        messages = "\n".join(str(diagnostic) for diagnostic in diagnostics)
        self.assertIn("radio.rx has no physical trace connection", messages)
        self.assertIn("cpu.p1 has no physical trace connection", messages)

    def test_checker_enforces_game_placement_margin(self) -> None:
        class OutsidePlacement(Solution):
            board = Sz035
            auto_route = False

            def build(self) -> None:
                self.place(MC6000("cpu"), at=(0, 1))

        messages = "\n".join(str(item) for item in check_solution(OutsidePlacement()))
        self.assertIn("outside placement range (1, 1)..(21, 13)", messages)

    def test_checker_rejects_nonreciprocal_trace_direction(self) -> None:
        class BareSolution(Solution):
            board = Sz035
            auto_route = False

            def build(self) -> None:
                pass

        solution = BareSolution()
        solution.board.traces = TraceGrid.from_masks(
            22,
            14,
            {(6, 3): EXISTS | RIGHT},
        )

        messages = "\n".join(str(item) for item in check_solution(solution))
        self.assertIn("has no reciprocal direction bit", messages)


if __name__ == "__main__":
    unittest.main()
