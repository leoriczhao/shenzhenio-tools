from __future__ import annotations

import unittest

from shzio.board_catalog import (
    DeviceDirection,
    ElectricalKind,
    board_spec_from_record,
    build_board_catalog,
    find_board,
)


class BoardCatalogTests(unittest.TestCase):
    def test_normalizes_tiles_parts_and_linked_terminal_contacts(self) -> None:
        chips = {
            "format": "shzio-chip-catalog",
            "source": {"game_sha256": "same"},
            "chips": [
                {
                    "static_field_token": "radio-token",
                    "name": "C2S-RF901",
                    "type": "RADIO",
                    "pins": [
                        {
                            "index": 2,
                            "name": "x0",
                            "kind": "xbus",
                            "contact_offset": [2, 0],
                            "rotated_contact_offset": [0, 1],
                        },
                        {
                            "index": 3,
                            "name": "x1",
                            "kind": "xbus",
                            "contact_offset": [2, 1],
                            "rotated_contact_offset": [0, 0],
                        },
                    ],
                }
            ],
        }
        puzzles = {
            "format": "shzio-puzzle-catalog",
            "source": {"game_sha256": "same"},
            "puzzles": [
                {
                    "id": "Sz035",
                    "canvas_size": [22, 14],
                    "board_variant_value": 0,
                    "board_data": {
                        "encoding": "raw-int32-tuples",
                        "tile_width": 2,
                        "tile_height": 1,
                        "tuple_semantics": {
                            "fields": [
                                "texture_index",
                                "quarter_turns_clockwise",
                                "flip_flags",
                            ],
                            "flip_flag_bits": {"horizontal": 1, "vertical": 2},
                        },
                        "cells": [[[7, 1, 0], [1, 0, 0]]],
                    },
                    "provided_chips": [
                        {
                            "array_index": 0,
                            "chip_static_field_token": "radio-token",
                            "position_raw": [6, 4],
                            "boolean_fields": {"0x0400087D": False},
                            "terminal_pin_links": [
                                {"terminal_name": "radio-rx", "pin_index": 3}
                            ],
                        }
                    ],
                    "terminals": [
                        {
                            "array_index": 0,
                            "name": "radio-rx",
                            "type": "NonBlockingXBus",
                            "direction": "Input",
                            "position_raw": [0, 0],
                            "nonblocking_flag": False,
                        },
                        {
                            "array_index": 1,
                            "name": "buzzer",
                            "type": "Analog",
                            "direction": "Output",
                            "position_raw": [15, 5],
                            "nonblocking_flag": False,
                        },
                    ],
                    "initial_traces": [],
                }
            ],
        }

        payload = build_board_catalog(puzzles, chips)
        record = find_board(payload, "Sz035")
        board = board_spec_from_record(record)

        self.assertEqual(1, payload["summary"]["board_count"])
        self.assertEqual(0, payload["summary"]["unresolved_contact_count"])
        self.assertEqual((6, 4), board.provided_parts[0].position)
        self.assertEqual((2, 1), board.tile_grid_size)
        self.assertEqual((1, 1), board.placement_origin)
        self.assertEqual((20, 12), board.placement_size)
        self.assertEqual(frozenset({(1, 0)}), board.routable_cells)
        self.assertTrue(board.contains_footprint((1, 1), (20, 12)))
        self.assertFalse(board.contains_footprint((0, 1), (1, 1)))
        self.assertEqual((8, 5), board.terminal("radio-rx").contact)
        self.assertEqual("x1", board.terminal("radio-rx").binding.pin_name)
        self.assertEqual(ElectricalKind.XBUS, board.terminal("radio-rx").electrical_kind)
        self.assertEqual(DeviceDirection.OUTPUT, board.terminal("radio-rx").device_direction)
        self.assertTrue(board.terminal("radio-rx").nonblocking)
        self.assertEqual((15, 5), board.terminal("buzzer").contact)
        self.assertIsNone(board.terminal("buzzer").binding)
        self.assertEqual(7, board.tiles[0].texture_index)
        self.assertEqual(1, board.tiles[0].quarter_turns)
        self.assertEqual("bottom-left", payload["coordinate_model"]["origin"])
        self.assertEqual("highest-y-first", payload["routing_model"]["saved_row_order"])


if __name__ == "__main__":
    unittest.main()
