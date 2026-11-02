"""The shared one-time money rule (lookahead/one_time.py): it imports nothing from the forecaster,
and `big_deposits` picks exactly the deposits the forecaster sets aside. Made-up rows only."""

import subprocess
import sys
from datetime import date, timedelta

from lookahead.flows import Txn, flow_types
from lookahead.forecast.spending import big_events
from lookahead.one_time import big_deposits

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
