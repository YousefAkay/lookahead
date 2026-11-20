"""Run a checked plan on this machine, step by step, with the code the other pages use.

Rows come from `lookahead.views.transactions`, the same rows (and categories, my corrections and
series categories included) the Transactions page shows. Money is integer cents; a row's value is
its size (money out as a positive number) when the rows are one direction, and its signed amount
when they are both.

Nothing here is sent anywhere. The outcome carries everything the page shows: the answer, each
step's result in plain words, notes (data ends earlier, a period cut), merchant matches and the
rows behind the answer.
"""

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from lookahead.ask import periods
from lookahead.ask.match import Match, match, stored_aliases
from lookahead.ask.plan import (
    FLOW_OF_KIND,
    Aggregate,
    CannotAnswer,
    Clarify,
    Compare,
    Group,
    ListRows,
    Plan,
    Rows,
    Top,
)
from lookahead.ask.words import CLARIFY, REFUSALS, plan_words
from lookahead.views import has_categories, transactions

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NO_MERCHANT = "(no merchant name)"
NOTHING_MATCHES = "No rows match."


@dataclass
class Context:
    """What a plan runs against. `today` is the app's today (demo mode and the test set: the day
    after the data ends). `choices` are my picks when a phrase matched several merchants."""

    conn: sqlite3.Connection
    account: str
    today: date
    key: bytes | None = None
    choices: Mapping[str, str] = field(default_factory=dict)
    limits: Mapping[str, int | None] | None = None  # demo mode: the session's budgets
    _cache: dict[str, Any] = field(default_factory=dict)

    def cached(self, name: str, make: Callable[[], Any]) -> Any:
        if name not in self._cache:
            self._cache[name] = make()
        return self._cache[name]

    def first_last(self) -> tuple[date, date] | None:
        def make() -> tuple[date, date] | None:
            lo, hi = self.conn.execute(
                "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
                (self.account,),
            ).fetchone()
            return (date.fromisoformat(lo), date.fromisoformat(hi)) if lo else None

        return self.cached("first_last", make)  # type: ignore[no-any-return]

    def all_rows(self) -> pd.DataFrame:
        return self.cached("rows", lambda: transactions(self.conn, self.account))  # type: ignore[no-any-return]

    def aliases(self) -> dict[str, str]:
        return self.cached("aliases", lambda: stored_aliases(self.conn))  # type: ignore[no-any-return]

    def merchant_names(self) -> list[str]:
        rows = self.all_rows()
        return sorted({m for m in rows["merchant"] if isinstance(m, str)})


# --- Results ---


@dataclass(frozen=True)
class RowSet:
    name: str
    rows: pd.DataFrame  # newest first, the Transactions page's columns
    direction: str
    span: periods.Span

    def values(self) -> pd.Series:
        """Each row's value in cents: its size for one direction, signed for both."""
        amounts = self.rows["amount_cents"]
        return amounts.abs() if self.direction != "both" else amounts


@dataclass(frozen=True)
class Table:
    name: str
    by: str  # month / week / weekday / category / merchant / top
    unit: str  # money / count
    pairs: tuple[tuple[str, int | None], ...]  # label, value (None: no rows in that group)


@dataclass(frozen=True)
class Number:
    name: str
    unit: str  # money (cents) / count / percent
    value: float | int | None  # None: nothing to compute from (no rows, a share of $0)
    row: dict[str, Any] | None = None  # max / min of rows: the row itself


@dataclass(frozen=True)
class Listing:
    name: str
    rows: pd.DataFrame
    total: int  # how many rows matched before the limit


@dataclass(frozen=True)
class Refusal:
    reason: str

    @property
    def message(self) -> str:
        return REFUSALS[self.reason]


@dataclass(frozen=True)
class AskBack:
    about: str
    candidates: tuple[str, ...] = ()  # several merchants matched: pick one
    phrase: str = ""

    @property
    def message(self) -> str:
        if self.candidates:
            return f"More than one merchant matches “{self.phrase}”. Which one do you mean?"
        return CLARIFY[self.about]


@dataclass(frozen=True)
class NotFound:
    phrase: str
    what: str = "merchant"

    @property
    def message(self) -> str:
        return f"No {self.what} like “{self.phrase}” in your data."


@dataclass
class Outcome:
    kind: str  # answer / refused / clarify / not_found
    result: Any  # the last step's result (answer), or Refusal / AskBack / NotFound
    words: list[str]  # How I worked it out, one line per step
    values: dict[str, Any] = field(default_factory=dict)  # every step's result, by name
    notes: list[str] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)


