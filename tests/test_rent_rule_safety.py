"""A typed rent rule must never crash the replay (and so the Recurring page). Made-up payments.

A rule that allows a common small amount used to pull in every small e-Transfer of that amount;
there were more such payments than months, so every rent cycle looked paid and the replay raised
"no window found". Now the amounts under $100 that a rule lists are not used to find e-Transfers
(the series keeps its own payments), and the next window is always found."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from lookahead import recurring_store as rs
from lookahead.db import connect
from lookahead.rent import RENT_FLOOR_CENTS, RentRule, next_window_day, window

ACCOUNT = "a"
ETRANSFER = "Free Interac E-Transfer "
SQL = (
    "INSERT INTO transactions (account, posted_date, amount_cents, description,"
    " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
    " VALUES (?, ?, ?, 'withdrawal', ?, 0, ?, 'f', '2026-10-01')"
)


def monthly(year: int, month: int, count: int, day: int = 2) -> list[date]:
    out = []
    for i in range(count):
        y, m = year + (month - 1 + i) // 12, (month - 1 + i) % 12 + 1
        out.append(date(y, m, day + (i % 3)))
    return out


def make_db(path: Path, payments: list[tuple[date, int]]) -> sqlite3.Connection:
    conn = connect(path)
    for n, (day, cents) in enumerate(payments):
        conn.execute(SQL, (ACCOUNT, day.isoformat(), -cents, ETRANSFER, f"fp{n:04d}"))
    conn.commit()
    return conn


def rent_and_small(path: Path, small_count: int = 12) -> sqlite3.Connection:
    rent = [(d, 125000) for d in monthly(2025, 1, 10)]
    small = [(date(2025, 1, 10) + timedelta(days=9 * i), 2500) for i in range(small_count)]
    return make_db(path, sorted(rent + small))


def give_rule(conn: sqlite3.Connection, amounts: tuple[int, ...], first: int = 30, last: int = 4):
    series, _ = rs.series_as_of(conn, ACCOUNT)
    target = max(series, key=lambda s: abs(s.expected_cents))
    rs.set_rent_rule(conn, ACCOUNT, target, RentRule(amounts, first, last), date(2026, 1, 5))
    return target


def test_a_rule_that_also_allows_a_common_small_amount_does_not_crash_the_replay(
    tmp_path: Path,
) -> None:
    conn = rent_and_small(tmp_path / "t.db")
    give_rule(conn, (125000, 2500))
    series, _ = rs.series_as_of(conn, ACCOUNT)  # used to raise "no window found"
    rented = [s for s in series if rs.rent_rule_of(s) is not None]
    assert len(rented) == 1 and rented[0].next_expected >= date(2025, 10, 1)
    assert rented[0].expected_cents == -125000  # the larger allowed amount


def test_the_small_amount_is_not_used_to_find_rent_among_the_e_transfers(tmp_path: Path) -> None:
    conn = rent_and_small(tmp_path / "t.db")
    target = give_rule(conn, (125000, 2500))
    series, _ = rs.series_as_of(conn, ACCOUNT)
    (rented,) = [s for s in series if s.id == target.id]
    marks = ",".join("?" * len(rented.evidence))
    rows = conn.execute(
        f"SELECT amount_cents FROM transactions WHERE fingerprint IN ({marks})", rented.evidence
    )
    assert {abs(r[0]) for r in rows} == {125000}  # the twelve $25 e-Transfers are not rent payments


def test_a_rule_of_only_small_amounts_matches_nothing_beyond_its_own_series(tmp_path: Path) -> None:
    payments = [(d, 4000) for d in monthly(2025, 1, 8)]
    extra = [(date(2025, 1, 20) + timedelta(days=7 * i), 4000) for i in range(30)]
    conn = make_db(tmp_path / "t.db", sorted(payments + extra))
    series, _ = rs.series_as_of(conn, ACCOUNT)
    target = max(series, key=lambda s: len(s.evidence))
    rs.set_rent_rule(conn, ACCOUNT, target, RentRule((4000,), 1, 4), date(2026, 1, 5))
    series, _ = rs.series_as_of(conn, ACCOUNT)  # must not raise
    (rented,) = [s for s in series if s.id == target.id]
    assert len(rented.evidence) <= len(target.evidence) + 1  # no vacuuming of the thirty others


def test_weekly_payments_at_an_allowed_amount_above_the_floor_do_not_crash_either(
    tmp_path: Path,
) -> None:
    """More payments than months: every cycle looks paid. The next window is still found."""
    weekly = [(date(2025, 1, 6) + timedelta(days=7 * i), 15000) for i in range(60)]
    conn = make_db(tmp_path / "t.db", weekly)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    target = max(series, key=lambda s: len(s.evidence))
    rs.set_rent_rule(conn, ACCOUNT, target, RentRule((15000,), 28, 3), date(2026, 3, 1))
    series, _ = rs.series_as_of(conn, ACCOUNT)  # must not raise
    assert any(rs.rent_rule_of(s) is not None for s in series)


def test_the_next_window_is_always_found_even_if_every_cycle_looks_paid() -> None:
    rule = RentRule((125000,), 30, 4)
    as_of = date(2025, 10, 15)
    paid = {window(rule, as_of.year, as_of.month + back)[0] for back in range(-3, 6)}
    found = next_window_day(rule, as_of, paid)  # used to raise ValueError
    assert found >= as_of


def test_with_nothing_paid_the_next_window_is_the_first_one_still_ahead() -> None:
    rule = RentRule((125000,), 30, 4)
    assert next_window_day(rule, date(2025, 10, 15), set()) == date(2025, 10, 30)
    assert next_window_day(rule, date(2025, 10, 2), set()) == date(2025, 10, 2)  # inside a window


def test_the_floor_is_one_hundred_dollars() -> None:
    assert RENT_FLOOR_CENTS == 10_000
