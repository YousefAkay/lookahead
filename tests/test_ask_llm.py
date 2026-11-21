"""Ask step 8: the LLM planner, with no network (a fake `send`). What a request carries
(spec docs/specs/2026-11-20-ask.md, definition of done 7), that the retry carries only the
check's problems (7b), the spend cap, and how an API failure is scored."""

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from eval.ask_dev_set import QUESTIONS
from eval.ask_score import Row, llm_planner, score_one
from lookahead.ask.llm import (
    SYSTEM,
    ApiFailed,
    Spend,
    SpendCapReached,
    build_request,
    plan_for,
    request_text,
)
from lookahead.ask.run import Context
from lookahead.taxonomy import CATEGORIES
from tests.test_ask_rows import ACCOUNT, ROWS, TODAY, build

GOOD = {"steps": [{"op": "rows", "name": "r", "categories": ["Groceries"],
                   "period": {"kind": "last_month"}},
                  {"op": "aggregate", "name": "t", "source": "r", "measure": "sum"}]}  # fmt: skip
QUESTION = "How much did I spend on groceries last month?"


def reply(plan: dict[str, Any]) -> Any:
    block = SimpleNamespace(type="tool_use", input=plan)
    return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=3000,
                                                                  output_tokens=200))  # fmt: skip


class Fake:
    """A stand-in for the API: returns the given plans in order and keeps every request."""

    def __init__(self, *plans: dict[str, Any]) -> None:
        self.plans = list(plans)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        return reply(self.plans.pop(0))


def test_a_request_holds_only_the_question_the_categories_and_the_format() -> None:
    r = build_request(QUESTION)
    assert set(r) == {"model", "max_tokens", "system", "tools", "tool_choice", "messages",
                      "extra_body"}  # fmt: skip
    assert r["messages"] == [{"role": "user", "content": QUESTION}]  # as typed
    assert r["system"] == SYSTEM  # fixed text: the format and the category list
    assert all(c in SYSTEM for c in CATEGORIES)


def test_the_planner_cannot_reach_the_data(tmp_path: Path) -> None:
    # plan_for takes the question, a sender and the spend: no context, no database.
    assert list(inspect.signature(plan_for).parameters) == ["question", "send", "spend"]
    # Through the scorer, with a real context on a made-up account: nothing of the data is sent.
    fake = Fake(GOOD)
    planner = llm_planner(fake, Spend(cap_usd=1.0))
    ctx = Context(build(tmp_path / "t.db"), ACCOUNT, TODAY)
    planner(QUESTION, ctx)
    # exactly the request built from the question alone: nothing from the context was added
    assert fake.requests[0] == build_request(QUESTION)
    sent = request_text(fake.requests[0])
    for day, cents, merchant, _, _ in ROWS:
        assert merchant not in sent and day not in sent and f"{abs(cents) / 100:.2f}" not in sent
    assert TODAY.isoformat() not in sent


def test_the_retry_sends_only_the_problems() -> None:
    bad = {"steps": [{"op": "aggregate", "name": "t", "source": "nowhere", "measure": "sum"}]}
    fake = Fake(bad, GOOD)
    planned = plan_for(QUESTION, fake, Spend(cap_usd=1.0))
    assert planned.plan == GOOD and planned.attempts == 2
    retry = fake.requests[1]["messages"]
    assert retry[0] == {"role": "user", "content": QUESTION}
    assert retry[1]["content"][0]["input"] == bad  # its own plan, echoed back
    result = retry[2]["content"][0]["content"]
    assert result.startswith("The plan failed its checks: ") and "names no earlier step" in result


def test_a_plan_still_failing_after_the_retry_gives_no_plan() -> None:
    bad = {"steps": [{"op": "python", "name": "x"}]}
    planned = plan_for(QUESTION, Fake(bad, bad), Spend(cap_usd=1.0))
    assert planned.plan is None and planned.problems


def test_merchant_phrases_must_be_copied_from_the_question() -> None:
    # A plan naming a merchant the question does not name is sent back, never run.
    total = {"op": "aggregate", "name": "t", "source": "r", "measure": "sum"}
    invented = {"steps": [{"op": "rows", "name": "r", "merchants": ["Quorvel Market"]}, total]}
    fake = Fake(invented, GOOD)
    assert plan_for(QUESTION, fake, Spend(cap_usd=1.0)).plan == GOOD
    assert "not copied from the question" in json.dumps(fake.requests[1]["messages"][2])


def test_the_spend_cap_stops_before_a_call() -> None:
    fake = Fake(GOOD)
    with pytest.raises(SpendCapReached):
        plan_for(QUESTION, fake, Spend(cap_usd=0.001))
    assert fake.requests == []  # nothing was sent
    spend = Spend(cap_usd=1.0)
    plan_for(QUESTION, Fake(GOOD), spend)
    assert spend.spent_usd == pytest.approx((3000 * 1.00 + 200 * 5.00) / 1e6)


def test_an_api_failure_is_not_correct_not_a_refusal_not_silent_wrong() -> None:
    def failing(request: dict[str, Any]) -> Any:
        raise ConnectionError("down")

    with pytest.raises(ApiFailed):
        plan_for(QUESTION, failing, Spend(cap_usd=1.0))

    class Stub:  # the scorer's runner, without databases
        def context(self, persona: str) -> None:
            return None

    q = next(q for q in QUESTIONS if q.expect == "refused")
    import eval.ask_score as score

    original = score.gold_outcome
    score.gold_outcome = lambda runner, question: None  # type: ignore[assignment]
    try:
        row: Row = score_one(Stub(), q, llm_planner(failing, Spend(cap_usd=1.0)))  # type: ignore[arg-type]
    finally:
        score.gold_outcome = original
    assert (row.produced, row.correct, row.refused, row.silent_wrong) == (
        "api_error", False, False, False)  # fmt: skip


def test_no_development_question_is_in_the_instructions() -> None:
    # The prompt's examples are not questions of the development set.
    for q in QUESTIONS:
        assert q.text not in SYSTEM


@pytest.mark.parametrize("expect", ["refused", "answer"])
def test_a_plan_still_failing_its_checks_is_not_a_refusal(
    expect: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """His reading, 2026-11-21: after the retry, a plan that still fails its checks is not
    correct, not a refusal and not silent wrong, also on a should-refuse question; the page says
    it with its own sentence, not the refusal one."""
    import eval.ask_score as score
    from lookahead.ask.words import COULD_NOT_PLAN, REFUSALS

    class Stub:
        def context(self, persona: str) -> None:
            return None

    monkeypatch.setattr(score, "gold_outcome", lambda runner, question: None)
    q = next(q for q in QUESTIONS if q.expect == expect and not q.ambiguous)
    broken = {"steps": [{"op": "aggregate", "name": "t", "source": "nowhere", "measure": "sum"}]}
    row = score_one(Stub(), q, lambda text, ctx: broken)  # type: ignore[arg-type]
    assert (row.produced, row.correct, row.refused, row.silent_wrong) == (
        "invalid", False, False, False)  # fmt: skip
    assert COULD_NOT_PLAN not in REFUSALS.values() and "can't" not in COULD_NOT_PLAN
