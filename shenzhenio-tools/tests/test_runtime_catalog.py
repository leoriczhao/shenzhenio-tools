from __future__ import annotations

import unittest

from shzio.board_catalog import (
    BoardPartSpec,
    BoardPinSpec,
    BoardSpec,
    BoardTerminalSpec,
    DeviceDirection,
    ElectricalKind,
    TerminalBinding,
)
from shzio.boards import Board
from shzio.parts import part_spec_from_catalog_record
from shzio.physical import endpoint_for_pin_ref
from shzio.traces import LEFT, RIGHT


class RuntimeCatalogTests(unittest.TestCase):
    def test_board_from_spec_resolves_bound_pin_by_game_index(self) -> None:
        spec = BoardSpec(
            puzzle_id="SzTest",
            canvas_size=(22, 14),
            tile_grid_size=(22, 14),
            board_variant_value=0,
            placement_origin=(1, 1),
            placement_size=(20, 12),
            routable_cells=frozenset((x, 5) for x in range(6, 16)),
            tiles=(),
            provided_parts=(
                BoardPartSpec(
                    index=4,
                    chip_type="RADIO",
                    chip_name="C2S-RF901",
                    position=(6, 4),
                    rotated=False,
                    pins=(
                        BoardPinSpec(
                            index=3,
                            name="x1",
                            kind=ElectricalKind.XBUS,
                            contact=(8, 5),
                        ),
                    ),
                ),
            ),
            terminals=(
                BoardTerminalSpec(
                    index=0,
                    name="radio-rx",
                    terminal_type="NonBlockingXBus",
                    electrical_kind=ElectricalKind.XBUS,
                    device_direction=DeviceDirection.OUTPUT,
                    position=(0, 0),
                    contact=(8, 5),
                    nonblocking=True,
                    binding=TerminalBinding(
                        provided_part_index=4,
                        chip_type="RADIO",
                        pin_index=3,
                        pin_name="x1",
                    ),
                ),
                BoardTerminalSpec(
                    index=1,
                    name="buzzer",
                    terminal_type="Analog",
                    electrical_kind=ElectricalKind.SIMPLE,
                    device_direction=DeviceDirection.INPUT,
                    position=(15, 5),
                    contact=(15, 5),
                    nonblocking=False,
                    binding=None,
                ),
            ),
            initial_traces=((9, 5, RIGHT), (10, 5, LEFT)),
        )

        board = Board.from_spec(spec)
        endpoint = endpoint_for_pin_ref(board.terminal("radio-rx"))

        self.assertEqual("rx", board.terminal("radio-rx").name)
        self.assertEqual((8, 5), (endpoint.x, endpoint.y))
        self.assertIs(board.radio, board.fixed_parts[0])
        self.assertIs(board.buzzer, board.ports["buzzer"])
        self.assertEqual({(9, 5), (10, 5)}, set(board.traces.nonempty_cells()))

    def test_chip_catalog_record_becomes_generic_part_spec(self) -> None:
        record = {
            "type": "RTC",
            "price": 2,
            "size": [3, 2],
            "pins": [
                {
                    "index": 3,
                    "name": "p0",
                    "kind": "simple",
                    "side": "right",
                    "side_offset": 0,
                    "direction": "unknown",
                    "contact_offset": [2, 1],
                    "rotated_contact_offset": [0, 0],
                }
            ],
            "scalar_fields": [
                {"field_token": "0x040008B5", "value": 14},
            ],
            "manual": {
                "program_lines": None,
                "internal_registers": [],
            },
        }

        spec = part_spec_from_catalog_record(record)

        self.assertEqual((3, 2), (spec.width, spec.height))
        self.assertEqual(14, spec.chip_kind_value)
        self.assertEqual((2, 1), (spec.pins["p0"].contact_dx, spec.pins["p0"].contact_dy))


if __name__ == "__main__":
    unittest.main()
