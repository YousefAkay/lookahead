"""Ask step 1: the plan format, its checks and 'How I worked it out' (spec
docs/specs/2026-11-20-ask.md, definition of done 2, 3 and 7b)."""

import json

import pytest

from lookahead.ask.plan import MAX_STEPS, PlanError, check_phrases, parse
from lookahead.ask.words import CLARIFY, REFUSALS, plan_words

ROWS = {"op": "rows", "name": "r", "categories": ["Groceries"], "period": {"kind": "last_month"}}
TOTAL = {"op": "aggregate", "name": "t", "source": "r", "measure": "sum"}


def plan(*steps: dict) -> dict:  # type: ignore[type-arg]
    return {"steps": list(steps)}


def problems(data: object) -> list[str]:
    with pytest.raises(PlanError) as e:
        parse(data)
    return e.value.problems


# --- Plans that pass ---


def test_a_two_step_lookup_passes() -> None:
    p = parse(plan(ROWS, TOTAL))
    assert [s.op for s in p.steps] == ["rows", "aggregate"]


def test_a_multi_step_plan_with_references_passes() -> None:
    p = parse(plan(
        {"op": "rows", "name": "now", "categories": ["Groceries"], "period": {"kind": "this_year"}},
        {"op": "group", "name": "m_now", "source": "now", "by": "month"},
        {"op": "aggregate", "name": "avg_now", "source": "m_now", "measure": "average"},
        {"op": "rows", "name": "then", "categories": ["Groceries"],
         "period": {"kind": "last_year"}},
        {"op": "group", "name": "m_then", "source": "then", "by": "month"},
        {"op": "aggregate", "name": "avg_then", "source": "m_then", "measure": "average"},
        {"op": "compare", "name": "diff", "a": "avg_now", "b": "avg_then", "how": "difference"},
        {"op": "whatif", "name": "w", "changes": [
            {"kind": "category", "category": "Groceries", "direction": "less",
             "amount": {"ref": "diff"}}]},
    ))  # fmt: skip
    assert len(p.steps) == 8


def test_every_block_parses() -> None:
    for step in (
        {"op": "forecast", "name": "f", "measure": "balance_on",
         "on": {"kind": "in_days", "n": 14}},
        {"op": "forecast", "name": "f", "measure": "lowest"},
        {"op": "forecast", "name": "f", "measure": "chance_below", "below": {"dollars": 0.01},
         "until": {"kind": "end_of_month"}},
        {"op": "budget", "name": "b"},
        {"op": "budget", "name": "b", "category": "Shopping"},
        {"op": "safe_to_spend", "name": "s"},
        {"op": "recurring", "name": "rec", "find": "netflix"},
        {"op": "cannot_answer", "reason": "advice"},
        {"op": "clarify", "about": "subject"},
        {"op": "whatif", "name": "w", "changes": [
            {"kind": "cancel", "target": "netflix"},
            {"kind": "swap", "target": "gym", "amount": {"dollars": 20}},
            {"kind": "one_off", "direction": "out", "amount": {"dollars": 500},
             "day": {"kind": "day_of_month", "day": 15}},
            {"kind": "everyday", "direction": "less", "percent": 10}]},
    ):  # fmt: skip
        parse(plan(step))


# --- Definition of done 2: what the checks refuse ---


def test_more_than_ten_steps_is_refused() -> None:
    steps = [ROWS] + [
        {"op": "aggregate", "name": f"t{i}", "source": "r", "measure": "sum"}
        for i in range(MAX_STEPS)
    ]
    assert any("too long" in p for p in problems(plan(*steps)))


def test_a_later_or_unknown_name_is_refused() -> None:
    assert any(
        "names no earlier step" in p for p in problems(plan(TOTAL, ROWS, TOTAL | {"name": "u"}))
    )
    assert any("names no earlier step" in p for p in problems(plan(ROWS, TOTAL | {"source": "x"})))


def test_type_mismatches_are_refused() -> None:
    # averaging a number
    bad = plan(ROWS, TOTAL, {"op": "aggregate", "name": "a", "source": "t", "measure": "average"})
    assert any("rows or table, not number" in p for p in problems(bad))
    # top N of rows (needs a table)
    bad = plan(ROWS, {"op": "top", "name": "x", "source": "r"})
    assert any("giving table" in p for p in problems(bad))
    # comparing money with a count
    count = {"op": "aggregate", "name": "c", "source": "r", "measure": "count"}
    bad = plan(ROWS, TOTAL, count, {"op": "compare", "name": "d", "a": "t", "b": "c",
                                    "how": "difference"})  # fmt: skip
    assert any("same unit" in p for p in problems(bad))
    # a what-if amount that is a count, not money
    bad = plan(ROWS, count, {"op": "whatif", "name": "w", "changes": [
        {"kind": "category", "category": "Groceries", "direction": "less",
         "amount": {"ref": "c"}}]})  # fmt: skip
    assert any("money amount" in p for p in problems(bad))
    # ending on rows (nothing to show)
    assert any("last step gives rows" in p for p in problems(plan(ROWS)))


