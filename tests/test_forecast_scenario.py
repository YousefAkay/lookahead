"""Tests for the what-if scenario engine: the original and the scenario use the same random draws,
so every difference is the change itself. Made-up rows only."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.forecast.path import Forecast, build_forecast
from lookahead.forecast.scenario import (
    ExtraItem,
    MovedBill,
    Scenario,
    SpendingChange,
    run_scenario,
)
from lookahead.planned import add_planned

ACCOUNT = "test"
CARD = "Opos {:<20}toron "
FRIEND = "Interac e-Transfer from a friend "


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:03d}"),
        )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A phone bill, varied card spending, and small deposits now and then. Data ends 26 Sep."""
    path = tmp_path / "s.db"
    c = connect(path)
    rows: list[tuple[date, int, str, str]] = []
    for m in range(1, 10):
        due = shift_to_business_day(monthly_date(2026, m, 10), "later")
        rows.append((due, -6500, "bill payment", "Phone Co "))
    day, i = date(2026, 1, 2), 0
    while day <= date(2026, 9, 28):
        rows.append((day, -(1500 + (i * 37) % 2500), "pos purchase", CARD.format("Bookshop")))
        day, i = day + timedelta(days=3), i + 1
    for week in range(8):
        rows.append((date(2026, 8, 5) + timedelta(days=7 * week), 6000, "deposit", FRIEND))
    for when, cents, kind, text in sorted(rows, key=lambda r: r[0]):
        insert(c, when, cents, kind, text)
    with c:
        c.execute("UPDATE transactions SET balance_after_cents = 150000")
    c.close()
    return path


@pytest.fixture
def forecast(db: Path) -> Forecast:
    built = build_forecast(connect(db), ACCOUNT, "weekday")
    assert built is not None and built.simulation is not None and built.inputs is not None
    assert built.simulation.p50 != built.simulation.median_without  # deposits do something
    return built


def with_planned(db: Path, items: list[tuple[date, int, str]]) -> Forecast:
    c = connect(db)
    for day, cents, what in items:
        add_planned(c, ACCOUNT, day, cents, what, date(2026, 9, 29))
    built = build_forecast(c, ACCOUNT, "weekday")
    assert built is not None and built.simulation is not None
    return built


def test_a_scenario_with_no_changes_gives_exactly_the_original(forecast: Forecast) -> None:
    result = run_scenario(forecast, Scenario())
    assert result.modified == result.original == forecast.simulation


def test_no_other_money_expected_leaves_only_planned_items_as_money_in(forecast: Forecast) -> None:
    result = run_scenario(forecast, Scenario(no_other_money=True))
    original, modified = result.original, result.modified
    assert modified.p50 == original.median_without  # same draws, deposits off
    assert modified.median_without == original.median_without
    assert modified.shortfall_probability == original.shortfall_probability_without
    assert modified.p50 != original.p50  # and it really did something


def test_a_planned_deposit_still_counts_when_other_money_is_off(db: Path) -> None:
    base = with_planned(db, [(date(2026, 10, 10), 2000000, "Family, for the term")])
    result = run_scenario(base, Scenario(no_other_money=True))
    for k, day in enumerate(result.modified.days):
        assert result.modified.p50[k] == result.original.median_without[k]
        assert (result.modified.p50[k] > 1000000) == (day >= date(2026, 10, 10))


def test_spending_less_by_a_percent_never_lowers_any_part_of_the_range(forecast: Forecast) -> None:
    first, last = forecast.start.first_day, forecast.start.last_day
    result = run_scenario(forecast, Scenario(spending=SpendingChange(first, last, percent=25.0)))
    o, m = result.original, result.modified
    assert all(b >= a for a, b in zip(o.p10, m.p10, strict=True))
    assert all(b >= a for a, b in zip(o.p50, m.p50, strict=True))
    assert all(b >= a for a, b in zip(o.p90, m.p90, strict=True))
    assert m.shortfall_probability <= o.shortfall_probability and m.p50 != o.p50


