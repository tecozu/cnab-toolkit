"""Seeded synthetic PIX return events for integration testing.

Produces realistic detail records: a weighted mix of payments, pending charges and
cancellations, payers with *valid* CPF/CNPJ check digits, and end-to-end IDs in the
Central Bank format (E + ISPB + yyyyMMddHHmm + 11 alphanumerics).
"""

from __future__ import annotations

import random
import string
from datetime import date, datetime, timedelta
from decimal import Decimal

from faker import Faker

MOVEMENT_WEIGHTS = {"06": 60, "02": 16, "07": 10, "08": 8, "09": 4, "03": 2}
ALNUM = string.ascii_letters + string.digits


def generate_pix_events(
    n: int,
    *,
    seed: int = 42,
    receiver_tax_id: str = "11222333000181",
    branch: str = "1234",
    account: str = "56789012",
    ispb: str = "12345678",
    start: date = date(2026, 7, 1),
    days: int = 27,
) -> list[dict]:
    rng = random.Random(seed)
    fake = Faker("pt_BR")
    fake.seed_instance(seed)
    codes, weights = zip(*MOVEMENT_WEIGHTS.items(), strict=True)

    events = []
    for _ in range(n):
        code = rng.choices(codes, weights)[0]
        paid = code == "06"
        moved = start + timedelta(days=rng.randrange(days))
        amount = Decimal(rng.randint(1_000, 200_000)) / 100
        is_person = rng.random() < 0.8
        event = {
            "ispb": ispb,
            "receiver_tax_id": receiver_tax_id,
            "branch": branch,
            "account": account,
            "pix_key": receiver_tax_id,
            "charge_type": "2",
            "movement_code": code,
            "movement_date": moved,
            "txid": "".join(rng.choices(ALNUM, k=32)),
            "original_amount": amount,
            "final_amount": amount,
            "amount_paid": amount if paid else Decimal(0),
        }
        if paid:
            ts = datetime.combine(moved, datetime.min.time()) + timedelta(
                minutes=rng.randrange(1440)
            )
            event |= {
                "payer_registration_type": "01" if is_person else "02",
                "payer_tax_id": (fake.cpf() if is_person else fake.cnpj()).translate(
                    str.maketrans("", "", ".-/")
                ),
                "payer_name": fake.name() if is_person else fake.company(),
                "settlement_code": "02",
                "end_to_end_id": f"E{ispb}{ts:%Y%m%d%H%M}{''.join(rng.choices(ALNUM, k=11))}",
            }
        events.append(event)
    return sorted(events, key=lambda e: e["movement_date"])
