"""From a question to what the page shows: the plan, the run, and the answer in a sentence.

`ask_offline` uses the offline parser. Every sentence here is built by code from computed numbers
and fixed wording; nothing is written by an LLM. Forecast figures say "forecast" and "estimate".
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from lookahead.ask.engine import (
    BudgetAnswer,
    ForecastAnswer,
    RecurringAnswer,
    SafeAnswer,
    WhatIfAnswer,
)
from lookahead.ask.match import Match
from lookahead.ask.offline import parse_question
from lookahead.ask.plan import Aggregate, Compare, Plan, PlanError, Rows, check_phrases, parse
from lookahead.ask.run import Context, Listing, Number, Outcome, Refusal, Table, run
from lookahead.ask.words import EXAMPLES, MEASURE, period_words

CANT_YET = "I can't answer that yet."


@dataclass
class Asked:
    question: str
    plan: Plan | None
    outcome: Outcome | None
    problems: list[str] = field(default_factory=list)  # a plan that failed its checks

    @property
    def show_examples(self) -> bool:
        """The refusal is shown with examples of what can be answered."""
        return self.outcome is None or self.outcome.kind == "refused"


def ask_offline(question: str, ctx: Context) -> Asked:
    """The offline parser's plan, checked and run. No plan: "I can't answer that yet"."""
    data = parse_question(question)
    if data is None:
        return Asked(question, None, None)
    try:
        plan = parse(data)
        check_phrases(plan, question)
    except PlanError as e:  # the parser's own plans always pass; kept for safety
        return Asked(question, None, None, e.problems)
    return Asked(question, plan, run(plan, ctx))


# --- Sentences ---


def dollars(cents: float | int) -> str:
    sign = "−" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _pct(share: float) -> str:
    return f"{share:.0%}"


def _rows_words(plan: Plan, name: str, matches: list[Match]) -> str:
    step = next(s for s in plan.steps if s.name == name)
    if not isinstance(step, Rows):
        return ""
    parts = []
    if step.categories:
        parts.append("in " + " or ".join(step.categories))
    if step.merchants:
        found = [m.names[0] for m in matches if m.status == "one" and m.phrase in step.merchants]
        parts.append("at " + " or ".join(found) if found else "at the merchants matched")
    parts.append(period_words(step.period))
    return ", ".join(parts)


def _number(plan: Plan, r: Number, matches: list[Match]) -> str:
    if r.value is None:
        return "There is nothing to work it out from: no rows match."
    step = next(s for s in plan.steps if s.name == r.name)
    if r.unit == "percent":
        if isinstance(step, Compare) and step.how == "percent_change":
            way = "up" if r.value >= 0 else "down"
            return f"{abs(r.value):.1f}% {way} (the change from “{step.b}” to “{step.a}”)."
        return f"{r.value:.1f}%."
    value = f"{int(r.value):,}" if r.unit == "count" else dollars(r.value)
    if isinstance(step, Aggregate):
        source = next(s for s in plan.steps if s.name == step.source)
        if isinstance(source, Rows):
            what = _rows_words(plan, source.name, matches)
            if step.measure == "count":
                return f"{value} transactions ({what})."
            if step.measure in ("max", "min") and r.row:
                merchant = r.row.get("merchant")
                at = f" at {merchant}" if isinstance(merchant, str) else ""
                return (f"The {MEASURE[step.measure]} was {value}{at} on "
                        f"{r.row['date']:%Y-%m-%d} ({what}).")  # fmt: skip
            verb = {"out": "You spent", "in": "You received", "both": "Net"}[source.direction]
            if step.measure == "average":
                return f"{value} on average per transaction ({what})."
            return f"{verb} {value} ({what})."
        return f"The {MEASURE[step.measure]} of “{step.source}”: {value}."
    if isinstance(step, Compare) and step.how == "difference":
        return f"“{step.a}” minus “{step.b}”: {value}."
    return f"{value}."


def headline(asked: Asked) -> str:
    """The answer in one or two sentences."""
    out = asked.outcome
    if out is None:
        return CANT_YET
    r = out.result
    if out.kind != "answer":
        return str(r.message)
    plan = asked.plan
    assert plan is not None
    if isinstance(r, Number):
        return _number(plan, r, out.matches)
    if isinstance(r, Table):
        if r.by == "top":
            return f"The top {len(r.pairs)}:"
        return f"By {r.by}:"
    if isinstance(r, Listing):
        return f"{r.total} transactions match; showing {len(r.rows)}."
    if isinstance(r, ForecastAnswer):
        return _forecast(r)
    if isinstance(r, WhatIfAnswer):
        return _whatif(r)
    if isinstance(r, BudgetAnswer):
        return " ".join(_budget_line(b) for b in r.lines)
    if isinstance(r, SafeAnswer):
        return r.sentence
    if isinstance(r, RecurringAnswer):
        n = len(r.lines)
        return f"{n} recurring {'payment' if n == 1 else 'payments'} found." if n else (
            "No recurring payments found.")  # fmt: skip
    return ""


