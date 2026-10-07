"""Tests for 3b step 4: the projected balance path and the forecast built from a database.
Made-up rows only."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.forecast.path import BASELINES, Forecast, build_forecast, project_balance
from lookahead.forecast.schedule import ScheduledItem
from lookahead.forecast.start import ForecastStart
from lookahead.recurring_store import add_series, confirm, refresh, set_rent_rule
from lookahead.rent import RentRule

ACCOUNT = "test"
CARD = "Opos {:<20}toron "


def start_on(last_data_day: date, balance: int = 100000, days: int = 10) -> ForecastStart:
    first = last_data_day + timedelta(days=1)
    return ForecastStart(ACCOUNT, last_data_day, balance, first, first + timedelta(days=days - 1))


def item(day: date, cents: int, key: str = "s") -> ScheduledItem:
    return ScheduledItem(day, cents, key, key, "detected", "")


# --- The arithmetic ---


def test_the_balance_is_the_anchor_plus_scheduled_money_minus_spending() -> None:
    start = start_on(date(2026, 9, 28), balance=100000, days=5)  # 29 Sep ... 3 Oct
    items = [item(date(2026, 10, 1), -6500), item(date(2026, 10, 2), 50000)]
    spending = [100, 200, 300, 400, 500]  # cumulative D(t, h), h = 1..5
    days = project_balance(start, items, spending)
    assert [d.day for d in days] == [date(2026, 9, 29) + timedelta(days=k) for k in range(5)]
    assert [d.balance_cents for d in days] == [
        100000 - 100, 100000 - 200, 100000 - 6500 - 300, 100000 - 6500 + 50000 - 400,
        100000 - 6500 + 50000 - 500,
    ]  # fmt: skip
    assert [d.scheduled_cents for d in days] == [0, 0, -6500, 50000, 0]
    assert [d.spending_cents for d in days] == [100, 100, 100, 100, 100]


def test_the_lowest_balance_and_the_first_day_below_the_buffer() -> None:
    start = start_on(date(2026, 9, 28), balance=30000, days=4)
    days = project_balance(start, [item(date(2026, 10, 1), -15000)], [0, 0, 0, 0])
    forecast = Forecast(start, "test", days, [], False, buffer_cents=20000)
    assert forecast.lowest == (date(2026, 10, 1), 15000)
    assert forecast.first_below_buffer == date(2026, 10, 1)
    assert Forecast(start, "test", days, [], False, buffer_cents=10000).first_below_buffer is None
    assert Forecast(start, "test", days, [], False, buffer_cents=0).first_below_buffer is None


def test_a_balance_exactly_at_the_buffer_is_not_below_it() -> None:
    start = start_on(date(2026, 9, 28), balance=20000, days=2)
    days = project_balance(start, [], [0, 0])
    assert Forecast(start, "t", days, [], False, buffer_cents=20000).first_below_buffer is None


# --- Built from a database ---


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str, bal: int) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, bal, f"fp{n:03d}"),
        )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Six months of a phone bill on the 10th, a card purchase every 4 days, and no income."""
    c = connect(tmp_path / "f.db")
    balance = 200000
    rows: list[tuple[date, int, str, str]] = []
    for m in range(1, 10):
        due = shift_to_business_day(monthly_date(2026, m, 10), "later")
        rows.append((due, -6500, "bill payment", "Phone Co "))
    day = date(2026, 1, 2)
    while day <= date(2026, 9, 28):
        rows.append((day, -2000, "pos purchase", CARD.format("Bookshop")))
        day += timedelta(days=4)
    for when, cents, kind, text in sorted(rows, key=lambda r: r[0]):
        balance += cents
        insert(c, when, cents, kind, text, balance)
    return c


def test_a_forecast_needs_no_confirmations(conn: sqlite3.Connection) -> None:
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert isinstance(forecast, Forecast)
    assert forecast.start.data_last_updated == date(2026, 9, 27)  # the last card purchase
    assert forecast.start.first_day == date(2026, 9, 28) and len(forecast.days) == 30
    assert {i.basis for i in forecast.items} == {"detected"}  # nothing confirmed, all used
    phone_day = shift_to_business_day(monthly_date(2026, 10, 10), "later")
    assert [i.day for i in forecast.items] == [phone_day]  # the phone bill, not the card purchases
    assert forecast.items[0].cents == -6500
    assert not forecast.has_income  # no income series: none is assumed
    assert forecast.days[0].balance_cents <= forecast.start.balance_cents


