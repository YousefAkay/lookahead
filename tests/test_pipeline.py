"""Tests for the generation pipeline: scheduled money, then everyday spending."""

from dataclasses import replace
from datetime import date

import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.pipeline import generate_persona

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
    p = PERSONAS["C"]
    with_spending = generate_persona(p, START, END, seed=42)
    without = generate_persona(replace(p, spending=None), START, END, seed=42)
    assert [t for t in with_spending if t.flow_type != "everyday"] == without


def test_quiet_months_have_little_spending() -> None:
    rows = generate_persona(PERSONAS["C"], START, END, seed=42)
    june = sum(t.flow_type == "everyday" and t.posted_date.month == 6 for t in rows)
    august = sum(t.flow_type == "everyday" and t.posted_date.month == 8 for t in rows)
    assert august < 0.4 * june  # C is quiet in August (activity 0.25)
