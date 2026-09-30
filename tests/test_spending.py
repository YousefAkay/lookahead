"""Tests for everyday spending: card purchases and cash withdrawals."""

from collections import Counter
from datetime import date

import numpy as np
import pytest

from lookahead.calendar_rules import is_card_posting_day
from lookahead.generator.spending import Merchant, SpendingProfile

START = date(2026, 6, 1)
END = date(2026, 9, 30)


def always(_: date) -> float:
    return 1.0


def profile(**overrides: object) -> SpendingProfile:
    fields: dict[str, object] = {
        "purchases_per_day": 2.0,
        "amount_sigma": 0.5,
        "merchants": (
            Merchant(
                "Tim Hortons", "Restaurants & cafés", online=False, weight=3, median_cents=600
            ),
            Merchant("Uber Eats", "Food delivery", online=True, weight=1, median_cents=2_500),
        ),
        "cash_per_month": 2.0,
    }
    fields.update(overrides)
    return SpendingProfile(**fields)  # type: ignore[arg-type]


def rows(p: SpendingProfile, start: date = START, end: date = END, seed: int = 42, activity=always):  # type: ignore[no-untyped-def]
    return p.generate(start, end, np.random.default_rng(seed), activity)


def test_purchases_post_on_card_posting_days_inside_the_range() -> None:
    got = rows(profile())
    assert got
    assert all(is_card_posting_day(t.posted_date) and START <= t.posted_date <= END for t in got)
    assert [t.posted_date for t in got] == sorted(t.posted_date for t in got)


def test_labels_and_signs() -> None:
    for t in rows(profile()):
        assert t.flow_type == "everyday"
        assert t.amount_cents < 0 and type(t.amount_cents) is int
        assert t.series_id is None and t.one_off is False
        if t.txn_type == "pos purchase":
            assert t.online is (t.merchant == "Uber Eats")
        else:
            assert t.txn_type == "abm withdrawal"
            assert t.category == "Cash" and t.online is None


def test_cash_withdrawals_are_round() -> None:
    cash = [t for t in rows(profile(cash_per_month=10.0)) if t.txn_type == "abm withdrawal"]
    assert cash and all(t.amount_cents % 2_000 == 0 for t in cash)


def test_monday_gets_sunday_purchases() -> None:
    # Flat weekday factors: Monday posts Sunday's and Monday's purchases (about 2x a
    # typical day). Compare with Wed-Sat, not Tuesday: on holiday Mondays (Victoria
    # Day, Labour Day, Thanksgiving...) both days' purchases post on Tuesday instead.
    got = rows(profile(cash_per_month=0.0), date(2026, 1, 1), date(2029, 12, 31))
    by_weekday = Counter(t.posted_date.weekday() for t in got)
    assert by_weekday[6] == 0
    typical = sum(by_weekday[d] for d in (2, 3, 4, 5)) / 4
    assert 1.6 <= by_weekday[0] / typical <= 2.1
    assert by_weekday[1] > typical  # Tuesday absorbs holiday-Monday spillover


def test_activity_scales_spending() -> None:
    def quiet_july(d: date) -> float:
        return 0.1 if d.month == 7 else 1.0

    got = rows(profile(), activity=quiet_july)
    june = sum(t.posted_date.month == 6 for t in got)
    july = sum(t.posted_date.month == 7 for t in got)
    assert july < 0.3 * june


def test_zero_activity_means_no_spending() -> None:
    assert rows(profile(), activity=lambda _: 0.0) == []


def test_same_seed_same_rows() -> None:
    assert rows(profile(), seed=3) == rows(profile(), seed=3)


def test_rejects_bad_weekday_factors() -> None:
    with pytest.raises(ValueError):
        profile(weekday_factors=(1.0, 1.0))


def test_rejects_invalid_merchant_category() -> None:
    with pytest.raises(ValueError):
        Merchant("X", "Food", online=False, weight=1, median_cents=100)