def test_the_everyday_spending_leaves_out_the_series_payments(conn: sqlite3.Connection) -> None:
    forecast = build_forecast(conn, ACCOUNT, "recent")
    assert forecast is not None
    # card purchases only: 2000 every 4 days over the last 28 days = 7 purchases = 14000
    assert forecast.days[-1].spending_cents > 0
    total = sum(d.spending_cents for d in forecast.days)
    assert total == pytest.approx(14000 / 28 * 30, abs=40)  # about 500 a day for 30 days


def test_an_earlier_as_of_never_sees_later_rows(conn: sqlite3.Connection) -> None:
    """Rule zero: a forecast made as of an earlier date does not change when later rows exist."""
    early = build_forecast(conn, ACCOUNT, "weekday", as_of=date(2026, 8, 31))
    insert(conn, date(2026, 9, 29), -90000, "pos purchase", CARD.format("Laptop"), 1)
    again = build_forecast(conn, ACCOUNT, "weekday", as_of=date(2026, 8, 31))
    assert early is not None and again is not None
    assert early.days == again.days and early.items == again.items
    assert early.start.data_last_updated <= date(2026, 8, 31)


def test_income_that_exists_is_used_and_not_assumed_away(conn: sqlite3.Connection) -> None:
    add_series(conn, ACCOUNT, "Bursary", "in", "monthly", date(2026, 10, 15), 120000,
               date(2026, 9, 1))  # fmt: skip
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None and forecast.has_income
    assert 120000 in [i.cents for i in forecast.items]


def test_rent_is_the_larger_amount_in_its_window_and_a_late_payment_counts(
    tmp_path: Path,
) -> None:
    c = connect(tmp_path / "rent.db")
    bal = 500000
    for when, cents in (
        (date(2026, 7, 29), -79500),
        (date(2026, 8, 30), -79500),
        (date(2026, 10, 12), -82000),
    ):  # the last one is September's, late
        bal += cents
        insert(c, when, cents, "withdrawal", "Free Interac E-Transfer ", bal)
    key = add_series(c, ACCOUNT, "Rent", "out", "monthly", date(2026, 7, 29), 82000,
                     date(2026, 9, 1))  # fmt: skip
    set_rent_rule(c, ACCOUNT, key, RentRule((79500, 82000), 27, 7), date(2026, 10, 13))
    forecast = build_forecast(c, ACCOUNT, "weekday")
    assert forecast is not None
    rent_items = [i for i in forecast.items if i.series_key == key]
    assert [(i.day, i.cents) for i in rent_items] == [(date(2026, 10, 27), -82000)]
    assert rent_items[0].basis == "entered"


def test_the_two_baselines_are_available_and_differ_in_method(conn: sqlite3.Connection) -> None:
    assert set(BASELINES) == {"recent", "weekday"}
    recent = build_forecast(conn, ACCOUNT, "recent")
    weekday = build_forecast(conn, ACCOUNT, "weekday")
    assert recent is not None and weekday is not None and recent.method != weekday.method
    with pytest.raises(ValueError):
        build_forecast(conn, ACCOUNT, "magic")


def test_no_rows_means_no_forecast(tmp_path: Path) -> None:
    assert build_forecast(connect(tmp_path / "e.db"), ACCOUNT, "weekday") is None


def test_a_confirmed_series_is_marked_confirmed(conn: sqlite3.Connection) -> None:
    (phone,) = [s for s in refresh(conn, ACCOUNT) if s.name == "Phone Co"]
    confirm(conn, ACCOUNT, phone, date(2026, 9, 29))
    forecast = build_forecast(conn, ACCOUNT, "weekday", as_of=None)
    assert forecast is not None and {i.basis for i in forecast.items} == {"confirmed"}


# --- Everything that belongs to a series stays out of everyday spending ---


