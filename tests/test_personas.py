"""Tests for the three hand-written v0 personas."""

from datetime import date

import numpy as np
import pytest

from lookahead.generator.personas import PERSONAS
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
