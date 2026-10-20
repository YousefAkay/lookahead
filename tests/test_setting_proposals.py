"""Proposals for recurring settings (lookahead.setting_proposals). Made-up payments only."""

import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead import recurring_store as rs
from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.flows import Txn
from lookahead.rent import RentRule
from lookahead.setting_proposals import (
    best_window,
    has_decision,
    propose_links,
    propose_possible,
    propose_rent,
)

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


# --- link: one subscription under two merchant names ---


def monthly_rows(plan: list[tuple[str, int, int, int]], start_id: int = 1) -> list[Txn]:
    """plan: (merchant text, cents, first month number, how many months). Month 1 = Jan 2025."""
    rows: list[Txn] = []
    for name, cents, first, count in plan:
        for m in range(first, first + count):
            year, month = 2025 + (m - 1) // 12, (m - 1) % 12 + 1
            day = shift_to_business_day(monthly_date(year, month, 8), "later")
            rows.append(Txn(start_id + len(rows), day, -cents, "bill payment", f"{name} "))
    return sorted(rows, key=lambda t: (t.posted_date, t.row_id))


def links_for(
    txns: list[Txn], decisions: list[rs.Decision] | None = None, as_of: date | None = None
):  # type: ignore[no-untyped-def]
    day = as_of or max(t.posted_date for t in txns)
    past = [t for t in txns if t.posted_date <= day]
    fp = fingerprints(txns)
    series = rs.current_series(past, fp, decisions or [], day)
    return propose_links(series, decisions or [], txns, fp, day), series


RENAMED = [("Fitplace", 2500, 1, 8), ("Fitplace Online", 2500, 9, 4)]


def test_a_subscription_under_a_new_name_is_proposed_as_a_link() -> None:
    (p,), series = links_for(monthly_rows(RENAMED))
    old, new = sorted(series, key=lambda s: s.anchor)
    assert p.kind == "link" and p.series_id == new.id and p.other_id == old.id
    assert p.confidence == "likely"  # it starts one period after the last old payment
    assert p.key == f"link|{new.id}|{old.id}" and "under a new name" in p.title
    assert set(p.evidence) == set(old.evidence) | set(new.evidence)


def test_a_different_name_with_no_shared_word_is_only_possible() -> None:
    plan = [("Fitplace", 2500, 1, 8), ("Zenstudio", 2500, 10, 4)]  # a month later than expected
    (p,), _ = links_for(monthly_rows(plan))
    assert p.confidence == "possible"


@pytest.mark.parametrize(
    "plan",
    [
        [("Fitplace", 2500, 1, 8), ("Fitplace Online", 3500, 9, 4)],  # the amount changed a lot
        [("Fitplace", 2500, 1, 8), ("Fitplace Online", 2500, 14, 4)],  # long gap: a new start
        [("Fitplace", 2500, 1, 8), ("Fitplace Online", 2500, 5, 8)],  # they overlap: two real ones
    ],
)
def test_no_link_when_the_amount_the_gap_or_the_overlap_is_wrong(
    plan: list[tuple[str, int, int, int]],
) -> None:
    proposals, series = links_for(monthly_rows(plan))
    assert len(series) == 2 and proposals == []


def test_no_link_between_different_cadences() -> None:
    rows = monthly_rows([("Fitplace", 2500, 1, 8)])
    weekly = [
        Txn(100 + i, date(2025, 9, 10) + timedelta(days=7 * i), -2500, "bill payment", name)
        for i, name in enumerate(["Fitplace Online "] * 10)
    ]
    proposals, _ = links_for(sorted(rows + weekly, key=lambda t: (t.posted_date, t.row_id)))
    assert proposals == []


def test_one_merchant_changing_its_price_is_not_a_new_name() -> None:
    plan = [("Fitplace", 2500, 1, 8), ("Fitplace", 2600, 9, 4)]
    proposals, _ = links_for(monthly_rows(plan))
    assert proposals == []