def _forecast(r: ForecastAnswer) -> str:
    if r.measure == "balance_on":
        assert r.middle_cents is not None and r.low_cents is not None
        assert r.high_cents is not None
        return (f"Your balance at the end of {r.day:%b} {r.day.day} is forecast at about "
                f"{dollars(r.middle_cents)} (10 in 100 simulated futures end below "
                f"{dollars(r.low_cents)}, 10 in 100 above {dollars(r.high_cents)}). "
                "An estimate.")  # fmt: skip
    if r.measure == "lowest":
        assert r.middle_cents is not None and r.lowest_day is not None
        return (f"Your lowest balance up to {r.day:%b} {r.day.day} is forecast at about "
                f"{dollars(r.middle_cents)}, on {r.lowest_day:%b} {r.lowest_day.day} "
                "(the middle future). An estimate.")  # fmt: skip
    assert r.chance is not None and r.below_cents is not None
    what = "your buffer of " if r.below_is_buffer else ""
    n = round(r.chance * r.futures)
    return (f"A {_pct(r.chance)} chance your balance goes below {what}{dollars(r.below_cents)} "
            f"on some day up to {r.day:%b} {r.day.day} ({n} of {r.futures} simulated futures). "
            "An estimate.")  # fmt: skip


def _whatif(r: WhatIfAnswer) -> str:
    return (f"With the change, your lowest balance is forecast at {dollars(r.low_after_cents)} "
            f"instead of {dollars(r.low_now_cents)}, and the chance of going below your "
            f"{dollars(r.buffer_cents)} buffer at {_pct(r.chance_after)} instead of "
            f"{_pct(r.chance_now)} (the same {r.futures} simulated futures). "
            "An estimate.")  # fmt: skip


def _budget_line(b: Any) -> str:
    if b.already_over:
        return (f"{b.category}: you're already over this budget ({dollars(b.spent_cents)} spent "
                f"of {dollars(b.limit_cents)}).")  # fmt: skip
    return (f"{b.category}: {dollars(b.spent_cents)} spent of {dollars(b.limit_cents)}; likely "
            f"end of month about {dollars(b.likely_end_cents)}, a {_pct(b.chance_over)} chance "
            "of going over (an estimate).")  # fmt: skip


# --- Tables ---


def result_table(r: Any) -> pd.DataFrame | None:
    """The answer as a table, when it has one (money in dollars for display only)."""
    if isinstance(r, Table):
        head = {"month": "Month", "week": "Week", "weekday": "Weekday", "category": "Category",
                "merchant": "Merchant", "top": "Name"}[r.by]  # fmt: skip
        value = "Count" if r.unit == "count" else "Amount ($)"
        return pd.DataFrame({
            head: [p[0] for p in r.pairs],
            value: [None if p[1] is None else (p[1] if r.unit == "count" else p[1] / 100)
                    for p in r.pairs],
        })  # fmt: skip
    if isinstance(r, RecurringAnswer):
        return pd.DataFrame({
            "Name": [x.name for x in r.lines],
            "In or out": [x.direction for x in r.lines],
            "How often": [x.cadence for x in r.lines],
            "Usual amount ($)": [x.usual_cents / 100 for x in r.lines],
            "Next expected": [f"{x.next_day:%Y-%m-%d}" for x in r.lines],
        })  # fmt: skip
    if isinstance(r, BudgetAnswer):
        return pd.DataFrame({
            "Category": [b.category for b in r.lines],
            "Limit ($)": [b.limit_cents / 100 for b in r.lines],
            "Spent so far ($)": [b.spent_cents / 100 for b in r.lines],
            "Likely end ($)": [b.likely_end_cents / 100 for b in r.lines],
            "Chance over": [_pct(b.chance_over) for b in r.lines],
        })  # fmt: skip
    return None


def step_value(r: Any) -> str:
    """One step's result in a few words, for 'How I worked it out'."""
    if isinstance(r, Number):
        if r.value is None:
            return "nothing to work it out from"
        if r.unit == "percent":
            return f"{r.value:.1f}%"
        return f"{int(r.value):,}" if r.unit == "count" else dollars(r.value)
    if isinstance(r, Table):
        return f"{len(r.pairs)} groups"
    if isinstance(r, Listing):
        return f"{len(r.rows)} of {r.total} rows"
    rows = getattr(r, "rows", None)
    if isinstance(rows, pd.DataFrame):
        n = len(rows)
        return f"{n} {'row' if n == 1 else 'rows'}"
    if isinstance(r, Refusal):
        return r.message
    return ""


__all__ = ["EXAMPLES", "Asked", "ask_offline", "headline", "result_table", "step_value"]