def test_spending_less_by_dollars_a_week_changes_only_the_days_named(forecast: Forecast) -> None:
    first = forecast.start.first_day + timedelta(days=7)
    last = first + timedelta(days=6)
    change = SpendingChange(first, last, per_week_cents=7000)  # 10 dollars a day
    result = run_scenario(forecast, Scenario(spending=change))
    o, m = result.original, result.modified
    for k, day in enumerate(o.days):
        if day < first:
            assert m.p50[k] == o.p50[k]  # nothing changes before the period
    assert m.p50[-1] >= o.p50[-1] and m.p50 != o.p50


def test_a_planned_purchase_in_a_scenario_lowers_every_future_by_its_amount(
    forecast: Forecast,
) -> None:
    day = forecast.start.first_day + timedelta(days=5)
    result = run_scenario(forecast, Scenario(extras=(ExtraItem(day, -40000, "Laptop repair"),)))
    o, m = result.original, result.modified
    for k, d in enumerate(o.days):
        shift = -40000 if d >= day else 0
        assert (m.p10[k], m.p50[k], m.p90[k]) == (
            o.p10[k] + shift,
            o.p50[k] + shift,
            o.p90[k] + shift,
        )
        assert m.median_without[k] == o.median_without[k] + shift


def test_a_planned_deposit_in_a_scenario_raises_every_future(forecast: Forecast) -> None:
    day = forecast.start.first_day + timedelta(days=5)
    result = run_scenario(forecast, Scenario(extras=(ExtraItem(day, 90000, "Gift"),)))
    assert all(
        m == o + (90000 if d >= day else 0)
        for d, o, m in zip(
            result.original.days, result.original.p50, result.modified.p50, strict=True
        )
    )


def test_delaying_money_in_moves_a_planned_deposit_later_by_exactly_that_many_days(
    db: Path,
) -> None:
    deposit_day = date(2026, 10, 10)
    base = with_planned(db, [(deposit_day, 2000000, "Family"), (date(2026, 10, 12), -50000, "Fee")])
    result = run_scenario(base, Scenario(delay_money_in_days=5))
    o, m = result.original, result.modified
    for k, d in enumerate(o.days):
        lower = -2000000 if deposit_day <= d < deposit_day + timedelta(days=5) else 0
        assert m.p10[k] == o.p10[k] + lower and m.p90[k] == o.p90[k] + lower
        assert m.median_without[k] == o.median_without[k] + lower


def test_delaying_money_in_does_not_delay_a_payment(db: Path) -> None:
    base = with_planned(db, [(date(2026, 10, 12), -50000, "Fee")])
    result = run_scenario(base, Scenario(delay_money_in_days=7))
    assert result.modified.p50 == result.original.p50


def test_a_deposit_delayed_past_the_last_day_is_not_in_the_forecast(db: Path) -> None:
    base = with_planned(db, [(date(2026, 10, 25), 2000000, "Family")])
    assert base.start.last_day == date(2026, 10, 26)
    result = run_scenario(base, Scenario(delay_money_in_days=5))
    assert result.modified.p50[-1] == result.original.p50[-1] - 2000000


def test_moving_a_bill_changes_only_the_days_between_the_old_and_new_date(db: Path) -> None:
    old, new = date(2026, 10, 10), date(2026, 10, 16)
    base = with_planned(db, [(old, -90000, "Insurance")])
    (item,) = [i for i in base.items if i.name == "Insurance"]
    result = run_scenario(base, Scenario(moved=(MovedBill(item.series_key, old, new),)))
    o, m = result.original, result.modified
    for k, d in enumerate(o.days):
        gain = 90000 if old <= d < new else 0
        assert m.p10[k] == o.p10[k] + gain and m.p50[k] == o.p50[k] + gain


def test_a_detected_bill_that_is_moved_stays_where_it_is_put(forecast: Forecast) -> None:
    (phone,) = [i for i in forecast.items if i.name == "Phone Co"]
    new = phone.day + timedelta(days=3)
    result = run_scenario(forecast, Scenario(moved=(MovedBill(phone.series_key, phone.day, new),)))
    assert result.modified.p50 != result.original.p50  # no longer scattered by its past offsets