class Stop(Exception):
    """Ends a run early with a refusal, an ask-back or a name not found."""

    def __init__(self, kind: str, result: Any) -> None:
        super().__init__(kind)
        self.kind, self.result = kind, result


# --- The row blocks ---


def _rows(step: Rows, ctx: Context, out: Outcome) -> RowSet:
    bounds = ctx.first_last()
    if bounds is None:
        raise Stop("refused", Refusal("no_data"))
    if not has_categories(ctx.conn, ctx.account):
        raise Stop("refused", Refusal("no_categories"))
    first, last = bounds
    span = periods.span(step.period, ctx.today, first, last)
    out.notes.extend(n for n in span.notes if n not in out.notes)
    df = ctx.all_rows()
    if span.empty:
        df = df.iloc[0:0]
    else:
        days = df["date"].dt.date
        df = df[(days >= span.start) & (days <= span.end)]
    if step.direction == "out":
        df = df[df["amount_cents"] < 0]
    elif step.direction == "in":
        df = df[df["amount_cents"] > 0]
    if "transfer" not in step.kinds and "Transfers" not in step.categories:
        df = df[df["flow_type"] != "transfer"]
    if step.kinds:
        df = df[df["flow_type"].isin([FLOW_OF_KIND[k] for k in step.kinds])]
    if step.categories:
        df = df[df["category"].isin(step.categories)]
    if step.merchants:
        names = [_merchant(p, ctx, out) for p in step.merchants]
        df = df[df["merchant"].isin(names)]
    size = df["amount_cents"].abs()
    if step.min_dollars is not None:
        df = df[size >= round(step.min_dollars * 100)]
        size = df["amount_cents"].abs()
    if step.max_dollars is not None:
        df = df[size <= round(step.max_dollars * 100)]
    return RowSet(step.name, df.reset_index(drop=True), step.direction, span)


def _merchant(phrase: str, ctx: Context, out: Outcome) -> str:
    if phrase in ctx.choices:
        chosen = ctx.choices[phrase]
        out.matches.append(Match(phrase, "one", (chosen,), "your pick"))
        return chosen
    m = match(phrase, ctx.merchant_names(), ctx.aliases())
    out.matches.append(m)
    if m.status == "none":
        raise Stop("not_found", NotFound(phrase))
    if m.status == "many":
        raise Stop("clarify", AskBack("which_one", m.names, phrase))
    return m.names[0]


def _labels(source: RowSet, by: str) -> tuple[pd.Series, list[str]]:
    """Each row's group label, and every label in order (empty months, weeks and weekdays of the
    period included, so an average counts a month with nothing spent as $0)."""
    rows = source.rows
    days = rows["date"].dt.date
    span = source.span
    if by == "month":
        labels = days.map(lambda d: f"{d:%Y-%m}")
        order = []
        if span.start is not None and span.end is not None:
            m = span.start.replace(day=1)
            while m <= span.end:
                order.append(f"{m:%Y-%m}")
                m = periods.add_months(m, 1)
        return labels, order
    if by == "week":
        labels = days.map(lambda d: f"week of {d - timedelta(days=d.weekday())}")
        order = []
        if span.start is not None and span.end is not None:
            w = span.start - timedelta(days=span.start.weekday())
            while w <= span.end:
                order.append(f"week of {w}")
                w += timedelta(days=7)
        return labels, order
    if by == "weekday":
        return days.map(lambda d: WEEKDAYS[d.weekday()]), list(WEEKDAYS)
    column = rows[by].map(lambda v: v if isinstance(v, str) else NO_MERCHANT)
    return column, sorted(set(column))


def _measure(values: list[int], measure: str) -> int | None:
    if measure == "count":
        return len(values)
    if measure == "sum":
        return sum(values)
    if not values:
        return None
    if measure == "average":
        return round(sum(values) / len(values))
    return max(values) if measure == "max" else min(values)


def _group(step: Group, source: RowSet) -> Table:
    labels, order = _labels(source, step.by)
    values = source.values().tolist()
    buckets: dict[str, list[int]] = {label: [] for label in order}
    for label, value in zip(labels.tolist(), values, strict=True):
        buckets.setdefault(label, []).append(int(value))
    pairs = tuple((label, _measure(v, step.measure)) for label, v in buckets.items())
    if step.by in ("category", "merchant"):
        pairs = tuple(sorted(pairs, key=lambda p: (-(p[1] or 0), p[0])))
    unit = "count" if step.measure == "count" else "money"
    return Table(step.name, step.by, unit, pairs)


