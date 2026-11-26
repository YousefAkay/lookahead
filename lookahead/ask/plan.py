"""The plan format: a list of named steps, each one building block, checked before anything runs.

A plan is data, never code. Pydantic checks each step's fields (unknown fields are refused, the
categories come from the fixed list); `check` then checks the plan as a whole: at most 10 steps,
unique names, a step uses only names defined before it, and the types fit (you cannot average a
number, and a what-if amount must be money).

Periods and days are words ("last_month", "in_days": 10) that `periods` turns into dates on this
machine, so whoever writes the plan never needs today's date or the range of my data.

Every error message is built from step positions, names and field names only, never from data:
it is what the LLM gets back on its one retry (spec, "Plan checks").
"""

import re
import unicodedata
from datetime import date
from datetime import date as Date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lookahead.taxonomy import CATEGORIES

MAX_STEPS = 10
MAX_TOP = 25
MAX_LIST = 200
MAX_DOLLARS = 1_000_000.0
MAX_CHANGES = 5
NAME_PATTERN = r"^[a-z][a-z0-9_]{0,29}$"
MAX_PHRASE = 60

Category = Literal[CATEGORIES]  # type: ignore[valid-type]
Kind = Literal["income", "bill", "everyday", "transfer", "refund"]
# What a kind means in the stored flow types (lookahead.taxonomy.FLOW_TYPES).
FLOW_OF_KIND: dict[str, str] = {
    "income": "income",
    "bill": "recurring_outflow",
    "everyday": "everyday",
    "transfer": "transfer",
    "refund": "refund",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Periods (past) and days (future) ---

PeriodKind = Literal[
    "all", "today", "yesterday", "this_week", "last_week", "this_month", "last_month",
    "this_year", "last_year", "last_n_days", "last_n_weeks", "last_n_months", "month",
    "named_month", "year", "range", "since", "since_named",
]  # fmt: skip
_PERIOD_FIELDS: dict[str, set[str]] = {
    "last_n_days": {"n"},
    "last_n_weeks": {"n"},
    "last_n_months": {"n"},
    "month": {"year", "month"},
    "named_month": {"month"},
    "year": {"year"},
    "range": {"start", "end"},
    "since": {"start"},
    "since_named": {"month", "day"},  # "since July 1": the latest July 1 by today
}
_N_LIMIT = {"last_n_days": 731, "last_n_weeks": 104, "last_n_months": 24}


class Period(Strict):
    """A stretch of past days. `named_month` is a month without a year ("in March"): the latest
    one that has started by today."""

    kind: PeriodKind
    n: int | None = None
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _fields_fit_the_kind(self) -> "Period":
        _need_exactly(self, _PERIOD_FIELDS.get(self.kind, set()), ("n", "year", "month", "day",
                                                                   "start", "end"))  # fmt: skip
        if self.n is not None and not 1 <= self.n <= _N_LIMIT[self.kind]:
            raise ValueError(f"n must be from 1 to {_N_LIMIT[self.kind]}")
        if self.kind == "range" and self.start and self.end and self.end < self.start:
            raise ValueError("the range must not end before it starts")
        return self


DayKind = Literal[
    "tomorrow", "in_days", "date", "day_of_month", "named_date", "end_of_week", "end_of_month"
]
_DAY_FIELDS: dict[str, set[str]] = {
    "in_days": {"n"},
    "date": {"date"},
    "day_of_month": {"day"},
    "named_date": {"month", "day"},
}


class Day(Strict):
    """A future day. `day_of_month` ("on the 15th") and `named_date` ("on March 3") are the next
    such day after my data ends."""

    kind: DayKind
    n: int | None = Field(default=None, ge=1, le=366)
    date: Date | None = None
    day: int | None = Field(default=None, ge=1, le=31)
    month: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def _fields_fit_the_kind(self) -> "Day":
        _need_exactly(self, _DAY_FIELDS.get(self.kind, set()), ("n", "date", "day", "month"))
        return self


def _need_exactly(model: BaseModel, needed: set[str], every: tuple[str, ...]) -> None:
    for f in every:
        given = getattr(model, f) is not None
        if f in needed and not given:
            raise ValueError(f"'{f}' is needed for this kind")
        if f not in needed and given:
            raise ValueError(f"'{f}' is not used for this kind")


class Amount(Strict):
    """Dollars, or the name of an earlier step whose result is money."""

    dollars: float | None = Field(default=None, ge=0, le=MAX_DOLLARS)
    ref: str | None = Field(default=None, pattern=NAME_PATTERN)

    @model_validator(mode="after")
    def _one_of(self) -> "Amount":
        if (self.dollars is None) == (self.ref is None):
            raise ValueError("give dollars or ref, not both and not neither")
        return self


Phrase = Annotated[str, Field(min_length=1, max_length=MAX_PHRASE)]
Name = Annotated[str, Field(pattern=NAME_PATTERN)]


# --- The building blocks ---


class Rows(Strict):
    """Transactions that match every filter given. Transfers are left out unless the kinds or
    categories ask for them; `direction` "out" is money out, "in" money in, "both" either."""

    op: Literal["rows"]
    name: Name
    categories: list[Category] = Field(default_factory=list, max_length=len(CATEGORIES))  # type: ignore[valid-type]
    merchants: list[Phrase] = Field(default_factory=list, max_length=5)
    period: Period | None = None  # None: all my data
    direction: Literal["out", "in", "both"] = "out"
    kinds: list[Kind] = Field(default_factory=list, max_length=5)
    min_dollars: float | None = Field(default=None, ge=0, le=MAX_DOLLARS)
    max_dollars: float | None = Field(default=None, gt=0, le=MAX_DOLLARS)

    @model_validator(mode="after")
    def _range(self) -> "Rows":
        if (self.min_dollars is not None and self.max_dollars is not None
                and self.max_dollars < self.min_dollars):  # fmt: skip
            raise ValueError("max_dollars must not be below min_dollars")
        return self


Measure = Literal["sum", "average", "count", "max", "min"]


class Group(Strict):
    op: Literal["group"]
    name: Name
    source: Name
    by: Literal["month", "week", "category", "merchant", "weekday"]
    measure: Measure = "sum"


class Aggregate(Strict):
    op: Literal["aggregate"]
    name: Name
    source: Name
    measure: Measure


class Top(Strict):
    op: Literal["top"]
    name: Name
    source: Name
    n: int = Field(default=5, ge=1, le=MAX_TOP)
    order: Literal["highest", "lowest"] = "highest"


class Compare(Strict):
    """difference: a - b; percent_change: the change from b to a, in percent; share: a as a
    percent of b."""

    op: Literal["compare"]
    name: Name
    a: Name
    b: Name
    how: Literal["difference", "percent_change", "share"]


class ListRows(Strict):
    op: Literal["list"]
    name: Name
    source: Name
    order: Literal["largest", "smallest", "latest", "earliest"] = "latest"
    limit: int = Field(default=20, ge=1, le=MAX_LIST)


class Forecast(Strict):
    """balance_on: the balance at the end of `on`; lowest: the lowest balance up to `until`;
    chance_below: the chance of going below `below` (my buffer if not given) up to `until`.
    Without `until`: the forecast's 30 days."""

    op: Literal["forecast"]
    name: Name
    measure: Literal["balance_on", "lowest", "chance_below"]
    on: Day | None = None
    until: Day | None = None
    below: Amount | None = None

    @model_validator(mode="after")
    def _fields(self) -> "Forecast":
        if (self.measure == "balance_on") != (self.on is not None):
            raise ValueError("'on' is needed for balance_on and only for it")
        if self.measure == "balance_on" and self.until is not None:
            raise ValueError("'until' is not used for balance_on")
        if self.below is not None and self.measure != "chance_below":
            raise ValueError("'below' is used only for chance_below")
        return self


class Change(Strict):
    """One what-if change.
    - category: spend more or less in a category, `amount` a month or `percent`;
    - everyday: the same for all everyday spending;
    - cancel: stop a recurring payment (`target`: its name as I wrote it);
    - swap: a recurring payment costs `amount` each time instead;
    - one_off: one payment (out) or deposit (in) of `amount` on `day` (default: the first day)."""

    kind: Literal["category", "everyday", "cancel", "swap", "one_off"]
    category: Category | None = None  # type: ignore[valid-type]
    target: Phrase | None = None
    direction: Literal["more", "less", "in", "out"] | None = None
    amount: Amount | None = None
    percent: float | None = Field(default=None, gt=0, le=100)
    day: Day | None = None

    @model_validator(mode="after")
    def _fields(self) -> "Change":
        k = self.kind
        need = {
            "category": {"category", "direction"},
            "everyday": {"direction"},
            "cancel": {"target"},
            "swap": {"target", "amount"},
            "one_off": {"direction", "amount"},
        }[k]
        may = need | ({"amount", "percent"} if k in ("category", "everyday") else set())
        may |= {"day"} if k == "one_off" else set()
        for f in ("category", "target", "direction", "amount", "percent", "day"):
            given = getattr(self, f) is not None
            if f in need and not given:
                raise ValueError(f"'{f}' is needed for a {k} change")
            if given and f not in may:
                raise ValueError(f"'{f}' is not used for a {k} change")
        if k in ("category", "everyday"):
            if (self.amount is None) == (self.percent is None):
                raise ValueError(f"a {k} change needs amount or percent, not both")
            if self.direction not in ("more", "less"):
                raise ValueError(f"a {k} change's direction is more or less")
            if self.direction == "less" and self.percent is not None and self.percent > 100:
                raise ValueError("percent at most 100")
        if k == "one_off" and self.direction not in ("in", "out"):
            raise ValueError("a one_off change's direction is in or out")
        return self


class WhatIf(Strict):
    op: Literal["whatif"]
    name: Name
    changes: list[Change] = Field(min_length=1, max_length=MAX_CHANGES)


class Budget(Strict):
    op: Literal["budget"]
    name: Name
    category: Category | None = None  # type: ignore[valid-type]  # None: every budget


class SafeToSpend(Strict):
    op: Literal["safe_to_spend"]
    name: Name


class Recurring(Strict):
    op: Literal["recurring"]
    name: Name
    find: Phrase | None = None  # a name as I wrote it; None: all of them
    direction: Literal["out", "in", "both"] = "out"


class CannotAnswer(Strict):
    op: Literal["cannot_answer"]
    name: Name = "answer"
    reason: Literal["advice", "not_in_data", "beyond_horizon", "unsupported"]


class Clarify(Strict):
    op: Literal["clarify"]
    name: Name = "answer"
    about: Literal["subject", "period", "amount", "which_one"]


Step = Annotated[
    Rows
    | Group
    | Aggregate
    | Top
    | Compare
    | ListRows
    | Forecast
    | WhatIf
    | Budget
    | SafeToSpend
    | Recurring
    | CannotAnswer
    | Clarify,
    Field(discriminator="op"),
]


class Plan(Strict):
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)


