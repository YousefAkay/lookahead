"""Tests for the generation pipeline: scheduled money, then everyday spending."""

from dataclasses import replace
from datetime import date

import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.pipeline import generate_persona
from lookahead.generator.schedules import GeneratedTxn

START = date(2026, 6, 1)
END = date(2026, 9, 30)


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_pipeline_combines_stages_sorted(pid: str) -> None:
    rows = generate_persona(PERSONAS[pid], START, END, seed=42)
    assert [t.posted_date for t in rows] == sorted(t.posted_date for t in rows)
    assert any(t.flow_type == "everyday" for t in rows)
    assert any(t.flow_type == "income" for t in rows)


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_same_seed_same_rows(pid: str) -> None:
    p = PERSONAS[pid]
    assert generate_persona(p, START, END, seed=5) == generate_persona(p, START, END, seed=5)


def test_stages_use_independent_random_streams() -> None:
    # Removing everyday spending must not change the scheduled rows: each stage
    # gets its own random stream, so one stage's draws never shift another's.
    # Persona A has no top-ups (those depend on spending by design). Text is ignored:
    # stage 4 draws store numbers across all rows in date order.
    def no_text(rows: list[GeneratedTxn]) -> list[GeneratedTxn]:
        return [replace(t, description="", sub_description="") for t in rows]

    p = PERSONAS["A"]
    with_spending = generate_persona(p, START, END, seed=42)
    without = generate_persona(replace(p, spending=None), START, END, seed=42)
    # Stage 2 rows = everyday and not one-off (A's big purchases are stage-1 one-offs).
    stage_1 = [t for t in with_spending if t.flow_type != "everyday" or t.one_off]
    assert no_text(stage_1) == no_text(without)


def test_quiet_months_have_little_spending() -> None:
    rows = generate_persona(PERSONAS["C"], START, END, seed=42)
    june = sum(t.flow_type == "everyday" and t.posted_date.month == 6 for t in rows)
    august = sum(t.flow_type == "everyday" and t.posted_date.month == 8 for t in rows)
    assert august < 0.4 * june  # C is quiet in August (activity 0.25)


def test_personas_with_the_same_seed_are_independent() -> None:
    # Regression: every persona used to get identical random streams for a given seed.
    same = replace(PERSONAS["A"], persona_id="A2")
    a = generate_persona(PERSONAS["A"], START, END, seed=42)
    a2 = generate_persona(same, START, END, seed=42)
    assert [t.amount_cents for t in a] != [t.amount_cents for t in a2]
