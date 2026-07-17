from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .board_compare import compare_board_to_puzzle
from .boards import BOARD_CLASSES, board_from_id
from .checker import check_solution
from .chip_catalog import ChipCatalogError, write_chip_catalog
from .custom_spec import find_custom_spec_files, read_custom_spec
from .game_metadata import GameMetadataError, extract_game_metadata
from .game_strings import GameStringError, write_game_strings
from .loader import load_solution
from .physical import analyze_physical_nets
from .parts import PART_SPECS, part_from_type
from .puzzle_catalog import (
    PuzzleCatalogError,
    find_puzzle,
    load_puzzle_catalog,
    write_puzzle_catalog,
)
from .solution_file import SavedSolution


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shzio")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="inspect a saved solution txt")
    p_inspect.add_argument("path")

    p_roundtrip = sub.add_parser("roundtrip", help="parse and rewrite a saved solution txt")
    p_roundtrip.add_argument("input")
    p_roundtrip.add_argument("output")

    p_check = sub.add_parser("check", help="run static checks on a Python solution")
    p_check.add_argument("solution")

    p_build = sub.add_parser("build", help="build a Python solution into a solution txt")
    p_build.add_argument("solution")
    p_build.add_argument("-o", "--output", required=True)

    p_analyze = sub.add_parser("analyze", help="show physical trace nets for a Python solution")
    p_analyze.add_argument("solution")

    p_analyze_txt = sub.add_parser("analyze-txt", help="show physical trace nets for a saved solution txt")
    p_analyze_txt.add_argument("path")

    sub.add_parser("parts-info", help="dump the current part/pin database as JSON")
    sub.add_parser("boards-info", help="dump the current board database as JSON")

    p_custom_info = sub.add_parser("custom-info", help="extract board and terminal metadata from a custom spec Lua file")
    p_custom_info.add_argument("path")

    p_scan_custom = sub.add_parser("scan-custom", help="find and summarize custom spec Lua files under a directory")
    p_scan_custom.add_argument("root")

    p_scan_saves = sub.add_parser("scan-saves", help="summarize saved solution txt files for board database mining")
    p_scan_saves.add_argument("root")

    p_extract_game = sub.add_parser("extract-game", help="extract read-only metadata and IL from Shenzhen.exe")
    p_extract_game.add_argument("--exe", help="path to Shenzhen.exe; auto-detected by default")
    p_extract_game.add_argument("--output", help="output JSON path; defaults to data/raw/game-metadata.json")

    p_decode_game_strings = sub.add_parser(
        "decode-game-strings",
        help="decode game strings from reflection-only metadata",
    )
    p_decode_game_strings.add_argument(
        "--metadata",
        help="metadata JSON path; defaults to data/raw/game-metadata.json",
    )
    p_decode_game_strings.add_argument(
        "--output",
        help="output JSON path; defaults to data/raw/game-strings.json",
    )

    p_extract_chips = sub.add_parser(
        "extract-chip-catalog",
        help="build a chip and pin catalog from extracted game metadata",
    )
    p_extract_chips.add_argument("--metadata", help="game metadata JSON path")
    p_extract_chips.add_argument("--strings", help="decoded game strings JSON path")
    p_extract_chips.add_argument("--output", help="output JSON path")

    p_extract_puzzles = sub.add_parser(
        "extract-puzzle-catalog",
        help="build the official puzzle and board catalog from extracted game metadata",
    )
    p_extract_puzzles.add_argument("--metadata", help="game metadata JSON path")
    p_extract_puzzles.add_argument("--strings", help="decoded game strings JSON path")
    p_extract_puzzles.add_argument("--chips", help="extracted chip catalog JSON path")
    p_extract_puzzles.add_argument("--output", help="output JSON path")

    p_compare_board = sub.add_parser(
        "compare-board-catalog",
        help="compare a hand-written BoardSpec with the extracted puzzle catalog",
    )
    p_compare_board.add_argument("puzzle")
    p_compare_board.add_argument("--catalog", help="puzzle catalog JSON path")

    p_install = sub.add_parser("install", help="copy a built solution into a save directory")
    p_install.add_argument("source")
    p_install.add_argument("dest")
    p_install.add_argument("--force", action="store_true", help="install even if Shenzhen.exe is running")

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _inspect(Path(args.path))
    if args.command == "roundtrip":
        return _roundtrip(Path(args.input), Path(args.output))
    if args.command == "check":
        return _check(Path(args.solution))
    if args.command == "build":
        return _build(Path(args.solution), Path(args.output))
    if args.command == "analyze":
        return _analyze(Path(args.solution))
    if args.command == "analyze-txt":
        return _analyze_txt(Path(args.path))
    if args.command == "parts-info":
        return _parts_info()
    if args.command == "boards-info":
        return _boards_info()
    if args.command == "custom-info":
        return _custom_info(Path(args.path))
    if args.command == "scan-custom":
        return _scan_custom(Path(args.root))
    if args.command == "scan-saves":
        return _scan_saves(Path(args.root))
    if args.command == "extract-game":
        return _extract_game(
            Path(args.exe) if args.exe else None,
            Path(args.output) if args.output else None,
        )
    if args.command == "decode-game-strings":
        return _decode_game_strings(
            Path(args.metadata) if args.metadata else None,
            Path(args.output) if args.output else None,
        )
    if args.command == "extract-chip-catalog":
        return _extract_chip_catalog(
            Path(args.metadata) if args.metadata else None,
            Path(args.strings) if args.strings else None,
            Path(args.output) if args.output else None,
        )
    if args.command == "extract-puzzle-catalog":
        return _extract_puzzle_catalog(
            Path(args.metadata) if args.metadata else None,
            Path(args.strings) if args.strings else None,
            Path(args.chips) if args.chips else None,
            Path(args.output) if args.output else None,
        )
    if args.command == "compare-board-catalog":
        return _compare_board_catalog(
            args.puzzle,
            Path(args.catalog) if args.catalog else None,
        )
    if args.command == "install":
        return _install(Path(args.source), Path(args.dest), force=args.force)
    parser.error("unknown command")
    return 2


