"""Tests for the three hand-written v0 personas."""

from datetime import date, timedelta

import numpy as np
import pytest

from lookahead.calendar_rules import is_business_day, is_card_posting_day
from lookahead.generator.personas import PERSONAS
from lookahead.generator.pipeline import generate_persona
from lookahead.generator.schedules import GeneratedTxn

START = date(2026, 6, 1)
END = date(2026, 9, 30)


def txns(persona_id: str, seed: int = 42) -> list[GeneratedTxn]:
    return PERSONAS[persona_id].generate(START, END, np.random.default_rng(seed))


def test_three_personas() -> None:
    assert sorted(PERSONAS) == ["A", "B", "C"]


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_rows_sorted_and_in_range(pid: str) -> None:
    got = [t.posted_date for t in txns(pid)]
    assert got and got == sorted(got)
    assert all(START <= d <= END for d in got)


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_signs_match_flow_types(pid: str) -> None:
    for t in txns(pid):
        if t.flow_type in ("income", "refund"):
            assert t.amount_cents > 0, t
        if t.flow_type == "recurring_outflow":
            assert t.amount_cents < 0, t


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_same_seed_same_rows(pid: str) -> None:
    assert txns(pid, seed=7) == txns(pid, seed=7)


def test_b_moves_money_to_savings_monthly() -> None:
    to_savings = [t for t in txns("B") if t.txn_type == "customer transfer dr."]
    assert len(to_savings) == 4
    assert all(t.flow_type == "transfer" and t.amount_cents < 0 for t in to_savings)


def test_c_main_income_is_credit_memos() -> None:
    memos = [t for t in txns("C") if t.txn_type == "credit memo"]
    assert len(memos) >= 1
    assert all(t.flow_type == "income" and t.one_off is False and t.series_id for t in memos)


@pytest.mark.parametrize("seed", range(5))
def test_c_earmarked_memo_arrives_before_and_covers_its_tuition(seed: int) -> None:
    rows = txns("C", seed)
    [tuition] = [t for t in rows if t.category == "Education"]
    assert tuition.flow_type == "recurring_outflow" and tuition.one_off is False
    [memo] = [t for t in rows if t.txn_type == "credit memo" and t.link_id == tuition.link_id]
    assert 0 <= (tuition.posted_date - memo.posted_date).days <= 8
    assert memo.amount_cents >= 0.75 * -tuition.amount_cents


def test_c_has_one_returned_etransfer_linked_to_the_original() -> None:
    rows = txns("C")
    [ret] = [t for t in rows if t.flow_type == "refund"]
    [sent] = [t for t in rows if t.link_id == ret.link_id and t is not ret]
    assert sent.amount_cents == -ret.amount_cents
    assert sent.posted_date < ret.posted_date
    assert ret.one_off and sent.one_off


def test_c_is_quiet_in_august() -> None:
    assert PERSONAS["C"].quiet_months == frozenset({8})
    assert PERSONAS["A"].quiet_months == frozenset()


# --- Full pipeline checks (scheduled + everyday + top-ups) ---


def full(pid: str, seed: int, start: date = START, end: date = END) -> list[GeneratedTxn]:
    return generate_persona(PERSONAS[pid], start, end, seed)


def evening_balances(pid: str, rows: list[GeneratedTxn], start: date, end: date) -> dict[date, int]:
    """End-of-day balance for every day in [start, end]."""
    by_day: dict[date, int] = {}
    for t in rows:
        by_day[t.posted_date] = by_day.get(t.posted_date, 0) + t.amount_cents
    balance, out, d = PERSONAS[pid].opening_balance_cents, {}, start
    while d <= end:
        balance += by_day.get(d, 0)
        out[d] = balance
        d += timedelta(days=1)
    return out


def topups(rows: list[GeneratedTxn]) -> list[GeneratedTxn]:
    """C's living-money credit memos: the ones not earmarked for tuition."""
    return [t for t in rows if t.txn_type == "credit memo" and t.link_id is None]


@pytest.mark.parametrize("seed", range(5))
def test_a_gets_tight(seed: int) -> None:
    balances = evening_balances("A", full("A", seed), START, END)
    assert min(balances.values()) < 10_000  # gets close to (or below) zero


