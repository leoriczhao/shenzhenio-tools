from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from shzio.cli import main


def run_cli(*args: str) -> object:
    output = io.StringIO()
    with redirect_stdout(output):
        rc = main(list(args))
    if rc != 0:
        raise AssertionError(f"command failed with {rc}: {args}")
    return json.loads(output.getvalue())


class DatabaseCliTests(unittest.TestCase):
    def test_parts_info_exposes_mc6000_pin_contacts(self) -> None:
        payload = run_cli("parts-info")
        uc6 = next(item for item in payload if item["type"] == "UC6")
        self.assertEqual(uc6["pins"]["x0"]["contact"], [0, 2])
        self.assertEqual(uc6["pins"]["p1"]["contact"], [2, 2])
        self.assertEqual(uc6["pins"]["x3"]["kind"], "xbus")

    def test_boards_info_exposes_sz035_fixed_radio_and_buzzer(self) -> None:
        payload = run_cli("boards-info")
        board = next(item for item in payload if item["puzzle_id"] == "Sz035")
        self.assertEqual(board["fixed_parts"][0]["type"], "RADIO")
        self.assertEqual(board["fixed_parts"][0]["position"], [7, 5])
        self.assertEqual(board["ports"]["buzzer"]["position"], [15, 8])

    def test_scan_saves_extracts_provided_parts(self) -> None:
        text = """[name] demo
[puzzle] Sz035

[traces] 
......................
......................
......................
......................
......................
......................
......................
......................
......................
......................
......................
......................
......................
......................

[chip] 
[type] RADIO
[x] 7
[y] 5
[is-puzzle-provided] true
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "demo.txt"
            path.write_text(text, encoding="utf-8")
            payload = run_cli("scan-saves", temp)
        self.assertEqual(payload[0]["puzzle"], "Sz035")
        self.assertEqual(payload[0]["provided_parts"][0]["type"], "RADIO")
        self.assertEqual(payload[0]["provided_parts"][0]["position"], [7, 5])


if __name__ == "__main__":
    unittest.main()
