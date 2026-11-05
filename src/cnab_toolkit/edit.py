"""Edit existing CNAB files safely: change fields, keep or drop records, renumber.

Typical operations requests: "postpone the due date of these 40 installments",
"resend the file without the cancelled contracts", "send only these records".
Doing it by slicing strings at hand-counted positions is error-prone. Here every
change goes through the layout:

* fields are addressed by NAME and re-encoded with the same strict rules as writing;
* untouched characters are preserved byte for byte;
* sequence numbers and trailer counts are recomputed, so the result always validates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cnab_toolkit.codec import decode_field, encode_field
from cnab_toolkit.spec import FieldSpec, Layout, RecordSpec


@dataclass
class EditReport:
    kept: int = 0
    dropped: int = 0
    changed: int = 0
    keys_not_found: set[str] = field(default_factory=set)


def _put(line: str, f: FieldSpec, value: Any) -> str:
    return line[: f.start - 1] + encode_field(f, value) + line[f.end :]


def _field(rec: RecordSpec, name: str) -> FieldSpec:
    for f in rec.fields:
        if f.name == name:
            return f
    raise ValueError(f"record '{rec.name}' has no field '{name}'. Fields: {rec.field_names()}")


def edit_lines(
    lines: list[str],
    layout: Layout,
    key_field: str,
    *,
    updates: dict[str, dict[str, Any]] | None = None,
    keep: set[str] | None = None,
    drop: set[str] | None = None,
) -> tuple[list[str], EditReport]:
    """Return the edited lines. `updates` maps key -> {field: new value}."""
    if keep is not None and drop is not None:
        raise ValueError("use either keep or drop, not both")
    updates = updates or {}
    detail = layout.records["detail"]
    key_spec = _field(detail, key_field)
    for changes in updates.values():
        for name in changes:
            _field(detail, name)  # fail fast on a typo in the CSV header

    report = EditReport()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        rec = layout.identify(line)
        if rec is None or rec.name != "detail":
            out.append(line)
            continue
        key = str(decode_field(key_spec, line[key_spec.slice])).strip()
        seen.add(key)
        if (keep is not None and key not in keep) or (drop is not None and key in drop):
            report.dropped += 1
            continue
        if key in updates:
            for name, value in updates[key].items():
                line = _put(line, _field(detail, name), value)
            report.changed += 1
        report.kept += 1
        out.append(line)

    report.keys_not_found = (set(updates) | (keep or set()) | (drop or set())) - seen
    return renumber(out, layout), report


def renumber(lines: list[str], layout: Layout) -> list[str]:
    """Recompute `auto: sequence` and `auto: detail_count` fields in every record."""
    n_details = sum(1 for ln in lines if (r := layout.identify(ln)) and r.name == "detail")
    out = []
    for i, line in enumerate(lines, start=1):
        rec = layout.identify(line)
        if rec is not None:
            for f in rec.fields:
                if f.auto == "sequence":
                    line = _put(line, f, i)
                elif f.auto == "detail_count":
                    line = _put(line, f, n_details)
        out.append(line)
    return out
