from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cnab_toolkit.cli import main
from cnab_toolkit.codec import FieldError, decode_field, encode_field
from cnab_toolkit.files import build_lines, read_records, validate_file, write_file
from cnab_toolkit.spec import FieldSpec, LayoutSpecError, load_layout, parse_layout
from cnab_toolkit.synthetic import generate_pix_events

LAYOUT = load_layout("pix_return_750")
GEN_DATE = date(2026, 7, 31)


# --------------------------------------------------------------------------- layout spec
def test_builtin_layout_is_consistent():
    assert set(LAYOUT.records) == {"header", "detail", "trailer"}
    for rec in LAYOUT.records.values():
        assert sum(f.size for f in rec.fields) == 750


def _layout(fields):
    return {
        "name": "t",
        "record_length": 10,
        "records": {"detail": {"match": {"pos": 1, "value": "1"}, "fields": fields}},
    }


def test_gap_is_reported():
    with pytest.raises(LayoutSpecError, match="gap at positions 2-4"):
        parse_layout(
            _layout(
                [
                    {"name": "a", "pos": [1, 1], "type": "int"},
                    {"name": "b", "pos": [5, 10], "type": "alpha"},
                ]
            )
        )


def test_overlap_is_reported():
    with pytest.raises(LayoutSpecError, match="overlaps"):
        parse_layout(
            _layout(
                [
                    {"name": "a", "pos": [1, 5], "type": "int"},
                    {"name": "b", "pos": [4, 10], "type": "alpha"},
                ]
            )
        )


def test_the_inconsistency_from_the_bank_manual_is_caught():
    """The manual says 194-213 / X(60) for one header field; read literally, it leaves a gap."""
    raw = __import__("yaml").safe_load(
        (
            __import__("importlib.resources").resources.files("cnab_toolkit")
            / "layouts"
            / "pix_return_750.yaml"
        ).read_text(encoding="utf-8")
    )
    for f in raw["records"]["header"]["fields"]:
        if f["name"] == "psp_reserved":
            f["pos"] = [194, 213]
    with pytest.raises(LayoutSpecError, match="gap at positions 214-253"):
        parse_layout(raw)


# --------------------------------------------------------------------------- fields
def F(t, size=10, **kw):  # noqa: N802
    return FieldSpec("f", 1, size, t, **kw)


def test_strict_encoding_never_truncates():
    with pytest.raises(FieldError):
        encode_field(F("alpha", 3), "ABCD")
    with pytest.raises(FieldError):
        encode_field(F("money", 4), "100.00")  # 10000 cents needs 5 digits
    with pytest.raises(FieldError):
        encode_field(F("money"), "-1")


def test_formatting_rules():
    assert encode_field(F("alpha", 6), "São") == "Sao   "
    assert encode_field(F("digits", 11), "123.456.789-09") == "12345678909"
    assert encode_field(F("money", 6), "12.345") == "001235"  # half-up rounding to cents
    assert encode_field(F("date", 8), date(2026, 7, 1)) == "20260701"
    assert encode_field(F("date", 8), "") == " " * 8
    assert encode_field(F("int", 3, value=7), None) == "007"  # default value


@given(st.decimals(min_value=0, max_value=Decimal("99999999.99"), places=2))
def test_money_round_trip(amount):
    f = F("money", 17)
    assert decode_field(f, encode_field(f, amount)) == amount


@given(st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=35))
def test_alpha_round_trip(text):
    f = F("alpha", 35)
    assert decode_field(f, encode_field(f, text)) == text.rstrip()


# --------------------------------------------------------------------------- files
@pytest.fixture()
def cnab_file(tmp_path):
    path = tmp_path / "out.txt"
    events = generate_pix_events(40, seed=1)
    write_file(path, build_lines(LAYOUT, events, generation_date=GEN_DATE))
    return path, events


def test_generated_file_is_valid_and_round_trips(cnab_file):
    path, events = cnab_file
    assert validate_file(path, LAYOUT) == []

    records = read_records(path, LAYOUT)
    kinds = [k for k, _ in records]
    assert kinds[0] == "header" and kinds[-1] == "trailer" and kinds.count("detail") == 40
    assert records[-1][1]["detail_count"] == 40
    for event, (_, decoded) in zip(events, records[1:-1], strict=True):
        assert decoded["txid"] == event["txid"]
        assert decoded["amount_paid"] == event["amount_paid"]
        assert decoded["movement_date"] == event["movement_date"]


def test_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    for p in (a, b):
        write_file(
            p, build_lines(LAYOUT, generate_pix_events(30, seed=9), generation_date=GEN_DATE)
        )
    assert a.read_bytes() == b.read_bytes()


def test_validation_finds_corruptions(cnab_file):
    path, _ = cnab_file
    lines = path.read_text(encoding="ascii").splitlines()
    lines[3] = lines[3][:-1]  # truncated record
    lines[5] = lines[5][:200] + "X" + lines[5][201:]  # letter inside a money field
    del lines[7]  # missing record -> sequence gap and wrong trailer count
    path.write_text("\n".join(lines), encoding="ascii")

    messages = " | ".join(i.message for i in validate_file(path, LAYOUT))
    assert "expected 750" in messages
    assert "non-digit" in messages
    assert "sequence number" in messages
    assert "trailer reports 40 details, file has 39" in messages


def test_cli_end_to_end(tmp_path):
    txt, csv_, txt2 = tmp_path / "a.txt", tmp_path / "a.csv", tmp_path / "b.txt"
    assert main(["generate", "-o", str(txt), "-n", "25"]) == 0
    assert main(["validate", str(txt)]) == 0
    assert main(["to-csv", str(txt), "-o", str(csv_)]) == 0
    assert main(["from-csv", str(csv_), "-o", str(txt2), "--date", "2026-07-31"]) == 0
    assert txt.read_bytes() == txt2.read_bytes()  # CNAB -> CSV -> CNAB is lossless
    assert main(["check-layout", "pix_return_750"]) == 0
