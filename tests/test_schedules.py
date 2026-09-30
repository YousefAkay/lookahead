"""Tests for the generator's schedule engine: recurring series, one-offs, irregular income."""

from datetime import date, timedelta

import numpy as np
import pytest

from lookahead.generator.schedules import (
    Amount,
    GeneratedTxn,
    IrregularIncome,
    OneOff,
    RecurringSchedule,
)

START = date(2026, 6, 1)
END = date(2026, 9, 30)


def rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def schedule(**overrides: object) -> RecurringSchedule:
    """A rent-like monthly schedule; tests override only what they care about."""
    fields: dict[str, object] = {
        "series_id": "rent",
        "merchant": "Landlord",
        "category": "Rent & housing",
        "flow_type": "recurring_outflow",
        "txn_type": "withdrawal",
        "cadence": "monthly",
        "anchor": date(2026, 6, 1),
        "amount": Amount(-75_000),
        "shift": "later",
    }
    fields.update(overrides)
    return RecurringSchedule(**fields)  # type: ignore[arg-type]


def dates(txns: list[GeneratedTxn]) -> list[date]:
    return [t.posted_date for t in txns]


# --- Amount ---


def test_fixed_amount_never_changes() -> None:
    r = rng()
    assert {Amount(-1_299).draw(r) for _ in range(50)} == {-1_299}


def test_variable_amount_stays_in_range_and_varies() -> None:
    r = rng()
    draws = [Amount(-8_000, spread_cents=2_000).draw(r) for _ in range(200)]
    assert all(-10_000 <= d <= -6_000 for d in draws)
    assert len(set(draws)) > 1


def test_amount_draw_returns_plain_python_int() -> None:
    # numpy returns np.int64; we want plain int everywhere.
    assert type(Amount(-8_000, spread_cents=2_000).draw(rng())) is int


@pytest.mark.parametrize("spread", [8_000, 9_000, -1])
def test_amount_rejects_spread_that_could_flip_sign_or_is_negative(spread: int) -> None:
    with pytest.raises(ValueError):
        Amount(-8_000, spread_cents=spread)


def test_zero_amount_allowed_when_fixed() -> None:
    assert Amount(0).draw(rng()) == 0  # $0.00 service charges exist


# --- RecurringSchedule: cadences and business-day shifts ---


def test_biweekly_paydays_are_14_days_apart() -> None:
    pay = schedule(cadence="biweekly", anchor=date(2026, 6, 5), shift="earlier")
    got = dates(pay.generate(START, END, rng()))
    assert got[0] == date(2026, 6, 5)
    assert len(got) == 9
    assert all(b - a == timedelta(days=14) for a, b in zip(got, got[1:], strict=False))


def test_biweekly_payday_on_holiday_moves_earlier() -> None:
    # Anchor Wed Jun 17 -> next is Jul 1 (Canada Day) -> paid Tue Jun 30.
    pay = schedule(cadence="biweekly", anchor=date(2026, 6, 17), shift="earlier")
    assert date(2026, 6, 30) in dates(pay.generate(START, END, rng()))


def test_semimonthly_pays_15th_and_last_day_moved_earlier() -> None:
    pay = schedule(cadence="semimonthly", anchor=date(2026, 6, 1), shift="earlier")
    assert dates(pay.generate(START, END, rng())) == [
        date(2026, 6, 15),
        date(2026, 6, 30),
        date(2026, 7, 15),
        date(2026, 7, 31),
        date(2026, 8, 14),  # Aug 15 is a Saturday
        date(2026, 8, 31),
        date(2026, 9, 15),
        date(2026, 9, 29),  # Sep 30 is a holiday
    ]


def test_monthly_rent_on_the_1st_moves_later() -> None:
    assert dates(schedule().generate(START, END, rng())) == [
        date(2026, 6, 1),
        date(2026, 7, 2),  # Jul 1 is Canada Day
        date(2026, 8, 3),  # Aug 1 is a Saturday
        date(2026, 9, 1),
    ]


def test_monthly_on_the_31st_clamps_and_shifts_across_the_range_edges() -> None:
    bill = schedule(anchor=date(2026, 5, 31))
    # May 31 is a Sunday: that bill posts Mon Jun 1, inside the range.
    # Sep 30 is a holiday: that bill posts Oct 1, outside the range.
    assert dates(bill.generate(START, END, rng())) == [
        date(2026, 6, 1),
        date(2026, 6, 30),
        date(2026, 7, 31),
        date(2026, 8, 31),
    ]


def test_weekly_stays_inside_the_range() -> None:
    got = dates(schedule(cadence="weekly", anchor=date(2026, 5, 4)).generate(START, END, rng()))
    assert got and all(START <= d <= END for d in got)
    assert len(got) == 18  # Mondays Jun 1 .. Sep 28, each shifted to a business day


def test_schedule_starts_at_anchor_and_stops_at_end() -> None:
    sub = schedule(anchor=date(2026, 7, 10), end=date(2026, 8, 31))
    assert dates(sub.generate(START, END, rng())) == [date(2026, 7, 10), date(2026, 8, 10)]


def test_recurring_rows_carry_series_id_and_labels() -> None:
    txns = schedule().generate(START, END, rng())
    assert all(t.series_id == "rent" for t in txns)
    assert all(t.category == "Rent & housing" and t.txn_type == "withdrawal" for t in txns)
    assert all(type(t.amount_cents) is int for t in txns)


# --- Determinism ---


def test_same_seed_same_output_different_seed_different_output() -> None:
    hydro = schedule(amount=Amount(-8_000, spread_cents=2_500))
    assert hydro.generate(START, END, rng(1)) == hydro.generate(START, END, rng(1))
    assert hydro.generate(START, END, rng(1)) != hydro.generate(START, END, rng(2))


# --- OneOff ---


def tuition(due: date) -> OneOff:
    return OneOff(
        due=due,
        amount_cents=-850_000,
        merchant="Concordia University",
        category="Education",
        flow_type="planned",
        txn_type="bill payment",
    )


def test_one_off_shifts_later_and_has_no_series() -> None:
    [txn] = tuition(date(2026, 9, 5)).generate(START, END, rng())  # Saturday
    assert txn.posted_date == date(2026, 9, 8)  # Mon Sep 7 is Labour Day
    assert txn.series_id is None


def test_one_off_outside_range_produces_nothing() -> None:
    assert tuition(date(2026, 12, 1)).generate(START, END, rng()) == []


# --- IrregularIncome ---


def family_etransfers() -> IrregularIncome:
    return IrregularIncome(
        merchant="Interac e-Transfer",
        category="Income",
        flow_type="income",
        txn_type="deposit",
        per_month=(2, 4),
        amount_range_cents=(5_000, 40_000),
        round_to_cents=1_000,
    )


def test_irregular_income_counts_and_amounts() -> None:
    txns = family_etransfers().generate(START, END, rng())
    for month in (6, 7, 8, 9):
        assert 2 <= sum(t.posted_date.month == month for t in txns) <= 4
    assert all(5_000 <= t.amount_cents <= 40_000 for t in txns)
    assert all(t.amount_cents % 1_000 == 0 for t in txns)  # round amounts, like family transfers
    assert all(START <= t.posted_date <= END for t in txns)
    assert all(type(t.amount_cents) is int and t.series_id is None for t in txns)


def test_irregular_income_is_sorted_by_date() -> None:
    got = dates(family_etransfers().generate(START, END, rng()))
    assert got == sorted(got)


# --- Labels are validated ---


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="category"):
        schedule(category="Groceries ")  # trailing space typo


def test_unknown_flow_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="flow_type"):
        schedule(flow_type="bill")
