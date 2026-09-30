"""Calibration checks: each persona's everyday spending matches its targets.

A and B to hand-set budgets. Measured over two simulated years at full activity,
in 30-day blocks, so random noise averages out.
"""

from datetime import date, timedelta

import numpy as np
import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.schedules import GeneratedTxn

START = date(2027, 1, 1)
DAYS = 720


def everyday(pid: str) -> list[GeneratedTxn]:
    spending = PERSONAS[pid].spending
    assert spending is not None
    end = START + timedelta(days=DAYS - 1)
    return spending.generate(START, end, np.random.default_rng(0), lambda _: 1.0)


def monthly_totals(txns: list[GeneratedTxn], txn_type: str | None = None) -> list[float]:
    """Dollars spent per 30-day block (optionally one transaction type only)."""
    totals = [0.0] * (DAYS // 30)
    for t in txns:
        if txn_type is None or t.txn_type == txn_type:
            totals[min((t.posted_date - START).days // 30, len(totals) - 1)] += (
                -t.amount_cents / 100
            )
    return totals


def test_c_card_spending_follows_its_profile() -> None:
    txns = everyday("C")
    card = [t for t in txns if t.txn_type == "pos purchase"]
    amounts = np.array([-t.amount_cents / 100 for t in card])

    per_month = len(card) / (DAYS / 30)
    assert 42 <= per_month <= 54  # 1.6 a day

    assert 15 <= float(np.median(amounts)) <= 21
    assert 45 <= float(np.percentile(amounts, 90)) <= 65

    online_share = sum(t.online for t in card) / len(card)
    assert 0.22 <= online_share <= 0.33

    monthly = monthly_totals(txns, "pos purchase")
    assert 1_050 <= float(np.median(monthly)) <= 1_400
    assert amounts.max() >= 100  # occasional larger purchases exist

    cash_per_month = sum(t.txn_type == "abm withdrawal" for t in txns) / (DAYS / 30)
    assert 0.7 <= cash_per_month <= 1.3  # 1 a month


@pytest.mark.parametrize(("pid", "low", "high"), [("A", 700, 900), ("B", 1_000, 1_300)])
def test_a_and_b_monthly_budgets(pid: str, low: int, high: int) -> None:
    assert low <= float(np.median(monthly_totals(everyday(pid)))) <= high