def _aggregate(step: Aggregate, source: RowSet | Table) -> Number:
    if isinstance(source, Table):
        values = [v for _, v in source.pairs if v is not None]
        return Number(step.name, source.unit, _measure(values, step.measure))
    values = [int(v) for v in source.values().tolist()]
    unit = "count" if step.measure == "count" else "money"
    value = _measure(values, step.measure)
    row = None
    if step.measure in ("max", "min") and values:
        i = values.index(value)  # type: ignore[arg-type]
        row = source.rows.iloc[i].to_dict()
    return Number(step.name, unit, value, row)


def _top(step: Top, source: Table) -> Table:
    present = [p for p in source.pairs if p[1] is not None]
    sign = -1 if step.order == "highest" else 1
    ranked = sorted(present, key=lambda p: (sign * (p[1] or 0), p[0]))
    return Table(step.name, "top", source.unit, tuple(ranked[: step.n]))


def _compare(step: Compare, a: Number, b: Number) -> Number:
    if a.value is None or b.value is None:
        return Number(step.name, "percent" if step.how != "difference" else a.unit, None)
    if step.how == "difference":
        return Number(step.name, a.unit, a.value - b.value)
    if b.value == 0:
        return Number(step.name, "percent", None)
    if step.how == "percent_change":
        return Number(step.name, "percent", round((a.value - b.value) / b.value * 100, 1))
    return Number(step.name, "percent", round(a.value / b.value * 100, 1))


def _list(step: ListRows, source: RowSet) -> Listing:
    rows = source.rows.assign(_value=source.values())
    by = {
        "largest": (["_value", "date"], [False, False]),
        "smallest": (["_value", "date"], [True, False]),
        "latest": (["date"], [False]),
        "earliest": (["date"], [True]),
    }[step.order]
    ordered = rows.sort_values(by[0], ascending=by[1], kind="stable").drop(columns="_value")
    return Listing(step.name, ordered.head(step.limit).reset_index(drop=True), len(rows))


# --- Running a plan ---

Engine = Callable[[Any, Context, Outcome], Any]
ENGINE_BLOCKS: dict[str, Engine] = {}  # forecast, whatif, budget, safe_to_spend, recurring


def run(plan: Plan, ctx: Context) -> Outcome:
    """Every step in order. A refusal, an ask-back or a name not found ends the run early."""
    out = Outcome("answer", None, plan_words(plan))
    try:
        for step in plan.steps:
            out.values[step.name] = _step(step, ctx, out)
    except Stop as stop:
        out.kind, out.result = stop.kind, stop.result
        return out
    out.result = out.values[plan.steps[-1].name]
    return out


def _step(step: Any, ctx: Context, out: Outcome) -> Any:
    v = out.values
    if isinstance(step, CannotAnswer):
        raise Stop("refused", Refusal(step.reason))
    if isinstance(step, Clarify):
        raise Stop("clarify", AskBack(step.about))
    if isinstance(step, Rows):
        return _rows(step, ctx, out)
    if isinstance(step, Group):
        return _group(step, v[step.source])
    if isinstance(step, Aggregate):
        return _aggregate(step, v[step.source])
    if isinstance(step, Top):
        return _top(step, v[step.source])
    if isinstance(step, Compare):
        return _compare(step, v[step.a], v[step.b])
    if isinstance(step, ListRows):
        return _list(step, v[step.source])
    if not ENGINE_BLOCKS:
        import lookahead.ask.engine  # noqa: F401  (registers the engine blocks)
    engine = ENGINE_BLOCKS.get(step.op)
    if engine is None:
        raise Stop("refused", Refusal("unsupported"))
    return engine(step, ctx, out)


# --- Comparing two outcomes (the test set's scoring) ---


def signature(out: Outcome) -> tuple[Any, ...]:
    """What makes two answers the same: the outcome kind and the shown result's numbers (money to
    the cent, percent to 0.1), rows by their fingerprints, tables by labels and values."""
    r = out.result
    if out.kind == "refused":
        return ("refused",)
    if out.kind == "clarify":
        return ("clarify",)
    if out.kind == "not_found":
        return ("not_found", r.phrase.casefold())
    return ("answer", *result_signature(r))


def result_signature(r: Any) -> tuple[Any, ...]:
    if isinstance(r, Number):
        return ("number", r.unit, r.value)
    if isinstance(r, Table):
        return ("table", r.unit, r.pairs)
    if isinstance(r, Listing):
        return ("list", tuple(r.rows["fingerprint"]))
    sign = getattr(r, "signature", None)
    if callable(sign):
        return sign()  # type: ignore[no-any-return]
    raise TypeError(f"no signature for {type(r).__name__}")
