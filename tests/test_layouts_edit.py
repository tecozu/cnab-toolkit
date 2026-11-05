import csv
from datetime import date
from decimal import Decimal

import pytest

from cnab_toolkit.cli import main
from cnab_toolkit.codec import decode_field, encode_field
from cnab_toolkit.edit import edit_lines
from cnab_toolkit.files import build_lines, read_records, validate_file, write_file
from cnab_toolkit.generic import detect_layout, synthetic_details
from cnab_toolkit.spec import FieldSpec, LayoutSpecError, builtin_layouts, load_layout, parse_layout

GEN = date(2026, 7, 31)
KEY = "participant_control_id"


@pytest.mark.parametrize("name", builtin_layouts())
def test_every_builtin_layout_is_consistent_and_round_trips(name, tmp_path):
    layout = load_layout(name)
    if "detail" not in layout.records or KEY not in layout.records["detail"].field_names():
        pytest.skip("layout without the generic key field")
    rows = synthetic_details(layout, 25, seed=3, key_field=KEY)
    path = tmp_path / "f.txt"
    write_file(path, build_lines(layout, rows, generation_date=GEN))

    assert validate_file(path, layout) == []
    decoded = [v for k, v in read_records(path, layout) if k == "detail"]
    for original, back in zip(rows, decoded, strict=True):
        for field, value in original.items():
            if value is not None:
                assert back[field] == value, field


def test_ddmmyy_dates_and_custom_decimals():
    d = FieldSpec("d", 1, 6, "date", format="%d%m%y")
    assert encode_field(d, date(2026, 7, 5)) == "050726"
    assert encode_field(d, "2026-07-05") == "050726"  # ISO from a CSV
    assert encode_field(d, "05/07/2026") == "050726"  # BR from a spreadsheet
    assert decode_field(d, "050726") == date(2026, 7, 5)
    assert decode_field(d, "000000") is None  # zero-filled = no date
    m = FieldSpec("m", 1, 10, "money", decimals=4)
    assert encode_field(m, "1.2345") == "0000012345"
    assert decode_field(m, "0000012345") == Decimal("1.2345")


def test_date_format_must_fit_the_field():
    raw = {
        "name": "t",
        "record_length": 7,
        "records": {
            "detail": {
                "match": {"pos": 1, "value": "1"},
                "fields": [
                    {"name": "a", "pos": [1, 1], "type": "int"},
                    {"name": "d", "pos": [2, 7], "type": "date"},
                ],
            }
        },
    }
    with pytest.raises(LayoutSpecError, match="does not fit"):
        parse_layout(raw)  # default %Y%m%d needs 8 positions


@pytest.fixture()
def cnab444(tmp_path):
    layout = load_layout("cnab444_fidc")
    rows = synthetic_details(layout, 10, seed=1, key_field=KEY)
    path = tmp_path / "rem.txt"
    write_file(path, build_lines(layout, rows, generation_date=GEN))
    return layout, path


def test_detect_layout(cnab444):
    _, path = cnab444
    assert detect_layout(path)[0] == ("cnab444_fidc", 0)


def test_edit_drop_renumbers_and_stays_valid(cnab444):
    layout, path = cnab444
    lines = path.read_text(encoding="ascii").splitlines()
    new, rep = edit_lines(lines, layout, KEY, drop={"ID00000002", "ID00000005", "NOPE"})
    assert (rep.kept, rep.dropped) == (8, 2)
    assert rep.keys_not_found == {"NOPE"}
    out = path.with_name("out.txt")
    write_file(out, new)
    assert validate_file(out, layout) == []  # sequences renumbered 1..n


def test_edit_keep_and_update_preserve_other_bytes(cnab444):
    layout, path = cnab444
    lines = path.read_text(encoding="ascii").splitlines()
    new, rep = edit_lines(
        lines,
        layout,
        KEY,
        keep={"ID00000003", "ID00000007"},
        updates={"ID00000003": {"due_date": "2026-12-15"}},
    )
    assert (rep.kept, rep.changed) == (2, 1)
    edited = next(ln for ln in new if "ID00000003" in ln)
    original = next(ln for ln in lines if "ID00000003" in ln)
    diff = [i + 1 for i, (a, b) in enumerate(zip(original, edited, strict=True)) if a != b]
    assert set(diff) <= set(range(121, 127)) | set(range(439, 445))  # due date + sequence only
    assert edited[120:126] == "151226"


def test_edit_rejects_unknown_field(cnab444):
    layout, path = cnab444
    lines = path.read_text(encoding="ascii").splitlines()
    with pytest.raises(ValueError, match="no field"):
        edit_lines(lines, layout, KEY, updates={"ID00000001": {"due_dat": "2026-01-01"}})


def test_cli_edit_and_excel(cnab444, tmp_path):
    _, path = cnab444
    changes = tmp_path / "changes.csv"
    with open(changes, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, delimiter=";").writerows([[KEY, "due_date"], ["ID00000004", "31/12/2026"]])
    out = tmp_path / "edited.txt"
    assert (
        main(
            [
                "edit",
                str(path),
                "-l",
                "cnab444_fidc",
                "--key",
                KEY,
                "--set",
                str(changes),
                "-o",
                str(out),
            ]
        )
        == 0
    )
    assert main(["validate", str(out), "-l", "cnab444_fidc"]) == 0
    xlsx = tmp_path / "d.xlsx"
    assert main(["to-excel", str(out), "-l", "cnab444_fidc", "-o", str(xlsx)]) == 0
    from openpyxl import load_workbook

    ws = load_workbook(xlsx).active
    assert ws.max_row == 11  # header + 10 details
