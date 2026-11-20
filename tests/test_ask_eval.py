"""Ask step 7: the development question set and the scorer (spec docs/specs/2026-11-20-ask.md,
definition of done 9). The set's mix, its personas (development only), every gold plan running to
its stated outcome, and the scorer's rules on made-up planners."""

from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from eval.ask_dev_set import QUESTIONS
from eval.ask_score import Runner, bootstrap_low, gold_outcome, kind_of, score, summarize
from lookahead.ask.periods import window_end
from lookahead.ask.plan import Day, check_phrases, parse
from lookahead.generator.archetypes import ALL_HELD_OUT


def test_the_set_has_200_questions_in_the_specs_mix() -> None:
    assert len(QUESTIONS) == 200 and len({q.id for q in QUESTIONS}) == 200
    kinds = Counter(kind_of(q) for q in QUESTIONS)
    assert kinds == {"answerable": 150, "should_refuse": 30, "ambiguous": 20}
    french = sum(q.lang == "fr" for q in QUESTIONS)
    assert 28 <= french <= 34  # about 15%
    tags = Counter(t for q in QUESTIONS for t in q.tags)
    assert tags["forecast"] + tags["whatif"] == 30
    assert tags["budget"] + tags["safe"] + tags["recurring"] == 20


def test_no_held_out_persona_is_used() -> None:
    assert not {q.persona for q in QUESTIONS} & ALL_HELD_OUT


def test_every_gold_plan_is_well_formed_and_reachable() -> None:
    for q in QUESTIONS:
        plan = parse(q.gold)
        check_phrases(plan, q.text)  # a planner could write it: its names are in the question


@pytest.mark.slow
def test_every_gold_plan_runs_to_its_stated_outcome(tmp_path: Path) -> None:
    runner = Runner(tmp_path)
    for q in QUESTIONS:
        gold_outcome(runner, q)  # raises with the question id if not


@pytest.mark.slow
def test_the_scorer_on_made_up_planners(tmp_path: Path) -> None:
    runner = Runner(tmp_path)
    sample = [q for q in QUESTIONS if q.persona == "v2-SAL-01"]
    by_text = {q.text: q.gold for q in sample}
    perfect = summarize(score(lambda text, ctx: by_text[text], sample, runner))
    assert perfect.correct_answerable == perfect.answerable
    assert perfect.refused == perfect.should_refuse and perfect.silent_wrong == 0
    silent = summarize(score(lambda text, ctx: None, sample, runner))
    assert silent.correct_answerable == 0 and silent.refused == silent.should_refuse
    assert silent.silent_wrong == 0  # refusing is never a silent wrong answer
    count = {"op": "aggregate", "name": "a", "source": "r", "measure": "count"}
    wrong = {"steps": [{"op": "rows", "name": "r", "categories": ["Cash"]}, count]}
    bad = summarize(score(lambda text, ctx: wrong, sample, runner))
    assert bad.silent_wrong >= bad.answerable - 1  # a confident wrong figure almost everywhere


def test_bootstrap_is_fixed_and_sensible() -> None:
    hits = [True] * 85 + [False] * 15
    low = bootstrap_low(hits)
    # 85 of 100: about 0.85 - 1.96 x 0.036 = 0.78 by the normal approximation
    assert low == bootstrap_low(hits) and 0.76 <= low <= 0.80


def test_a_window_of_n_days_ends_on_day_n() -> None:
    today = date(2026, 9, 15)
    assert window_end(Day(kind="in_days", n=30), today) == date(2026, 10, 14)
    assert window_end(Day(kind="end_of_month"), today) == date(2026, 9, 30)
