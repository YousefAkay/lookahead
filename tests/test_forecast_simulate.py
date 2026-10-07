"""Tests for the simulated spending paths: 500 futures from the person's own history.
Made-up numbers and dates only."""

import random
from datetime import date, timedelta

import pytest

from lookahead.forecast.schedule import ScheduledItem
from lookahead.forecast.simulate import N_FUTURES, Simulation, sample_item_day, simulate
from lookahead.forecast.start import ForecastStart
from lookahead.recurring_store import Stored
from lookahead.rent import RentRule

AS_OF = date(2026, 9, 30)  # a Wednesday


def start_for(balance: int = 100000, days: int = 14) -> ForecastStart:
    first = AS_OF + timedelta(days=1)
    return ForecastStart("a", AS_OF, balance, first, first + timedelta(days=days - 1))


def history(per_day: int, days: int = 91) -> dict[date, int]:
    return {AS_OF - timedelta(days=i): per_day for i in range(days)}


def run(**kw: object) -> Simulation:
    args: dict[str, object] = dict(
        start=start_for(), items=[], series=[], posted={}, spending_daily={}, deposits_daily={},
        history_start=AS_OF - timedelta(days=200), buffer_cents=20000,
    )  # fmt: skip
    return simulate(**{**args, **kw})  # type: ignore[arg-type]


def test_there_are_500_futures_by_default() -> None:
    assert N_FUTURES == 500 and run().futures == 500
    assert run(futures=40).futures == 40


def test_the_same_seed_gives_the_same_answer_and_another_seed_a_different_one() -> None:
    mixed = {AS_OF - timedelta(days=i): (i * 37) % 900 for i in range(91)}
    a = run(spending_daily=mixed, seed=1)
    assert a == run(spending_daily=mixed, seed=1)
    assert (
        a.p50 != run(spending_daily=mixed, seed=2).p50
        or a.p10 != run(spending_daily=mixed, seed=2).p10
    )


def test_a_steady_history_gives_one_path_so_the_range_is_a_single_line() -> None:
    sim = run(spending_daily=history(100), start=start_for(100000, days=10))
    expected = tuple(100000 - 100 * (k + 1) for k in range(10))
    assert sim.p10 == sim.p50 == sim.p90 == expected
    assert sim.shortfall_probability == 0.0


def test_scheduled_money_is_added_on_its_day() -> None:
    items = [ScheduledItem(AS_OF + timedelta(days=3), -6500, "s", "Phone", "confirmed", "")]
    sim = run(items=items, start=start_for(100000, days=5))
    assert sim.p50 == (100000, 100000, 93500, 93500, 93500)  # first day is AS_OF + 1


def test_the_shortfall_probability_is_the_share_of_futures_that_dip_below_the_buffer() -> None:
    """Two possible weeks in the history: a heavy one (14000 on its first day) and a quiet one.
    Each of the two forecast weeks draws one of them. One heavy week in the forecast takes the
    balance from 20000 to 6000 (below the 10000 buffer); two take it to -8000 (below zero)."""
    first_block_day = AS_OF - timedelta(days=13)
    sim = run(
        start=start_for(20000, days=14), spending_daily={first_block_day: 14000},
        history_start=first_block_day, buffer_cents=10000, futures=2000,
    )  # fmt: skip
    assert sim.shortfall_probability == pytest.approx(0.75, abs=0.05)  # at least one heavy week
    assert sim.below_zero_probability == pytest.approx(0.25, abs=0.05)  # both weeks heavy
    assert sim.median_lowest_cents in (6000, -8000)


def test_a_balance_exactly_at_the_buffer_is_not_a_shortfall() -> None:
    sim = run(start=start_for(20000, days=3), buffer_cents=20000)
    assert sim.shortfall_probability == 0.0


def test_irregular_deposits_raise_the_path_and_the_without_version_leaves_them_out() -> None:
    sim = run(deposits_daily=history(300), start=start_for(100000, days=3))
    assert sim.p50 == (100300, 100600, 100900)
    assert sim.median_without == (100000, 100000, 100000)


def test_deposits_and_spending_of_the_same_week_are_drawn_together() -> None:
    """A heavy-spending week that is also the week money came in stays that way."""
    first_block_day = AS_OF - timedelta(days=13)
    sim = run(
        start=start_for(20000, days=7), spending_daily={first_block_day: 14000},
        deposits_daily={first_block_day: 14000}, history_start=first_block_day, futures=300,
    )  # fmt: skip
    assert sim.p10 == sim.p50 == sim.p90  # spending and deposit always travel together
    assert sim.shortfall_probability == 0.0  # so the balance never dips


def test_rows_posted_after_the_forecast_date_are_never_used() -> None:
    """Rule zero."""
    later = {AS_OF + timedelta(days=k): 999999 for k in range(1, 20)}
    base = run(spending_daily=history(100))
    assert run(spending_daily={**history(100), **later}) == base
    assert run(deposits_daily={**history(100), **later}) == run(deposits_daily=history(100))


