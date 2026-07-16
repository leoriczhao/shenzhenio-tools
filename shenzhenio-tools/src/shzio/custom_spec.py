from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


STRING = r'"((?:[^"\\]|\\.)*)"'


@dataclass(frozen=True)
class CustomTerminal:
    name: str
    board_character: str
    type_name: str
    direction: str


@dataclass(frozen=True)
class CustomDial:
    name: str
    board_character: str | None = None


@dataclass(frozen=True)
class CustomSpec:
    name: str | None
    board_rows: list[str]
    terminals: list[CustomTerminal]
    has_radio: bool
    dials: list[CustomDial]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def read_custom_spec(path: str | Path) -> CustomSpec:
    return parse_custom_spec_text(Path(path).read_text(encoding="utf-8-sig"))


def parse_custom_spec_text(text: str) -> CustomSpec:
    return CustomSpec(
        name=_parse_name(text),
        board_rows=_parse_board(text),
        terminals=_parse_terminals(text),
        has_radio=bool(re.search(r"\bcreate_radio\s*\(", text)),
        dials=_parse_dials(text),
    )


def find_custom_spec_files(root: str | Path) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root] if root.suffix.lower() == ".lua" else []
    return sorted(path for path in root.rglob("*.lua") if path.is_file())


def _parse_name(text: str) -> str | None:
    match = re.search(r"\bfunction\s+get_name\s*\(\s*\).*?\breturn\s+" + STRING, text, re.DOTALL)
    if not match:
        return None
    return _unescape_lua_string(match.group(1))


def _parse_board(text: str) -> list[str]:
    match = re.search(r"\bfunction\s+get_board\s*\(\s*\).*?\breturn\s+\[\[(.*?)\]\]", text, re.DOTALL)
    if not match:
        return []
    rows = [line.rstrip() for line in match.group(1).splitlines()]
    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()
    return rows


def _parse_terminals(text: str) -> list[CustomTerminal]:
    pattern = re.compile(
        r"\bcreate_terminal\s*\(\s*"
        + STRING
        + r"\s*,\s*"
        + STRING
        + r"\s*,\s*([A-Z_]+)\s*,\s*([A-Z_]+)",
        re.DOTALL,
    )
    terminals: list[CustomTerminal] = []
    for match in pattern.finditer(text):
        terminals.append(
            CustomTerminal(
                name=_unescape_lua_string(match.group(1)),
                board_character=_unescape_lua_string(match.group(2)),
                type_name=match.group(3),
                direction=match.group(4),
            )
        )
    return terminals


def _parse_dials(text: str) -> list[CustomDial]:
    pattern = re.compile(r"\bcreate_dial\s*\(\s*" + STRING, re.DOTALL)
    dials: list[CustomDial] = []
    for index, match in enumerate(pattern.finditer(text)):
        board_character = "ABC"[index] if index < 3 else None
        dials.append(CustomDial(name=_unescape_lua_string(match.group(1)), board_character=board_character))
    return dials


def _unescape_lua_string(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(value):
            out.append("\\")
            break
        escaped = value[i]
        i += 1
        if escaped == "n":
            out.append("\n")
        elif escaped == "r":
            out.append("\r")
        elif escaped == "t":
            out.append("\t")
        elif escaped in {'"', "'", "\\"}:
            out.append(escaped)
        elif escaped.isdigit():
            digits = escaped
            while i < len(value) and len(digits) < 3 and value[i].isdigit():
                digits += value[i]
                i += 1
            out.append(chr(int(digits, 10)))
        else:
            out.append(escaped)
    return "".join(out)
