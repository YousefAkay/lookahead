"""The shared one-time money rule (lookahead/one_time.py): it imports nothing from the forecaster,
and `big_deposits` picks exactly the deposits the forecaster sets aside. Made-up rows only."""

import subprocess
import sys
from datetime import date, timedelta

from lookahead.flows import Txn, flow_types
from lookahead.forecast.spending import big_events
from lookahead.one_time import big_deposits, big_payments

AS_OF = date(2026, 9, 30)
CARD = "Opos {:<20}toron "


def txn(row_id: int, day: date, cents: int, kind: str, text: str) -> Txn:
    return Txn(row_id, day, cents, kind, text)


def ordinary() -> list[Txn]:
    """Small purchases and small deposits, so the typical row is small."""
    rows = [txn(100 + i, date(2026, 9, 1) + timedelta(days=i), -2000, "pos purchase",
                CARD.format("Shop")) for i in range(12)]  # fmt: skip
    rows += [txn(120 + i, date(2026, 9, 2) + timedelta(days=3 * i), 3000, "deposit", "Friend ")
             for i in range(4)]  # fmt: skip
    return rows


def set_aside_deposits(rows: list[Txn]) -> list[tuple[date, int]]:
    """What the forecaster lists as set-aside money in (large deposits and pass-throughs)."""
    return sorted((e.day, e.cents) for e in big_events(rows, flow_types(rows), set(), AS_OF)
                  if e.cents > 0)  # fmt: skip


def mine(rows: list[Txn]) -> list[tuple[date, int]]:
    return sorted(
        (r[0].posted_date, r[2]) for r in big_deposits(rows, flow_types(rows), set(), AS_OF)
    )


def test_a_large_deposit_and_a_pass_through_are_both_big_deposits() -> None:
    rows = ordinary() + [
        txn(1, date(2026, 9, 3), 600000, "deposit", "Payroll "),  # paid straight to savings
        txn(2, date(2026, 9, 4), -300000, "customer transfer dr.", "To savings "),
        txn(3, date(2026, 9, 6), -295000, "customer transfer dr.", "To savings "),
        txn(4, date(2026, 9, 20), 250000, "deposit", "Branch Deposit "),  # kept
    ]
    assert mine(rows) == set_aside_deposits(rows) == [(date(2026, 9, 3), 600000),
                                                      (date(2026, 9, 20), 250000)]  # fmt: skip


def test_ordinary_deposits_are_not_big() -> None:
    rows = ordinary()
    assert mine(rows) == set_aside_deposits(rows) == []


def test_the_rule_imports_nothing_from_the_forecaster() -> None:
    code = (
        "import sys, lookahead.one_time; print([m for m in sys.modules if "
        "m.startswith(('lookahead.forecast', 'lookahead.proposals'))])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- Big one-off payments (2026-11-17: the scenario's daily spend leaves them out) ---


def set_aside_payments(rows: list[Txn]) -> list[tuple[date, int]]:
    """What the forecaster lists as set-aside money out (large payments and pass-throughs)."""
    return sorted((e.day, -e.cents) for e in big_events(rows, flow_types(rows), set(), AS_OF)
                  if e.cents < 0)  # fmt: skip


def mine_out(rows: list[Txn]) -> list[tuple[date, int]]:
    return sorted(
        (r[0].posted_date, r[2]) for r in big_payments(rows, flow_types(rows), set(), AS_OF)
    )


def test_a_big_occasional_bill_payment_is_a_big_payment() -> None:
    """My real-data check: one big bill payment was 72% of the counted daily spend."""
    rows = ordinary() + [txn(1, date(2026, 9, 15), -480000, "bill payment", "Tax Office ")]
    assert mine_out(rows) == set_aside_payments(rows) == [(date(2026, 9, 15), 480000)]


def test_big_payments_include_what_paid_a_big_deposit_straight_back_out() -> None:
    rows = ordinary() + [
        txn(1, date(2026, 9, 3), 600000, "deposit", "Payroll "),
        txn(2, date(2026, 9, 4), -300000, "customer transfer dr.", "To savings "),
        txn(3, date(2026, 9, 6), -295000, "customer transfer dr.", "To savings "),
        txn(4, date(2026, 9, 20), -90000, "bill payment", "Insurer "),  # big on its own
    ]
    assert mine_out(rows) == set_aside_payments(rows)
    assert mine_out(rows) == [(date(2026, 9, 4), 300000), (date(2026, 9, 6), 295000),
                              (date(2026, 9, 20), 90000)]  # fmt: skip


def test_ordinary_payments_and_payments_of_a_series_are_not_big() -> None:
    rows = ordinary() + [txn(1, date(2026, 9, 15), -480000, "bill payment", "Tax Office ")]
    assert mine_out(ordinary()) == set_aside_payments(ordinary()) == []
    flows = flow_types(rows)
    assert big_payments(rows, flows, {1}, AS_OF) == []  # in a series: placed by the series
