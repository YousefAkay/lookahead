"""Tests for 3b step 1: where a forecast starts (the data's last date, not today), the
balance it starts from, and the safety buffer setting. Made-up rows in a temporary database."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.forecast.start import ForecastStart, forecast_start
from lookahead.settings import DEFAULT_BUFFER_CENTS, safety_buffer_cents, set_safety_buffer_cents

ACCOUNT = "test"


def insert(conn: sqlite3.Connection, day: date, cents: int, balance: int) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, 'pos purchase', 'Shop ', ?, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, balance, f"fp{n:03d}"),
        )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def test_the_forecast_starts_the_day_after_the_data_ends_not_today(
    conn: sqlite3.Connection,
) -> None:
    insert(conn, date(2026, 9, 20), -500, 100000)
    insert(conn, date(2026, 9, 28), -300, 99700)
    start = forecast_start(conn, ACCOUNT)
    assert isinstance(start, ForecastStart)
    assert start.data_last_updated == date(2026, 9, 28)  # whatever today's date is
    assert start.first_day == date(2026, 9, 29)
    assert start.last_day == date(2026, 10, 28)  # 30 days: 29 Sep ... 28 Oct
    assert (start.last_day - start.first_day).days + 1 == 30


def test_the_balance_is_the_one_after_the_last_posting_of_the_last_day(
    conn: sqlite3.Connection,
) -> None:
    # An export lists a day's postings newest first, so the first row stored for that day
    # is its last posting and carries the day's closing balance.
    insert(conn, date(2026, 9, 28), -300, 99700)  # newest of the day
    insert(conn, date(2026, 9, 28), -400, 100000)  # earlier the same day
    insert(conn, date(2026, 9, 27), -100, 100400)
    start = forecast_start(conn, ACCOUNT)
    assert start is not None and start.balance_cents == 99700


def test_an_earlier_date_uses_only_rows_posted_by_then(conn: sqlite3.Connection) -> None:
    """Rule zero: a forecast as of a date never sees later rows. For a replay the data is
    complete through that date, so the forecast starts the day after it."""
    insert(conn, date(2026, 9, 20), -500, 100000)
    insert(conn, date(2026, 9, 28), -300, 99700)
    early = forecast_start(conn, ACCOUNT, as_of=date(2026, 9, 25))
    assert early is not None
    assert (early.data_last_updated, early.balance_cents) == (date(2026, 9, 25), 100000)
    assert early.first_day == date(2026, 9, 26)
    assert forecast_start(conn, ACCOUNT, as_of=date(2026, 9, 1)) is None  # nothing yet


def test_an_account_with_no_rows_has_no_forecast_start(conn: sqlite3.Connection) -> None:
    assert forecast_start(conn, ACCOUNT) is None
    insert(conn, date(2026, 9, 28), -300, 99700)
    assert forecast_start(conn, "another account") is None


def test_the_horizon_can_be_set(conn: sqlite3.Connection) -> None:
    insert(conn, date(2026, 9, 28), -300, 99700)
    start = forecast_start(conn, ACCOUNT, horizon_days=7)
    assert start is not None and start.last_day == date(2026, 10, 5)


# --- The safety buffer ---


def test_the_safety_buffer_defaults_to_200_dollars(conn: sqlite3.Connection) -> None:
    assert DEFAULT_BUFFER_CENTS == 20000
    assert safety_buffer_cents(conn) == 20000


def test_the_safety_buffer_can_be_changed_and_is_kept(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    set_safety_buffer_cents(c, 50000)
    assert safety_buffer_cents(c) == 50000
    c.close()
    assert safety_buffer_cents(connect(tmp_path / "s.db")) == 50000
    set_safety_buffer_cents(connect(tmp_path / "s.db"), 0)  # zero is allowed
    assert safety_buffer_cents(connect(tmp_path / "s.db")) == 0


def test_a_negative_safety_buffer_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        set_safety_buffer_cents(conn, -1)
    assert safety_buffer_cents(conn) == 20000


def test_a_damaged_buffer_setting_falls_back_to_the_default(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('safety_buffer_cents', 'x')")
    assert safety_buffer_cents(conn) == DEFAULT_BUFFER_CENTS
