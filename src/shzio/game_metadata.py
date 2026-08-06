from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


FORMAT_NAME = "shzio-game-metadata"
FORMAT_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPT = PROJECT_ROOT / "tools" / "extract-game-metadata.ps1"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "raw" / "game-metadata.json"


class GameMetadataError(RuntimeError):
    pass


def discover_game_exe(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return _require_file(Path(explicit), "game executable")

    candidates: list[Path] = []
    configured = os.environ.get("SHENZHEN_IO_EXE")
    if configured:
        candidates.append(Path(configured))

    for start in (Path.cwd(), PROJECT_ROOT):
        candidates.extend(parent / "Shenzhen.exe" for parent in (start, *start.parents))

    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve()))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()

    raise GameMetadataError(
        "Shenzhen.exe was not found; pass --exe or set SHENZHEN_IO_EXE"
    )


def extract_game_metadata(
    game_exe: str | Path | None = None,
    output: str | Path | None = None,
    *,
    script: str | Path = DEFAULT_SCRIPT,
    powershell: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    exe_path = discover_game_exe(game_exe)
    script_path = _require_file(Path(script), "metadata extraction script")
    output_path = Path(output) if output is not None else DEFAULT_OUTPUT
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    shell = powershell or shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        raise GameMetadataError("PowerShell was not found")

    command = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-GameExe",
        str(exe_path),
        "-Output",
        str(output_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise GameMetadataError(
            f"metadata extraction failed with exit code {result.returncode}: {details}"
        )
    if not output_path.is_file():
        raise GameMetadataError(f"extractor did not create {output_path}")

    payload = load_game_metadata(output_path)
    source_path = payload["source"]["path"]
    if os.path.normcase(str(Path(source_path).resolve())) != os.path.normcase(str(exe_path)):
        raise GameMetadataError(
            f"metadata source path {source_path!r} does not match {str(exe_path)!r}"
        )
    return output_path, payload


def load_game_metadata(path: str | Path) -> dict[str, Any]:
    metadata_path = _require_file(Path(path), "game metadata JSON")
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GameMetadataError(f"failed to read {metadata_path}: {exc}") from exc
    validate_game_metadata(payload)
    return payload


def validate_game_metadata(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise GameMetadataError("metadata root must be a JSON object")
    if payload.get("format") != FORMAT_NAME:
        raise GameMetadataError(f"unexpected metadata format {payload.get('format')!r}")
    if payload.get("format_version") != FORMAT_VERSION:
        raise GameMetadataError(
            f"unsupported metadata format version {payload.get('format_version')!r}"
        )

    extractor = _require_mapping(payload, "extractor")
    if extractor.get("reflection_only") is not True:
        raise GameMetadataError("metadata was not produced in reflection-only mode")
    if extractor.get("static_constructors_executed") is not False:
        raise GameMetadataError("extractor may have executed game static constructors")

    source = _require_mapping(payload, "source")
    sha256 = source.get("sha256")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise GameMetadataError("source.sha256 must be a lowercase SHA-256 digest")
    if not isinstance(source.get("path"), str) or not source["path"]:
        raise GameMetadataError("source.path must be a non-empty string")

    summary = _require_mapping(payload, "summary")
    if not isinstance(summary.get("type_count"), int) or summary["type_count"] <= 0:
        raise GameMetadataError("summary.type_count must be positive")
    chip_type_count = summary.get("chip_type_static_field_count")
    if not isinstance(chip_type_count, int) or chip_type_count <= 0:
        raise GameMetadataError("no static ChipType fields were extracted")

    for field in ("type_names", "types", "disassembly", "manifest_resources"):
        if not isinstance(payload.get(field), list):
            raise GameMetadataError(f"{field} must be a JSON array")


def _require_mapping(payload: dict[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise GameMetadataError(f"{field} must be a JSON object")
    return value


def _require_file(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise GameMetadataError(f"{description} not found: {resolved}")
    return resolved