def test_no_payment_of_any_series_is_counted_as_everyday_spending(tmp_path: Path) -> None:
    """Rent sent by e-Transfer in two amounts with a late payment, a detected phone bill and a
    series added by hand: all of their payments sit in the last eight weeks, and none of them
    may appear in the weekday history. Only the card purchases do."""
    c = connect(tmp_path / "all.db")
    rows: list[tuple[date, int, str, str]] = []
    for when, cents in ((date(2026, 1, 1), -79500), (date(2026, 2, 1), -79500),
                        (date(2026, 3, 1), -79500), (date(2026, 4, 1), -79500),
                        (date(2026, 5, 1), -79500), (date(2026, 6, 1), -79500),
                        (date(2026, 7, 1), -82000), (date(2026, 8, 12), -82000),  # late
                        (date(2026, 9, 1), -82000)):  # fmt: skip
        rows.append((when, cents, "withdrawal", "Free Interac E-Transfer "))
    for m in range(1, 10):
        rows.append((shift_to_business_day(monthly_date(2026, m, 10), "later"), -6500,
                     "bill payment", "Phone Co "))  # fmt: skip
    for when, cents in ((date(2026, 8, 20), -4500), (date(2026, 9, 21), -4700)):
        rows.append((when, cents, "pos purchase", CARD.format("Gym Club")))  # hand-added below
    day = date(2026, 1, 2)
    while day <= date(2026, 9, 28):
        rows.append((day, -2000, "pos purchase", CARD.format("Bookshop")))
        day += timedelta(days=4)
    balance = 900000
    for when, cents, kind, text in sorted(rows, key=lambda r: r[0]):
        balance += cents
        insert(c, when, cents, kind, text, balance)
    rent = add_series(
        c, ACCOUNT, "Rent", "out", "monthly", date(2026, 9, 1), 82000, date(2026, 1, 1)
    )
    set_rent_rule(c, ACCOUNT, rent, RentRule((79500, 82000), 30, 7), date(2026, 9, 29))
    add_series(c, ACCOUNT, "Gym Club", "out", "monthly", date(2026, 9, 21), 4600, date(2026, 1, 1))
    forecast = build_forecast(c, ACCOUNT, "recent")
    assert forecast is not None
    series = {s.name: s for s in forecast.series}
    assert len(series["Rent"].evidence) == 9 and len(series["Gym Club"].evidence) == 2
    # Card purchases only: 7 purchases of 2000 in the last 28 days = 500 a day, 30 days ahead.
    assert sum(d.spending_cents for d in forecast.days) == pytest.approx(15000, abs=40)


def test_the_forecast_carries_the_table_of_what_is_in_everyday_spending(
    conn: sqlite3.Connection,
) -> None:
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None
    kinds = {r.type for r in forecast.breakdown}
    assert kinds == {"Card purchases"}  # the phone bill is a series, so it is not in the table
    week = sum(r.per_week for r in forecast.breakdown)
    assert week == pytest.approx(7 * 2000 / 4, rel=0.15)  # about 3500 a week


def test_big_one_time_rows_are_listed_and_not_in_the_forecast(conn: sqlite3.Connection) -> None:
    """A move to savings and a tuition payment in the last 13 weeks: neither is everyday
    spending, so neither is repeated; both are listed as events that are not forecast."""
    insert(conn, date(2026, 9, 3), -600000, "customer transfer dr.", "To savings ", 1)
    insert(conn, date(2026, 9, 20), -1900000, "bill payment", "City University ", 1)
    forecast = build_forecast(conn, ACCOUNT, "recent")
    assert forecast is not None
    assert [(e.day, e.kind, e.cents) for e in forecast.big_events] == [
        (date(2026, 9, 3), "Transfers to my own accounts", -600000),
        (date(2026, 9, 20), "Bill payments (in no series)", -1900000),
    ]
    assert sum(d.spending_cents for d in forecast.days) == pytest.approx(15000, abs=40)
    weekday = build_forecast(conn, ACCOUNT, "weekday")
    assert weekday is not None and {r.type for r in weekday.breakdown} == {"Card purchases"}


def test_fees_are_forecast_at_their_recent_daily_average(conn: sqlite3.Connection) -> None:
    insert(conn, date(2026, 9, 5), -500, "service charge", "Monthly ", 1)
    insert(conn, date(2026, 9, 20), -300, "overdraft interest", "Interest ", 1)
    forecast = build_forecast(conn, ACCOUNT, "recent")
    assert forecast is not None
    assert forecast.fees_per_day == pytest.approx(800 / 28)
    assert sum(d.fees_cents for d in forecast.days) == pytest.approx(800 / 28 * 30, abs=1)
    assert sum(d.spending_cents for d in forecast.days) == pytest.approx(15000, abs=40)  # not fees
    income = sum(i.cents for i in forecast.items)
    spent = sum(d.spending_cents + d.fees_cents for d in forecast.days)
    assert forecast.days[-1].balance_cents == forecast.start.balance_cents + income - spent


