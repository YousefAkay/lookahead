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
