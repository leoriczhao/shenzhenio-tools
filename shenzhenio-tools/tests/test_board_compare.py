from __future__ import annotations

import unittest

from shzio.board_compare import compare_board_to_puzzle
from shzio.boards import Sz035


def extracted_sz035(chip_type: str = "RADIO") -> dict:
    return {
        "id": "Sz035",
        "canvas_size": [22, 14],
        "board_data": {
            "encoding": "raw-int32-tuples",
            "tile_width": 22,
            "tile_height": 14,
        },
        "terminals": [
            {
                "name": "radio-rx",
                "type": "NonBlockingXBus",
                "direction": "Input",
                "nonblocking_flag": False,
            },
            {
                "name": "buzzer",
                "type": "Analog",
                "direction": "Output",
                "nonblocking_flag": False,
            },
        ],
        "provided_chips": [
            {
                "chip_type": chip_type,
                "terminal_pin_links": [
                    {"terminal_name": "radio-rx", "pin_index": 3}
                ],
            }
        ],
    }


class BoardCompareTests(unittest.TestCase):
    def test_sz035_verified_structure_matches_and_geometry_stays_unresolved(self) -> None:
        result = compare_board_to_puzzle(Sz035(), extracted_sz035())

        self.assertEqual("partial-match", result["status"])
        self.assertEqual(0, result["summary"]["mismatch"])
        self.assertEqual(3, result["summary"]["unresolved"])
        self.assertGreaterEqual(result["summary"]["match"], 6)

    def test_provided_chip_type_mismatch_is_reported(self) -> None:
        result = compare_board_to_puzzle(Sz035(), extracted_sz035("RTC"))

        self.assertEqual("mismatch", result["status"])
        mismatch_fields = {
            check["field"] for check in result["checks"] if check["status"] == "mismatch"
        }
        self.assertIn("provided_chip_types", mismatch_fields)


if __name__ == "__main__":
    unittest.main()
