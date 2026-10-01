"""Tests for category rules, defaults and your preferences. Made-up rows only."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.categories import (
    Decision,
    category_by_rules,
    conflicts,
    load_category_preferences,
    set_category_preference,
)
from lookahead.db import connect
from lookahead.flows import Txn
from lookahead.resolve import UserDecisions

D = date(2026, 9, 10)


def txn(i: int, kind: str, cents: int, text: str = " ", day: date = D) -> Txn:
    return Txn(i, day, cents, kind, text)


def card(merchant: str, prefix: str = "Apos", location: str = "Montr") -> str:
    return f"{prefix} {merchant[:20]:<20}{location:<5} "


def decide(*rows: Txn, **kwargs: object) -> dict[int, Decision]:
    return category_by_rules(list(rows), **kwargs)  # type: ignore[arg-type]


# --- By transaction type ---


@pytest.mark.parametrize(
    ("kind", "cents", "text", "category"),
    [
        ("abm withdrawal", -6_000, " ", "Cash"),
        ("service charge", -500, " ", "Fees & interest"),
        ("service charge", 0, " ", "Fees & interest"),
        ("overdraft interest", -312, " ", "Fees & interest"),
        ("overdrawn handling chg.", -500, " ", "Fees & interest"),
        ("customer transfer dr.", -20_000, "Mb-Transfer ", "Transfers"),
        ("withdrawal", -82_000, "Free Interac E-Transfer ", "Transfers"),
        ("deposit", 12_000, "Interac E-Transfer Rtn ", "Transfers"),
        ("credit memo", 300_000, "1/Nadia Haddad ", "Income"),
        ("deposit", 80_000, "Boutique Mile End ", "Income"),
        ("deposit", 12_000, "Free Interac E-Transfer ", "Income"),
    ],
)
def test_category_from_the_transaction_type(
    kind: str, cents: int, text: str, category: str
) -> None:
    got = decide(txn(1, kind, cents, text))[1]
    assert got.category == category and got.source.startswith("transaction type")


@pytest.mark.parametrize(
    ("kind", "cents", "text"),
    [
        ("accounts payable", 4_500, "Ticket Company Refund "),
        ("bill payment", 9_500, "Mb-Hydro-Quebec 55231204"),  # money back from a payee
        ("bill payment", -9_500, "Mb-Hydro-Quebec 55231204"),  # no default for this payee
        ("pos purchase", -1_200, card("Cafe Velo")),
    ],
)
def test_everything_else_is_left_for_the_categorizer(kind: str, cents: int, text: str) -> None:
    got = decide(txn(1, kind, cents, text))[1]
    assert got == Decision(None, "categorizer")


# --- Merchant defaults (HANDOFF section 07) ---


@pytest.mark.parametrize(
    ("merchant", "category"),
    [
        ("Couche-Tard #496", "Groceries"),
        ("Couche-Tard Lasalle", "Groceries"),
        ("Saq #5751", "Groceries"),
        ("Amazon.Ca*7Efhd", "Shopping"),
        ("Amazon Prime*Ab12c", "Subscriptions"),
        ("Dollarama #9543", "Shopping"),
    ],
)
def test_merchant_defaults(merchant: str, category: str) -> None:
    got = decide(txn(1, "pos purchase", -1_000, card(merchant)))[1]
    assert got.category == category and got.source == "merchant default"


def test_a_default_needs_the_whole_word() -> None:
    assert decide(txn(1, "pos purchase", -1_000, card("Saqqara Grill")))[1].category is None


# --- Delivery platforms ---


@pytest.mark.parametrize(
    ("field", "category"),
    [
        ("Dd/Doordashpizzaroma", "Food delivery"),
        ("Ubereatsthaiexpress", "Food delivery"),
        ("Uber Canada/Ubereats", "Food delivery"),  # the alias list's Uber Eats
        ("Instacartmetro", "Groceries"),
        ("Dd/Doordashdashpass", "Subscriptions"),
    ],
)
def test_delivery_platforms(field: str, category: str) -> None:
    got = decide(txn(1, "pos purchase", -2_500, card(field, "Opos", "+1855")))[1]
    assert got.category == category and got.source.startswith("delivery platform")


# --- Corrections and returns inherit the paired purchase's category ---


def test_a_fully_reversed_pair_needs_no_category() -> None:
    text = card("Uber Canada/Ubereats", "Opos", "toron")
    got = decide(txn(1, "pos purchase", -2_500, text), txn(2, "correction", 2_500, text))
    netted = Decision(None, "netted pair, no category needed")
    assert got[1] == netted and got[2] == netted


def test_full_return_and_its_purchase_need_no_category() -> None:
    text = card("Winners #9074")
    buy = txn(1, "pos purchase", -2_999, text)
    ret = txn(2, "pos return", 2_999, text, D + timedelta(days=5))
    got = decide(buy, ret)
    assert got[1].source == got[2].source == "netted pair, no category needed"


def test_partial_return_inherits_from_its_purchase() -> None:
    text = card("Dollarama #9543")
    buy = txn(1, "pos purchase", -5_000, text)
    ret = txn(2, "pos return", 2_000, text, D + timedelta(days=5))
    assert decide(buy, ret)[2] == Decision("Shopping", "inherited from the paired purchase", 1)


def test_partial_return_of_an_undecided_purchase_waits_for_the_categorizer() -> None:
    text = card("Winners #9074")
    buy = txn(1, "pos purchase", -5_000, text)
    ret = txn(2, "pos return", 2_000, text, D + timedelta(days=5))
    assert decide(buy, ret)[2] == Decision(None, "inherits; waiting for the categorizer", 1)


def test_unpaired_return_uses_its_own_merchant() -> None:
    got = decide(txn(1, "pos return", 2_999, card("Dollarama #9543")))[1]
    assert got.category == "Shopping" and got.source == "merchant default"


# --- Your preferences ---


def test_your_preference_beats_a_default() -> None:
    prefs = {"couche tard": "Restaurants & cafés"}
    got = decide(txn(1, "pos purchase", -500, card("Couche-Tard #496")), preferences=prefs)[1]
    assert got == Decision("Restaurants & cafés", "your preference")


def test_your_preference_covers_every_name_in_the_group() -> None:
    mine = UserDecisions(merges={"Gestion": {"gestion immobiliere", "gestion immo plateau"}})
    prefs = {"gestion immobiliere": "Rent & housing"}
    row = txn(1, "bill payment", -115_000, "Gestion Immo Plateau ")
    assert decide(row, user=mine, preferences=prefs)[1].category == "Rent & housing"


def test_type_rules_come_before_preferences() -> None:
    prefs = {"nadia haddad": "Transfers"}
    row = txn(1, "credit memo", 300_000, "1/Nadia Haddad ")
    assert decide(row, preferences=prefs)[1].category == "Income"


# --- Stored preferences, dated ---


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def test_preferences_are_stored_by_name_key_with_a_date(conn: sqlite3.Connection) -> None:
    set_category_preference(conn, "COUCHE-TARD", "Restaurants & cafés", D)
    assert load_category_preferences(conn) == {"couche tard": "Restaurants & cafés"}


def test_the_latest_preference_made_by_a_date_wins(conn: sqlite3.Connection) -> None:
    set_category_preference(conn, "Couche-Tard", "Groceries", D)
    set_category_preference(conn, "Couche-Tard", "Restaurants & cafés", D + timedelta(days=10))
    assert load_category_preferences(conn, as_of=D + timedelta(days=5)) == {
        "couche tard": "Groceries"
    }
    assert load_category_preferences(conn) == {"couche tard": "Restaurants & cafés"}
    assert load_category_preferences(conn, as_of=D - timedelta(days=1)) == {}


def test_unknown_category_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown category"):
        set_category_preference(conn, "Couche-Tard", "Snacks", D)


# --- Flow / category conflicts ---


@pytest.mark.parametrize(
    ("flow", "category", "bad"),
    [
        ("income", "Income", False),
        ("everyday", "Income", True),
        ("income", "Groceries", True),
        ("transfer", "Transfers", False),
        ("refund", "Transfers", False),  # a returned e-Transfer
        ("everyday", "Transfers", True),
        ("transfer", "Rent & housing", False),  # e-Transfer rent (Week 3)
        ("transfer", "Groceries", True),
        ("everyday", "Groceries", False),
        ("everyday", None, False),  # nothing decided yet
    ],
)
def test_conflicts(flow: str, category: str | None, bad: bool) -> None:
    assert conflicts(flow, category) is bad


# --- Uber services ---


@pytest.mark.parametrize(
    ("field", "category"),
    [
        ("Uber* Eats Pending", "Food delivery"),
        ("Uber* One", "Subscriptions"),
        ("Uber* Pending", None),  # unknown service: left for the categorizer
        ("Uber Canada/Ubertrip", None),  # rides: left for the categorizer (Transport)
    ],
)
def test_uber_services(field: str, category: str | None) -> None:
    got = decide(txn(1, "pos purchase", -2_500, card(field, "Opos", "+1855")))[1]
    assert got.category == category
