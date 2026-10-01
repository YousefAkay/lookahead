"""Tests for flow typing and pairing. Made-up rows in the bank's format, never real data."""

import random
from datetime import date, timedelta

import pytest

from lookahead.flows import Flow, Txn, flow_types

D = date(2026, 9, 10)
UBER = "Opos Uber Canada/Ubertriptoron "
SHOP = "Apos Winners #9074       Montr "
ETRANSFER = "Free Interac E-Transfer "
RTN = "Interac E-Transfer Rtn "


def txn(i: int, kind: str, cents: int, text: str = " ", day: date = D) -> Txn:
    return Txn(row_id=i, posted_date=day, amount_cents=cents, txn_type=kind, text=text)


def one(t: Txn) -> Flow:
    return flow_types([t])[t.row_id]


# --- One row at a time ---


@pytest.mark.parametrize(
    ("kind", "cents", "text", "flow"),
    [
        ("credit memo", 300_000, "1/Nadia Haddad ", "income"),
        ("deposit", 80_000, "Boutique Mile End ", "income"),  # payroll
        ("deposit", 12_000, ETRANSFER, "income"),  # incoming e-Transfer
        ("deposit", 12_000, RTN, "refund"),  # returned e-Transfer
        ("withdrawal", -82_000, ETRANSFER, "transfer"),  # e-Transfer out (rent: Week 3)
        ("customer transfer dr.", -20_000, "Mb-Transfer ", "transfer"),
        ("customer transfer cr.", 20_000, "Mb-Transfer ", "transfer"),
        ("bill payment", -9_500, "Mb-Hydro-Quebec 55231204", "recurring_outflow"),
        ("pos purchase", -648, "Apos Maxi #350           Montr ", "everyday"),
        ("abm withdrawal", -6_000, " ", "everyday"),
        ("pos return", 2_999, SHOP, "refund"),
        ("correction", 1_500, UBER, "refund"),
        ("accounts payable", 4_500, "Ticket Company Refund ", "refund"),
    ],
)
def test_flow_type_of_one_row(kind: str, cents: int, text: str, flow: str) -> None:
    assert one(txn(1, kind, cents, text)).flow_type == flow


@pytest.mark.parametrize(
    "kind", ["service charge", "overdraft interest", "overdrawn handling chg."]
)
def test_bank_fees_are_everyday_for_now(kind: str) -> None:
    got = one(txn(1, kind, -500))
    assert got.flow_type == "everyday" and got.reason == "bank fee"


def test_zero_dollar_service_charge() -> None:
    assert one(txn(1, "service charge", 0)).flow_type == "everyday"


def test_reversed_fee_is_a_refund() -> None:
    assert one(txn(1, "service charge", 500)).flow_type == "refund"


def test_type_names_ignore_case_and_spacing() -> None:
    assert one(txn(1, "  POS Purchase ", -648)).flow_type == "everyday"


def test_unknown_type_falls_back_and_says_so() -> None:
    out = one(txn(1, "mystery debit", -1_000))
    assert out.flow_type == "everyday" and out.reason.startswith("no rule")
    inc = one(txn(2, "mystery credit", 1_000))
    assert inc.flow_type == "refund" and inc.reason.startswith("no rule")  # never income


def test_a_rule_for_the_wrong_direction_falls_back() -> None:
    assert one(txn(1, "credit memo", -5_000)).reason.startswith("no rule")


# --- Pre-authorization hold and its correction ---


def test_hold_and_correction_on_the_same_day_are_paired() -> None:
    hold = txn(1, "pos purchase", -2_500, UBER)
    final = txn(2, "pos purchase", -2_506, UBER)
    corr = txn(3, "correction", 2_500, UBER)
    f = flow_types([hold, final, corr])
    assert f[3].link == 1 and f[1].link == 3 and f[3].link_kind == "preauth"
    assert f[1].flow_type == "everyday" and f[3].flow_type == "refund"
    assert f[2].link is None  # the real charge stays a normal purchase


