"""cnab: generate, convert and validate fixed-width CNAB files.

cnab layouts                                  list built-in layouts
cnab check-layout LAYOUT                      verify a layout spec (gaps, overlaps, length)
cnab generate -o out.txt [-n 50 --seed 42]    synthetic file for integration tests
cnab from-csv details.csv -o out.txt          CSV (one row per detail) -> CNAB
cnab to-csv in.txt -o details.csv             CNAB -> CSV (details only)
cnab validate in.txt                          structural validation (exit 1 on issues)
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from cnab_toolkit.codec import FieldError
from cnab_toolkit.files import build_lines, read_records, validate_file, write_file
from cnab_toolkit.spec import LayoutSpecError, builtin_layouts, load_layout
from cnab_toolkit.synthetic import generate_pix_events

DEFAULT_LAYOUT = "pix_return_750"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cnab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("layouts")
    c = sub.add_parser("check-layout")
    c.add_argument("layout")

    g = sub.add_parser("generate")
    g.add_argument("-o", "--output", type=Path, required=True)
    g.add_argument("-n", "--rows", type=int, default=50)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date(2026, 7, 31),
        help="file generation date (fixed by default for reproducible files)",
    )

    f = sub.add_parser("from-csv")
    f.add_argument("csv", type=Path)
    f.add_argument("-o", "--output", type=Path, required=True)
    f.add_argument("--date", type=date.fromisoformat, default=None)

    t = sub.add_parser("to-csv")
    t.add_argument("file", type=Path)
    t.add_argument("-o", "--output", type=Path, required=True)

    v = sub.add_parser("validate")
    v.add_argument("file", type=Path)

    for sp in (g, f, t, v):
        sp.add_argument("-l", "--layout", default=DEFAULT_LAYOUT, help="built-in name or YAML path")

    args = p.parse_args(argv)
    try:
        return _run(args)
    except (LayoutSpecError, FieldError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    if args.cmd == "layouts":
        for name in builtin_layouts():
            lay = load_layout(name)
            print(f"{name:<20} {lay.record_length} pos  {lay.description}")
        return 0

    if args.cmd == "check-layout":
        lay = load_layout(args.layout)  # raises with every problem listed
        for rec in lay.records.values():
            print(f"{rec.name:<8} {len(rec.fields):>3} fields  1-{lay.record_length}  OK")
        return 0

    layout = load_layout(args.layout)

    if args.cmd == "generate":
        lines = build_lines(
            layout, generate_pix_events(args.rows, seed=args.seed), generation_date=args.date
        )
        write_file(args.output, lines)
        print(f"{args.output}: {len(lines)} records x {layout.record_length} positions")
        return 0

    if args.cmd == "from-csv":
        with open(args.csv, encoding="utf-8", newline="") as fh:
            details = list(csv.DictReader(fh))
        lines = build_lines(layout, details, generation_date=args.date)
        write_file(args.output, lines)
        print(f"{args.output}: {len(details)} details + header + trailer")
        return 0

    if args.cmd == "to-csv":
        details = [vals for kind, vals in read_records(args.file, layout) if kind == "detail"]
        cols = [
            f.name
            for f in layout.records["detail"].fields
            if not f.name.startswith("filler") and not f.auto and f.name != "record_type"
        ]
        with open(args.output, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(details)
        print(f"{args.output}: {len(details)} details")
        return 0

    if args.cmd == "validate":
        issues = validate_file(args.file, layout)
        for i in issues:
            print(f"line {i.line}: {i.message}")
        print(f"{args.file}: {'OK' if not issues else f'{len(issues)} issue(s)'}")
        return 1 if issues else 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
