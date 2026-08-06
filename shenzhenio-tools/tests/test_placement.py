from __future__ import annotations

import unittest

from shzio.api import Solution
from shzio.boards import Board, Sz035
from shzio.checker import check_solution
from shzio.model import Part, PartSpec
from shzio.parts import MC6000
from shzio.placement import PlacementError, place_parts
from shzio.traces import EXISTS, TraceGrid


class AutoPlacedBuzzer(Solution):
    board = Sz035

    def build(self) -> None:
        self.cpu = self.place(MC6000("cpu"))
        self.connect(self.board.radio.rx, self.cpu.x0)
        self.connect(self.cpu.p1, self.board.buzzer.input)


class PlacementTests(unittest.TestCase):
    def test_solution_automatically_places_and_routes_unpositioned_parts(self) -> None:
        solution = AutoPlacedBuzzer()

        self.assertEqual((9, 3, False), (solution.cpu.x, solution.cpu.y, solution.cpu.rotated))
        self.assertIsNotNone(solution.placement_result)
        self.assertIsNotNone(solution.routing_result)
        self.assertEqual(7, solution.placement_result.score.trace_cells)
        self.assertEqual(0, solution.placement_result.score.bends)
        self.assertTrue(solution.placement_result.exhaustive)

    def test_automatic_placement_is_deterministic(self) -> None:
        first = AutoPlacedBuzzer()
        second = AutoPlacedBuzzer()

        self.assertEqual(
            first.placement_result.placements,
            second.placement_result.placements,
        )
        self.assertEqual(first.board.traces.rows, second.board.traces.rows)

    def test_rotation_can_be_enabled_when_only_rotated_footprint_fits(self) -> None:
        board = Board(
            puzzle_id="rotation",
            width=4,
            height=5,
            placement_origin=(1, 1),
            placement_size=(2, 3),
        )
        part = Part(
            PartSpec(
                type_name="RECT",
                width=3,
                height=2,
                cost=0,
                max_code_lines=None,
                registers=(),
                pins={},
            ),
            name="rect",
        )

        result = place_parts(
            board,
            [part],
            [],
            orientation_options={id(part): (False, True)},
        )

        self.assertEqual((1, 1, True), (part.x, part.y, part.rotated))
        self.assertEqual(1, result.evaluated_layouts)

    def test_search_budget_returns_a_deterministic_best_known_layout(self) -> None:
        board = Board(
            puzzle_id="budget",
            width=6,
            height=6,
            placement_origin=(1, 1),
            placement_size=(4, 4),
        )
        part = Part(
            PartSpec("DOT", 1, 1, 0, None, (), {}),
            name="dot",
        )

        result = place_parts(board, [part], [], max_states=1)

        self.assertEqual((1, 1), (part.x, part.y))
        self.assertEqual(1, result.evaluated_layouts)
        self.assertFalse(result.exhaustive)

    def test_failed_search_restores_unplaced_state(self) -> None:
        board = Board(
            puzzle_id="too-small",
            width=4,
            height=4,
            placement_origin=(1, 1),
            placement_size=(1, 1),
        )
        part = Part(
            PartSpec("RECT", 2, 2, 0, None, (), {}),
            name="rect",
        )

        with self.assertRaisesRegex(PlacementError, "no legal placement"):
            place_parts(board, [part], [])

        self.assertEqual((None, None, False), (part.x, part.y, part.rotated))

    def test_checker_rejects_trace_under_part_body(self) -> None:
        board = Board(
            puzzle_id="trace-under-part",
            width=5,
            height=5,
            placement_origin=(1, 1),
            placement_size=(3, 3),
            routable_cells=frozenset({(2, 2)}),
            traces=TraceGrid.from_masks(5, 5, {(2, 2): EXISTS}),
        )

        class TraceUnderPart(Solution):
            auto_route = False

            def build(self) -> None:
                self.place(
                    Part(PartSpec("BLOCK", 1, 1, 0, None, (), {}), name="block"),
                    at=(2, 2),
                )

        TraceUnderPart.board = board
        diagnostics = check_solution(TraceUnderPart())

        self.assertTrue(
            any("passes through a part footprint" in item.message for item in diagnostics)
        )


if __name__ == "__main__":
    unittest.main()
