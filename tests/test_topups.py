"""Tests for balance-triggered top-ups (stage 3 of the generator)."""

from datetime import date, timedelta

import numpy as np
import pytest

from lookahead.calendar_rules import is_business_day
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.topups import BalanceTopUp, apply_topups

START = date(2026, 6, 1)  # Monday
END = date(2026, 9, 30)


def rule(**overrides: object) -> BalanceTopUp:
    fields: dict[str, object] = {
        "series_id": "family",
        "merchant": "Family",
        "category": "Income",
        "flow_type": "income",
        "txn_type": "credit memo",
        "threshold_cents": 50_000,
        "delay_business_days": (1, 5),
        "late_chance": 0.0,
        "late_delay_business_days": (5, 9),
        "amount_range_cents": (120_000, 280_000),
        "round_to_cents": 5_000,
        "round_share": 0.5,
        "min_gap_days": 10,
    }
    fields.update(overrides)
    return BalanceTopUp(**fields)  # type: ignore[arg-type]


def spend(d: date, cents: int) -> GeneratedTxn:
    return GeneratedTxn(d, -cents, "pos purchase", "Maxi", "Groceries", "everyday")


def run(
    rows: list[GeneratedTxn], opening: int, r: BalanceTopUp, seed: int = 42
) -> list[GeneratedTxn]:
    return apply_topups(rows, opening, (r,), START, END, np.random.default_rng(seed))


def business_days_between(a: date, b: date) -> int:
    """Business days in (a, b]."""
    return sum(is_business_day(a + timedelta(days=i)) for i in range(1, (b - a).days + 1))


def test_no_topup_while_balance_stays_above_threshold() -> None:
    assert run([spend(date(2026, 6, 3), 10_000)], opening=100_000, r=rule()) == []


def test_drop_below_threshold_triggers_one_topup_after_a_business_day_delay() -> None:
    trigger = date(2026, 6, 3)  # balance 1,000 - 800 = 200 < 500 that evening
    [topup] = run([spend(trigger, 80_000)], opening=100_000, r=rule())
    assert is_business_day(topup.posted_date)
    assert 1 <= business_days_between(trigger, topup.posted_date) <= 5
    assert topup.txn_type == "credit memo" and topup.series_id == "family"
    assert topup.one_off is False and topup.amount_cents > 0


def test_opening_balance_below_threshold_triggers_immediately() -> None:
    [topup] = run([], opening=10_000, r=rule())
    assert topup.posted_date <= START + timedelta(days=7)


def test_late_topup_lets_the_balance_go_negative_first() -> None:
    trigger = date(2026, 6, 3)
    rows = [spend(trigger, 80_000)] + [
        spend(trigger + timedelta(days=i), 5_000) for i in range(1, 8)
    ]
    [topup] = run(rows, opening=100_000, r=rule(late_chance=1.0))
    assert 5 <= business_days_between(trigger, topup.posted_date) <= 9
    balance = 100_000 - 80_000 - 5_000 * sum(r.posted_date < topup.posted_date for r in rows[1:])
    assert balance < 0  # dipped below zero before the late top-up arrived


def test_min_gap_between_topups() -> None:
    # Spending that keeps the balance low every day: top-ups still come >= 10 days apart.
    rows = [spend(START + timedelta(days=i), 40_000) for i in range(120)]
    got = [t.posted_date for t in run(rows, opening=0, r=rule())]
    assert len(got) >= 3
    assert all((b - a).days >= 10 for a, b in zip(got, got[1:], strict=False))


def test_amounts_in_range_and_mostly_round() -> None:
    rows = [spend(START + timedelta(days=i), 40_000) for i in range(3_000)]
    topups = apply_topups(
        rows, 0, (rule(),), START, START + timedelta(days=2_999), np.random.default_rng(1)
    )
    amounts = [t.amount_cents for t in topups]
    assert all(120_000 <= a <= 280_000 for a in amounts)
    assert 0.35 <= sum(a % 5_000 == 0 for a in amounts) / len(amounts) <= 0.65


def test_occasional_larger_topup() -> None:
    r = rule(large_chance=0.5, large_range_cents=(280_000, 400_000))
    rows = [spend(START + timedelta(days=i), 40_000) for i in range(3_000)]
    topups = apply_topups(
        rows, 0, (r,), START, START + timedelta(days=2_999), np.random.default_rng(1)
    )
    assert any(t.amount_cents > 280_000 for t in topups)
    assert all(t.amount_cents <= 400_000 for t in topups)


def test_same_seed_same_topups() -> None:
    rows = [spend(START + timedelta(days=i), 30_000) for i in range(100)]
    assert run(rows, 50_000, rule(), seed=9) == run(rows, 50_000, rule(), seed=9)


@pytest.mark.parametrize(
    "bad",
    [
        {"delay_business_days": (0, 5)},
        {"delay_business_days": (5, 1)},
        {"late_chance": 1.5},
        {"large_chance": 0.2},  # no large_range_cents given
    ],
)
def test_rejects_bad_settings(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        rule(**bad)


def test_lookahead_triggers_before_a_known_bill() -> None:
    # Balance $800 is above the $500 threshold, but $820 rent is due in 3 days.
    rent_day = date(2026, 6, 5)
    rent = GeneratedTxn(
        rent_day, -82_000, "withdrawal", "Landlord", "Rent & housing", "recurring_outflow"
    )
    without = run([rent], opening=80_000, r=rule())
    with_lookahead = run([rent], opening=80_000, r=rule(lookahead_days=5))
    assert without[0].posted_date > rent_day  # only reacts after rent has gone out
    assert with_lookahead[0].posted_date <= rent_day + timedelta(days=1)


def test_lookahead_ignores_everyday_spending() -> None:
    # A future grocery purchase is not "known in advance": no early trigger.
    future_spend = spend(date(2026, 6, 4), 70_000)
    got = run([future_spend], opening=80_000, r=rule(lookahead_days=5))
    assert got and got[0].posted_date > date(2026, 6, 4)
