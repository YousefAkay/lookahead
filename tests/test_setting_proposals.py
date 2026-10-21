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
    has_link,
    link_blocked,
    propose_links,
    propose_possible,
    propose_rent,
    rent_blocked,
    standing,
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


# What blocks a rent proposal: a rent rule, "It ended", "Not recurring" (and "Not this")


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


@pytest.mark.parametrize("how", ["reject", "end", "rent rule"])
def test_a_rent_rule_it_ended_or_not_recurring_blocks_the_rent_proposal(
    how: str, conn: sqlite3.Connection
) -> None:
    txns = rent_rows(DAYS, SIZES)
    (first,), series = proposals_for(txns)
    assert first  # a proposal before the decision
    decide_about_the_series(conn, how, series)
    decisions = rs.load_decisions(conn, ACCOUNT)
    proposals, series_after = proposals_for(txns, decisions)
    assert proposals == []
    assert any(rent_blocked(s, decisions) for s in series_after)


@pytest.mark.parametrize("how", ["confirm", "rename", "category", "link"])
def test_confirming_renaming_a_category_or_a_link_do_not_block_the_rent_proposal(
    how: str, conn: sqlite3.Connection
) -> None:
    txns = rent_rows(DAYS, SIZES)
    _, series = proposals_for(txns)
    decide_about_the_series(conn, how, series)
    decisions = rs.load_decisions(conn, ACCOUNT)
    proposals, series_after = proposals_for(txns, decisions)
    assert len(proposals) == 1 and proposals[0].kind == "rent"
    assert not any(rent_blocked(s, decisions) for s in series_after)


def test_a_confirmed_series_with_no_rent_rule_still_gets_a_rent_proposal(
    conn: sqlite3.Connection,
) -> None:
    txns = rent_rows(DAYS, SIZES)
    _, series = proposals_for(txns)
    for s in series:  # "confirm all" on everything detected
        rs.confirm(conn, ACCOUNT, s, date(2025, 9, 30))
    proposals, after = proposals_for(txns, rs.load_decisions(conn, ACCOUNT))
    assert {s.status for s in after} == {"confirmed"}
    assert len(proposals) == 1 and proposals[0].rent is not None


def test_an_ended_series_confirmed_again_is_proposed_again(conn: sqlite3.Connection) -> None:
    """The latest of confirm / reject / end counts, as everywhere else."""
    txns = rent_rows(DAYS, SIZES)
    _, series = proposals_for(txns)
    big = max(series, key=lambda s: len(s.evidence))
    rs.end(conn, ACCOUNT, big, date(2025, 9, 29))
    assert proposals_for(txns, rs.load_decisions(conn, ACCOUNT))[0] == []
    rs.confirm(conn, ACCOUNT, big, date(2025, 9, 30))
    assert len(proposals_for(txns, rs.load_decisions(conn, ACCOUNT))[0]) == 1


def hand_added(key: str = "user|1") -> rs.Stored:
    return rs.Stored(
        key=key, name="Rent", kind="user", direction="out", cadence="monthly",
        anchor=date(2025, 1, 1), anchor_day=1, amount_type="fixed", expected_cents=-90000,
        range_cents=(-90000, -90000), next_expected=date(2025, 11, 1), status="user-added",
        active=True, id=key,
    )  # fmt: skip


def test_a_hand_added_series_never_gets_a_proposal() -> None:
    added = hand_added()
    assert rent_blocked(added, []) and link_blocked(added, [])


def test_standing_is_the_latest_confirm_reject_or_end() -> None:
    (_,), series = proposals_for(rent_rows(DAYS, SIZES))
    s = series[0]
    log = [
        rs.Decision(s.id, "confirm", {}, date(2025, 1, 1), 1),
        rs.Decision(s.id, "end", {}, date(2025, 2, 1), 2),
        rs.Decision(s.id, "rename", {"name": "x"}, date(2025, 3, 1), 3),
    ]
    assert standing(s, log) == "end" and standing(s, log[:1]) == "confirm"
    assert standing(s, []) is None and has_link(s, log) is False


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


def decide_about_one_of_the_two(conn: sqlite3.Connection, how: str, target: rs.Stored) -> None:
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


