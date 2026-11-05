"""Whole-file operations: write, read and structurally validate CNAB files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from cnab_toolkit.codec import FieldError, decode_record, encode_record
from cnab_toolkit.spec import Layout, RecordSpec


def _auto_values(rec: RecordSpec, sequence: int, n_details: int, gen_date: date) -> dict:
    auto = {"sequence": sequence, "detail_count": n_details, "generation_date": gen_date}
    return {f.name: auto[f.auto] for f in rec.fields if f.auto}


def build_lines(
    layout: Layout,
    details: list[dict[str, Any]],
    header: dict[str, Any] | None = None,
    trailer: dict[str, Any] | None = None,
    generation_date: date | None = None,
) -> list[str]:
    """Header + one detail per dict + trailer, with sequence numbers and counts filled in.

    Header/trailer fields that share a name with a detail field (ispb, account...)
    default to the first detail's value, so a CSV of details is usually enough.
    """
    if not details:
        raise ValueError("at least one detail record is required")
    gen_date = generation_date or date.today()
    first = details[0]
    recs = layout.records
    n = len(details)

    def inherit(rec: RecordSpec, given: dict | None) -> dict:
        # never inherit structural fields (record type, constants, sequence numbers)
        shared = [f.name for f in rec.fields if f.value is None and not f.auto and f.name in first]
        return {k: first[k] for k in shared} | (given or {})

    lines = [
        encode_record(
            recs["header"],
            inherit(recs["header"], header) | _auto_values(recs["header"], 1, n, gen_date),
            layout.record_length,
        )
    ]
    for i, d in enumerate(details, start=2):
        values = d | _auto_values(recs["detail"], i, n, gen_date)
        try:
            lines.append(encode_record(recs["detail"], values, layout.record_length))
        except FieldError as exc:
            raise FieldError(exc.field, exc.value, f"{exc.reason} [detail #{i - 1}]") from exc
    lines.append(
        encode_record(
            recs["trailer"],
            inherit(recs["trailer"], trailer) | _auto_values(recs["trailer"], n + 2, n, gen_date),
            layout.record_length,
        )
    )
    return lines


def write_file(path: str | Path, lines: list[str]) -> None:
    # CRLF is what most bank systems expect for CNAB files
    Path(path).write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


def read_records(path: str | Path, layout: Layout) -> list[tuple[str, dict[str, Any]]]:
    """[(record_name, values), ...] in file order. Raises on unknown/invalid records."""
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="ascii").splitlines(), start=1):
        rec = layout.identify(line)
        if rec is None:
            raise ValueError(f"line {n}: unknown record type {line[:1]!r}")
        out.append((rec.name, decode_record(rec, line)))
    return out


@dataclass
class Issue:
    line: int
    message: str


def validate_file(path: str | Path, layout: Layout) -> list[Issue]:
    """Structural validation: lengths, record order, field types, sequence and trailer count."""
    issues: list[Issue] = []
    raw = Path(path).read_bytes()
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        return [Issue(0, f"file is not ASCII (byte {exc.start})")]
    if not lines:
        return [Issue(0, "empty file")]

    kinds: list[str | None] = []
    decoded: list[dict | None] = []
    for n, line in enumerate(lines, start=1):
        if len(line) != layout.record_length:
            issues.append(Issue(n, f"has {len(line)} positions, expected {layout.record_length}"))
        rec = layout.identify(line)
        kinds.append(rec.name if rec else None)
        if rec is None:
            issues.append(Issue(n, f"unknown record type {line[:1]!r}"))
            decoded.append(None)
            continue
        try:
            decoded.append(decode_record(rec, line.ljust(layout.record_length)))
        except (FieldError, ValueError) as exc:
            issues.append(Issue(n, str(exc)))
            decoded.append(None)

    if kinds[0] != "header":
        issues.append(Issue(1, "first record must be the header"))
    if kinds[-1] != "trailer":
        issues.append(Issue(len(lines), "last record must be the trailer"))
    for n, k in enumerate(kinds[1:-1], start=2):
        if k in ("header", "trailer"):
            issues.append(Issue(n, f"{k} found in the middle of the file"))

    for n, values in enumerate(decoded, start=1):
        if values and "sequence" in values and values["sequence"] != n:
            issues.append(Issue(n, f"sequence number {values['sequence']}, expected {n}"))

    trailer = decoded[-1]
    n_details = kinds.count("detail")
    if trailer and "detail_count" in trailer and trailer["detail_count"] != n_details:
        issues.append(
            Issue(
                len(lines),
                f"trailer reports {trailer['detail_count']} details, file has {n_details}",
            )
        )
    return issues
