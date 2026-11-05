"""Layout-driven helpers that work for ANY layout: synthetic data and layout detection."""

from __future__ import annotations

import random
import string
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from cnab_toolkit.files import validate_file
from cnab_toolkit.spec import FieldSpec, Layout, builtin_layouts, load_layout

_WORDS = ["ALFA", "BETA", "COMERCIO", "SERVICOS", "LTDA", "SILVA", "SOUZA", "RUA", "CENTRO"]


def _fake_value(f: FieldSpec, rng: random.Random, i: int):
    if f.value is not None or f.auto or f.name.startswith(("filler", "zeros")):
        return None  # constants, bookkeeping and fillers come from the spec
    if f.type == "alpha":
        text = " ".join(rng.choice(_WORDS) for _ in range(3))
        return text[: f.size].rstrip()
    if f.type == "digits":
        return "".join(rng.choices(string.digits, k=f.size))
    if f.type == "int":
        return rng.randint(1, min(10**f.size - 1, 360))
    if f.type == "money":
        top = min(10**f.size - 1, 50_000 * 10**f.decimals)
        return Decimal(rng.randint(10**f.decimals, top)) / 10**f.decimals
    if f.type == "date":
        return date(2026, 1, 1) + timedelta(days=rng.randrange(365))
    return None


def synthetic_details(
    layout: Layout, n: int, seed: int = 42, key_field: str | None = None
) -> list[dict]:
    """`n` random detail records valid for `layout`; `key_field` gets unique ids."""
    rng = random.Random(seed)
    detail = layout.records["detail"]
    rows = []
    for i in range(1, n + 1):
        row = {f.name: _fake_value(f, rng, i) for f in detail.fields}
        if key_field:
            row[key_field] = f"ID{i:08d}"
        rows.append(row)
    return rows


def detect_layout(path: str | Path) -> list[tuple[str, int]]:
    """Built-in layouts whose record length matches the file, best fit first.

    Returns (layout name, number of validation issues) pairs; 0 issues = clean match.
    """
    lines = Path(path).read_bytes().decode("ascii", errors="replace").splitlines()
    lengths = {len(line) for line in lines if line}
    candidates = []
    for name in builtin_layouts():
        lay = load_layout(name)
        if lengths == {lay.record_length}:
            candidates.append((name, len(validate_file(path, lay))))
    return sorted(candidates, key=lambda c: c[1])