def test_the_balance_arithmetic_takes_fees_off_too() -> None:
    start = start_on(date(2026, 9, 28), balance=100000, days=3)
    days = project_balance(start, [], [0, 0, 0], [10, 20, 30])
    assert [d.balance_cents for d in days] == [99990, 99980, 99970]
    assert [d.fees_cents for d in days] == [10, 10, 10]


# --- The simulated futures, built from a database ---


def test_the_forecast_carries_500_simulated_futures(conn: sqlite3.Connection) -> None:
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None and forecast.simulation is not None
    sim = forecast.simulation
    assert sim.futures == 500 and len(sim.p50) == 30 == len(sim.days)
    assert sim.days[0] == forecast.start.first_day
    assert all(a <= b <= c for a, b, c in zip(sim.p10, sim.p50, sim.p90, strict=True))
    assert 0.0 <= sim.shortfall_probability <= 1.0
    assert build_forecast(conn, ACCOUNT, "weekday", futures=0).simulation is None  # type: ignore[union-attr]


def test_simulated_futures_use_irregular_deposits_from_history_but_not_big_ones(
    conn: sqlite3.Connection,
) -> None:
    for week in range(6):  # small deposits now and then
        insert(conn, date(2026, 8, 12) + timedelta(days=7 * week), 4000, "deposit",
               "Interac e-Transfer from a friend ", 1)  # fmt: skip
    insert(conn, date(2026, 9, 5), 900000, "deposit", "Payroll ", 1)  # a big one-time deposit
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None and forecast.simulation is not None
    sim = forecast.simulation
    gap = sim.p50[-1] - sim.median_without[-1]
    assert 0 < gap < 50000  # about eight thousand in a month, not the nine thousand a week
    assert [e.cents for e in forecast.big_events if e.cents > 0] == [900000]


def test_with_no_deposits_in_history_the_two_medians_are_the_same(conn: sqlite3.Connection) -> None:
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None and forecast.simulation is not None
    assert forecast.simulation.p50 == forecast.simulation.median_without


def test_the_simulation_does_not_change_when_later_rows_arrive(conn: sqlite3.Connection) -> None:
    """Rule zero, end to end."""
    early = build_forecast(conn, ACCOUNT, "weekday", as_of=date(2026, 8, 31))
    insert(conn, date(2026, 9, 15), 800000, "deposit", "Interac e-Transfer from a friend ", 1)
    insert(conn, date(2026, 9, 16), -700000, "pos purchase", CARD.format("Laptop"), 1)
    again = build_forecast(conn, ACCOUNT, "weekday", as_of=date(2026, 8, 31))
    assert early is not None and again is not None and early.simulation == again.simulation


def test_a_large_deposit_with_no_matching_outflow_is_listed_and_not_forecast(
    conn: sqlite3.Connection,
) -> None:
    insert(conn, date(2026, 9, 5), 1500000, "credit memo", "Family ", 1)
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None and forecast.simulation is not None
    assert [(e.group, e.cents) for e in forecast.big_events] == [("Large deposit", 1500000)]
    assert [e.cents for e in forecast.large_deposits] == [1500000]
    assert forecast.simulation.p50 == forecast.simulation.median_without  # not in the futures


def test_a_deposit_paid_straight_out_is_a_pass_through_in_the_forecast(
    conn: sqlite3.Connection,
) -> None:
    insert(conn, date(2026, 9, 5), 2000000, "credit memo", "Family ", 1)
    insert(conn, date(2026, 9, 5), -1865900, "bill payment", "City University ", 1)
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None
    assert {e.group for e in forecast.big_events} == {"Pass-through"}
    assert forecast.large_deposits == ()


# --- Planned items: money in or out that I entered ---


def test_a_planned_payment_is_placed_exactly_on_its_day(conn: sqlite3.Connection) -> None:
    from lookahead.planned import add_planned

    base = build_forecast(conn, ACCOUNT, "weekday")
    add_planned(conn, ACCOUNT, date(2026, 10, 10), -50000, "Concert tickets", date(2026, 9, 29))
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert base is not None and forecast is not None
    planned = [i for i in forecast.items if i.series_key.startswith("planned|")]
    assert [(i.day, i.cents, i.name, i.basis) for i in planned] == [
        (date(2026, 10, 10), -50000, "Concert tickets", "entered")
    ]
    for old, new in zip(base.days, forecast.days, strict=True):
        shift = -50000 if new.day >= date(2026, 10, 10) else 0
        assert new.balance_cents == old.balance_cents + shift  # exactly, on and after the day


