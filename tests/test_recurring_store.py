"""Tests for step 3a-iii: stored recurring series and the dated decision log.
Made-up rows in a temporary database."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect, connect_readonly
from lookahead.recurring_store import (
    Stored,
    add_series,
    confirm,
    current_series,
    edit,
    end,
    load_decisions,
    load_series,
    refresh,
    reject,
)

ACCOUNT = "test"
CARD = "Opos {:<20}toron "


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:03d}"),
        )


def monthly(conn: sqlite3.Connection, name: str, day: int, months: range, cents: int) -> None:
    for m in months:
        insert(conn, shift_to_business_day(monthly_date(2026, m, day), "later"), cents,
               "bill payment", f"{name} ")  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    monthly(c, "Phone Co", 10, range(1, 7), -6500)
    monthly(c, "Gym Club", 5, range(1, 7), -4000)
    for m in range(1, 7):  # same-amount e-Transfers on the 1st: a candidate
        insert(c, shift_to_business_day(date(2026, m, 1), "later"), -75000, "withdrawal",
               "Free Interac E-Transfer ")  # fmt: skip
    insert(c, date(2026, 6, 20), -2500, "pos purchase", CARD.format("Bookshop"))
    return c


def by_name(series: list[Stored]) -> dict[str | None, Stored]:
    return {s.name: s for s in series}


def test_refresh_stores_detected_series_with_fingerprints_as_evidence(
    conn: sqlite3.Connection,
) -> None:
    series = refresh(conn, ACCOUNT)
    assert {s.status for s in series} == {"detected"} and len(series) == 3
    phone = by_name(series)["Phone Co"]
    assert phone.evidence == tuple(f"fp{n:03d}" for n in range(1, 7))
    assert (phone.cadence, phone.expected_cents, phone.last_paid) == (
        "monthly", -6500, date(2026, 6, 10),
    )  # fmt: skip
    assert load_series(conn, ACCOUNT) == series  # what the pages read


def test_confirm_reject_and_the_latest_one_wins(conn: sqlite3.Connection) -> None:
    series = by_name(refresh(conn, ACCOUNT))
    confirm(conn, ACCOUNT, series["Phone Co"], date(2026, 6, 21))
    reject(conn, ACCOUNT, series["Gym Club"].key, date(2026, 6, 21))
    got = by_name(refresh(conn, ACCOUNT))
    assert (got["Phone Co"].status, got["Gym Club"].status) == ("confirmed", "rejected")
    assert got[None].status == "detected"  # the e-Transfer candidate: untouched
    confirm(conn, ACCOUNT, got["Gym Club"], date(2026, 6, 22))  # changed my mind
    assert by_name(refresh(conn, ACCOUNT))["Gym Club"].status == "confirmed"


def test_an_edit_holds_until_the_next_payment_posts(conn: sqlite3.Connection) -> None:
    phone = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    assert phone.next_expected == date(2026, 7, 10)
    edit(conn, ACCOUNT, phone.key, next_date=date(2026, 7, 15), amount_cents=-7000,
         when=date(2026, 6, 21))  # fmt: skip
    edited = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    assert (edited.next_expected, edited.expected_cents, edited.edited) == (
        date(2026, 7, 15), -7000, True,
    )  # fmt: skip
    insert(conn, date(2026, 7, 10), -6500, "bill payment", "Phone Co ")  # the July payment
    after = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    assert (after.next_expected, after.expected_cents, after.edited) == (
        date(2026, 8, 10), -6500, False,
    )  # fmt: skip
    with pytest.raises(ValueError, match="nothing to edit"):
        edit(conn, ACCOUNT, phone.key)


def test_ended_until_a_later_payment(conn: sqlite3.Connection) -> None:
    gym = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    end(conn, ACCOUNT, gym.key, date(2026, 6, 21))
    assert not by_name(refresh(conn, ACCOUNT))["Gym Club"].active
    insert(conn, date(2026, 7, 6), -4000, "bill payment", "Gym Club ")
    assert by_name(refresh(conn, ACCOUNT))["Gym Club"].active


def test_a_series_added_by_hand(conn: sqlite3.Connection) -> None:
    key = add_series(conn, ACCOUNT, "Family support", "in", "monthly", date(2026, 7, 3), 150000,
                     when=date(2026, 6, 21))  # fmt: skip
    added = by_name(refresh(conn, ACCOUNT))["Family support"]
    assert (added.key, added.status, added.kind) == (key, "user-added", "user")
    assert (added.expected_cents, added.direction, added.evidence) == (150000, "in", ())
    assert added.next_expected == date(2026, 7, 3)
    out = add_series(conn, ACCOUNT, "Tuition", "out", "yearly", date(2026, 9, 1), 250000)
    assert by_name(refresh(conn, ACCOUNT))["Tuition"].expected_cents == -250000 and out != key
    reject(conn, ACCOUNT, key, date(2026, 6, 25))  # removing one I added
    assert by_name(refresh(conn, ACCOUNT))["Family support"].status == "rejected"
    for bad in (("", "in", "monthly"), ("X", "sideways", "monthly"), ("X", "in", "daily")):
        with pytest.raises(ValueError):
            add_series(conn, ACCOUNT, bad[0], bad[1], bad[2], date(2026, 7, 1), 100)


def test_a_confirmed_series_stays_if_detection_loses_it(conn: sqlite3.Connection) -> None:
    gym = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    confirm(conn, ACCOUNT, gym, date(2026, 6, 21))
    with conn:  # most of its history disappears: detection alone no longer finds it
        conn.execute("DELETE FROM transactions WHERE sub_description = 'Gym Club '"
                     " AND posted_date < '2026-05-01'")  # fmt: skip
    kept = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    assert (kept.status, kept.cadence, kept.expected_cents) == ("confirmed", "monthly", -4000)
    assert kept.evidence == () and kept.next_expected >= date(2026, 6, 20)


def test_replay_uses_only_decisions_made_by_the_date(conn: sqlite3.Connection) -> None:
    series = by_name(refresh(conn, ACCOUNT))
    confirm(conn, ACCOUNT, series["Phone Co"], date(2026, 6, 25))
    reject(conn, ACCOUNT, series["Gym Club"].key, date(2026, 7, 2))
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions"
    ).fetchall()
    from lookahead.flows import Txn

    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    prints = {r[0]: r[5] for r in raw}

    def at(day: date) -> dict[str | None, str]:
        got = current_series(txns, prints, load_decisions(conn, ACCOUNT, as_of=day), day)
        return {s.name: s.status for s in got}

    assert at(date(2026, 6, 24)) == {"Phone Co": "detected", "Gym Club": "detected",
                                     None: "detected"}  # fmt: skip
    assert at(date(2026, 6, 30))["Phone Co"] == "confirmed"
    assert at(date(2026, 6, 30))["Gym Club"] == "detected"
    assert at(date(2026, 7, 5))["Gym Club"] == "rejected"
    # and only rows posted by the date: in March there are too few payments for any series
    assert at(date(2026, 2, 20)) == {}


def test_an_older_database_has_no_series(tmp_path: Path, conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE recurring_series")
    conn.commit()
    conn.close()
    assert load_series(connect_readonly(tmp_path / "t.db"), ACCOUNT) == []