def test_fees_are_taken_off_at_their_daily_average() -> None:
    sim = run(start=start_for(100000, days=3), fees_per_day=150.0)
    assert sim.p50 == (99850, 99700, 99550)


def test_with_no_history_there_is_nothing_to_draw_from() -> None:
    sim = run(history_start=None, spending_daily={}, start=start_for(5000, days=3))
    assert sim.p10 == sim.p90 == (5000, 5000, 5000)


def test_the_range_is_ordered() -> None:
    mixed = {AS_OF - timedelta(days=i): (i * 53) % 1500 for i in range(91)}
    sim = run(spending_daily=mixed)
    assert all(a <= b <= c for a, b, c in zip(sim.p10, sim.p50, sim.p90, strict=True))


# --- Dates inside the futures ---

RULE = RentRule((79500, 82000), 27, 7)


def series(**fields: object) -> Stored:
    base: dict[str, object] = dict(
        key="s", name="s", kind="merchant", direction="out", cadence="monthly",
        anchor=date(2026, 1, 5), anchor_day=5, amount_type="fixed", expected_cents=-6500,
        range_cents=(-6500, -6500), next_expected=date(2026, 10, 5), status="detected",
        active=True,
    )  # fmt: skip
    return Stored(**{**base, **fields})  # type: ignore[arg-type]


def test_a_detected_series_date_moves_by_its_own_past_offsets() -> None:
    s = series(offsets=(-2, 0, 3))
    item = ScheduledItem(date(2026, 10, 10), -6500, "s", "s", "detected", "")
    rng = random.Random(0)
    seen = {sample_item_day(item, s, {}, start_for(), rng) for _ in range(200)}
    assert seen == {date(2026, 10, 8), date(2026, 10, 10), date(2026, 10, 13)}


def test_a_confirmed_series_date_does_not_move() -> None:
    s = series(offsets=(-2, 0, 3), status="confirmed")
    item = ScheduledItem(date(2026, 10, 10), -6500, "s", "s", "confirmed", "")
    rng = random.Random(0)
    assert {sample_item_day(item, s, {}, start_for(), rng) for _ in range(50)} == {
        date(2026, 10, 10)
    }


def test_a_date_never_moves_before_the_first_forecast_day() -> None:
    s = series(offsets=(-9,))
    item = ScheduledItem(date(2026, 10, 3), -6500, "s", "s", "detected", "")
    assert sample_item_day(item, s, {}, start_for(), random.Random(0)) == date(2026, 10, 1)


def test_rent_is_drawn_inside_its_window_weighted_by_past_rent_dates() -> None:
    first_of_month = {  # always on the 1st, for nearly three years
        f"p{y}{m}": date(y, m, 1) for y in (2024, 2025, 2026) for m in range(1, 10)
    }
    s = series(key="rent", kind="e-transfer", allowed_cents=RULE.amounts_cents, window=(27, 7),
               evidence=tuple(first_of_month), status="confirmed")  # fmt: skip
    start = ForecastStart("a", date(2026, 10, 15), 100000, date(2026, 10, 16), date(2026, 11, 14))
    item = ScheduledItem(date(2026, 10, 27), -82000, "rent", "Rent", "confirmed", "rent: larger")
    rng = random.Random(0)
    days = [sample_item_day(item, s, first_of_month, start, rng) for _ in range(400)]
    assert all(date(2026, 10, 27) <= d <= date(2026, 11, 7) for d in days)  # inside the window
    assert days.count(date(2026, 11, 1)) / 400 > 0.5  # follows my own past dates


def test_an_overdue_rent_stays_on_the_first_forecast_day() -> None:
    s = series(key="rent", kind="e-transfer", allowed_cents=RULE.amounts_cents, window=(27, 7))
    start = ForecastStart("a", date(2026, 10, 15), 100000, date(2026, 10, 16), date(2026, 11, 14))
    item = ScheduledItem(date(2026, 10, 16), -82000, "rent", "Rent", "entered",
                         "rent: the window ended with no payment (not seen yet)")  # fmt: skip
    assert sample_item_day(item, s, {}, start, random.Random(0)) == date(2026, 10, 16)


def test_the_weeks_the_futures_draw_from_are_listed_by_their_first_day() -> None:
    from lookahead.forecast.simulate import pool_week_starts

    starts = pool_week_starts(start_for(), AS_OF - timedelta(days=200))
    assert len(starts) == 13 and starts[0] == date(2026, 7, 2) and starts[-1] == date(2026, 9, 24)
    assert all(d.weekday() == start_for().first_day.weekday() for d in starts)  # Thursdays
    assert pool_week_starts(start_for(), None) == []
    short = pool_week_starts(start_for(), AS_OF - timedelta(days=13))  # two weeks of history
    assert short == [date(2026, 9, 17), date(2026, 9, 24)]