def test_a_bill_cannot_be_moved_that_is_not_in_the_forecast_or_into_the_past(
    forecast: Forecast,
) -> None:
    (phone,) = [i for i in forecast.items if i.name == "Phone Co"]
    with pytest.raises(ValueError):
        run_scenario(forecast, Scenario(moved=(MovedBill("nothing", phone.day, phone.day),)))
    with pytest.raises(ValueError):
        run_scenario(
            forecast,
            Scenario(
                moved=(
                    MovedBill(
                        phone.series_key,
                        phone.day + timedelta(days=1),
                        phone.day + timedelta(days=2),
                    ),
                )
            ),
        )
    before = forecast.start.first_day - timedelta(days=1)
    with pytest.raises(ValueError):
        run_scenario(forecast, Scenario(moved=(MovedBill(phone.series_key, phone.day, before),)))


def test_changes_combine(forecast: Forecast) -> None:
    first, last = forecast.start.first_day, forecast.start.last_day
    scenario = Scenario(
        spending=SpendingChange(first, last, percent=10.0),
        extras=(ExtraItem(first + timedelta(days=2), -20000, "Shoes"),),
        no_other_money=True,
    )
    result = run_scenario(forecast, scenario)
    assert result.modified.p50 != result.original.p50
    assert len(result.changes) == 3


def test_the_differences_are_reported_plainly(forecast: Forecast) -> None:
    day = forecast.start.first_day + timedelta(days=2)
    result = run_scenario(forecast, Scenario(extras=(ExtraItem(day, -900000, "Big purchase"),)))
    assert result.shortfall_change == pytest.approx(
        result.modified.shortfall_probability - result.original.shortfall_probability
    )
    assert (
        result.lowest_change
        == result.modified.median_lowest_cents - result.original.median_lowest_cents
    )
    assert result.lowest_change < 0 and result.final_change == -900000


def test_the_changes_are_described_in_words(forecast: Forecast) -> None:
    first, last = forecast.start.first_day, forecast.start.last_day
    scenario = Scenario(
        spending=SpendingChange(first, last, per_week_cents=2500),
        extras=(ExtraItem(first, -20000, "Shoes"),),
        delay_money_in_days=4,
        no_other_money=True,
    )
    text = " | ".join(run_scenario(forecast, scenario).changes)
    assert "25.00 a week" in text and "Shoes" in text and "200.00" in text
    assert "4 days" in text and "no other money" in text.lower()


@pytest.mark.parametrize(
    "build",
    [
        lambda a, b: SpendingChange(a, b),  # neither a percent nor an amount
        lambda a, b: SpendingChange(a, b, percent=10.0, per_week_cents=100),  # both
        lambda a, b: SpendingChange(a, b, percent=0.0),
        lambda a, b: SpendingChange(a, b, percent=101.0),
        lambda a, b: SpendingChange(a, b, per_week_cents=-1),
        lambda a, b: SpendingChange(b, a, percent=10.0),  # the end before the start
    ],
)
def test_a_bad_spending_change_is_refused(build) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        build(date(2026, 10, 1), date(2026, 10, 9))


def test_other_bad_changes_are_refused() -> None:
    with pytest.raises(ValueError):
        Scenario(delay_money_in_days=-1)
    with pytest.raises(ValueError):
        Scenario(delay_money_in_days=61)
    with pytest.raises(ValueError):
        ExtraItem(date(2026, 10, 1), 0, "Nothing")
    with pytest.raises(ValueError):
        ExtraItem(date(2026, 10, 1), -100, "   ")


def test_a_scenario_needs_the_futures(tmp_path: Path) -> None:
    c = connect(tmp_path / "n.db")
    insert(c, date(2026, 9, 1), -2000, "pos purchase", CARD.format("Shop"))
    plain = build_forecast(c, ACCOUNT, "weekday", futures=0)
    assert plain is not None
    with pytest.raises(ValueError):
        run_scenario(plain, Scenario())
