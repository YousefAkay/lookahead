"""'How I worked it out': a checked plan in plain words, written by code from the plan's fields.

Nothing here comes from the plan's author as free text: every word is fixed wording or a value
of a checked field (a category from the fixed list, a number, a day). The only text copied from
the plan is a name phrase, which `plan.check_phrases` has already found in my own question.
The refusal and ask-back messages are fixed sentences chosen by a reason code.
"""

from calendar import month_name

from lookahead.ask.plan import (
    Aggregate,
    Amount,
    Budget,
    CannotAnswer,
    Change,
    Clarify,
    Compare,
    Day,
    Forecast,
    Group,
    ListRows,
    Period,
    Plan,
    Recurring,
    Rows,
    SafeToSpend,
    Top,
    WhatIf,
)

REFUSALS: dict[str, str] = {
    "advice": "I can't give advice or opinions. I can only work out figures from your data.",
    "not_in_data": "I can't answer that: it is not something your bank data shows.",
    "beyond_horizon": "I can't answer that: I forecast only 30 days ahead of your data.",
    "unsupported": "I can't answer that yet.",
}
CLARIFY: dict[str, str] = {
    "subject": "Which category, merchant or payment do you mean? Try asking again with a name.",
    "period": "Which period do you mean? Try asking again with a month, a year or 'last month'.",
    "amount": "Which amount do you mean? Try asking again with a dollar figure.",
    "which_one": "That could mean more than one thing. Try asking again more precisely.",
}
EXAMPLES: tuple[str, ...] = (
    "How much did I spend on groceries last month?",
    "What were my top 5 categories this year?",
    "What will my balance be in 2 weeks?",
    "What is the chance I go below my buffer this month?",
    "What if I spend $100 less on restaurants a month?",
    "How much is safe to spend?",
    "How am I doing on my Shopping budget?",
    "What are my recurring payments?",
)
MEASURE: dict[str, str] = {
    "sum": "total",
    "average": "average",
    "count": "number of rows",
    "max": "largest",
    "min": "smallest",
}


def money(dollars: float) -> str:
    return f"${dollars:,.2f}".replace(".00", "")


def quoted(phrase: str) -> str:
    return f"“{phrase}”"


def period_words(p: Period | None) -> str:
    if p is None or p.kind == "all":
        return "all your data"
    k = p.kind
    simple = {
        "today": "today",
        "yesterday": "yesterday",
        "this_week": "this week so far (from Monday)",
        "last_week": "last week (Monday to Sunday)",
        "this_month": "this month so far",
        "last_month": "last month",
        "this_year": "this year so far",
        "last_year": "last year",
    }
    if k in simple:
        return simple[k]
    if k == "last_n_days":
        return f"the last {p.n} days"
    if k == "last_n_weeks":
        return f"the last {p.n} full weeks"
    if k == "last_n_months":
        return f"the last {p.n} full months"
    if k == "month":
        return f"{month_name[p.month or 1]} {p.year}"
    if k == "named_month":
        return f"{month_name[p.month or 1]} (the latest one)"
    if k == "year":
        return f"{p.year}"
    if k == "range":
        return f"from {p.start} to {p.end}"
    return f"since {p.start}"  # since


def day_words(d: Day) -> str:
    k = d.kind
    if k == "tomorrow":
        return "tomorrow"
    if k == "in_days":
        return f"in {d.n} days"
    if k == "date":
        return f"on {d.date}"
    if k == "day_of_month":
        return f"on the next {_ordinal(d.day or 1)}"
    if k == "named_date":
        return f"on the next {month_name[d.month or 1]} {d.day}"
    if k == "end_of_week":
        return "at the end of this week (Sunday)"
    return "at the end of this month"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def amount_words(a: Amount) -> str:
    return money(a.dollars) if a.dollars is not None else f"the result of {quoted(a.ref or '')}"