def test_unknown_fields_categories_and_blocks_are_refused() -> None:
    assert any("not a known field" in p for p in problems(plan(ROWS | {"sql": "DROP TABLE"})))
    assert any(
        "allowed values" in p for p in problems(plan(ROWS | {"categories": ["Coffee"]}, TOTAL))
    )
    assert any("not a known block" in p for p in problems(plan({"op": "python", "name": "x"})))


def test_period_and_change_fields_must_fit_their_kind() -> None:
    assert problems(plan(ROWS | {"period": {"kind": "last_n_days"}}, TOTAL))  # n missing
    assert problems(plan(ROWS | {"period": {"kind": "last_month", "n": 3}}, TOTAL))  # n not used
    assert problems(plan({"op": "whatif", "name": "w", "changes": [
        {"kind": "cancel"}]}))  # fmt: skip  # target missing
    assert problems(plan({"op": "whatif", "name": "w", "changes": [
        {"kind": "everyday", "direction": "less", "percent": 10,
         "amount": {"dollars": 5}}]}))  # fmt: skip


def test_refusal_and_ask_back_must_stand_alone() -> None:
    bad = plan(ROWS, TOTAL, {"op": "cannot_answer", "reason": "advice"})
    assert any("only step" in p for p in problems(bad))


def test_a_name_used_twice_is_refused() -> None:
    assert any("used twice" in p for p in problems(plan(ROWS, TOTAL | {"name": "r"})))


def test_merchant_phrases_must_come_from_the_question() -> None:
    p = parse(plan(ROWS | {"merchants": ["Café Velisaro"]}, TOTAL))
    check_phrases(p, "how much at cafe  VELISARO last month?")  # case, accents, spaces ignored
    with pytest.raises(PlanError):
        check_phrases(p, "how much on coffee last month?")


# --- Definition of done 7b: an error sent back on the retry carries no data ---

DATA_LIKE = ["Quorvel Market", "54.12", "2026-03-14", "123456", "$1,234.56"]


def test_errors_never_repeat_values_from_the_plan() -> None:
    bad = plan(
        {"op": "rows", "name": "r", "categories": ["Quorvel Market"], "merchants": [],
         "period": {"kind": "range", "start": "2026-03-14", "end": "2026-03-01"},
         "min_dollars": -54.12, "note": "$1,234.56"},
        {"op": "aggregate", "name": "t", "source": "123456", "measure": "sum"},
        {"op": "forecast", "name": "f", "measure": "chance_below", "below": {"dollars": 1234.56},
         "on": {"kind": "date", "date": "2026-03-14"}},
    )  # fmt: skip
    text = json.dumps(problems(bad))
    for value in DATA_LIKE:
        assert value not in text, value


def test_whole_plan_errors_name_only_steps_and_fields() -> None:
    bad = plan(ROWS, {"op": "aggregate", "name": "t", "source": "quorvel_market", "measure": "sum"})
    for problem in problems(bad):
        assert "quorvel" not in problem


# --- Definition of done 3: 'How I worked it out' is written by code ---


def test_plain_words_for_a_lookup() -> None:
    lines = plan_words(parse(plan(ROWS, TOTAL)))
    assert lines == [
        "1. Find money out in Groceries, last month; transfers between accounts left out. (“r”)",
        "2. The total of “r”. (“t”)",
    ]


def test_refusals_and_ask_backs_are_fixed_sentences() -> None:
    for reason, sentence in REFUSALS.items():
        assert plan_words(parse(plan({"op": "cannot_answer", "reason": reason}))) == [
            f"1. {sentence}"
        ]
    for about, sentence in CLARIFY.items():
        assert plan_words(parse(plan({"op": "clarify", "about": about}))) == [f"1. {sentence}"]


def test_the_plan_cannot_carry_free_text_to_the_page() -> None:
    # Any field that is not in the format is refused, so an author cannot add a sentence of its
    # own for the page to show; refusal and ask-back reasons are codes.
    for extra in ({"text": "You should buy a car"}, {"explanation": "x"}, {"message": "x"}):
        assert problems(plan({"op": "cannot_answer", "reason": "advice"} | extra))
        assert problems(plan(ROWS | extra, TOTAL))


def test_every_block_has_plain_words() -> None:
    p = parse(plan(
        ROWS | {"merchants": ["velisaro"], "kinds": ["everyday"], "min_dollars": 10},
        {"op": "group", "name": "g", "source": "r", "by": "merchant", "measure": "count"},
        {"op": "top", "name": "tp", "source": "g", "n": 3},
        {"op": "list", "name": "l", "source": "r", "order": "largest", "limit": 5},
        {"op": "aggregate", "name": "t", "source": "r", "measure": "sum"},
        {"op": "aggregate", "name": "t2", "source": "r", "measure": "max"},
        {"op": "compare", "name": "c", "a": "t2", "b": "t", "how": "share"},
        {"op": "forecast", "name": "f", "measure": "chance_below", "below": {"ref": "t"}},
        {"op": "budget", "name": "b", "category": "Shopping"},
        {"op": "recurring", "name": "rc", "find": "velisaro", "direction": "both"},
    ))  # fmt: skip
    lines = plan_words(p)
    assert len(lines) == 10
    assert "at merchants matching “velisaro”" in lines[0]
    assert lines[2] == "3. The 3 highest of “g”. (“tp”)"
    assert "below the result of “t”" in lines[7]