@pytest.mark.parametrize(("gap", "paired"), [(0, True), (1, True), (7, True), (8, False)])
def test_correction_window_is_7_days(gap: int, paired: bool) -> None:
    hold = txn(1, "pos purchase", -2_500, UBER, D)
    corr = txn(2, "correction", 2_500, UBER, D + timedelta(days=gap))
    assert (flow_types([hold, corr])[2].link == 1) is paired


def test_correction_never_pairs_with_a_later_charge() -> None:
    corr = txn(1, "correction", 2_500, UBER, D)
    later = txn(2, "pos purchase", -2_500, UBER, D + timedelta(days=1))
    assert flow_types([corr, later])[1].link is None


def test_correction_needs_the_same_merchant_and_amount() -> None:
    lyft = txn(1, "pos purchase", -2_500, "Opos Lyft *Ride Y1Qkq    Vanco ")
    other_amount = txn(2, "pos purchase", -2_400, UBER)
    corr = txn(3, "correction", 2_500, UBER)
    assert flow_types([lyft, other_amount, corr])[3].link is None


def test_each_hold_is_used_once() -> None:
    rows = [
        txn(1, "pos purchase", -2_500, UBER),
        txn(2, "pos purchase", -2_500, UBER),
        txn(3, "correction", 2_500, UBER),
        txn(4, "correction", 2_500, UBER),
    ]
    f = flow_types(rows)
    assert {f[3].link, f[4].link} == {1, 2}


def test_the_nearest_earlier_hold_is_chosen() -> None:
    old = txn(1, "pos purchase", -2_500, UBER, D - timedelta(days=5))
    near = txn(2, "pos purchase", -2_500, UBER, D)
    corr = txn(3, "correction", 2_500, UBER, D)
    assert flow_types([old, near, corr])[3].link == 2


# --- Returns ---


def test_full_return_pairs_with_the_purchase() -> None:
    buy = txn(1, "pos purchase", -2_999, SHOP, D)
    ret = txn(2, "pos return", 2_999, SHOP, D + timedelta(days=20))
    f = flow_types([buy, ret])
    assert f[2].link == 1 and f[1].link == 2 and f[2].link_kind == "return"


def test_partial_return_pairs_with_a_larger_purchase() -> None:
    buy = txn(1, "pos purchase", -5_000, SHOP, D)
    ret = txn(2, "pos return", 2_000, SHOP, D + timedelta(days=3))
    assert flow_types([buy, ret])[2].link == 1


def test_return_prefers_the_exact_amount() -> None:
    big = txn(1, "pos purchase", -5_000, SHOP, D + timedelta(days=1))
    exact = txn(2, "pos purchase", -2_000, SHOP, D)
    ret = txn(3, "pos return", 2_000, SHOP, D + timedelta(days=3))
    assert flow_types([big, exact, ret])[3].link == 2


def test_return_never_pairs_with_a_smaller_purchase_or_after_90_days() -> None:
    small = txn(1, "pos purchase", -1_000, SHOP, D)
    old = txn(2, "pos purchase", -2_000, SHOP, D - timedelta(days=91))
    ret = txn(3, "pos return", 2_000, SHOP, D + timedelta(days=1))
    assert flow_types([small, old, ret])[3].link is None


def test_unpaired_return_is_still_a_refund() -> None:
    f = one(txn(1, "pos return", 2_000, SHOP))
    assert f.flow_type == "refund" and f.link is None


# --- Returned e-Transfer ---


def test_returned_etransfer_pairs_with_the_one_it_returns() -> None:
    sent = txn(1, "withdrawal", -12_000, ETRANSFER, D)
    other = txn(2, "withdrawal", -5_000, ETRANSFER, D)
    back = txn(3, "deposit", 12_000, RTN, D + timedelta(days=4))
    f = flow_types([sent, other, back])
    assert f[3].link == 1 and f[3].link_kind == "etransfer_return" and f[2].link is None