@pytest.mark.parametrize("how", ["reject", "end", "link"])
@pytest.mark.parametrize("which", ["old", "new"])
def test_an_end_a_reject_or_an_existing_link_on_either_series_blocks_the_link_proposal(
    how: str, which: str, conn: sqlite3.Connection
) -> None:
    txns = monthly_rows(RENAMED)
    (p,), series = links_for(txns)
    old, new = sorted(series, key=lambda s: s.anchor)
    decide_about_one_of_the_two(conn, how, old if which == "old" else new)
    proposals, series_after = links_for(txns, rs.load_decisions(conn, ACCOUNT))
    assert proposals == []


@pytest.mark.parametrize("how", ["confirm", "rename", "category"])
@pytest.mark.parametrize("which", ["old", "new"])
def test_confirming_renaming_or_a_category_do_not_block_the_link_proposal(
    how: str, which: str, conn: sqlite3.Connection
) -> None:
    txns = monthly_rows(RENAMED)
    (before,), series = links_for(txns)
    old, new = sorted(series, key=lambda s: s.anchor)
    decide_about_one_of_the_two(conn, how, old if which == "old" else new)
    proposals, _ = links_for(txns, rs.load_decisions(conn, ACCOUNT))
    assert [p.key for p in proposals] == [before.key]


def test_confirm_all_on_the_two_names_does_not_hide_the_link_proposal(
    conn: sqlite3.Connection,
) -> None:
    txns = monthly_rows(RENAMED)
    _, series = links_for(txns)
    for s in series:
        rs.confirm(conn, ACCOUNT, s, date(2025, 12, 1))
    proposals, after = links_for(txns, rs.load_decisions(conn, ACCOUNT))
    assert {s.status for s in after} == {"confirmed"} and len(proposals) == 1


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


def card_pair(first_cents: int, second_cents: int, second_day: date) -> list[Txn]:
    text = "Opos Netflix.Com         +1866 "
    return [
        Txn(1, date(2025, 1, 10), -first_cents, "pos purchase", text),
        Txn(2, second_day, -second_cents, "pos purchase", text),
    ]


def test_a_subscription_paid_by_card_is_proposed_when_the_amounts_are_exactly_equal() -> None:
    (p,), _ = possible_for(card_pair(1454, 1454, date(2025, 2, 10)))
    assert p.kind == "possible" and p.amount_cents == -1454


@pytest.mark.parametrize(
    ("second_cents", "second_day"),
    [(1454, date(2025, 2, 3)), (1454, date(2025, 2, 17)), (1454, date(2025, 3, 20))],
)
def test_card_purchases_that_are_not_a_calendar_month_apart_are_not_proposed(
    second_cents: int, second_day: date
) -> None:
    proposals, _ = possible_for(card_pair(1454, second_cents, second_day))
    assert proposals == []


def pair_of(card: bool, first: int, second: int) -> list[Txn]:
    """Two payments at one merchant, a calendar month apart, by card or as a bill payment."""
    if card:
        return card_pair(first, second, date(2025, 2, 10))
    return [
        Txn(1, date(2025, 1, 10), -first, "bill payment", "Zenstudio "),
        Txn(2, date(2025, 2, 10), -second, "bill payment", "Zenstudio "),
    ]


@pytest.mark.parametrize("card", [False, True])
def test_a_pair_three_percent_apart_is_proposed(card: bool) -> None:
    """A currency conversion moves the amount by a few percent: 12.99 and 13.38 are 3 percent."""
    txns = pair_of(card, 1299, 1338)
    (p,), _ = possible_for(txns)
    assert p.kind == "possible" and p.amount_cents == -1338


@pytest.mark.parametrize("card", [False, True])
def test_a_pair_seven_percent_apart_is_not_proposed(card: bool) -> None:
    txns = pair_of(card, 1299, 1390)
    proposals, _ = possible_for(txns)
    assert proposals == []


def test_two_one_off_purchases_a_month_apart_within_five_percent_are_only_possible(
    conn: sqlite3.Connection,
) -> None:
    """The proposer cannot tell a subscription from two similar one-off purchases, so it only says
    'may be' with the lowest confidence, and nothing is decided or confirmed on its own."""
    shop = "Opos Bookshop            toron "
    txns = [
        Txn(1, date(2025, 1, 10), -2400, "pos purchase", shop),
        Txn(2, date(2025, 2, 10), -2490, "pos purchase", shop),
    ]
    (p,), series = possible_for(txns)
    assert p.confidence == "possible" and "may be" in p.title
    assert series == [] and rs.load_decisions(conn, ACCOUNT) == []  # nothing decided or confirmed
    assert all(s.status != "confirmed" for s in series)


