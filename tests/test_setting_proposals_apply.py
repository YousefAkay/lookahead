"""Showing, accepting and dismissing proposals on a made-up database."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead import recurring_store as rs
from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.proposals import decisions_as_of
from lookahead.rent import RentRule
from lookahead.setting_proposals import SettingProposal, accept, current_proposals, dismiss

ACCOUNT = "a"
RENT_DAYS = [
    date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3), date(2025, 3, 31), date(2025, 5, 2),
    date(2025, 6, 4), date(2025, 7, 1), date(2025, 8, 2), date(2025, 9, 3), date(2025, 9, 30),
]  # fmt: skip
RENT_SIZES = [90000] * 6 + [95000] * 4
LAST_DAY = date(2025, 12, 31)


def month_day(month: int) -> date:
    year, m = 2025 + (month - 1) // 12, (month - 1) % 12 + 1
    return shift_to_business_day(monthly_date(year, m, 8), "later")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    n = 0

    def insert(day: date, cents: int, kind: str, text: str) -> None:
        nonlocal n
        n += 1
        c.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:04d}"),
        )

    for day, cents in zip(RENT_DAYS, RENT_SIZES, strict=True):
        insert(day, -cents, "withdrawal", "Free Interac E-Transfer ")
    for month in range(1, 9):
        insert(month_day(month), -2500, "bill payment", "Fitplace ")
    for month in range(9, 13):
        insert(month_day(month), -2500, "bill payment", "Fitplace Online ")
    for month in (11, 12):
        insert(month_day(month), -1299, "bill payment", "Zenstudio ")
    c.commit()
    yield c
    c.close()


def by_kind(conn: sqlite3.Connection, **kwargs: object) -> dict[str, SettingProposal]:
    found = current_proposals(conn, ACCOUNT, **kwargs)  # type: ignore[arg-type]
    assert len({p.kind for p in found}) == len(found)  # one of each kind in this database
    return {p.kind: p for p in found}


def test_the_three_proposals_are_shown_rent_first(conn: sqlite3.Connection) -> None:
    found = current_proposals(conn, ACCOUNT)
    assert [p.kind for p in found] == ["rent", "link", "possible"]


def test_looks_right_on_rent_writes_a_rent_rule_by_id_and_logs_the_click(
    conn: sqlite3.Connection,
) -> None:
    proposal = by_kind(conn)["rent"]
    series, _ = rs.series_as_of(conn, ACCOUNT)
    accept(conn, ACCOUNT, proposal, series, date(2026, 1, 5))
    (decision,) = [d for d in rs.load_decisions(conn, ACCOUNT) if d.action == "rent"]
    assert decision.key == proposal.series_id and decision.key.startswith("sid|")
    rule = next(s for s in rs.series_as_of(conn, ACCOUNT)[0] if s.id == proposal.series_id)
    assert rule.allowed_cents == (90000, 95000) and rule.window == (1, 4)
    assert decisions_as_of(conn, ACCOUNT)[proposal.key] == "looks right"
    assert "rent" not in by_kind(conn)  # a series with a decision is not proposed again


def test_a_rent_rule_from_a_click_equals_the_same_rule_typed_by_hand(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    proposal = by_kind(conn)["rent"]
    series, _ = rs.series_as_of(conn, ACCOUNT)
    target = next(s for s in series if s.id == proposal.series_id)
    rs.set_rent_rule(conn, ACCOUNT, target, RentRule((90000, 95000), 1, 4), date(2026, 1, 5))
    typed = rs.series_as_of(conn, ACCOUNT)[0]
    other = connect(tmp_path / "other.db")
    conn.backup(other)
    other.execute("DELETE FROM recurring_decisions")
    other.execute("DELETE FROM series_members")
    other.execute("DELETE FROM proposal_decisions")
    other.commit()
    accept(other, ACCOUNT, proposal, rs.series_as_of(other, ACCOUNT)[0], date(2026, 1, 5))
    assert rs.series_as_of(other, ACCOUNT)[0] == typed
    other.close()


def test_looks_right_on_a_link_joins_the_two_names_into_one_series(
    conn: sqlite3.Connection,
) -> None:
    proposal = by_kind(conn)["link"]
    series, _ = rs.series_as_of(conn, ACCOUNT)
    accept(conn, ACCOUNT, proposal, series, date(2026, 1, 5))
    after, _ = rs.series_as_of(conn, ACCOUNT)
    joined = [s for s in after if s.id == proposal.series_id or s.id == proposal.other_id]
    assert len(joined) == 1 and len(joined[0].continues) == 1
    assert "link" not in by_kind(conn)


def test_looks_right_on_a_possible_subscription_adds_it_by_hand(conn: sqlite3.Connection) -> None:
    proposal = by_kind(conn)["possible"]
    accept(conn, ACCOUNT, proposal, rs.series_as_of(conn, ACCOUNT)[0], date(2026, 1, 5))
    added = [s for s in rs.series_as_of(conn, ACCOUNT)[0] if s.status == "user-added"]
    assert len(added) == 1 and added[0].name == "Zenstudio" and len(added[0].evidence) == 2
    assert "possible" not in by_kind(conn)


def test_not_this_removes_the_proposal_and_it_stays_removed(conn: sqlite3.Connection) -> None:
    proposal = by_kind(conn)["link"]
    dismiss(conn, ACCOUNT, proposal, date(2026, 1, 5))
    assert "link" not in by_kind(conn)
    assert "link" not in by_kind(conn)  # asking again changes nothing
    assert rs.load_decisions(conn, ACCOUNT) == []  # nothing recurring was decided


def test_replaying_as_of_a_date_before_the_click_still_shows_the_proposal(
    conn: sqlite3.Connection,
) -> None:
    proposals = by_kind(conn)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    accept(conn, ACCOUNT, proposals["rent"], series, date(2026, 1, 5))
    dismiss(conn, ACCOUNT, proposals["link"], date(2026, 1, 6))
    assert {"rent", "link"} <= set(by_kind(conn, as_of=LAST_DAY))  # decided after that date
    assert "rent" not in by_kind(conn)


def test_rows_posted_after_the_date_are_not_used(conn: sqlite3.Connection) -> None:
    # by 31 August the new name and the new subscription had not started
    kinds = {p.kind for p in current_proposals(conn, ACCOUNT, as_of=date(2025, 8, 31))}
    assert kinds <= {"rent"}
    assert {p.kind for p in current_proposals(conn, ACCOUNT, as_of=LAST_DAY)} == {
        "rent", "link", "possible",
    }  # fmt: skip


def test_an_empty_account_has_no_proposals(conn: sqlite3.Connection) -> None:
    assert current_proposals(conn, "nobody") == []


def test_every_decision_a_click_writes_is_an_id(conn: sqlite3.Connection) -> None:
    proposals = by_kind(conn)
    for proposal in proposals.values():
        accept(conn, ACCOUNT, proposal, rs.series_as_of(conn, ACCOUNT)[0], date(2026, 1, 5))
    keys = [d.key for d in rs.load_decisions(conn, ACCOUNT)]
    assert keys and all(k.startswith(("sid|", "user|")) for k in keys)
