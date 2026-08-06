from __future__ import annotations

import unittest

from shzio.custom_spec import parse_custom_spec_text


SAMPLE = r'''
function get_name()
  return "Frobnicator"
end

function get_board()
  return [[
.0###############.
.################.
.################.
.1##############3.
.################.
.################.
.2###############.
]]
end

function get_data()
  create_terminal("keypad", "0", TYPE_XBUS_NONBLOCKING, DIR_INPUT, keypad)
  create_terminal("prog", "1", TYPE_SIMPLE, DIR_INPUT, prog)
  create_terminal("data-out", "3", TYPE_XBUS, DIR_OUTPUT, data_out)
  create_radio(rx, tx)
  create_dial("speed", 7)
end
'''


class CustomSpecTests(unittest.TestCase):
    def test_extracts_board_and_terminals_from_lua_spec(self) -> None:
        spec = parse_custom_spec_text(SAMPLE)
        self.assertEqual(spec.name, "Frobnicator")
        self.assertEqual(len(spec.board_rows), 7)
        self.assertEqual(len(spec.board_rows[0]), 18)
        self.assertEqual([terminal.name for terminal in spec.terminals], ["keypad", "prog", "data-out"])
        self.assertEqual(spec.terminals[0].type_name, "TYPE_XBUS_NONBLOCKING")
        self.assertEqual(spec.terminals[2].direction, "DIR_OUTPUT")
        self.assertTrue(spec.has_radio)
        self.assertEqual(spec.dials[0].board_character, "A")

    def test_lua_string_unescape_preserves_non_ascii(self) -> None:
        spec = parse_custom_spec_text(
            r'''
function get_name()
  return "蜂音器 \"测试\""
end
'''
        )
        self.assertEqual(spec.name, '蜂音器 "测试"')


if __name__ == "__main__":
    unittest.main()
