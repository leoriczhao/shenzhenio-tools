from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shzio.game_metadata import (
    GameMetadataError,
    extract_game_metadata,
    validate_game_metadata,
)


def metadata_payload(source_path: Path) -> dict:
    return {
        "format": "shzio-game-metadata",
        "format_version": 1,
        "extractor": {
            "reflection_only": True,
            "static_constructors_executed": False,
        },
        "source": {
            "path": str(source_path.resolve()),
            "sha256": "a" * 64,
        },
        "summary": {
            "type_count": 10,
            "chip_type_static_field_count": 3,
            "disassembled_method_count": 2,
            "string_decoder_candidate_count": 1,
        },
        "type_names": [],
        "types": [],
        "disassembly": [],
        "manifest_resources": [],
    }


class GameMetadataTests(unittest.TestCase):
    def test_validator_requires_reflection_only_extraction(self) -> None:
        payload = metadata_payload(Path("Shenzhen.exe"))
        payload["extractor"]["reflection_only"] = False

        with self.assertRaisesRegex(GameMetadataError, "reflection-only"):
            validate_game_metadata(payload)

    def test_validator_rejects_executed_static_constructors(self) -> None:
        payload = metadata_payload(Path("Shenzhen.exe"))
        payload["extractor"]["static_constructors_executed"] = True

        with self.assertRaisesRegex(GameMetadataError, "static constructors"):
            validate_game_metadata(payload)

    def test_extractor_invokes_powershell_and_validates_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_exe = root / "Shenzhen.exe"
            game_exe.write_bytes(b"managed assembly fixture")
            script = root / "extract-game-metadata.ps1"
            script.write_text("# fixture", encoding="utf-8")
            output = root / "raw" / "game-metadata.json"
            invoked: list[str] = []

            def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                invoked.extend(command)
                output_arg = Path(command[command.index("-Output") + 1])
                output_arg.parent.mkdir(parents=True, exist_ok=True)
                output_arg.write_text(
                    json.dumps(metadata_payload(game_exe)),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout=str(output_arg), stderr="")

            with patch("shzio.game_metadata.subprocess.run", side_effect=fake_run):
                output_path, payload = extract_game_metadata(
                    game_exe,
                    output,
                    script=script,
                    powershell="powershell",
                )

            self.assertEqual(output.resolve(), output_path)
            self.assertEqual("a" * 64, payload["source"]["sha256"])
            self.assertIn("-NoProfile", invoked)
            self.assertIn("-GameExe", invoked)
            self.assertEqual(str(game_exe.resolve()), invoked[invoked.index("-GameExe") + 1])


if __name__ == "__main__":
    unittest.main()