def test_returned_etransfer_never_pairs_with_a_later_one() -> None:
    back = txn(1, "deposit", 12_000, RTN, D)
    sent = txn(2, "withdrawal", -12_000, ETRANSFER, D + timedelta(days=1))
    assert flow_types([back, sent])[1].link is None


# --- Rule zero: nothing looks into the future ---


def test_order_of_the_input_does_not_matter() -> None:
    rows = [
        txn(1, "pos purchase", -2_500, UBER, D),
        txn(2, "correction", 2_500, UBER, D),
        txn(3, "pos purchase", -2_999, SHOP, D),
        txn(4, "pos return", 2_999, SHOP, D + timedelta(days=5)),
        txn(5, "withdrawal", -12_000, ETRANSFER, D),
        txn(6, "deposit", 12_000, RTN, D + timedelta(days=2)),
    ]
    expected = flow_types(rows)
    shuffled = rows[:]
    random.Random(1).shuffle(shuffled)
    assert flow_types(shuffled) == expected


def test_links_from_a_refund_only_point_back_in_time() -> None:
    rows = [
        txn(1, "pos purchase", -2_999, SHOP, D),
        txn(2, "pos return", 2_999, SHOP, D + timedelta(days=5)),
        txn(3, "pos purchase", -2_999, SHOP, D + timedelta(days=9)),
    ]
    f = flow_types(rows)
    by_id = {r.row_id: r for r in rows}
    for r in rows:
        if f[r.row_id].flow_type == "refund" and f[r.row_id].link is not None:
            assert by_id[f[r.row_id].link].posted_date <= r.posted_date


def test_flow_types_on_data_up_to_a_date_match_the_full_history() -> None:
    rows = [
        txn(1, "pos purchase", -2_999, SHOP, D),
        txn(2, "pos return", 2_999, SHOP, D + timedelta(days=5)),
        txn(3, "withdrawal", -12_000, ETRANSFER, D + timedelta(days=6)),
    ]
    cut = D + timedelta(days=4)
    early = flow_types([r for r in rows if r.posted_date <= cut])
    full = flow_types(rows)
    assert early[1].flow_type == full[1].flow_type  # the type never depends on later rows


def test_same_day_pairing_ignores_the_order_within_the_day() -> None:
    # The importer stores the export newest first, so a correction can get a lower id than
    # its hold. Dates have no time of day: same day counts as "not later".
    corr = txn(1, "correction", 2_500, UBER, D)
    hold = txn(2, "pos purchase", -2_500, UBER, D)
    assert flow_types([corr, hold])[1].link == 2


# --- Bill payment coming in (owner's real data: 1 row) ---


def test_bill_payment_coming_in_is_a_refund_not_income() -> None:
    got = one(txn(1, "bill payment", 9_500, "Mb-Hydro-Quebec 55231204"))
    assert got.flow_type == "refund" and got.reason == "bill payment credit"


# --- Pairing uses the resolved merchant group (step 2), not the raw name ---

from lookahead.resolve import UserDecisions  # noqa: E402

WINNERS = "Apos Winners #9074       Montr "
WINNERS_WEB = "Opos Winners.Ca          +1877 "


def test_return_pairs_across_two_names_in_one_group() -> None:
    mine = UserDecisions(merges={"Winners": {"winners", "winners ca"}})  # your merge
    buy = txn(1, "pos purchase", -2_999, WINNERS_WEB, D)
    ret = txn(2, "pos return", 2_999, WINNERS, D + timedelta(days=5))
    assert flow_types([buy, ret], user=mine)[2].link == 1


def test_names_in_different_groups_do_not_pair() -> None:
    buy = txn(1, "pos purchase", -2_999, WINNERS_WEB, D)
    ret = txn(2, "pos return", 2_999, WINNERS, D + timedelta(days=5))
    assert flow_types([buy, ret])[2].link is None  # no merge: two merchants


