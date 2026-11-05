"""Encode/decode single fields and records.

Encoding is strict. A value that does not fit its field raises `FieldError`
instead of being truncated. Silent truncation is how fixed-width integrations
end up paying the wrong amount to the wrong account.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from cnab_toolkit.spec import FieldSpec, RecordSpec

_NON_DIGITS = re.compile(r"\D")


class FieldError(ValueError):
    def __init__(self, field: FieldSpec, value: Any, reason: str):
        self.field, self.value, self.reason = field, value, reason
        super().__init__(
            f"field '{field.name}' ({field.start}-{field.end}, {field.type}"
            f"[{field.size}]): {reason} (value={value!r})"
        )


def _ascii(text: str) -> str:
    """CNAB files are ASCII: strip accents (São -> Sao) and reject anything else."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def encode_field(f: FieldSpec, value: Any) -> str:
    if _blank(value):
        value = f.value
    t = f.type

    if t == "alpha":
        text = _ascii("" if _blank(value) else str(value))
        if len(text) > f.size:
            raise FieldError(f, value, f"text longer than {f.size}")
        return text.ljust(f.size)

    if t == "date":
        if _blank(value):
            return " " * f.size
        if isinstance(value, datetime | date):
            return value.strftime(f.format)
        text = str(value).strip()
        for fmt in (f.format, "%Y-%m-%d", "%d/%m/%Y"):  # layout format, or ISO/BR from a CSV
            try:
                return datetime.strptime(text, fmt).strftime(f.format)
            except ValueError:
                continue
        raise FieldError(f, value, f"not a date ({f.format})")

    if t == "money":
        try:
            scaled = (Decimal(str(value or 0)) * 10**f.decimals).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        except InvalidOperation as exc:
            raise FieldError(f, value, "not a number") from exc
        if scaled < 0:
            raise FieldError(f, value, "negative amount")
        text = str(int(scaled))
    elif t == "int":
        try:
            n = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise FieldError(f, value, "not an integer") from exc
        if n < 0:
            raise FieldError(f, value, "negative integer")
        text = str(n)
    else:  # digits: keep only digits, so "123.456.789-09" is accepted
        text = _NON_DIGITS.sub("", "" if _blank(value) else str(value))

    if len(text) > f.size:
        raise FieldError(f, value, f"does not fit in {f.size} digits")
    return text.zfill(f.size)


def decode_field(f: FieldSpec, raw: str) -> Any:
    t = f.type
    if t == "alpha":
        return raw.rstrip()
    if t == "date":
        if raw.strip("0 ") == "":  # blank or all zeros = "no date" in most CNAB layouts
            return None
        try:
            return datetime.strptime(raw, f.format).date()
        except ValueError as exc:
            raise FieldError(f, raw, f"not a date ({f.format})") from exc
    if not raw.isdigit():
        if raw.strip() == "" and t != "digits":
            return None  # optional numeric field left blank
        raise FieldError(f, raw, "non-digit characters in numeric field")
    if t == "int":
        return int(raw)
    if t == "money":
        return Decimal(int(raw)) / 10**f.decimals
    return raw  # digits: keep leading zeros


def encode_record(rec: RecordSpec, values: dict[str, Any], length: int) -> str:
    line = "".join(encode_field(f, values.get(f.name)) for f in rec.fields)
    assert len(line) == length, f"{rec.name}: built {len(line)} positions, expected {length}"
    return line


def decode_record(rec: RecordSpec, line: str) -> dict[str, Any]:
    return {f.name: decode_field(f, line[f.slice]) for f in rec.fields}