def test_accepting_a_possible_subscription_adds_it_and_it_is_not_proposed_again(
    conn: sqlite3.Connection,
) -> None:
    txns = monthly_rows([("Zenstudio", 1299, 1, 2)])
    (p,), _ = possible_for(txns)
    assert p.name and p.amount_cents and p.anchor and p.cadence
    rs.add_series(conn, ACCOUNT, p.name, "out", p.cadence, p.anchor, abs(p.amount_cents))
    proposals, series = possible_for(txns, rs.load_decisions(conn, ACCOUNT))
    assert proposals == [] and len(series) == 1 and len(series[0].evidence) == 2


# --- rent is judged on a cluster of the recipient's payments, not on the whole group ---

from lookahead.setting_proposals import rent_reason  # noqa: E402


def mixed_group(rent: list[tuple[date, int]], small: list[tuple[date, int]]) -> list[Txn]:
    rows = sorted([*rent, *small])
    return [Txn(i + 1, d, -c, "withdrawal", ETRANSFER) for i, (d, c) in enumerate(rows)]


def small_transfers(count: int, cents: int = 2500) -> list[tuple[date, int]]:
    start = date(2025, 1, 10)
    return [(start + timedelta(days=3 * i), cents + 100 * (i % 7)) for i in range(count)]


def test_rent_among_many_small_unnamed_e_transfers_is_proposed() -> None:
    txns = mixed_group(list(zip(DAYS, [90000] * 6 + [95000] * 4, strict=True)), small_transfers(60))
    (p,), _ = proposals_for(txns)
    assert p.rent is not None and p.rent.amounts_cents == (90000, 95000)
    assert len(p.evidence) == len(DAYS)  # judged on the rent cluster: ten payments, not seventy


def monthly_small(count: int = 9) -> list[tuple[date, int]]:
    """A regular $40 e-Transfer on the 5th: it makes a detected series, so the rent code looks at
    the recipient's payments instead of stopping at 'no series'."""
    return [(date(2025, 1 + i % 12, 5), 4000) for i in range(count)]


def reason_for(txns: list[Txn], series: list, wanted: set[int]) -> str:  # type: ignore[type-arg]
    fp = fingerprints(txns)
    return rent_reason(
        series, [], txns, fp, None, None, max(t.posted_date for t in txns),
        {fp[t.row_id] for t in txns if t.row_id in wanted},
    )  # fmt: skip


def test_a_group_of_only_small_transfers_gets_no_proposal() -> None:
    txns = mixed_group([], monthly_small() + small_transfers(40))
    proposals, series = proposals_for(txns)
    assert series and proposals == []  # there is a series; the recipient has no rent in it
    assert reason_for(txns, series, {t.row_id for t in txns}) == "amount_too_small"


def test_an_irregular_friend_paid_150_now_and_then_is_not_rent() -> None:
    day, friend = date(2025, 1, 6), []
    for g in (0, 23, 71, 12, 95, 33, 8):  # days between payments: nothing monthly about it
        day += timedelta(days=g)
        friend.append((day, 15000))
    txns = mixed_group([], friend + monthly_small() + small_transfers(12))
    proposals, series = proposals_for(txns)
    assert series and proposals == []
    big = {t.row_id for t in txns if abs(t.amount_cents) == 15000}
    assert reason_for(txns, series, big) == "not_monthly"


def test_an_irregular_friend_in_the_same_group_as_the_rent_is_left_out_of_the_rent() -> None:
    rent = list(zip(DAYS, [90000] * 6 + [95000] * 4, strict=True))
    friend = [(date(2025, 1, 20) + timedelta(days=g), 15000) for g in (0, 41, 97, 130, 210)]
    txns = mixed_group(rent, friend + small_transfers(30))
    (p,), _ = proposals_for(txns)
    assert p.rent is not None and p.rent.amounts_cents == (90000, 95000)  # not 15000
    assert len(p.evidence) == len(DAYS)


def test_rent_that_rose_ten_percent_stays_one_cluster_among_noise() -> None:
    rent = list(zip(DAYS, [100000] * 7 + [110000] * 3, strict=True))
    txns = mixed_group(rent, small_transfers(50))
    (p,), _ = proposals_for(txns)
    assert p.rent is not None and p.rent.amounts_cents == (100000, 110000)
