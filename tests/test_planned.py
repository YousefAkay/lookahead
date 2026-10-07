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
