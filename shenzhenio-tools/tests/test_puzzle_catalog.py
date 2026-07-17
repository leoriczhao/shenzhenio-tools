from __future__ import annotations

import base64
import struct
import unittest

from shzio.puzzle_catalog import build_puzzle_catalog


def instruction(offset: int, opcode: str, operand=None) -> dict:
    return {"offset": offset, "opcode": opcode, "operand": operand}


def member(token: str, declaring_type: str, name: str = "field") -> dict:
    return {
        "kind": "field",
        "token": token,
        "declaring_type": declaring_type,
        "name": name,
    }


class PuzzleCatalogTests(unittest.TestCase):
    def test_extracts_sz035_terminals_provided_radio_and_board_data(self) -> None:
        puzzle_id = 1001
        radio_rx = 1002
        buzzer = 1003
        context = 1004
        decoder = {"kind": "method", "token": "decoder"}
        terminal_ctor = {"kind": "method", "declaring_type": "Terminal", "name": ".ctor"}
        provided_ctor = {"kind": "method", "declaring_type": "Provided", "name": ".ctor"}
        index2_ctor = {"kind": "method", "declaring_type": "Index2", "name": ".ctor"}
        link_ctor = {
            "kind": "method",
            "declaring_type": "PuzzleProvidedChipTerminalPin",
            "name": ".ctor",
        }

        rows = [
            ("newobj", {"kind": "method", "declaring_type": "Puzzle", "name": ".ctor"}),
            ("ldloc.1", None),
            ("ldc.i4", puzzle_id),
            ("call", decoder),
            ("stfld", member("0x04000A7E", "Puzzle")),
            ("ldloc.1", None),
            ("ldc.i4.8", None),
            ("stfld", member("0x04000A8C", "Puzzle")),
            ("ldloc.1", None),
            ("ldc.i4.2", None),
            ("newarr", {"kind": "type", "name": "Terminal"}),
            ("dup", None),
            ("ldc.i4.0", None),
            ("ldc.i4", radio_rx),
            ("call", decoder),
            ("ldc.i4", context),
            ("call", decoder),
            ("call", {"kind": "method", "name": "L"}),
            ("ldc.i4.4", None),
            ("ldc.i4.0", None),
            ("ldc.i4.0", None),
            ("ldc.i4.0", None),
            ("ldc.i4.0", None),
            ("newobj", terminal_ctor),
            ("stelem.ref", None),
            ("dup", None),
            ("ldc.i4.1", None),
            ("ldc.i4", buzzer),
            ("call", decoder),
            ("ldc.i4", context),
            ("call", decoder),
            ("call", {"kind": "method", "name": "L"}),
            ("ldc.i4.1", None),
            ("ldc.i4.1", None),
            ("ldc.i4.s", 15),
            ("ldc.i4.5", None),
            ("ldc.i4.0", None),
            ("newobj", terminal_ctor),
            ("stelem.ref", None),
            ("stfld", member("0x04000A89", "Puzzle")),
            ("ldloc.1", None),
            ("ldc.i4", 924),
            ("newarr", {"kind": "type", "name": "Int32"}),
            ("dup", None),
            ("ldtoken", {"kind": "token", "token": "0x04000775"}),
            ("call", {"kind": "method", "name": "InitializeArray"}),
            ("stfld", member("0x04000A8E", "Puzzle")),
            ("ldloc.1", None),
            ("ldc.i4.1", None),
            ("newarr", {"kind": "type", "name": "Provided"}),
            ("dup", None),
            ("ldc.i4.0", None),
            ("newobj", provided_ctor),
            ("stloc.2", None),
            ("ldloc.2", None),
            ("ldsfld", member("0x040008DA", "ChipTypes")),
            ("stfld", member("0x0400087B", "Provided")),
            ("ldloc.2", None),
            ("ldc.i4.6", None),
            ("ldc.i4.4", None),
            ("newobj", index2_ctor),
            ("stfld", member("0x0400087C", "Provided")),
            ("ldloc.2", None),
            ("ldc.i4.1", None),
            ("newarr", {"kind": "type", "name": "PuzzleProvidedChipTerminalPin"}),
            ("dup", None),
            ("ldc.i4.0", None),
            ("ldc.i4", radio_rx),
            ("call", decoder),
            ("ldc.i4.3", None),
            ("newobj", link_ctor),
            ("stelem", {"kind": "type", "name": "PuzzleProvidedChipTerminalPin"}),
            ("stfld", member("0x0400087F", "Provided")),
            ("ldloc.2", None),
            ("stelem.ref", None),
            ("stfld", member("0x04000A8A", "Puzzle")),
            ("ldloc.1", None),
            ("stsfld", member("0x04000AB8", "Puzzles")),
        ]
        instructions = [
            instruction(index, opcode, operand) for index, (opcode, operand) in enumerate(rows)
        ]
        board_values = [-1, 0, 0] * (22 * 14)
        board_bytes = struct.pack(f"<{len(board_values)}i", *board_values)
        metadata = {
            "format": "shzio-game-metadata",
            "source": {"path": "Shenzhen.exe", "sha256": "a" * 64},
            "types": [
                {
                    "full_name": "Puzzle",
                    "fields": [
                        {"metadata_token": "0x04000A7E", "type": "System.String"},
                        {"metadata_token": "0x04000A88", "type": "Delegate"},
                        {"metadata_token": "0x04000A89", "type": "Terminal[]"},
                        {"metadata_token": "0x04000A8A", "type": "Provided[]"},
                        {"metadata_token": "0x04000A8B", "type": "TraceDictionary"},
                        {"metadata_token": "0x04000A8C", "type": "BoardVariant"},
                        {"metadata_token": "0x04000A8E", "type": "System.Int32[]"},
                    ],
                },
                {
                    "full_name": "Puzzles",
                    "fields": [{"metadata_token": "0x04000AB8", "type": "Puzzle"}],
                },
                {"full_name": "Terminal", "fields": []},
                {
                    "full_name": "TerminalType",
                    "fields": [
                        {"name": "Analog", "constant": 1},
                        {"name": "NonBlockingXBus", "constant": 4},
                    ],
                },
                {
                    "full_name": "TerminalDirection",
                    "fields": [
                        {"name": "Input", "constant": 0},
                        {"name": "Output", "constant": 1},
                    ],
                },
                {
                    "full_name": "Provided",
                    "fields": [
                        {"metadata_token": "0x0400087B", "type": "ChipType"},
                        {"metadata_token": "0x0400087C", "type": "Index2"},
                        {"metadata_token": "0x0400087D", "type": "System.Boolean"},
                        {"metadata_token": "0x0400087E", "type": "System.String"},
                        {
                            "metadata_token": "0x0400087F",
                            "type": "PuzzleProvidedChipTerminalPin[]",
                        },
                    ],
                },
                {"full_name": "PuzzleProvidedChipTerminalPin", "fields": []},
            ],
            "disassembly": [
                {
                    "category": "initializer",
                    "type": "Puzzles",
                    "body": {"instructions": instructions},
                }
            ],
            "initialized_data_fields": [
                {
                    "metadata_token": "0x04000775",
                    "sha256": "fixture",
                    "data_base64": base64.b64encode(board_bytes).decode("ascii"),
                }
            ],
        }
        strings = {
            "format": "shzio-game-strings",
            "strings": [
                {"id": puzzle_id, "value": "Sz035"},
                {"id": radio_rx, "value": "radio-rx"},
                {"id": buzzer, "value": "buzzer"},
                {"id": context, "value": "SIGNAL NAME"},
            ],
        }
        chips = {
            "format": "shzio-chip-catalog",
            "chips": [
                {
                    "static_field_token": "0x040008DA",
                    "name": "C2S-RF901",
                    "type": "RADIO",
                }
            ],
        }

        catalog = build_puzzle_catalog(metadata, strings, chips)
        puzzle = catalog["puzzles"][0]

        self.assertEqual(1, catalog["summary"]["puzzle_count"])
        self.assertEqual("Sz035", puzzle["id"])
        self.assertEqual("NonBlockingXBus", puzzle["terminals"][0]["type"])
        self.assertEqual("Output", puzzle["terminals"][1]["direction"])
        self.assertEqual([15, 5], puzzle["terminals"][1]["position_raw"])
        self.assertEqual("RADIO", puzzle["provided_chips"][0]["chip_type"])
        self.assertEqual([6, 4], puzzle["provided_chips"][0]["position_raw"])
        self.assertEqual(
            {"terminal_name": "radio-rx", "pin_index": 3},
            puzzle["provided_chips"][0]["terminal_pin_links"][0],
        )
        self.assertEqual(924, puzzle["board_data"]["int_count"])
        self.assertEqual("0x04000775", puzzle["board_data"]["initialized_data_field_token"])
        self.assertEqual("raw-int32-tuples", puzzle["board_data"]["encoding"])
        self.assertEqual([22, 14, 3], [
            puzzle["board_data"]["tile_width"],
            puzzle["board_data"]["tile_height"],
            puzzle["board_data"]["tuple_width"],
        ])
        self.assertEqual([-1, 0, 0], puzzle["board_data"]["cells"][0][0])


if __name__ == "__main__":
    unittest.main()
