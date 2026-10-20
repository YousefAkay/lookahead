"""Proposals for recurring settings (lookahead.setting_proposals). Made-up payments only."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead import recurring_store as rs
from lookahead.db import connect
from lookahead.flows import Txn
from lookahead.rent import RentRule
from lookahead.setting_proposals import best_window, has_decision, propose_rent

ETRANSFER = "Free Interac E-Transfer "
ACCOUNT = "a"


def rent_rows(days: list[date], sizes: list[int]) -> list[Txn]:
    pairs = zip(days, sizes, strict=True)
    return [Txn(i + 1, d, -c, "withdrawal", ETRANSFER) for i, (d, c) in enumerate(pairs)]


# ten monthly payments, sent between the 30th and the 4th, the amount rising after the sixth
DAYS = [
    date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3), date(2025, 3, 31), date(2025, 5, 2),
    date(2025, 6, 4), date(2025, 7, 1), date(2025, 8, 2), date(2025, 9, 3), date(2025, 9, 30),
]  # fmt: skip
SIZES = [90000] * 6 + [95000] * 4


def fingerprints(txns: list[Txn]) -> dict[int, str]:
    return {t.row_id: f"fp{t.row_id}" for t in txns}


def proposals_for(
    txns: list[Txn], decisions: list[rs.Decision] | None = None, as_of: date | None = None
):  # type: ignore[no-untyped-def]
    day = as_of or max(t.posted_date for t in txns)
    past = [t for t in txns if t.posted_date <= day]
    fp = fingerprints(txns)
    series = rs.current_series(past, fp, decisions or [], day)
    return propose_rent(series, decisions or [], txns, fp, None, None, day), series


def test_two_sizes_and_a_window_are_proposed_for_rent_sent_by_e_transfer() -> None:
    (p,), _ = proposals_for(rent_rows(DAYS, SIZES))
    assert p.kind == "rent" and p.confidence == "likely"
    assert p.rent == RentRule((90000, 95000), 1, 4)  # covers 8 of the 10 send days
    assert len(p.evidence) == 10 and p.series_id.startswith("sid|")
    assert p.key == f"rent|{p.series_id}" and "$900.00 or $950.00" in p.title


def test_only_the_latest_sizes_are_used_going_forward() -> None:
    spread = [0, 4, 0, 6, 1, 0, 6, 3, 0, 5, 1, 0]
    days = [date(2025, m, 2) + timedelta(days=d) for m, d in zip(range(1, 13), spread, strict=True)]
    sizes = [80000] * 5 + [90000] * 7  # the old size is out of the last six payments
    (p,), _ = proposals_for(rent_rows(days, sizes))
    assert p.rent is not None and p.rent.amounts_cents == (90000,)


def test_the_window_wraps_a_month_end() -> None:
    assert best_window([30, 31, 1, 2, 3, 30, 31, 1, 2, 3], cover=1.0) == (30, 3)
    assert best_window([30, 31, 1, 2, 3, 30, 31, 1, 2, 3]) == (30, 2)  # 80 percent is enough
    assert best_window([5, 5, 5, 6, 6]) == (5, 6)


def test_a_window_needing_more_than_two_weeks_is_not_proposed() -> None:
    assert best_window([1, 8, 15, 22, 29, 3, 10, 17, 24, 6]) is None
    assert best_window([]) is None


def test_two_payments_are_not_enough() -> None:
    (p,), _ = proposals_for(rent_rows(DAYS, SIZES))  # control
    assert p
    proposals, _ = proposals_for(rent_rows(DAYS[:2], SIZES[:2]))
    assert proposals == []


def test_a_small_amount_is_not_rent() -> None:
    proposals, _ = proposals_for(rent_rows(DAYS, [4000] * 6 + [4500] * 4))
    assert proposals == []


def test_one_amount_on_about_one_day_needs_no_proposal() -> None:
    days = [date(2025, m, 1) for m in range(1, 9)]
    proposals, series = proposals_for(rent_rows(days, [90000] * 8))
    assert series and proposals == []  # an ordinary series already covers it


def test_payments_that_are_not_monthly_are_not_rent() -> None:
    days = [date(2025, 1, 2) + timedelta(days=9 * i) for i in range(10)]
    proposals, _ = proposals_for(rent_rows(days, [90000] * 5 + [95000] * 5))
    assert proposals == []


def test_nothing_is_proposed_without_a_detected_series_to_attach_to() -> None:
    txns = rent_rows(DAYS[:4], SIZES[:4])
    past = [t for t in txns]
    assert propose_rent([], [], past, fingerprints(txns), None, None, DAYS[3]) == []


def test_rows_posted_after_the_date_are_never_used() -> None:
    txns = rent_rows(DAYS, SIZES)
    early, _ = proposals_for(txns, as_of=DAYS[7])  # eight payments were posted by then
    late, _ = proposals_for(txns)
    assert len(early[0].evidence) == 8 and len(late[0].evidence) == 10
    assert early[0].rent is not None and late[0].rent is not None


# No proposal for a series I have made a decision about


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def decide_about_the_series(conn: sqlite3.Connection, how: str, series: list[rs.Stored]) -> None:
    s = max(series, key=lambda x: len(x.evidence))
    when = date(2025, 9, 30)
    if how == "confirm":
        rs.confirm(conn, ACCOUNT, s, when)
    elif how == "reject":
        rs.reject(conn, ACCOUNT, s, when)
    elif how == "end":
        rs.end(conn, ACCOUNT, s, when)
    elif how == "rent rule":
        rs.set_rent_rule(conn, ACCOUNT, s, RentRule((90000,), 1, 5), when)
    elif how == "rename":
        rs.set_name(conn, ACCOUNT, s, "My rent", when)
    elif how == "category":
        rs.set_category(conn, ACCOUNT, s, "Groceries", when)
    elif how == "link":
        rs.link_continuation(conn, ACCOUNT, s, "sid|merchant|out|Old|@fp0", when)
    else:
        raise AssertionError(how)


@pytest.mark.parametrize(
    "how", ["confirm", "reject", "end", "rent rule", "rename", "category", "link"]
)
def test_no_proposal_for_a_series_with_a_decision(how: str, conn: sqlite3.Connection) -> None:
    txns = rent_rows(DAYS, SIZES)
    (first,), series = proposals_for(txns)
    assert first  # a proposal before the decision
    decide_about_the_series(conn, how, series)
    decisions = rs.load_decisions(conn, ACCOUNT)
    proposals, series_after = proposals_for(txns, decisions)
    assert proposals == []
    assert any(has_decision(s, decisions) for s in series_after)


def test_a_hand_added_series_counts_as_decided() -> None:
    added = rs.Stored(
        key="user|1", name="Rent", kind="user", direction="out", cadence="monthly",
        anchor=date(2025, 1, 1), anchor_day=1, amount_type="fixed", expected_cents=-90000,
        range_cents=(-90000, -90000), next_expected=date(2025, 11, 1), status="user-added",
        active=True, id="user|1",
    )  # fmt: skip
    assert has_decision(added, [])


def test_a_series_nobody_decided_about_has_no_decision() -> None:
    (_,), series = proposals_for(rent_rows(DAYS, SIZES))
    assert not any(has_decision(s, []) for s in series)