def test_two_new_names_for_one_old_one_are_ambiguous_so_nothing_is_proposed() -> None:
    plan = [
        ("Fitplace", 2500, 1, 8),
        ("Fitplace Online", 2500, 9, 4),
        ("Fitplace Plus", 2500, 9, 4),
    ]
    proposals, series = links_for(monthly_rows(plan))
    assert len(series) == 3 and proposals == []


def test_no_link_before_the_new_name_has_started() -> None:
    txns = monthly_rows(RENAMED)
    proposals, _ = links_for(txns, as_of=date(2025, 8, 31))
    assert proposals == []


def test_the_rows_after_the_date_are_not_used_for_a_link() -> None:
    txns = monthly_rows(RENAMED)
    (p,), _ = links_for(txns)
    early, _ = links_for(txns, as_of=max(t.posted_date for t in txns) - timedelta(days=40))
    assert len(early) <= 1
    if early:
        assert len(early[0].evidence) < len(p.evidence)


@pytest.mark.parametrize("how", ["confirm", "reject", "end", "rename", "category", "link"])
@pytest.mark.parametrize("which", ["old", "new"])
def test_no_link_for_a_series_with_a_decision(
    how: str, which: str, conn: sqlite3.Connection
) -> None:
    txns = monthly_rows(RENAMED)
    (p,), series = links_for(txns)
    old, new = sorted(series, key=lambda s: s.anchor)
    target = old if which == "old" else new
    when = date(2025, 12, 1)
    if how == "confirm":
        rs.confirm(conn, ACCOUNT, target, when)
    elif how == "reject":
        rs.reject(conn, ACCOUNT, target, when)
    elif how == "end":
        rs.end(conn, ACCOUNT, target, when)
    elif how == "rename":
        rs.set_name(conn, ACCOUNT, target, "Gym", when)
    elif how == "category":
        rs.set_category(conn, ACCOUNT, target, "Groceries", when)
    else:
        rs.link_continuation(conn, ACCOUNT, target, "sid|merchant|out|Elsewhere|@fp0", when)
    proposals, _ = links_for(txns, rs.load_decisions(conn, ACCOUNT))
    assert proposals == []


def test_a_hand_added_series_is_never_part_of_a_link() -> None:
    txns = monthly_rows(RENAMED)
    (_,), series = links_for(txns)
    old = min(series, key=lambda s: s.anchor)
    hand = replace(old, status="user-added", id="user|1", key="user|1", kind="user")
    new = max(series, key=lambda s: s.anchor)
    last = max(t.posted_date for t in txns)
    assert propose_links([hand, new], [], txns, fingerprints(txns), last) == []


def test_accepting_a_link_joins_the_two_into_one_series(conn: sqlite3.Connection) -> None:
    txns = monthly_rows(RENAMED)
    (p,), series = links_for(txns)
    old, new = sorted(series, key=lambda s: s.anchor)
    rs.link_continuation(conn, ACCOUNT, new, old, date(2025, 12, 20))
    decisions = rs.load_decisions(conn, ACCOUNT)
    proposals, joined = links_for(txns, decisions)
    assert len(joined) == 1 and proposals == []  # one history; nothing more to propose


# --- possible: a monthly charge seen exactly twice ---


def possible_for(
    txns: list[Txn], decisions: list[rs.Decision] | None = None, as_of: date | None = None
):  # type: ignore[no-untyped-def]
    day = as_of or max(t.posted_date for t in txns)
    past = [t for t in txns if t.posted_date <= day]
    fp = fingerprints(txns)
    series = rs.current_series(past, fp, decisions or [], day)
    return propose_possible(series, decisions or [], txns, fp, None, None, day), series


def test_two_monthly_charges_of_one_amount_are_a_possible_subscription() -> None:
    (p,), series = possible_for(monthly_rows([("Zenstudio", 1299, 1, 2)]))
    assert series == []  # detection needs three
    assert p.kind == "possible" and p.confidence == "possible"
    assert p.name == "Zenstudio" and p.amount_cents == -1299 and p.cadence == "monthly"
    assert p.anchor == date(2025, 2, 10) and len(p.evidence) == 2
    assert p.key == p.series_id == f"possible|{p.evidence[0]}"
    assert "may be a monthly subscription" in p.title and "$12.99" in p.title


