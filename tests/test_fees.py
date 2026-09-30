"""Tests for event-driven bank fees."""

from collections import Counter
from datetime import date, timedelta

import numpy as np
import pytest

from lookahead.generator.fees import add_bank_fees
from lookahead.generator.personas import PERSONAS
from lookahead.generator.pipeline import generate_persona
from lookahead.generator.schedules import GeneratedTxn

START = date(2026, 6, 1)
END = date(2026, 9, 30)
RICH = 10**9  # opening balance that never goes negative


def row(
    d: date, cents: int, txn_type: str, flow: str = "everyday", category: str = "Groceries"
) -> GeneratedTxn:
    return GeneratedTxn(d, cents, txn_type, "X", category, flow)


def memo(d: date) -> GeneratedTxn:
    return row(d, 300_000, "credit memo", "income", "Income")


def abm(d: date) -> GeneratedTxn:
    return row(d, -6_000, "abm withdrawal", "everyday", "Cash")


def bill(d: date, cents: int) -> GeneratedTxn:
    return row(d, -cents, "bill payment", "recurring_outflow", "Phone, internet & utilities")


def fees(
    rows: list[GeneratedTxn], opening: int, end: date = END, seed: int = 0
) -> list[GeneratedTxn]:
    out = add_bank_fees(rows, opening, START, end, np.random.default_rng(seed))
    return [t for t in out if t.category == "Fees & interest"]


# --- a fee on each incoming credit memo ---


def test_one_transfer_fee_per_credit_memo_same_day() -> None:
    days = [date(2026, 6, 3), date(2026, 7, 8), date(2026, 9, 1)]
    got = fees([memo(d) for d in days], RICH)
    assert sorted(t.posted_date for t in got) == days
    assert all(t.txn_type == "service charge" and t.amount_cents == -1_500 for t in got)


# --- ATM fees: most withdrawals have none ---


def test_atm_fee_rows_are_occasional_and_mostly_zero() -> None:
    days = [START + timedelta(days=i) for i in range(1_000)]
    got = fees([abm(d) for d in days], RICH, end=days[-1], seed=1)
    per_day = Counter(t.posted_date for t in got)
    assert max(per_day.values()) == 1  # never more than one per withdrawal
    assert 0.14 <= len(got) / 1_000 <= 0.26  # ATM_FEE_ROW_SHARE 0.20
    zero_share = sum(t.amount_cents == 0 for t in got) / len(got)
    assert 0.6 <= zero_share <= 0.8  # ATM_FEE_ZERO_SHARE 0.70
    assert {t.amount_cents for t in got} == {0, -200}
    assert all(t.txn_type == "service charge" for t in got)


# --- Overdrawn handling charge: once per day, only when a scheduled payment overdraws ---


def handling(got: list[GeneratedTxn]) -> list[GeneratedTxn]:
    return [t for t in got if t.txn_type == "overdrawn handling chg."]


def test_bill_that_overdraws_gets_one_handling_charge() -> None:
    [charge] = handling(fees([bill(date(2026, 6, 10), 20_000)], opening=10_000))
    assert charge.posted_date == date(2026, 6, 10) and charge.amount_cents == -500


def test_two_overdrawing_bills_same_day_get_one_larger_charge() -> None:
    d = date(2026, 6, 10)
    [charge] = handling(fees([bill(d, 20_000), bill(d, 5_000)], opening=10_000))
    assert charge.amount_cents == -1_000


def test_bill_that_does_not_overdraw_gets_no_charge() -> None:
    assert handling(fees([bill(date(2026, 6, 10), 20_000)], opening=100_000)) == []


def test_everyday_purchase_into_overdraft_gets_no_handling_charge() -> None:
    purchase = row(date(2026, 6, 10), -20_000, "pos purchase")
    assert handling(fees([purchase], opening=10_000)) == []


# --- Overdraft interest: on the statement date, from the cycle's negative days ---


def interest(got: list[GeneratedTxn]) -> list[GeneratedTxn]:
    return [t for t in got if t.txn_type == "overdraft interest"]


def test_interest_for_two_negative_days_posts_on_the_statement_date() -> None:
    dip = [
        row(date(2026, 6, 10), -7_500, "pos purchase"),
        row(date(2026, 6, 12), 7_500, "deposit", "income", "Income"),
    ]
    [charge] = interest(fees(dip, opening=0))
    assert charge.posted_date == date(2026, 6, 25)  # Thursday
    assert charge.amount_cents == -6  # $75 x 2 days x 15% / 365 = $0.06


def test_no_negative_days_no_interest() -> None:
    assert interest(fees([row(date(2026, 6, 10), -7_500, "pos purchase")], opening=10_000)) == []


def test_a_negative_day_after_the_25th_counts_in_the_next_cycle() -> None:
    dip = [
        row(date(2026, 6, 26), -7_500, "pos purchase"),
        row(date(2026, 6, 27), 7_500, "deposit", "income", "Income"),
    ]
    [charge] = interest(fees(dip, opening=0))
    assert charge.posted_date == date(2026, 7, 27)  # Jul 25 is a Saturday
    assert charge.amount_cents == -3  # one day


# --- General properties ---


def test_only_adds_rows_and_is_deterministic() -> None:
    rows = [memo(date(2026, 6, 3)), abm(date(2026, 6, 4)), bill(date(2026, 6, 10), 50_000)]
    out = add_bank_fees(rows, 0, START, END, np.random.default_rng(2))
    assert Counter(rows) - Counter(out) == Counter()
    assert out == add_bank_fees(rows, 0, START, END, np.random.default_rng(2))
    assert [t.posted_date for t in out] == sorted(t.posted_date for t in out)


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_transfer_fees_only_where_there_are_credit_memos(pid: str) -> None:
    rows = generate_persona(PERSONAS[pid], START, END, seed=42)
    n_memos = sum(t.txn_type == "credit memo" for t in rows)
    n_fees = sum(t.txn_type == "service charge" and t.amount_cents == -1_500 for t in rows)
    assert n_fees == n_memos
    assert (n_memos > 0) == (pid == "C")
    assert all(t.sub_description == " " for t in rows if t.category == "Fees & interest")