def _rows(s: Rows) -> str:
    direction = {"out": "money out", "in": "money in", "both": "money in and out"}[s.direction]
    parts = [f"Find {direction}"]
    if s.categories:
        parts.append(f"in {' or '.join(s.categories)}")
    if s.merchants:
        parts.append(f"at merchants matching {' or '.join(quoted(m) for m in s.merchants)}")
    if s.kinds:
        parts.append(f"of kind {' or '.join(s.kinds)}")
    if s.min_dollars is not None:
        parts.append(f"of {money(s.min_dollars)} or more")
    if s.max_dollars is not None:
        parts.append(f"of at most {money(s.max_dollars)}")
    text = " ".join(parts[:2]) + "".join(f", {p}" for p in parts[2:])
    text += f", {period_words(s.period)}"
    if "transfer" not in s.kinds and "Transfers" not in s.categories:
        text += "; transfers between accounts left out"
    return text + "."


def _change(c: Change) -> str:
    if c.kind in ("category", "everyday"):
        what = f"on {c.category}" if c.kind == "category" else "on everyday spending"
        size = amount_words(c.amount) + " a month" if c.amount else f"{c.percent:g}%"
        return f"spend {size} {c.direction} {what}"
    if c.kind == "cancel":
        return f"stop the recurring payment {quoted(c.target or '')}"
    if c.kind == "swap":
        assert c.amount is not None
        return f"pay {amount_words(c.amount)} each time for {quoted(c.target or '')}"
    assert c.amount is not None
    when = day_words(c.day) if c.day else "on the first forecast day"
    way = "a deposit" if c.direction == "in" else "a payment"
    return f"add {way} of {amount_words(c.amount)} {when}"


def step_words(step: object) -> str:
    """One step in plain words."""
    if isinstance(step, Rows):
        return _rows(step)
    if isinstance(step, Group):
        return (f"Group {quoted(step.source)} by {step.by}: the "
                f"{MEASURE[step.measure]} of each.")  # fmt: skip
    if isinstance(step, Aggregate):
        return f"The {MEASURE[step.measure]} of {quoted(step.source)}."
    if isinstance(step, Top):
        return f"The {step.n} {step.order} of {quoted(step.source)}."
    if isinstance(step, Compare):
        a, b = quoted(step.a), quoted(step.b)
        return {
            "difference": f"{a} minus {b}.",
            "percent_change": f"The change from {b} to {a}, in percent.",
            "share": f"{a} as a percent of {b}.",
        }[step.how]
    if isinstance(step, ListRows):
        return f"List {quoted(step.source)}, {step.order} first, up to {step.limit} rows."
    if isinstance(step, Forecast):
        until = day_words(step.until) if step.until else "over the next 30 days"
        if step.measure == "balance_on":
            assert step.on is not None
            return f"Forecast your balance at the end of the day {day_words(step.on)}."
        if step.measure == "lowest":
            return f"Forecast your lowest balance, up to and {until}."
        below = amount_words(step.below) if step.below else "your safety buffer"
        return f"Forecast the chance of going below {below}, up to and {until}."
    if isinstance(step, WhatIf):
        changes = "; ".join(_change(c) for c in step.changes)
        return (f"What if you {changes}? Compare your lowest balance and the chance of going "
                "below your buffer over the next 30 days, as things are now and with the "
                "change (the same simulated futures).")  # fmt: skip
    if isinstance(step, Budget):
        return (f"Your {step.category} budget this month." if step.category
                else "All your budgets this month.")  # fmt: skip
    if isinstance(step, SafeToSpend):
        return "Safe to spend (the same figure as Home)."
    if isinstance(step, Recurring):
        which = {"out": "payments", "in": "deposits", "both": "payments and deposits"}
        found = f" matching {quoted(step.find)}" if step.find else ""
        return f"Your recurring {which[step.direction]}{found}."
    if isinstance(step, CannotAnswer):
        return REFUSALS[step.reason]
    if isinstance(step, Clarify):
        return CLARIFY[step.about]
    raise TypeError(f"unknown step {type(step).__name__}")


def plan_words(plan: Plan) -> list[str]:
    """Numbered lines, one per step, naming each result so later steps can point at it."""
    lines = []
    for i, step in enumerate(plan.steps, start=1):
        label = "" if isinstance(step, CannotAnswer | Clarify) else f" ({quoted(step.name)})"
        lines.append(f"{i}. {step_words(step)}{label}")
    return lines
