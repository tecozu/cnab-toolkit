"""Layout specification for fixed-width (CNAB-style) files, loaded from YAML.

A layout is checked when it is loaded. Gaps, overlaps, a wrong total length or a
duplicated field name raise `LayoutSpecError`, so a typo in a 750-position spec
fails immediately instead of shifting every later field by a few characters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

FIELD_TYPES = {"alpha", "digits", "int", "money", "date"}
AUTO_KINDS = {"sequence", "detail_count", "generation_date"}


class LayoutSpecError(ValueError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    start: int  # 1-based, inclusive (as written in bank manuals)
    end: int
    type: str
    value: Any = None  # constant/default
    auto: str | None = None

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    @property
    def slice(self) -> slice:
        return slice(self.start - 1, self.end)


@dataclass(frozen=True)
class RecordSpec:
    name: str
    match_pos: int
    match_value: str
    fields: tuple[FieldSpec, ...]

    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


@dataclass(frozen=True)
class Layout:
    name: str
    record_length: int
    records: dict[str, RecordSpec]
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def identify(self, line: str) -> RecordSpec | None:
        for rec in self.records.values():
            if (
                line[rec.match_pos - 1 : rec.match_pos - 1 + len(rec.match_value)]
                == rec.match_value
            ):
                return rec
        return None


def check_record(rec: RecordSpec, record_length: int) -> list[str]:
    """Return every structural problem of a record spec (empty list = valid)."""
    problems: list[str] = []
    expected = 1
    seen: set[str] = set()
    for f in sorted(rec.fields, key=lambda f: f.start):
        if f.name in seen:
            problems.append(f"{rec.name}.{f.name}: duplicated field name")
        seen.add(f.name)
        if f.end < f.start:
            problems.append(f"{rec.name}.{f.name}: end {f.end} before start {f.start}")
        if f.start > expected:
            problems.append(f"{rec.name}: gap at positions {expected}-{f.start - 1}")
        elif f.start < expected:
            problems.append(
                f"{rec.name}.{f.name}: overlaps previous field (starts at {f.start}, "
                f"previous ends at {expected - 1})"
            )
        if f.type not in FIELD_TYPES:
            problems.append(f"{rec.name}.{f.name}: unknown type '{f.type}'")
        if f.auto and f.auto not in AUTO_KINDS:
            problems.append(f"{rec.name}.{f.name}: unknown auto '{f.auto}'")
        expected = max(expected, f.end + 1)
    if expected - 1 != record_length:
        problems.append(
            f"{rec.name}: fields cover {expected - 1} positions, expected {record_length}"
        )
    return problems


def parse_layout(raw: dict[str, Any]) -> Layout:
    length = int(raw["record_length"])
    records: dict[str, RecordSpec] = {}
    problems: list[str] = []
    for rname, rraw in raw["records"].items():
        fields_ = tuple(
            FieldSpec(
                name=f["name"],
                start=int(f["pos"][0]),
                end=int(f["pos"][1]),
                type=f["type"],
                value=f.get("value"),
                auto=f.get("auto"),
            )
            for f in rraw["fields"]
        )
        rec = RecordSpec(rname, int(rraw["match"]["pos"]), str(rraw["match"]["value"]), fields_)
        problems += check_record(rec, length)
        records[rname] = rec
    if problems:
        raise LayoutSpecError(
            f"layout '{raw.get('name')}' is invalid:\n  - " + "\n  - ".join(problems)
        )
    extra = {
        k: v for k, v in raw.items() if k not in {"name", "description", "record_length", "records"}
    }
    return Layout(raw["name"], length, records, raw.get("description", ""), extra)


def builtin_layouts() -> list[str]:
    files = resources.files("cnab_toolkit") / "layouts"
    return sorted(p.name.removesuffix(".yaml") for p in files.iterdir() if p.name.endswith(".yaml"))


def load_layout(name_or_path: str | Path) -> Layout:
    """Load a built-in layout by name, or any layout YAML by path."""
    path = Path(name_or_path)
    if path.suffix in {".yaml", ".yml"} and path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        res = resources.files("cnab_toolkit") / "layouts" / f"{name_or_path}.yaml"
        if not res.is_file():
            raise LayoutSpecError(f"unknown layout '{name_or_path}'. Built-in: {builtin_layouts()}")
        text = res.read_text(encoding="utf-8")
    return parse_layout(yaml.safe_load(text))