@pytest.mark.parametrize(
    "plan",
    [
        [("Zenstudio", 1299, 1, 1)],  # one charge
        [("Zenstudio", 1299, 1, 3)],  # three charges: a normal series
        [("Zenstudio", 1299, 1, 1), ("Zenstudio", 1499, 2, 1)],  # the amount differs
        [("Zenstudio", 1299, 1, 1), ("Zenstudio", 1299, 4, 1)],  # three months apart
        [("Zenstudio", 50, 1, 2)],  # under a dollar
    ],
)
def test_no_possible_subscription_for_other_patterns(plan: list[tuple[str, int, int, int]]) -> None:
    proposals, _ = possible_for(monthly_rows(plan))
    assert proposals == []


def test_a_merchant_with_other_payments_is_not_proposed() -> None:
    rows = monthly_rows([("Zenstudio", 1299, 1, 2)])
    extra = Txn(99, date(2025, 2, 20), -777, "bill payment", "Zenstudio ")
    proposals, _ = possible_for(sorted([*rows, extra], key=lambda t: (t.posted_date, t.row_id)))
    assert proposals == []


def test_a_stale_pair_is_not_proposed_and_rows_after_the_date_are_not_used() -> None:
    txns = monthly_rows([("Zenstudio", 1299, 1, 2), ("Otherplace", 2000, 5, 1)])
    stale, _ = possible_for(txns, as_of=date(2025, 5, 30))
    assert stale == []  # the latest charge was 3 months before
    before, _ = possible_for(txns, as_of=date(2025, 1, 31))
    assert before == []  # only one charge was posted by then


def test_nothing_for_charges_that_already_belong_to_a_series(conn: sqlite3.Connection) -> None:
    txns = monthly_rows([("Zenstudio", 1299, 1, 2)])
    key = rs.add_series(conn, ACCOUNT, "Zenstudio", "out", "monthly", date(2025, 2, 10), 1299)
    decisions = rs.load_decisions(conn, ACCOUNT)
    proposals, series = possible_for(txns, decisions)
    assert [s.key for s in series] == [key] and series[0].status == "user-added"
    assert proposals == []  # a hand-added series already has these payments


def test_nothing_for_a_name_i_added_by_hand_even_if_its_payments_are_not_found() -> None:
    txns = monthly_rows([("Zenstudio", 1299, 1, 2)])
    add = rs.Decision(
        "user|1", "add", {"name": "ZENSTUDIO", "direction": "out", "cadence": "monthly",
                          "anchor": "2025-02-10", "amount_cents": -1299}, date(2025, 2, 11), 1,
    )  # fmt: skip
    proposals, _ = possible_for(txns, [add])
    assert proposals == []


def test_card_purchases_only_are_not_proposed() -> None:
    rows = [
        Txn(i + 1, d, -400, "pos purchase", "Opos Coffeebar         toron ")
        for i, d in enumerate([date(2025, 1, 10), date(2025, 2, 10)])
    ]
    proposals, _ = possible_for(rows)
    assert proposals == []


def test_accepting_a_possible_subscription_adds_it_and_it_is_not_proposed_again(
    conn: sqlite3.Connection,
) -> None:
    txns = monthly_rows([("Zenstudio", 1299, 1, 2)])
    (p,), _ = possible_for(txns)
    assert p.name and p.amount_cents and p.anchor and p.cadence
    rs.add_series(conn, ACCOUNT, p.name, "out", p.cadence, p.anchor, abs(p.amount_cents))
    proposals, series = possible_for(txns, rs.load_decisions(conn, ACCOUNT))
    assert proposals == [] and len(series) == 1 and len(series[0].evidence) == 2
