from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .checker import check_solution
from .boards import BOARD_CLASSES, board_from_id
from .custom_spec import find_custom_spec_files, read_custom_spec
from .loader import load_solution
from .physical import analyze_physical_nets
from .parts import PART_SPECS, part_from_type
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
