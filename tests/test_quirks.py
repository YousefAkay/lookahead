"""Tests for planted card quirks: pre-authorizations, returns, duplicates."""

from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.pipeline import generate_persona
from lookahead.generator.quirks import plant_card_quirks
from lookahead.generator.schedules import GeneratedTxn

START = date(2026, 6, 1)
END = date(2026, 9, 30)


def buy(
    d: date, merchant: str, cents: int, category: str = "Transport", **extra: object
) -> GeneratedTxn:
    fields: dict[str, object] = {
        "posted_date": d,
        "amount_cents": -cents,
        "txn_type": "pos purchase",
        "merchant": merchant,
        "category": category,
        "flow_type": "everyday",
        "online": True,
    }
    fields.update(extra)
    return GeneratedTxn(**fields)  # type: ignore[arg-type]


def many(merchant: str, n: int = 600) -> list[GeneratedTxn]:
    return [buy(START + timedelta(days=i % 100), merchant, 1_500 + i) for i in range(n)]


def plant(rows: list[GeneratedTxn], seed: int = 0, pid: str = "T") -> list[GeneratedTxn]:
    return plant_card_quirks(rows, pid, START, END, np.random.default_rng(seed))


def of(rows: list[GeneratedTxn], quirk: str) -> list[GeneratedTxn]:
    return [t for t in rows if t.quirk == quirk]


# --- Pre-authorization holds ---


@pytest.mark.parametrize(
    ("merchant", "low", "high"), [("Uber Eats", 0.30, 0.48), ("Lyft", 0.05, 0.28)]
)
def test_hold_share_per_merchant(merchant: str, low: float, high: float) -> None:
    rows = plant(many(merchant))
    share = len(of(rows, "preauth_hold")) / 600
    assert low <= share <= high


def test_each_hold_is_cancelled_by_one_same_day_correction() -> None:
    rows = plant(many("Uber"))
    corrections = {t.link_id: t for t in of(rows, "preauth_correction")}
    holds = of(rows, "preauth_hold")
    assert holds and len(corrections) == len(holds)
    for hold in holds:
        fix = corrections[hold.link_id]
        assert fix.posted_date == hold.posted_date
        assert fix.amount_cents == -hold.amount_cents > 0
        assert fix.txn_type == "correction" and fix.flow_type == "refund"
        assert hold.txn_type == "pos purchase" and hold.amount_cents < 0


def test_final_charge_is_usually_close_to_the_hold() -> None:
    rows = plant(many("Uber"))
    finals = {(t.posted_date, t.link_id): t for t in rows if t.quirk is None and t.link_id}
    diffs = [
        finals[(h.posted_date, h.link_id)].amount_cents - h.amount_cents
        for h in of(rows, "preauth_hold")
    ]
    assert abs(float(np.median(diffs))) <= 50
    assert max(abs(d) for d in diffs) > 500  # occasionally far apart


# --- Returns ---


def shopping_rows() -> list[GeneratedTxn]:
    return [buy(START + timedelta(days=i), "Amazon.ca", 3_000 + i, "Shopping") for i in range(60)]


def test_returns_link_to_an_earlier_purchase_of_the_same_amount() -> None:
    rows = plant(shopping_rows())
    returns = [t for t in rows if t.txn_type == "pos return"]
    assert 1 <= len(returns) <= 2
    for ret in returns:
        [orig] = [t for t in rows if t.link_id == ret.link_id and t is not ret]
        assert orig.txn_type == "pos purchase" and orig.merchant == ret.merchant
        assert ret.amount_cents == -orig.amount_cents > 0
        assert 3 <= (ret.posted_date - orig.posted_date).days <= 22
        assert ret.flow_type == "refund" and ret.posted_date <= END


def test_foreign_purchase_return_keeps_its_currency() -> None:
    usd = [
        buy(START + timedelta(days=i), "Steam", 1_690, "Entertainment",
            currency="USD", original_amount_cents=-1_199)
        for i in range(20)
    ]  # fmt: skip
    rows = plant(shopping_rows()[:5] + usd)
    foreign = [t for t in rows if t.txn_type == "pos return" and t.currency == "USD"]
    assert len(foreign) == 1
    assert foreign[0].original_amount_cents == 1_199


# --- Duplicates ---


def test_one_genuine_double_charge_within_48_hours() -> None:
    rows = plant(shopping_rows())
    [dup] = of(rows, "duplicate_charge")
    [orig] = [t for t in rows if t.link_id == dup.link_id and t is not dup]
    assert (dup.merchant, dup.amount_cents) == (orig.merchant, orig.amount_cents)
    assert 0 <= (dup.posted_date - orig.posted_date).days <= 2


def test_one_pair_of_identical_legitimate_purchases() -> None:
    rows = plant(shopping_rows())
    pair = of(rows, "identical_legit")
    assert len(pair) == 2
    a, b = pair
    assert (a.posted_date, a.merchant, a.amount_cents) == (
        b.posted_date,
        b.merchant,
        b.amount_cents,
    )
    assert a.link_id == b.link_id


# --- General properties ---


def test_existing_rows_keep_everything_but_link_and_quirk() -> None:
    before = shopping_rows() + many("Uber", 50)
    after = plant(before)
    originals = Counter(replace(t, link_id=None, quirk=None) for t in before)
    kept = Counter(replace(t, link_id=None, quirk=None) for t in after)
    assert all(kept[t] >= n for t, n in originals.items())  # nothing removed or altered


def test_sorted_and_deterministic() -> None:
    rows = plant(shopping_rows() + many("Uber", 50), seed=3)
    assert [t.posted_date for t in rows] == sorted(t.posted_date for t in rows)
    assert rows == plant(shopping_rows() + many("Uber", 50), seed=3)


# --- Personas, through the full pipeline ---


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_each_persona_has_returns_and_both_duplicate_kinds(pid: str) -> None:
    rows = generate_persona(PERSONAS[pid], START, END, seed=42)
    assert 1 <= sum(t.txn_type == "pos return" for t in rows) <= 2
    assert len(of(rows, "duplicate_charge")) == 1
    assert len(of(rows, "identical_legit")) == 2


@pytest.mark.parametrize("seed", range(5))
def test_c_has_a_foreign_return(seed: int) -> None:
    rows = generate_persona(PERSONAS["C"], START, END, seed=seed)
    assert any(t.txn_type == "pos return" and t.currency != "CAD" for t in rows)


def test_uber_holds_appear_in_persona_output() -> None:
    rows = generate_persona(PERSONAS["C"], START, END, seed=42)
    assert of(rows, "preauth_hold") and len(of(rows, "preauth_hold")) == len(
        of(rows, "preauth_correction")
    )