def test_a_hovers_without_a_trend() -> None:
    opening = PERSONAS["A"].opening_balance_cents
    ends = [evening_balances("A", full("A", s), START, END)[END] for s in range(20)]
    assert abs(sum(ends) / len(ends) - opening) < 30_000  # average drift under $300


def test_a_dips_below_zero_in_some_months_but_not_others() -> None:
    dipped = {m: 0 for m in (6, 7, 8, 9)}
    for seed in range(20):
        balances = evening_balances("A", full("A", seed), START, END)
        for m in dipped:
            dipped[m] += min(b for d, b in balances.items() if d.month == m) < 0
    assert all(2 <= n <= 18 for n in dipped.values()), dipped  # 10%-90% of runs


def test_a_has_larger_one_off_expenses() -> None:
    big = [t for t in txns("A") if t.one_off]
    assert len(big) == 3  # phone repair (Jul), concert (Aug), textbooks (Sep)
    assert all(t.flow_type == "everyday" and t.amount_cents < 0 for t in big)
    assert all(is_card_posting_day(t.posted_date) for t in big)


def test_card_billed_subscriptions_are_online() -> None:
    spotify = [t for t in txns("A") if t.merchant == "Spotify"]
    assert spotify and all(t.online is True for t in spotify)


@pytest.mark.parametrize("seed", range(5))
def test_c_credit_memos_post_on_business_days(seed: int) -> None:
    memos = [t for t in full("C", seed) if t.txn_type == "credit memo"]
    assert topups(memos)  # at least one balance-triggered top-up
    assert all(is_business_day(t.posted_date) for t in memos)


@pytest.mark.parametrize("seed", range(5))
def test_c_topups_follow_a_low_balance(seed: int) -> None:
    # Trigger rule: evening balance + scheduled rows due in the next 5 days < $500.
    rows = full("C", seed)
    balances = evening_balances("C", rows, START, END)
    scheduled: dict[date, int] = {}
    for t in rows:
        if t.flow_type != "everyday" and t not in topups(rows):
            scheduled[t.posted_date] = scheduled.get(t.posted_date, 0) + t.amount_cents

    def looks_low(d: date) -> bool:
        upcoming = sum(scheduled.get(d + timedelta(days=k), 0) for k in range(1, 6))
        return balances.get(d, 10**9) + upcoming < 50_000

    for t in topups(rows):
        window = [t.posted_date - timedelta(days=k) for k in range(1, 16)]  # 9 business days
        assert any(looks_low(d) for d in window), t


def test_c_topups_follow_their_profile() -> None:
    # Two years, ten seeds: balance the evening before arrival, and amounts.
    start, end = date(2026, 6, 1), date(2028, 5, 31)
    before, amounts = [], []
    for seed in range(10):
        rows = full("C", seed, start, end)
        balances = evening_balances("C", rows, start, end)
        for t in topups(rows):
            if t.posted_date > start:
                before.append(balances[t.posted_date - timedelta(days=1)] / 100)
                amounts.append(t.amount_cents / 100)
    # Measured over these 10 seeds (209 top-ups): median ~$240 the evening before,
    # 75% under ~$540, 22% after going below zero; median amount ~$2,090.
    assert 0 <= float(np.median(before)) <= 500
    assert float(np.percentile(before, 75)) <= 800
    assert sum(b < 0 for b in before) / len(before) <= 0.3  # overdrafts are not the rule
    assert 1_700 <= float(np.median(amounts)) <= 2_500  # drawn from $1,200-2,800
    assert max(amounts) > 2_800  # the occasional larger one


def test_foreign_currencies_per_persona() -> None:
    def currencies(pid: str) -> set[str]:
        spending = PERSONAS[pid].spending
        assert spending is not None
        return {m.currency for m in spending.merchants} - {"CAD"}

    assert currencies("A") == set()
    assert currencies("B") == {"USD"}
    assert currencies("C") == {"USD", "AED"}  # Steam in USD, Etsy in AED


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_every_row_has_bank_text(pid: str) -> None:
    for t in full(pid, 42):
        assert t.description == t.txn_type
        assert t.sub_description, t  # at least a single space