def test_a_planned_deposit_raises_every_simulated_future_by_the_same_amount(
    conn: sqlite3.Connection,
) -> None:
    from lookahead.planned import add_planned

    base = build_forecast(conn, ACCOUNT, "weekday")
    add_planned(
        conn, ACCOUNT, date(2026, 10, 10), 2000000, "Family, for the term", date(2026, 9, 29)
    )
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert base is not None and forecast is not None
    assert base.simulation is not None and forecast.simulation is not None
    for k, day in enumerate(forecast.simulation.days):
        lift = 2000000 if day >= date(2026, 10, 10) else 0
        assert forecast.simulation.p10[k] == base.simulation.p10[k] + lift
        assert forecast.simulation.p50[k] == base.simulation.p50[k] + lift
        assert forecast.simulation.p90[k] == base.simulation.p90[k] + lift
        assert forecast.simulation.median_without[k] == base.simulation.median_without[k] + lift
    assert forecast.simulation.shortfall_probability <= base.simulation.shortfall_probability


def test_a_planned_item_outside_the_forecast_days_is_not_used(conn: sqlite3.Connection) -> None:
    from lookahead.planned import add_planned

    base = build_forecast(conn, ACCOUNT, "weekday")
    add_planned(conn, ACCOUNT, date(2027, 1, 5), -1900000, "Tuition", date(2026, 9, 29))  # far off
    add_planned(conn, ACCOUNT, date(2026, 9, 20), -82000, "Already happened", date(2026, 9, 29))
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert base is not None and forecast is not None
    assert forecast.days == base.days and forecast.items == base.items


def test_a_forecast_as_of_an_earlier_date_does_not_know_a_planned_item_entered_later(
    conn: sqlite3.Connection,
) -> None:
    from lookahead.planned import add_planned

    early = build_forecast(conn, ACCOUNT, "weekday", as_of=date(2026, 8, 31))
    add_planned(conn, ACCOUNT, date(2026, 9, 10), -900000, "Entered later", date(2026, 9, 15))
    again = build_forecast(conn, ACCOUNT, "weekday", as_of=date(2026, 8, 31))
    assert early is not None and again is not None
    assert early.days == again.days and early.simulation == again.simulation


def test_planned_items_are_listed_on_the_forecast(conn: sqlite3.Connection) -> None:
    from lookahead.planned import add_planned

    add_planned(conn, ACCOUNT, date(2026, 10, 10), -50000, "Concert", date(2026, 9, 29))
    add_planned(conn, ACCOUNT, date(2027, 1, 5), -1900000, "Tuition", date(2026, 9, 29))
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None
    assert [(p.description, p.cents) for p in forecast.planned] == [
        ("Concert", -50000), ("Tuition", -1900000),
    ]  # fmt: skip


def test_the_forecast_lists_the_weeks_its_futures_draw_deposits_from(
    conn: sqlite3.Connection,
) -> None:
    insert(conn, date(2026, 9, 12), 1500000, "credit memo", "Family ", 1)
    for week in range(6):
        insert(conn, date(2026, 8, 12) + timedelta(days=7 * week), 4000, "deposit",
               "Interac e-Transfer from a friend ", 1)  # fmt: skip
    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None and forecast.simulation is not None
    assert len(forecast.deposit_weeks) == forecast.simulation.weeks_drawn_from == 13
    assert sum(w.total for w in forecast.deposit_weeks) == 6 * 4000
    assert [e.cents for w in forecast.deposit_weeks for e in w.set_aside] == [1500000]


def test_the_forecast_keeps_what_its_futures_were_made_from(conn: sqlite3.Connection) -> None:
    from lookahead.forecast.simulate import run

    forecast = build_forecast(conn, ACCOUNT, "weekday")
    assert forecast is not None and forecast.inputs is not None
    assert run(forecast.inputs) == forecast.simulation  # the same inputs give the same futures
    assert forecast.inputs.items == tuple(forecast.items)
    assert forecast.inputs.buffer_cents == forecast.buffer_cents
    assert build_forecast(conn, ACCOUNT, "weekday", futures=0).inputs is None  # type: ignore[union-attr]
