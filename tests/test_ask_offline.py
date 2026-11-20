"""Ask step 5: the offline parser (spec docs/specs/2026-11-20-ask.md, definition of done 6). Each
shape in English and French builds a plan that passes every check, with its name phrases copied
from the question; advice and things outside the data are refused; anything else is None (the
page then says "I can't answer that yet" with examples)."""

from typing import Any

import pytest

from lookahead.ask.offline import parse_question
from lookahead.ask.plan import check_phrases, parse


def plan_of(question: str) -> list[dict[str, Any]]:
    data = parse_question(question)
    assert data is not None, question
    check_phrases(parse(data), question)
    return data["steps"]  # type: ignore[no-any-return]


def last(question: str) -> dict[str, Any]:
    return plan_of(question)[-1]


def first(question: str) -> dict[str, Any]:
    return plan_of(question)[0]


@pytest.mark.parametrize(
    "question",
    [
        "How much did I spend on groceries last month?",
        "Combien j'ai dépensé en épicerie le mois dernier ?",
    ],
)
def test_category_total_over_a_period(question: str) -> None:
    rows = first(question)
    assert rows["categories"] == ["Groceries"] and rows["period"] == {"kind": "last_month"}
    assert last(question)["measure"] == "sum"


@pytest.mark.parametrize(
    ("question", "merchant"),
    [
        ("how much did i spend at Cafe Velisaro in March?", "cafe velisaro"),
        ("Combien ai-je dépensé chez Quorvel cette année ?", "quorvel"),
    ],
)
def test_merchant_total(question: str, merchant: str) -> None:
    assert first(question)["merchants"] == [merchant]


@pytest.mark.parametrize(
    "question",
    [
        "What's my average monthly spending on restaurants?",
        "Combien je dépense au resto en moyenne par mois ?",
    ],
)
def test_average_a_month(question: str) -> None:
    steps = plan_of(question)
    assert steps[0]["categories"] == ["Restaurants & cafés"]
    assert steps[1]["by"] == "month" and steps[2]["measure"] == "average"


@pytest.mark.parametrize(
    ("question", "measure"),
    [
        ("How many times did I go to Pho Tavo last month?", "count"),
        ("Combien de fois chez Pho Tavo ce mois-ci ?", "count"),
        ("What was my biggest purchase last month?", "max"),
        ("Ma plus grosse dépense le mois dernier ?", "max"),
    ],
)
def test_count_and_largest(question: str, measure: str) -> None:
    assert last(question)["measure"] == measure


@pytest.mark.parametrize(
    "question", ["How much did I earn last month?", "Combien j'ai reçu le mois dernier ?"]
)
def test_income(question: str) -> None:
    rows = first(question)
    assert rows["direction"] == "in" and rows["kinds"] == ["income"]


@pytest.mark.parametrize(
    ("question", "n", "by"),
    [
        ("Top 3 categories this year", 3, "category"),
        ("Quelles sont mes 4 principales catégories le mois dernier ?", 4, "category"),
        ("top merchants last month", 5, "merchant"),
    ],
)
def test_top(question: str, n: int, by: str) -> None:
    steps = plan_of(question)
    assert steps[1]["by"] == by and steps[2]["op"] == "top" and steps[2]["n"] == n


@pytest.mark.parametrize(
    ("question", "day"),
    [
        ("What will my balance be in 2 weeks?", {"kind": "in_days", "n": 14}),
        ("Quel sera mon solde dans 10 jours ?", {"kind": "in_days", "n": 10}),
        ("Quel sera mon solde à la fin du mois ?", {"kind": "end_of_month"}),
        ("balance on the 15th?", {"kind": "day_of_month", "day": 15}),
        ("What will my balance be on December 3?", {"kind": "named_date", "month": 12, "day": 3}),
    ],
)
def test_balance_on_a_day(question: str, day: dict[str, Any]) -> None:
    step = last(question)
    assert step["measure"] == "balance_on" and step["on"] == day


@pytest.mark.parametrize(
    ("question", "below", "until"),
    [
        ("What's the chance I go below my buffer this month?", None, {"kind": "end_of_month"}),
        ("Quel est le risque de passer sous zéro ?", {"dollars": 0}, None),
        (
            "What are the odds I dip below $200 in the next 2 weeks?",
            {"dollars": 200.0},
            {"kind": "in_days", "n": 14},
        ),
    ],
)
def test_chance_of_a_dip(question: str, below: Any, until: Any) -> None:
    step = last(question)
    assert step["measure"] == "chance_below"
    assert step.get("below") == below and step.get("until") == until


@pytest.mark.parametrize(
    ("question", "op", "extra"),
    [
        ("What's my lowest balance going to be?", "forecast", {"measure": "lowest"}),
        ("How much is safe to spend?", "safe_to_spend", {}),
        ("Combien puis-je dépenser ?", "safe_to_spend", {}),
        ("How am I doing on my shopping budget?", "budget", {"category": "Shopping"}),
        ("Où en est mon budget ?", "budget", {}),
        ("What are my recurring payments?", "recurring", {}),
        ("Quels sont mes paiements récurrents ?", "recurring", {}),
    ],
)
def test_single_block_shapes(question: str, op: str, extra: dict[str, Any]) -> None:
    step = last(question)
    assert step["op"] == op and {k: step.get(k) for k in extra} == extra


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("Should I buy a car?", "advice"),
        ("Est-ce que je devrais annuler Netflix ?", "advice"),
        ("What's the price of bitcoin?", "not_in_data"),
        ("Should I invest in stocks?", "advice"),
    ],
)
def test_advice_and_outside_the_data_are_refused(question: str, reason: str) -> None:
    assert last(question) == {"op": "cannot_answer", "reason": reason}


@pytest.mark.parametrize("question", ["Tell me a joke", "What is my name?", "Write me a poem", ""])
def test_anything_else_is_not_guessed(question: str) -> None:
    assert parse_question(question) is None
