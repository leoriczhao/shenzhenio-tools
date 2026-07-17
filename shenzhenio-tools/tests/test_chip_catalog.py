from __future__ import annotations

import unittest

from shzio.chip_catalog import ChipCatalogError, _apply_manual_spec, build_chip_catalog
from shzio.manual_parts import MANUAL_PARTS


def member(token: str, declaring_type: str, name: str) -> dict:
    return {"kind": "field", "token": token, "declaring_type": declaring_type, "name": name}


def instruction(offset: int, opcode: str, operand=None) -> dict:
    return {"offset": offset, "opcode": opcode, "operand": operand}


class ChipCatalogTests(unittest.TestCase):
    def test_extracts_core_fields_and_pin_slots(self) -> None:
        display_id = 1001
        type_id = 1002
        instructions = [
            instruction(0, "newobj", {"declaring_type": "ChipType"}),
            instruction(1, "ldc.i4", display_id),
            instruction(6, "call", {"kind": "method", "token": "decoder"}),
            instruction(11, "stfld", member("0x040008A4", "ChipType", "display")),
            instruction(16, "ldc.i4", type_id),
            instruction(21, "call", {"kind": "method", "token": "decoder"}),
            instruction(26, "stfld", member("0x040008A5", "ChipType", "type")),
            instruction(31, "ldc.i4", 300),
            instruction(36, "stfld", member("0x040008A6", "ChipType", "price")),
            instruction(41, "ldc.i4.3"),
            instruction(42, "ldc.i4.2"),
            instruction(43, "newobj", {"kind": "method", "declaring_type": "Index2"}),
            instruction(48, "stfld", member("0x040008A7", "ChipType", "size")),
            instruction(53, "newobj", {"kind": "method", "declaring_type": "Dictionary"}),
            instruction(58, "ldc.i4.0"),
            instruction(59, "ldc.i4.2"),
            instruction(60, "callvirt", {"kind": "method", "name": "Add"}),
            instruction(65, "ldc.i4.1"),
            instruction(66, "ldc.i4.1"),
            instruction(67, "callvirt", {"kind": "method", "name": "Add"}),
            instruction(72, "ldc.i4.2"),
            instruction(73, "ldc.i4.2"),
            instruction(74, "callvirt", {"kind": "method", "name": "Add"}),
            instruction(79, "ldc.i4.3"),
            instruction(80, "ldc.i4.1"),
            instruction(81, "callvirt", {"kind": "method", "name": "Add"}),
            instruction(86, "stfld", member("0x040008C1", "ChipType", "pin kinds")),
            instruction(91, "newobj", {"kind": "method", "declaring_type": "RegisterMap"}),
            instruction(96, "ldc.i4.0"),
            instruction(97, "ldc.i4.1"),
            instruction(98, "callvirt", {"kind": "method", "name": "obfuscated add"}),
            instruction(103, "ldc.i4.1"),
            instruction(104, "ldc.i4.5"),
            instruction(105, "callvirt", {"kind": "method", "name": "obfuscated add"}),
            instruction(110, "ldc.i4.2"),
            instruction(111, "ldc.i4.2"),
            instruction(112, "callvirt", {"kind": "method", "name": "obfuscated add"}),
            instruction(117, "ldc.i4.3"),
            instruction(118, "ldc.i4.6"),
            instruction(119, "callvirt", {"kind": "method", "name": "obfuscated add"}),
            instruction(124, "stfld", member("0x040008C3", "ChipType", "registers")),
            instruction(129, "stsfld", member("0x040008C9", "ChipTypes", "mc4000")),
        ]
        metadata = {
            "format": "shzio-game-metadata",
            "source": {},
            "types": [
                {
                    "full_name": "ChipType",
                    "fields": [
                        {"metadata_token": token, "type": field_type}
                        for token, field_type in {
                            "0x040008A4": "LocString",
                            "0x040008A5": "StringId",
                            "0x040008A6": "System.Int32",
                            "0x040008A7": "Index2",
                            "0x040008C1": "PinKinds",
                            "0x040008C3": "Registers",
                        }.items()
                    ],
                },
                {
                    "full_name": "ChipTypes",
                    "fields": [{"metadata_token": "0x040008C9", "type": "ChipType"}],
                },
                {
                    "full_name": "Register",
                    "fields": [
                        {"name": "X0", "constant": 1},
                        {"name": "X1", "constant": 2},
                        {"name": "P0", "constant": 5},
                        {"name": "P1", "constant": 6},
                    ],
                },
            ],
            "disassembly": [
                {
                    "category": "initializer",
                    "type": "ChipTypes",
                    "body": {"instructions": instructions},
                }
            ],
        }
        strings = {
            "format": "shzio-game-strings",
            "strings": [
                {"id": display_id, "value": "MC4000"},
                {"id": type_id, "value": "UC4"},
            ],
        }

        catalog = build_chip_catalog(metadata, strings)
        chip = catalog["chips"][0]

        self.assertEqual("MC4000", chip["name"])
        self.assertEqual("UC4", chip["type"])
        self.assertEqual(3, chip["price"])
        self.assertEqual([3, 2], chip["size"])
        self.assertEqual(1, catalog["summary"]["manual_verified_chip_count"])
        self.assertEqual(9, chip["manual"]["program_lines"])
        self.assertEqual(["acc"], chip["manual"]["internal_registers"])
        self.assertEqual(18, chip["manual"]["sources"][0]["pdf_page"])
        self.assertEqual(
            [
                ("x0", "xbus", "left", 0),
                ("p0", "simple", "left", 1),
                ("x1", "xbus", "right", 1),
                ("p1", "simple", "right", 0),
            ],
            [
                (pin["name"], pin["kind"], pin["side"], pin["side_offset"])
                for pin in chip["pins"]
            ],
        )
        self.assertTrue(all(pin["direction"] == "bidirectional" for pin in chip["pins"]))
        self.assertTrue(all(pin["official_name"] == pin["name"] for pin in chip["pins"]))

    def test_manual_validation_rejects_pin_geometry_mismatch(self) -> None:
        spec = MANUAL_PARTS["DX300"]
        chip = {
            "name": spec.name,
            "type": spec.expected_type_name,
            "price": spec.expected_game_price,
            "size": list(spec.expected_size),
            "pins": [
                {
                    "index": pin.index,
                    "name": pin.alias,
                    "kind": pin.kind,
                    "side": pin.side,
                    "side_offset": pin.side_offset,
                }
                for pin in spec.pins
            ],
        }
        chip["pins"][0]["side"] = "right"

        with self.assertRaisesRegex(ChipCatalogError, "pin 0 side"):
            _apply_manual_spec(chip, spec)

    def test_dx300_xbus_names_are_explicitly_generated_aliases(self) -> None:
        xbus_pins = MANUAL_PARTS["DX300"].pins[:3]

        self.assertEqual(("x0", "x1", "x2"), tuple(pin.alias for pin in xbus_pins))
        self.assertTrue(all(pin.official_name is None for pin in xbus_pins))


if __name__ == "__main__":
    unittest.main()