def _inspect(path: Path) -> int:
    saved = SavedSolution.read(path)
    print(f"name: {saved.name}")
    print(f"puzzle: {saved.puzzle}")
    print(f"traces: {saved.traces.width}x{saved.traces.height}")
    print(f"chips: {len(saved.chips)}")
    for chip in saved.chips:
        flags = []
        if chip.rotated:
            flags.append("rotated")
        if chip.provided:
            flags.append("provided")
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(f"  - {chip.type_name} at ({chip.x}, {chip.y}), code={len(chip.code_lines)} lines{suffix}")
    return 0


def _roundtrip(src: Path, dest: Path) -> int:
    saved = SavedSolution.read(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    saved.write(dest)
    print(dest)
    return 0


def _check(solution_path: Path) -> int:
    solution = load_solution(solution_path)
    diagnostics = check_solution(solution)
    for diagnostic in diagnostics:
        print(diagnostic)
    if any(d.level == "error" for d in diagnostics):
        return 1
    if not diagnostics:
        print("ok")
    return 0


def _build(solution_path: Path, output: Path) -> int:
    solution = load_solution(solution_path)
    diagnostics = check_solution(solution)
    errors = [d for d in diagnostics if d.level == "error"]
    for diagnostic in diagnostics:
        print(diagnostic, file=sys.stderr)
    if errors:
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    solution.write(output)
    print(output)
    return 0


def _analyze(solution_path: Path) -> int:
    solution = load_solution(solution_path)
    nets = analyze_physical_nets(solution.board, solution.parts)
    for index, net in enumerate(nets, start=1):
        if not net.endpoints:
            continue
        endpoints = ", ".join(endpoint.label for endpoint in net.endpoints)
        print(f"net {index}: {endpoints}")
    return 0


def _analyze_txt(path: Path) -> int:
    saved = SavedSolution.read(path)
    board = board_from_id(saved.puzzle)
    board.traces = saved.traces
    board.fixed_parts = []
    parts = []
    for index, chip in enumerate(saved.chips):
        try:
            part = part_from_type(chip.type_name, name=f"{chip.type_name}_{index}")
        except KeyError:
            continue
        part.x = chip.x
        part.y = chip.y
        part.rotated = chip.rotated
        part.provided = chip.provided
        part.code_lines = chip.code_lines
        parts.append(part)

    nets = analyze_physical_nets(board, parts)
    for index, net in enumerate(nets, start=1):
        if not net.endpoints:
            continue
        endpoints = ", ".join(endpoint.label for endpoint in net.endpoints)
        print(f"net {index}: {endpoints}")
    return 0


def _parts_info() -> int:
    payload = []
    for spec in PART_SPECS.values():
        payload.append(
            {
                "type": spec.type_name,
                "size": [spec.width, spec.height],
                "cost": spec.cost,
                "max_code_lines": spec.max_code_lines,
                "registers": list(spec.registers),
                "pins": {
                    name: {
                        "kind": pin.kind.value,
                        "side": pin.side.value,
                        "offset": pin.offset,
                        "direction": pin.direction.value,
                        "contact": [pin.contact_dx, pin.contact_dy],
                    }
                    for name, pin in spec.pins.items()
                },
            }
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _boards_info() -> int:
    payload = []
    for puzzle_id, cls in BOARD_CLASSES.items():
        board = cls()
        payload.append(
            {
                "puzzle_id": puzzle_id,
                "size": [board.width, board.height],
                "fixed_parts": [
                    {
                        "name": part.name,
                        "type": part.type_name,
                        "position": [part.x, part.y],
                        "provided": part.provided,
                    }
                    for part in board.fixed_parts
                ],
                "ports": {
                    name: {
                        "pin_name": port.pin_name,
                        "kind": port.kind.value,
                        "direction": port.direction.value,
                        "position": [port.x, port.y],
                        "label": port.label,
                    }
                    for name, port in board.ports.items()
                },
                "traces": board.traces.rows if board.traces is not None else [],
            }
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _custom_info(path: Path) -> int:
    spec = read_custom_spec(path)
    print(spec.to_json())
    return 0


def _scan_custom(root: Path) -> int:
    rows = []
    for path in find_custom_spec_files(root):
        spec = read_custom_spec(path)
        rows.append(
            {
                "path": str(path),
                "name": spec.name,
                "board_size": _board_size(spec.board_rows),
                "terminals": len(spec.terminals),
                "has_radio": spec.has_radio,
                "dials": len(spec.dials),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _scan_saves(root: Path) -> int:
    paths = [root] if root.is_file() else sorted(path for path in root.rglob("*.txt") if path.is_file())
    rows = []
    for path in paths:
        try:
            saved = SavedSolution.read(path)
        except Exception as exc:
            rows.append({"path": str(path), "error": str(exc)})
            continue
        rows.append(
            {
                "path": str(path),
                "name": saved.name,
                "puzzle": saved.puzzle,
                "traces": [saved.traces.width, saved.traces.height],
                "chips": [
                    {
                        "type": chip.type_name,
                        "position": [chip.x, chip.y],
                        "provided": chip.provided,
                        "rotated": chip.rotated,
                        "code_lines": len(chip.code_lines),
                    }
                    for chip in saved.chips
                ],
                "provided_parts": [
                    {
                        "type": chip.type_name,
                        "position": [chip.x, chip.y],
                        "rotated": chip.rotated,
                    }
                    for chip in saved.chips
                    if chip.provided
                ],
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _extract_game(game_exe: Path | None, output: Path | None) -> int:
    try:
        output_path, payload = extract_game_metadata(game_exe, output)
    except GameMetadataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = payload["summary"]
    print(output_path)
    print(f"sha256: {payload['source']['sha256']}")
    print(f"types: {summary['type_count']}")
    print(f"chip type fields: {summary['chip_type_static_field_count']}")
    print(f"disassembled methods: {summary['disassembled_method_count']}")
    print(f"string decoder candidates: {summary['string_decoder_candidate_count']}")
    return 0


def _decode_game_strings(metadata: Path | None, output: Path | None) -> int:
    try:
        kwargs = {}
        if metadata is not None:
            kwargs["metadata_path"] = metadata
        if output is not None:
            kwargs["output"] = output
        output_path, payload = write_game_strings(**kwargs)
    except (GameMetadataError, GameStringError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = payload["summary"]
    print(output_path)
    print(f"references: {summary['reference_count']}")
    print(f"unique string ids: {summary['unique_id_count']}")
    print(f"decoded strings: {summary['decoded_count']}")
    print(f"index xor: {payload['keys']['index_xor']}")
    print(f"decoder key: {payload['keys']['decoder']}")
    return 0


def _extract_chip_catalog(
    metadata: Path | None,
    strings: Path | None,
    output: Path | None,
) -> int:
    try:
        kwargs = {}
        if metadata is not None:
            kwargs["metadata_path"] = metadata
        if strings is not None:
            kwargs["strings_path"] = strings
        if output is not None:
            kwargs["output"] = output
        output_path, payload = write_chip_catalog(**kwargs)
    except (GameMetadataError, ChipCatalogError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = payload["summary"]
    print(output_path)
    print(f"chips: {summary['chip_count']}")
    print(f"named chips: {summary['named_chip_count']}")
    print(f"typed chips: {summary['typed_chip_count']}")
    print(f"manual verified chips: {summary['manual_verified_chip_count']}")
    print(f"pin kinds: {payload['pin_kind_values']}")
    return 0


def _extract_puzzle_catalog(
    metadata: Path | None,
    strings: Path | None,
    chips: Path | None,
    output: Path | None,
) -> int:
    try:
        kwargs = {}
        if metadata is not None:
            kwargs["metadata_path"] = metadata
        if strings is not None:
            kwargs["strings_path"] = strings
        if chips is not None:
            kwargs["chips_path"] = chips
        if output is not None:
            kwargs["output"] = output
        output_path, payload = write_puzzle_catalog(**kwargs)
    except (GameMetadataError, PuzzleCatalogError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = payload["summary"]
    print(output_path)
    print(f"puzzles: {summary['puzzle_count']}")
    print(f"terminals: {summary['terminal_count']}")
    print(f"provided chips: {summary['provided_chip_count']}")
    print(f"initial traces: {summary['initial_trace_count']}")
    print(f"decoded boards: {summary['decoded_board_count']}")
    return 0


def _compare_board_catalog(puzzle_id: str, catalog: Path | None) -> int:
    try:
        payload = load_puzzle_catalog(catalog) if catalog is not None else load_puzzle_catalog()
        puzzle = find_puzzle(payload, puzzle_id)
        board = board_from_id(puzzle_id)
    except (PuzzleCatalogError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    result = compare_board_to_puzzle(board, puzzle)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "mismatch" else 0


def _board_size(rows: list[str]) -> str | None:
    if not rows:
        return None
    return f"{len(rows[0])}x{len(rows)}"


def _install(source: Path, dest: Path, force: bool = False) -> int:
    if _is_game_running() and not force:
        print("ERROR: Shenzhen.exe is running. Close the game before installing solution files.", file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        backup = dest.with_suffix(dest.suffix + ".bak")
        shutil.copy2(dest, backup)
        print(f"backup: {backup}")
    shutil.copy2(source, dest)
    print(dest)
    return 0


def _is_game_running() -> bool:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Process -Name Shenzhen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Id"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return bool(result.stdout.strip())


if __name__ == "__main__":
    raise SystemExit(main())
