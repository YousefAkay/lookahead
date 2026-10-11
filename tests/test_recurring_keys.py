"""What a series key change does to my saved decisions (DECISIONS.md, 2026-10-12). Made-up
purchases only.

A weekly habit among other purchases at one merchant is found as a cluster of purchases of about
one amount, and its key ends in the median of that amount. The median moves as purchases arrive,
so the key moves. Decisions are stored against the key. These tests show what happens today;
those that describe what should happen are marked as known failures until it is fixed (a decision
of mine should keep applying to the habit it was made about, and a habit should not be counted
twice)."""

import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead import recurring_store as rs
from lookahead.db import connect
from lookahead.flows import Txn
from lookahead.recurring import detect

CARD = "Opos {:<20}toron "
EARLY, LATER = date(2026, 3, 31), date(2026, 5, 5)


def purchases() -> list[Txn]:
    """A weekly purchase of about $12 at one cafe, with other purchases of other amounts there
    so that the cafe as a whole is not one series: only the cluster of about $12 is."""
    rng = random.Random(3)
    rows, row_id = [], 1
    for i in range(30):
        amount = -(1200 + rng.randint(-60, 60))
        rows.append(Txn(row_id, date(2026, 1, 6) + timedelta(days=7 * i), amount, "pos purchase",
                        CARD.format("Corner Cafe")))  # fmt: skip
        row_id += 1
    for i in range(16):
        day = date(2026, 1, 8) + timedelta(days=int(13.1 * i) + 1)
        amount = -rng.choice([350, 475, 520, 830, 910, 2300, 2750, 3100])
        rows.append(Txn(row_id, day, amount, "pos purchase", CARD.format("Corner Cafe")))
        row_id += 1
    return rows


ROWS = purchases()
FINGERPRINT = {t.row_id: f"fp{t.row_id}" for t in ROWS}


def series_on(day: date, decisions: list[rs.Decision]) -> list[rs.Stored]:
    past = [t for t in ROWS if t.posted_date <= day]
    return rs.current_series(past, FINGERPRINT, decisions, day)


def amount_of(key: str) -> str:
    return key.rsplit("|", 1)[-1]


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    c = connect(tmp_path / "d.db")
    yield c
    c.close()


def test_known_the_key_of_a_card_cluster_moves_with_its_median_amount() -> None:
    (early,) = series_on(EARLY, [])
    (later,) = series_on(LATER, [])
    assert early.key != later.key and amount_of(early.key) != amount_of(later.key)
    assert early.key.rsplit("|", 1)[0] == later.key.rsplit("|", 1)[0]  # the same family


def test_known_a_confirmation_made_before_the_key_moved_leaves_two_series_for_one_habit(
    conn,  # type: ignore[no-untyped-def]
) -> None:
    (early,) = series_on(EARLY, [])
    rs.confirm(conn, "a", early, EARLY)
    later = series_on(LATER, rs.load_decisions(conn, "a", LATER))
    assert [s.status for s in later] == ["detected", "confirmed"] or sorted(
        s.status for s in later) == ["confirmed", "detected"]  # fmt: skip
    assert all(s.active for s in later)  # both are placed: the habit is counted twice


@pytest.mark.xfail(strict=True, reason="decisions are stored against a key that moves")
def test_a_confirmation_keeps_applying_to_the_one_habit_after_its_key_moved(
    conn,  # type: ignore[no-untyped-def]
) -> None:
    (early,) = series_on(EARLY, [])
    rs.confirm(conn, "a", early, EARLY)
    later = series_on(LATER, rs.load_decisions(conn, "a", LATER))
    assert [s.status for s in later] == ["confirmed"]  # one series, still confirmed


@pytest.mark.xfail(strict=True, reason="decisions are stored against a key that moves")
@pytest.mark.parametrize("action", ["reject", "end", "rename", "category"])
def test_a_decision_keeps_applying_after_the_key_moved(
    action: str, conn  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    (early,) = series_on(EARLY, [])
    if action == "reject":
        rs.reject(conn, "a", early.key, EARLY)
    elif action == "end":
        rs.end(conn, "a", early.key, EARLY)
    elif action == "rename":
        rs.set_name(conn, "a", early.key, "My cafe", EARLY)
    else:
        rs.set_category(conn, "a", early.key, "Groceries", EARLY)
    (later,) = series_on(LATER, rs.load_decisions(conn, "a", LATER))
    assert (
        later.status == "rejected" if action == "reject"
        else not later.active if action == "end"
        else later.name == "My cafe" if action == "rename"
        else later.category == "Groceries"
    )  # fmt: skip


def test_known_these_decisions_are_lost_today(conn) -> None:  # type: ignore[no-untyped-def]
    """The same cases, stating what happens now: nothing is applied to the new key."""
    (early,) = series_on(EARLY, [])
    rs.reject(conn, "a", early.key, EARLY)
    rs.end(conn, "a", early.key, EARLY)
    rs.set_name(conn, "a", early.key, "My cafe", EARLY)
    rs.set_category(conn, "a", early.key, "Groceries", EARLY)
    assert series_on(EARLY, rs.load_decisions(conn, "a", EARLY))[0].status == "rejected"
    (later,) = series_on(LATER, rs.load_decisions(conn, "a", LATER))
    assert (later.status, later.active, later.name, later.category) == (
        "detected", True, "Corner Cafe", None,
    )  # fmt: skip


def test_detection_alone_is_fine_the_key_is_the_only_thing_that_moves() -> None:
    first, second = detect(ROWS, None, as_of=EARLY), detect(ROWS, None, as_of=LATER)
    assert len(first) == len(second) == 1
    assert first[0].cadence == second[0].cadence == "weekly"
