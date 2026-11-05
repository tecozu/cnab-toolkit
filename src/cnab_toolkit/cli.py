"""cnab: read, convert, edit, generate and validate fixed-width CNAB files.

  cnab layouts                                 list built-in layouts
  cnab check-layout LAYOUT                     lint a layout spec (gaps, overlaps, length)
  cnab detect FILE                             which layout does this file use?
  cnab validate FILE -l LAYOUT                 structural validation (exit 1 on issues)
  cnab to-csv FILE -l LAYOUT -o out.csv        details -> CSV
  cnab to-excel FILE -l LAYOUT -o out.xlsx     details -> Excel (needs the [excel] extra)
  cnab from-csv details.csv -l LAYOUT -o OUT   CSV -> CNAB (header/trailer/sequence built)
  cnab edit FILE -l LAYOUT --key FIELD -o OUT [--set changes.csv] [--keep ids.csv | --drop ids.csv]
  cnab generate -l LAYOUT -o OUT [-n 50]       synthetic file for integration tests

Layouts: a built-in name (see `cnab layouts`) or the path to your own YAML spec.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from cnab_toolkit.codec import FieldError
from cnab_toolkit.edit import edit_lines
from cnab_toolkit.files import build_lines, read_records, validate_file, write_file
from cnab_toolkit.generic import detect_layout, synthetic_details
from cnab_toolkit.spec import Layout, LayoutSpecError, builtin_layouts, load_layout
from cnab_toolkit.synthetic import generate_pix_events

DEFAULT_LAYOUT = "pix_return_750"


def _detail_columns(layout: Layout) -> list[str]:
    return [
        f.name
        for f in layout.records["detail"].fields
        if not f.name.startswith(("filler", "zeros")) and not f.auto and f.name != "record_type"
    ]


def _read_csv(path: Path) -> list[dict]:
    """CSV with ',' or ';' (Brazilian spreadsheets), decided from the header line."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        header = fh.readline()
        fh.seek(0)
        delimiter = ";" if header.count(";") > header.count(",") else ","
        return list(csv.DictReader(fh, delimiter=delimiter))


def _key_set(path: Path) -> set[str]:
    """First column of a CSV (with header) as a set of keys."""
    return {next(iter(row.values())).strip() for row in _read_csv(path)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cnab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("layouts")
    sub.add_parser("check-layout").add_argument("layout")
    sub.add_parser("detect").add_argument("file", type=Path)

    with_layout = []
    v = sub.add_parser("validate")
    v.add_argument("file", type=Path)
    with_layout.append(v)

    for name in ("to-csv", "to-excel"):
        t = sub.add_parser(name)
        t.add_argument("file", type=Path)
        t.add_argument("-o", "--output", type=Path, required=True)
        with_layout.append(t)

    f = sub.add_parser("from-csv")
    f.add_argument("csv", type=Path)
    f.add_argument("-o", "--output", type=Path, required=True)
    f.add_argument("--date", type=date.fromisoformat, default=None)
    with_layout.append(f)

    e = sub.add_parser("edit")
    e.add_argument("file", type=Path)
    e.add_argument("-o", "--output", type=Path, required=True)
    e.add_argument("--key", required=True, help="detail field that identifies a record")
    e.add_argument("--set", type=Path, help="CSV: key column + one column per field to change")
    group = e.add_mutually_exclusive_group()
    group.add_argument("--keep", type=Path, help="CSV whose first column lists keys to keep")
    group.add_argument("--drop", type=Path, help="CSV whose first column lists keys to drop")
    with_layout.append(e)

    g = sub.add_parser("generate")
    g.add_argument("-o", "--output", type=Path, required=True)
    g.add_argument("-n", "--rows", type=int, default=50)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--key", default="participant_control_id", help="field given unique ids")
    g.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date(2026, 7, 31),
        help="file generation date (fixed by default for reproducible files)",
    )
    with_layout.append(g)

    for sp in with_layout:
        sp.add_argument("-l", "--layout", default=DEFAULT_LAYOUT, help="built-in name or YAML path")

    args = p.parse_args(argv)
    try:
        return _run(args)
    except (LayoutSpecError, FieldError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:  # noqa: C901 - a flat command dispatcher
    if args.cmd == "layouts":
        for name in builtin_layouts():
            lay = load_layout(name)
            print(f"{name:<22} {lay.record_length} pos  {lay.description}")
        return 0

    if args.cmd == "check-layout":
        lay = load_layout(args.layout)  # raises with every problem listed
        for rec in lay.records.values():
            print(f"{rec.name:<8} {len(rec.fields):>3} fields  1-{lay.record_length}  OK")
        return 0

    if args.cmd == "detect":
        found = detect_layout(args.file)
        if not found:
            print("no built-in layout matches this record length")
            return 1
        for name, issues in found:
            print(f"{name:<22} {'clean match' if issues == 0 else f'{issues} issue(s)'}")
        return 0

    layout = load_layout(args.layout)

    if args.cmd == "validate":
        issues = validate_file(args.file, layout)
        for i in issues:
            print(f"line {i.line}: {i.message}")
        print(f"{args.file}: {'OK' if not issues else f'{len(issues)} issue(s)'}")
        return 1 if issues else 0

    if args.cmd in ("to-csv", "to-excel"):
        details = [vals for kind, vals in read_records(args.file, layout) if kind == "detail"]
        cols = _detail_columns(layout)
        if args.cmd == "to-csv":
            with open(args.output, "w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(details)
        else:
            _write_excel(args.output, cols, details)
        print(f"{args.output}: {len(details)} details")
        return 0

    if args.cmd == "from-csv":
        details = _read_csv(args.csv)
        lines = build_lines(layout, details, generation_date=args.date)
        write_file(args.output, lines)
        print(f"{args.output}: {len(details)} details + header + trailer")
        return 0

    if args.cmd == "edit":
        updates = {}
        if args.set:
            for row in _read_csv(args.set):
                key = row.pop(args.key, None) or row.pop(next(iter(row)))
                updates[key.strip()] = {k: v for k, v in row.items() if k}
        lines = args.file.read_bytes().decode("ascii").splitlines()
        new_lines, rep = edit_lines(
            lines,
            layout,
            args.key,
            updates=updates,
            keep=_key_set(args.keep) if args.keep else None,
            drop=_key_set(args.drop) if args.drop else None,
        )
        write_file(args.output, new_lines)
        print(f"{args.output}: kept {rep.kept}, dropped {rep.dropped}, changed {rep.changed}")
        if rep.keys_not_found:
            print(
                f"warning: {len(rep.keys_not_found)} key(s) not found in the file: "
                f"{sorted(rep.keys_not_found)[:10]}"
            )
        return 0

    if args.cmd == "generate":
        if layout.name == "pix_return_750":
            details = generate_pix_events(args.rows, seed=args.seed)
        else:
            details = synthetic_details(layout, args.rows, seed=args.seed, key_field=args.key)
        lines = build_lines(layout, details, generation_date=args.date)
        write_file(args.output, lines)
        print(f"{args.output}: {len(lines)} records x {layout.record_length} positions")
        return 0

    return 2


def _write_excel(path: Path, cols: list[str], rows: list[dict]) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover
        raise ValueError("Excel output needs openpyxl: pip install 'cnab-toolkit[excel]'") from exc
    wb = Workbook()
    ws = wb.active
    ws.title = "details"
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c) for c in cols])
    ws.freeze_panes = "A2"
    wb.save(path)


if __name__ == "__main__":
    sys.exit(main())