def test_your_split_keeps_rule_merged_names_apart() -> None:
    # The accents rule joins "Caf? Velo" and "Cafe Velo"; your split keeps them apart.
    a, b = "Apos Caf? Velo           Montr ", "Apos Cafe Velo           Montr "
    hold, corr = txn(1, "pos purchase", -500, a), txn(2, "correction", 500, b)
    assert flow_types([hold, corr])[2].link == 1  # one group: paired
    split = UserDecisions(splits={frozenset(("caf? velo", "cafe velo"))})
    assert flow_types([hold, corr], user=split)[2].link is None


def test_correction_pairs_across_alias_list_names() -> None:
    hold = txn(1, "pos purchase", -2_500, "Opos Uber Canada/Ubertriptoron ")
    corr = txn(2, "correction", 2_500, "Opos Uber* Trip          +1855 ")
    assert flow_types([hold, corr])[2].link == 1  # both are rides on the alias list


def test_a_bare_uber_does_not_pair_with_a_ride() -> None:
    # "Uber" alone names no service (owner's real-data find): it is not a ride.
    hold = txn(1, "pos purchase", -2_500, "Opos Uber Canada/Ubertriptoron ")
    corr = txn(2, "correction", 2_500, "Opos Uber                +1855 ")
    assert flow_types([hold, corr])[2].link is None


def test_groups_only_use_rows_given_rule_zero() -> None:
    # A merge of yours that names a merchant seen only later still works on early data,
    # because groups are built from the rows passed in plus your stored decisions.
    mine = UserDecisions(merges={"Winners": {"winners", "winners ca"}})
    rows = [
        txn(1, "pos purchase", -2_999, WINNERS_WEB, D),
        txn(2, "pos return", 2_999, WINNERS, D + timedelta(days=5)),
        txn(3, "pos purchase", -100, "Apos Maxi #350           Montr ", D + timedelta(days=30)),
    ]
    early = flow_types([r for r in rows if r.posted_date <= D + timedelta(days=5)], user=mine)
    assert early[2].link == 1 == flow_types(rows, user=mine)[2].link


# --- Netting: a fully reversed pair (owner's choice) ---


def test_hold_and_its_correction_are_netted() -> None:
    hold = txn(1, "pos purchase", -2_500, UBER)
    final = txn(2, "pos purchase", -2_506, UBER)
    corr = txn(3, "correction", 2_500, UBER)
    f = flow_types([hold, final, corr])
    assert f[1].netted and f[3].netted
    assert not f[2].netted  # the real charge is spending
    assert f[1].flow_type == "everyday" and f[3].flow_type == "refund"  # kept for the record


def test_full_return_is_netted_partial_is_not() -> None:
    buy = txn(1, "pos purchase", -2_999, SHOP, D)
    full = txn(2, "pos return", 2_999, SHOP, D + timedelta(days=5))
    assert flow_types([buy, full])[1].netted and flow_types([buy, full])[2].netted
    big = txn(3, "pos purchase", -5_000, SHOP, D)
    part = txn(4, "pos return", 2_000, SHOP, D + timedelta(days=5))
    f = flow_types([big, part])
    assert f[4].link == 3 and not f[4].netted and not f[3].netted


def test_returned_etransfer_pair_is_netted() -> None:
    sent = txn(1, "withdrawal", -12_000, ETRANSFER, D)
    back = txn(2, "deposit", 12_000, RTN, D + timedelta(days=4))
    f = flow_types([sent, back])
    assert f[1].netted and f[2].netted


def test_unpaired_rows_are_not_netted() -> None:
    assert not one(txn(1, "pos return", 2_000, SHOP)).netted
    assert not one(txn(2, "pos purchase", -2_000, SHOP)).netted


def test_netting_is_only_known_once_the_credit_posts() -> None:
    hold = txn(1, "pos purchase", -2_999, SHOP, D)
    ret = txn(2, "pos return", 2_999, SHOP, D + timedelta(days=5))
    assert not flow_types([hold])[1].netted  # before the return: it is spending
    assert flow_types([hold, ret])[1].netted
