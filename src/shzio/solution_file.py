from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .traces import TraceGrid


@dataclass
class SavedChip:
    type_name: str
    x: int
    y: int
    rotated: bool = False
    provided: bool = False
    code_lines: list[str] = field(default_factory=list)
    extra_fields: dict[str, str] = field(default_factory=dict)


@dataclass
class SavedSolution:
    name: str
    puzzle: str
    traces: TraceGrid
    chips: list[SavedChip]
    scores: dict[str, int] = field(default_factory=dict)
    extra_fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def read(cls, path: str | Path) -> "SavedSolution":
        return parse_solution(Path(path).read_text(encoding="utf-8-sig").splitlines())

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_text(), encoding="utf-8")

    def to_text(self) -> str:
        lines: list[str] = []
        lines.append(f"[name] {self.name}")
        lines.append(f"[puzzle] {self.puzzle}")
        for key in ("production-cost", "power-usage", "lines-of-code"):
            if key in self.scores:
                lines.append(f"[{key}] {self.scores[key]}")
        for key, value in self.extra_fields.items():
            lines.append(f"[{key}] {value}")
        lines.append("")
        lines.append("[traces] ")
        lines.extend(self.traces.rows)
        lines.append("")

        for chip in self.chips:
            lines.append("[chip] ")
            lines.append(f"[type] {chip.type_name}")
            lines.append(f"[x] {chip.x}")
            lines.append(f"[y] {chip.y}")
            if chip.rotated:
                lines.append("[rotated] true")
            if chip.provided:
                lines.append("[is-puzzle-provided] true")
            for key, value in chip.extra_fields.items():
                lines.append(f"[{key}] {value}")
            if chip.code_lines:
                lines.append("[code] ")
                lines.extend(chip.code_lines)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def parse_solution(lines: list[str]) -> SavedSolution:
    name = ""
    puzzle = ""
    scores: dict[str, int] = {}
    extra_fields: dict[str, str] = {}
    traces: TraceGrid | None = None
    chips: list[SavedChip] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        key_value = _parse_header(line)
        if key_value is None:
            i += 1
            continue
        key, value = key_value

        if key == "name":
            name = value
            i += 1
        elif key == "puzzle":
            puzzle = value
            i += 1
        elif key in {"production-cost", "power-usage", "lines-of-code"}:
            scores[key] = int(value)
            i += 1
        elif key == "traces":
            rows: list[str] = []
            i += 1
            while i < len(lines):
                candidate = lines[i]
                if candidate.startswith("[chip]"):
                    break
                if candidate.strip():
                    rows.append(candidate.rstrip())
                i += 1
            traces = TraceGrid(rows)
        elif key == "chip":
            chip, i = _parse_chip(lines, i + 1)
            chips.append(chip)
        else:
            extra_fields[key] = value
            i += 1

    if not name:
        raise ValueError("solution is missing [name]")
    if not puzzle:
        raise ValueError("solution is missing [puzzle]")
    if traces is None:
        raise ValueError("solution is missing [traces]")
    return SavedSolution(name=name, puzzle=puzzle, traces=traces, chips=chips, scores=scores, extra_fields=extra_fields)


def _parse_chip(lines: list[str], start: int) -> tuple[SavedChip, int]:
    fields: dict[str, str] = {}
    code_lines: list[str] = []
    i = start
    in_code = False

    while i < len(lines):
        line = lines[i]
        if line.startswith("[chip]"):
            break
        header = _parse_header(line)
        if in_code and header is None:
            code_lines.append(line.rstrip())
            i += 1
            continue
        if header is None:
            i += 1
            continue
        key, value = header
        if key == "code":
            in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line.rstrip())
            i += 1
            continue
        fields[key] = value
        i += 1

    try:
        type_name = fields.pop("type")
        x = int(fields.pop("x"))
        y = int(fields.pop("y"))
    except KeyError as exc:
        raise ValueError(f"chip is missing field {exc.args[0]!r}") from exc

    while code_lines and not code_lines[-1].strip():
        code_lines.pop()

    rotated = _bool_field(fields.pop("rotated", "false"))
    provided = _bool_field(fields.pop("is-puzzle-provided", "false"))
    return SavedChip(type_name=type_name, x=x, y=y, rotated=rotated, provided=provided, code_lines=code_lines, extra_fields=fields), i


def _parse_header(line: str) -> tuple[str, str] | None:
    if not line.startswith("["):
        return None
    end = line.find("]")
    if end == -1:
        return None
    key = line[1:end]
    value = line[end + 1 :].strip()
    return key, value


def _bool_field(value: str) -> bool:
    return value.strip().lower() == "true"