# --- Types, for the whole-plan check ---

# What each block gives: rows, table, number, list, or a result that only shows.
_GIVES = {
    "rows": "rows", "group": "table", "top": "table", "aggregate": "number", "compare": "number",
    "list": "list", "forecast": "forecast", "whatif": "whatif", "budget": "budget",
    "safe_to_spend": "safe", "recurring": "recurring", "cannot_answer": "refusal",
    "clarify": "clarify",
}  # fmt: skip


class PlanError(ValueError):
    """The plan failed its checks. `problems` are short sentences with no data in them."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def unit_of(steps: list[BaseModel], name: str) -> str:
    """The unit of a step's numbers: money, count or percent. "unknown" when its chain of
    sources is broken (a source that is not an earlier step): the whole-plan check reports that."""
    at = next((i for i, s in enumerate(steps) if s.name == name), None)  # type: ignore[attr-defined]
    if at is None:
        return "unknown"
    step, earlier = steps[at], steps[:at]  # sources are earlier steps only, so this ends
    if isinstance(step, Group | Aggregate):
        return "count" if step.measure == "count" else unit_of(earlier, step.source)
    if isinstance(step, Top):
        return unit_of(earlier, step.source)
    if isinstance(step, Compare):
        return unit_of(earlier, step.a) if step.how == "difference" else "percent"
    return "money"


def check(plan: Plan) -> None:
    """Raise PlanError unless the plan holds together as a whole."""
    problems: list[str] = []
    seen: dict[str, str] = {}  # name -> what it gives
    steps: list[BaseModel] = list(plan.steps)

    def needs(i: int, step_name: str, ref: str, field: str, kinds: tuple[str, ...]) -> bool:
        if ref not in seen:
            problems.append(f"step {i} ('{step_name}'): '{field}' names no earlier step")
            return False
        if seen[ref] not in kinds:
            problems.append(f"step {i} ('{step_name}'): '{field}' must name a step giving "
                            f"{' or '.join(kinds)}, not {seen[ref]}")  # fmt: skip
            return False
        return True

    for i, step in enumerate(plan.steps, start=1):
        name = step.name
        if isinstance(step, CannotAnswer | Clarify) and len(plan.steps) > 1:
            problems.append(f"step {i} ('{name}'): {step.op} must be the only step")
        if name in seen:
            problems.append(f"step {i}: the name '{name}' is used twice")
        if isinstance(step, Group | ListRows):
            needs(i, name, step.source, "source", ("rows",))
        elif isinstance(step, Aggregate):
            if needs(i, name, step.source, "source", ("rows", "table")) and (
                step.measure == "count" and seen[step.source] == "table"
            ):
                problems.append(f"step {i} ('{name}'): count works on rows, not on a table")
        elif isinstance(step, Top):
            needs(i, name, step.source, "source", ("table",))
        elif isinstance(step, Compare):
            ok = needs(i, name, step.a, "a", ("number",)) & needs(i, name, step.b, "b",
                                                                   ("number",))  # fmt: skip
            if ok and unit_of(steps, step.a) != unit_of(steps, step.b):
                problems.append(f"step {i} ('{name}'): 'a' and 'b' must have the same unit")
        elif isinstance(step, Forecast) and step.below is not None and step.below.ref:
            _money_ref(i, name, step.below.ref, "below", seen, steps, problems, needs)
        elif isinstance(step, WhatIf):
            for change in step.changes:
                if change.amount is not None and change.amount.ref:
                    _money_ref(i, name, change.amount.ref, "amount", seen, steps, problems,
                               needs)  # fmt: skip
        seen.setdefault(name, _GIVES[step.op])
    last = plan.steps[-1]
    if _GIVES[last.op] == "rows":
        problems.append("the last step gives rows: end with list, aggregate or group")
    if problems:
        raise PlanError(problems)


def _money_ref(i, name, ref, field, seen, steps, problems, needs) -> None:  # type: ignore[no-untyped-def]
    if needs(i, name, ref, field, ("number",)) and unit_of(steps, ref) != "money":
        problems.append(f"step {i} ('{name}'): '{field}' must name a money amount")


def parse(data: object) -> Plan:
    """A checked plan from JSON-like data, or PlanError with data-free problems."""
    try:
        plan = Plan.model_validate(data)
        check(plan)
    except ValidationError as e:
        raise PlanError([_problem(err) for err in e.errors()]) from None
    except PlanError:
        raise
    except Exception:  # an odd shape the checks did not foresee: failed checks, never a crash
        raise PlanError(["the plan could not be checked"]) from None
    return plan


def _problem(err: dict) -> str:  # type: ignore[type-arg]
    """One pydantic error as a short sentence: where it is and what is wrong, without the input
    value (the value might be anything the plan's author wrote)."""
    where = ".".join(str(p) for p in err["loc"]) or "plan"
    kind = err["type"]
    message = {
        "missing": "is missing",
        "extra_forbidden": "is not a known field",
        "literal_error": "is not one of the allowed values",
        "union_tag_invalid": "is not a known block",
        "union_tag_not_found": "needs an 'op'",
        "string_pattern_mismatch": "must be a short lowercase name (letters, digits, _)",
        "too_long": "is too long",
        "too_short": "is too short",
    }.get(kind)
    if message is None:
        message = str(err.get("msg", "is not valid"))
        message = message.removeprefix("Value error, ")
    return f"{where}: {message}"


def _plain(text: str) -> str:
    """Lowercase, no accents, punctuation as spaces, single spaces: how a phrase is looked for in
    the question ("Hydro-Québec" is found in "hydro quebec")."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", letters).split())


def phrases(plan: Plan) -> list[str]:
    """Every name phrase in the plan (merchants, recurring payments to find, cancel or swap)."""
    out: list[str] = []
    for step in plan.steps:
        if isinstance(step, Rows):
            out.extend(step.merchants)
        elif isinstance(step, Recurring) and step.find:
            out.append(step.find)
        elif isinstance(step, WhatIf):
            out.extend(c.target for c in step.changes if c.target)
    return out


def check_phrases(plan: Plan, question: str) -> None:
    """A name phrase must be copied from the question (ignoring case, accents and spacing), so a
    plan cannot bring in a name I did not write."""
    asked = _plain(question)
    missing = [p for p in phrases(plan) if _plain(p) not in asked]
    if missing:
        raise PlanError(
            [f"{len(missing)} name phrase(s) are not copied from the question; copy them exactly"]
        )
