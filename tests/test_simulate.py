"""Tests for the day-by-day simulation: everyday spending that reacts to the balance."""

from dataclasses import replace
from datetime import date, timedelta

import numpy as np

from lookahead.generator.personas import Persona
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.simulate import cutback_applies, simulate
from lookahead.generator.spending import Merchant, SpendingProfile

START = date(2026, 6, 1)
END = date(2026, 9, 30)

# $50.00 every time (sigma 0), several a day, no cash: easy to reason about.
FIXED_50 = SpendingProfile(
    purchases_per_day=3.0,
    amount_sigma=0.0,
    merchants=(Merchant("Maxi", "Groceries", online=False, weight=1, median_cents=5_000),),
)


def persona(**overrides: object) -> Persona:
    fields: dict[str, object] = {
        "persona_id": "T",
        "description": "test persona",
        "opening_balance_cents": 0,
        "sources": (),
        "spending": FIXED_50,
        "overdraft_limit_cents": -50_000,
        "cutback_below_cents": 10_000,
        "cutback_factor": 0.5,
    }
    fields.update(overrides)
    return Persona(**fields)  # type: ignore[arg-type]


def run(
    p: Persona, scheduled: list[GeneratedTxn] | None = None, seed: int = 0
) -> list[GeneratedTxn]:
    rng_a, rng_b = np.random.default_rng(seed), np.random.default_rng(seed + 1)
    return simulate(p, scheduled or [], START, END, rng_a, rng_b)


def bill(d: date, cents: int) -> GeneratedTxn:
    return GeneratedTxn(
        d, -cents, "bill payment", "Landlord", "Rent & housing", "recurring_outflow"
    )


# --- The cut-back rule itself ---


def test_cutback_when_balance_is_low() -> None:
    assert cutback_applies(available_cents=5_000, upcoming_outflows_cents=0, below_cents=10_000)
    assert not cutback_applies(
        available_cents=50_000, upcoming_outflows_cents=0, below_cents=10_000
    )


def test_cutback_looks_ahead_at_scheduled_outflows() -> None:
    # $300 available but $750 rent due within 7 days: cut back now, before rent.
    assert cutback_applies(
        available_cents=30_000, upcoming_outflows_cents=-75_000, below_cents=10_000
    )


# --- Overdraft limit: everyday purchases are declined, scheduled payments are not ---


def test_purchases_never_take_the_balance_past_the_limit() -> None:
    rows = run(persona(opening_balance_cents=-40_000, cutback_factor=1.0))
    spent = -sum(t.amount_cents for t in rows)
    assert spent == 10_000  # exactly two $50 purchases fit between -$400 and -$500
    assert all(t.flow_type == "everyday" for t in rows)


def test_purchases_resume_after_money_arrives() -> None:
    pay = GeneratedTxn(date(2026, 7, 1), 100_000, "deposit", "Employer", "Income", "income")
    rows = run(persona(opening_balance_cents=-50_000, cutback_factor=1.0), [pay])
    assert rows and min(t.posted_date for t in rows) >= date(2026, 7, 1)


def test_scheduled_payments_may_cross_the_limit() -> None:
    p = persona(opening_balance_cents=-40_000, spending=None)
    rent = bill(date(2026, 6, 3), 70_000)
    # simulate only adds everyday rows and top-ups; the scheduled row is never removed.
    assert run(p, [rent]) == []
    assert p.opening_balance_cents + rent.amount_cents < p.overdraft_limit_cents


def test_available_balance_counts_purchases_not_yet_posted() -> None:
    # Sunday purchases post Monday, but they already use up the available balance.
    rows = run(persona(opening_balance_cents=-40_000, cutback_factor=1.0))
    assert -sum(t.amount_cents for t in rows) <= 10_000


# --- Cutting back halves the purchase rate while low ---


def test_spending_halves_while_balance_is_low() -> None:
    low = persona(opening_balance_cents=5_000, overdraft_limit_cents=-(10**12))  # always < $100
    normal = replace(low, cutback_factor=1.0)
    n_low, n_normal = len(run(low)), len(run(normal))
    assert 0.4 <= n_low / n_normal <= 0.6


def test_no_cutback_when_comfortable() -> None:
    rich = persona(opening_balance_cents=10**9)
    same = replace(rich, cutback_factor=1.0)
    assert run(rich) == run(same)


def test_rent_ahead_triggers_cutback_before_it_posts() -> None:
    # $300 available, $750 rent due Jun 5 (in 4 days): cut back from Jun 1 already.
    p = persona(opening_balance_cents=30_000, overdraft_limit_cents=-(10**12))
    rent = [bill(date(2026, 6, 5), 75_000)]
    first_days = [t for t in run(p, rent) if t.posted_date < date(2026, 6, 5)]
    normal = [
        t for t in run(replace(p, cutback_factor=1.0), rent) if t.posted_date < date(2026, 6, 5)
    ]
    assert len(first_days) < len(normal)


def test_same_seed_same_rows() -> None:
    p = persona(opening_balance_cents=20_000)
    assert run(p, seed=4) == run(p, seed=4)


def test_rows_are_everyday_or_topups_only() -> None:
    rows = run(persona(opening_balance_cents=100_000))
    assert rows and all(t.flow_type == "everyday" for t in rows)
    assert all(START <= t.posted_date <= END for t in rows)
    assert [t.posted_date for t in rows] == sorted(t.posted_date for t in rows)
    assert max(t.posted_date for t in rows) <= END - timedelta(days=0)
