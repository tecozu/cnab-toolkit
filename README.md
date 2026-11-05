# cnab-toolkit

[![CI](https://github.com/tecozu/cnab-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/tecozu/cnab-toolkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Spec-driven generator, parser and validator for CNAB fixed-width banking files.**

Brazilian banks still exchange payments, collections and PIX events with companies
through **CNAB** files: plain text where every record has exactly 240, 400 or 750
characters and every field sits at a fixed position. Testing an integration usually
requires realistic files the bank won't send you, and a single field that is
off by one position quietly corrupts every field after it.

This toolkit describes a layout **once, as data (YAML)**, and gets from it:

- **Generation**: seeded synthetic files for integration tests, or files built from a CSV.
- **Parsing**: CNAB → typed records → CSV (amounts as `Decimal`, dates as `date`).
- **Validation**: record length, header/trailer order, sequence numbers, trailer
  totals and field types, reported with line numbers.
- **Layout linting**: gaps, overlaps and wrong record length in the spec itself.

```text
$ cnab generate -o pix_return.txt -n 50
pix_return.txt: 52 records x 750 positions

$ cnab validate pix_return.txt
pix_return.txt: OK

$ cnab to-csv pix_return.txt -o events.csv      # and back: cnab from-csv events.csv -o same.txt
```

## Why spec-driven?

Most CNAB code hard-codes positions inside string concatenations, which makes it
hard to review and easy to break. Here the layout is a reviewable table:

```yaml
detail:
  match: { pos: 1, value: "1" }
  fields:
    - { name: movement_code,   pos: [132, 133], type: digits }
    - { name: movement_date,   pos: [134, 141], type: date }
    - { name: txid,            pos: [142, 176], type: alpha }
    - { name: original_amount, pos: [200, 216], type: money }
    - { name: sequence,        pos: [745, 750], type: int, auto: sequence }
```

The loader checks that the fields of every record cover positions `1..N` with no
gap or overlap. That check has already paid off: the bank manual this layout is
based on lists one header field as **positions 194-213 with size X(60)**, which is
contradictory. Taken literally it leaves a 40-character hole. A test reproduces the
manual's version and asserts that the linter reports `gap at positions 214-253`.

## Design decisions

| Decision | Reason |
|---|---|
| **Strict encoding**: values that don't fit raise `FieldError` (field name, positions, record #) | Silent truncation of an amount or an account number is the worst possible failure in a payment file |
| `digits` type separate from `int` | Tax IDs, branches and accounts keep their leading zeros when parsed |
| `money` encoded in cents with `Decimal` and half-up rounding | No float rounding drift; property-based tests check the round trip |
| Accents stripped to ASCII (`São` → `Sao`) | CNAB is ASCII-only; bank systems reject other bytes |
| `auto` fields (`sequence`, `detail_count`, `generation_date`) | The writer owns the bookkeeping fields, so they can't be wrong |
| CRLF line endings | What bank systems expect |

## Built-in layouts

| Layout | Records | Description |
|---|---|---|
| `pix_return_750` | header · detail · trailer | PIX collection **return** file (bank → company): QR-code issuance, payment and cancellation events, modelled on a large Brazilian bank's public manual |

Add a layout by dropping a YAML file in `src/cnab_toolkit/layouts/`, or pass any YAML
path with `--layout`. `cnab check-layout my_layout.yaml` lints it.

The synthetic generator produces a realistic mix of payments, pending charges and
cancellations. Payers have **valid** CPF/CNPJ check digits, and end-to-end IDs follow
the Central Bank format (`E` + ISPB + `yyyyMMddHHmm` + 11 alphanumerics). Every ID,
name and account is fake.

## Install & use

```bash
pip install git+https://github.com/tecozu/cnab-toolkit
cnab --help
```

```python
from cnab_toolkit.spec import load_layout
from cnab_toolkit.files import read_records, validate_file

layout = load_layout("pix_return_750")
assert not validate_file("retorno.txt", layout)
paid = [r for kind, r in read_records("retorno.txt", layout)
        if kind == "detail" and r["movement_code"] == "06"]
```

## Tests

```bash
pip install -e ".[dev]" && pytest
```

The tests cover layout linting (including the manual's inconsistency), strict
encoding, **property-based round trips** with Hypothesis for money and text fields,
deterministic generation, detection of corrupted files (truncated record, letter in a
numeric field, missing record), and a lossless **CNAB → CSV → CNAB** CLI round trip.

## Roadmap

- CNAB 240 (FEBRABAN) and CNAB 400 collection layouts
- REMESSA (company → bank) PIX layout
- Optional validation hooks (e.g. CPF/CNPJ check digits on ID fields)

## License

MIT
