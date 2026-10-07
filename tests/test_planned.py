"""Tests for planned items: money in or out that I enter, placed exactly on its date.
Made-up rows in a temporary database."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.planned import PlannedItem, add_planned, planned_as_of, remove_planned

ACCOUNT = "test"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "p.db")


def test_a_planned_payment_is_money_out_and_a_planned_deposit_is_money_in(
    conn: sqlite3.Connection,
) -> None:
    add_planned(
        conn, ACCOUNT, date(2027, 1, 5), -1900000, "Tuition, winter term", date(2026, 10, 6)
    )
    add_planned(conn, ACCOUNT, date(2027, 1, 2), 2000000, "Family, for the term", date(2026, 10, 6))
    items = planned_as_of(conn, ACCOUNT)
    assert [(i.day, i.cents, i.description) for i in items] == [
        (date(2027, 1, 2), 2000000, "Family, for the term"),
        (date(2027, 1, 5), -1900000, "Tuition, winter term"),
    ]  # by date
    assert all(isinstance(i, PlannedItem) for i in items)
    assert [i.is_deposit for i in items] == [True, False]


def test_an_item_is_ignored_before_the_day_it_was_entered(conn: sqlite3.Connection) -> None:
    """A replay as of an earlier date must not know about it (rule zero)."""
    add_planned(conn, ACCOUNT, date(2026, 11, 3), -50000, "Concert", date(2026, 10, 10))
    assert planned_as_of(conn, ACCOUNT, date(2026, 10, 9)) == []
    assert [i.description for i in planned_as_of(conn, ACCOUNT, date(2026, 10, 10))] == ["Concert"]
    assert [i.description for i in planned_as_of(conn, ACCOUNT)] == ["Concert"]


def test_a_removed_item_is_gone_from_the_day_it_was_removed(conn: sqlite3.Connection) -> None:
    item_id = add_planned(conn, ACCOUNT, date(2026, 11, 3), -50000, "Concert", date(2026, 10, 10))
    remove_planned(conn, ACCOUNT, item_id, date(2026, 10, 20))
    assert [i.description for i in planned_as_of(conn, ACCOUNT, date(2026, 10, 15))] == ["Concert"]
    assert planned_as_of(conn, ACCOUNT, date(2026, 10, 20)) == []
    assert planned_as_of(conn, ACCOUNT) == []


def test_changing_an_item_is_removing_it_and_entering_it_again(conn: sqlite3.Connection) -> None:
    first = add_planned(conn, ACCOUNT, date(2026, 11, 3), -50000, "Concert", date(2026, 10, 10))
    remove_planned(conn, ACCOUNT, first, date(2026, 10, 12))
    add_planned(conn, ACCOUNT, date(2026, 11, 3), -65000, "Concert", date(2026, 10, 12))
    assert [i.cents for i in planned_as_of(conn, ACCOUNT, date(2026, 10, 11))] == [-50000]
    assert [i.cents for i in planned_as_of(conn, ACCOUNT, date(2026, 10, 12))] == [-65000]


def test_items_belong_to_one_account(conn: sqlite3.Connection) -> None:
    add_planned(conn, "one", date(2026, 11, 3), -50000, "Concert", date(2026, 10, 10))
    assert planned_as_of(conn, "two") == []
    item_id = planned_as_of(conn, "one")[0].item_id
    with pytest.raises(ValueError):
        remove_planned(conn, "two", item_id, date(2026, 10, 11))  # not that account's item


def test_a_bad_item_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        add_planned(conn, ACCOUNT, date(2026, 11, 3), 0, "Nothing", date(2026, 10, 10))
    with pytest.raises(ValueError):
        add_planned(conn, ACCOUNT, date(2026, 11, 3), -100, "   ", date(2026, 10, 10))
    with pytest.raises(ValueError):
        add_planned(conn, ACCOUNT, date(2026, 11, 3), -100, "x" * 81, date(2026, 10, 10))
    with pytest.raises(ValueError):
        remove_planned(conn, ACCOUNT, 999, date(2026, 10, 10))  # no such item
    assert planned_as_of(conn, ACCOUNT) == []


def test_removing_twice_is_refused(conn: sqlite3.Connection) -> None:
    item_id = add_planned(conn, ACCOUNT, date(2026, 11, 3), -50000, "Concert", date(2026, 10, 10))
    remove_planned(conn, ACCOUNT, item_id, date(2026, 10, 12))
    with pytest.raises(ValueError):
        remove_planned(conn, ACCOUNT, item_id, date(2026, 10, 13))


def test_the_description_is_tidied(conn: sqlite3.Connection) -> None:
    add_planned(
        conn, ACCOUNT, date(2026, 11, 3), -50000, "  Concert   tickets ", date(2026, 10, 10)
    )
    assert planned_as_of(conn, ACCOUNT)[0].description == "Concert tickets"


# --- Received: a real transaction matches the item, so it is not counted twice ---


def post(
    conn: sqlite3.Connection, day: date, cents: int, imported: str = "2026-10-15T09:00:00"
) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, 'credit memo', 'Family ', 0, ?, 'f', ?)",
            (ACCOUNT, day.isoformat(), cents, f"rx{n:03d}", imported),
        )


def received(conn: sqlite3.Connection, as_of: date | None = None) -> set[str]:
    from lookahead.planned import received_planned

    items = planned_as_of(conn, ACCOUNT, as_of)
    ids = received_planned(conn, ACCOUNT, items, as_of)
    return {i.description for i in items if i.item_id in ids}


def test_a_matching_deposit_marks_the_item_received(conn: sqlite3.Connection) -> None:
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 2000000, "Family", date(2026, 10, 7))
    assert received(conn) == set()  # nothing posted yet
    post(conn, date(2026, 10, 14), 1950000)  # two days late, 2.5 percent less
    assert received(conn) == {"Family"}


def test_the_amount_must_be_within_five_percent(conn: sqlite3.Connection) -> None:
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 2000000, "Family", date(2026, 10, 7))
    post(conn, date(2026, 10, 12), 1899999)  # 5.00005 percent under
    assert received(conn) == set()
    post(conn, date(2026, 10, 12), 1900000)  # exactly 5 percent under
    assert received(conn) == {"Family"}


def test_the_date_must_be_within_seven_days(conn: sqlite3.Connection) -> None:
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 2000000, "Family", date(2026, 10, 7))
    post(conn, date(2026, 10, 20), 2000000)  # eight days after
    post(conn, date(2026, 10, 4), 2000000)  # eight days before
    assert received(conn) == set()
    post(conn, date(2026, 10, 19), 2000000)  # exactly seven days after
    assert received(conn) == {"Family"}


def test_a_payment_does_not_match_a_deposit_and_the_other_way_round(
    conn: sqlite3.Connection,
) -> None:
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 2000000, "Family", date(2026, 10, 7))
    add_planned(conn, ACCOUNT, date(2026, 10, 12), -50000, "Concert", date(2026, 10, 7))
    post(conn, date(2026, 10, 12), -2000000)  # same size, wrong direction
    post(conn, date(2026, 10, 12), 50000)  # same size, wrong direction
    assert received(conn) == set()
    post(conn, date(2026, 10, 13), -51000)
    assert received(conn) == {"Concert"}


def test_a_transaction_imported_before_the_item_was_entered_does_not_count(
    conn: sqlite3.Connection,
) -> None:
    """An older row cannot be the money I was waiting for."""
    post(conn, date(2026, 10, 12), 2000000, imported="2026-10-06T09:00:00")
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 2000000, "Family", date(2026, 10, 7))
    assert received(conn) == set()
    post(
        conn, date(2026, 10, 12), 2000000, imported="2026-10-07T08:00:00"
    )  # the day it was entered
    assert received(conn) == {"Family"}


def test_one_transaction_marks_only_one_item(conn: sqlite3.Connection) -> None:
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 100000, "First", date(2026, 10, 7))
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 100000, "Second", date(2026, 10, 7))
    post(conn, date(2026, 10, 12), 100000)
    assert len(received(conn)) == 1  # counted once, never twice
    post(conn, date(2026, 10, 13), 100000)
    assert received(conn) == {"First", "Second"}


def test_the_nearest_item_gets_the_transaction(conn: sqlite3.Connection) -> None:
    add_planned(conn, ACCOUNT, date(2026, 10, 8), 100000, "Early", date(2026, 10, 7))
    add_planned(conn, ACCOUNT, date(2026, 10, 14), 100000, "Late", date(2026, 10, 7))
    post(conn, date(2026, 10, 13), 100000)
    assert received(conn) == {"Late"}  # one day away, not five


def test_a_replay_ignores_rows_posted_after_its_date(conn: sqlite3.Connection) -> None:
    """Rule zero: a forecast for an earlier date cannot know a later posting."""
    add_planned(conn, ACCOUNT, date(2026, 10, 12), 2000000, "Family", date(2026, 10, 7))
    post(conn, date(2026, 10, 14), 2000000)
    assert received(conn, as_of=date(2026, 10, 13)) == set()
    assert received(conn, as_of=date(2026, 10, 14)) == {"Family"}


def test_a_received_item_is_not_placed_in_the_forecast(conn: sqlite3.Connection) -> None:
    from lookahead.forecast.path import build_forecast

    for m in range(1, 8):  # some history: a card purchase on the 3rd, the last on Jul 3
        post(conn, date(2026, m, 3), -2000, imported="2026-07-04T09:00:00")
    add_planned(conn, ACCOUNT, date(2026, 7, 20), -50000, "Concert", date(2026, 7, 4))
    before = build_forecast(conn, ACCOUNT, "weekday")
    assert before is not None
    assert [i.name for i in before.items if i.series_key.startswith("planned|")] == ["Concert"]
    assert before.received == frozenset()
    post(conn, date(2026, 7, 18), -49000, imported="2026-07-19T09:00:00")  # the real payment
    after = build_forecast(conn, ACCOUNT, "weekday")
    assert after is not None
    assert not [i for i in after.items if i.series_key.startswith("planned|")]  # not twice
    assert [p.description for p in after.planned] == ["Concert"]  # still listed
    assert {p.description for p in after.planned if p.item_id in after.received} == {"Concert"}
