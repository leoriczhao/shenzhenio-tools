from __future__ import annotations

import unittest

from shzio import MC6000
from shzio.boards import Sz035
from shzio.checker import check_solution
from shzio.physical import analyze_physical_nets
from shzio.traces import DOWN, LEFT, RIGHT, UP, decode_char, encode_mask
from shzio.api import Solution


def endpoint_sets(board: Sz035, cpu_y: int) -> list[frozenset[str]]:
    cpu = MC6000("cpu")
    cpu.x = 11
    cpu.y = cpu_y
    nets = analyze_physical_nets(board, [cpu])
    return [frozenset(endpoint.label for endpoint in net.endpoints) for net in nets if net.endpoints]


class TracePhysicsTests(unittest.TestCase):
    def test_trace_character_bitmask_mapping(self) -> None:
        self.assertEqual(decode_char("."), 0)
        self.assertEqual(decode_char("0"), 0)
        self.assertEqual(decode_char("1"), RIGHT)
        self.assertEqual(decode_char("2"), UP)
        self.assertEqual(decode_char("4"), LEFT)
        self.assertEqual(decode_char("8"), DOWN)
        self.assertEqual(encode_mask(RIGHT | UP | LEFT | DOWN), "F")

    def test_sz035_good_placement_hits_x0_and_p1(self) -> None:
        labels = endpoint_sets(Sz035(), cpu_y=5)
        self.assertIn(frozenset({"radio.rx", "cpu.x0"}), labels)
        self.assertIn(frozenset({"cpu.p1", "buzzer.input"}), labels)

    def test_sz035_bad_placement_hits_x1_and_x3(self) -> None:
        labels = endpoint_sets(Sz035(), cpu_y=4)
        self.assertIn(frozenset({"radio.rx", "cpu.x1"}), labels)
        self.assertIn(frozenset({"cpu.x3", "buzzer.input"}), labels)

    def test_checker_rejects_api_net_that_does_not_match_physical_trace(self) -> None:
        class BadPlacement(Solution):
            board = Sz035

            def build(self) -> None:
                cpu = self.place(MC6000("cpu"), at=(11, 4))
                self.connect(self.board.radio.rx, cpu.x0)
                self.connect(cpu.p1, self.board.buzzer.input)

        diagnostics = check_solution(BadPlacement())
        messages = "\n".join(str(diagnostic) for diagnostic in diagnostics)
        self.assertIn("cpu.x0 has no physical trace connection", messages)
        self.assertIn("cpu.p1 has no physical trace connection", messages)


if __name__ == "__main__":
    unittest.main()
