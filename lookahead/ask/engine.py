"""The engine blocks: forecast, what-if, budget, safe to spend and recurring payments.

Each one reads the same code and the same forecast as its page, so the figures match:
- forecast: the Forecast page's forecast (weekday estimate, 500 futures). The balance on a day is
  the middle future's (with the 10-in-100 range), the lowest balance the middle future's lowest
  point, the chance the share of futures below the buffer (or an amount) on some day, over the
  same futures cut to the window (as safe-to-spend cuts them);
- what-if: the Forecast page's what-if engine idea (the same futures, draw for draw, run again with
  the change), over the forecast's 30 days. A category change uses the budgets' split of each
  drawn day by category;
- budget: the Budgets tab's pace (`budgets.budget_month`) for my saved limits;
- safe to spend: Home's figure and sentence (`safe_to_spend`);
- recurring: the series the forecast uses.

Rule zero holds as on those pages: the forecast reads rows up to the data date only.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from lookahead.ask import periods
from lookahead.ask.match import Match, match
from lookahead.ask.plan import Amount, Budget, Change, Forecast, Recurring, SafeToSpend, WhatIf
from lookahead.ask.run import (
    ENGINE_BLOCKS,
    AskBack,
    Context,
    NotFound,
    Number,
    Outcome,
    Refusal,
    Stop,
)
from lookahead.budgets import (
    budget_forecast,
    budget_month,
    category_rows,
    future_category_days,
    load_limits,
)
from lookahead.forecast.path import Forecast as BuiltForecast
from lookahead.forecast.path import build_forecast
from lookahead.forecast.schedule import ScheduledItem
from lookahead.forecast.simulate import BLOCK, Simulation, pool_week_starts, run
from lookahead.safe_to_spend import safe_to_spend_from, sentence
from lookahead.scenario import day_share

ASK_NOTE = "added in this question only"


def forecast_of(ctx: Context) -> BuiltForecast:
    """The Forecast page's forecast, made once per question."""
    fc = ctx.cached("forecast", lambda: build_forecast(ctx.conn, ctx.account, "weekday", None,
                                                       ctx.key))  # fmt: skip
    if fc is None or fc.inputs is None or fc.simulation is None:
        raise Stop("refused", Refusal("no_data"))
    return fc  # type: ignore[no-any-return]


def _day_in_forecast(day: date, fc: BuiltForecast) -> date:
    first, last = fc.start.first_day, fc.start.last_day
    if day > last:
        raise Stop("refused", Refusal("beyond_horizon"))
    if day < first:
        raise Stop("refused", Refusal("before_forecast"))
    return day


def _cents(amount: Amount, out: Outcome) -> int:
    if amount.dollars is not None:
        return round(amount.dollars * 100)
    value = out.values[amount.ref or ""]
    if not isinstance(value, Number) or value.value is None:
        raise Stop("refused", Refusal("no_amount"))
    return abs(int(value.value))


# --- forecast ---


@dataclass(frozen=True)
class ForecastAnswer:
    measure: str
    day: date  # balance_on: that day; otherwise the window's last day
    first_day: date
    middle_cents: int | None = None  # balance_on / lowest: the middle future
    low_cents: int | None = None  # balance_on: 10 in 100 futures end the day below this
    high_cents: int | None = None  # ... and 10 in 100 above this
    lowest_day: date | None = None
    chance: float | None = None  # chance_below
    below_cents: int | None = None
    futures: int = 0
    below_is_buffer: bool = False

    def signature(self) -> tuple[Any, ...]:
        return ("forecast", self.measure, self.day, self.middle_cents, self.chance,
                self.below_cents)  # fmt: skip


def window_run(fc: BuiltForecast, last: date, below_cents: int | None = None) -> Simulation:
    """The forecast's futures up to `last`, against `below_cents` (the buffer if None)."""
    inputs = fc.inputs
    assert inputs is not None
    start = replace(inputs.start, last_day=last)
    buffer = inputs.buffer_cents if below_cents is None else below_cents
    return run(replace(inputs, start=start, buffer_cents=buffer))


def _forecast(step: Forecast, ctx: Context, out: Outcome) -> ForecastAnswer:
    fc = forecast_of(ctx)
    sim = fc.simulation
    assert sim is not None
    first = fc.start.first_day
    if step.measure == "balance_on":
        assert step.on is not None
        day = _day_in_forecast(periods.future_day(step.on, ctx.today), fc)
        k = (day - first).days
        return ForecastAnswer("balance_on", day, first, sim.p50[k], sim.p10[k], sim.p90[k],
                              futures=sim.futures)  # fmt: skip
    last = fc.start.last_day
    if step.until is not None:
        last = _day_in_forecast(periods.window_end(step.until, ctx.today), fc)
    days = (last - first).days + 1
    if step.measure == "lowest":
        k = min(range(days), key=lambda i: (sim.p50[i], i))
        return ForecastAnswer("lowest", last, first, sim.p50[k], lowest_day=sim.days[k],
                              futures=sim.futures)  # fmt: skip
    below = None if step.below is None else _cents(step.below, out)
    whole = last == fc.start.last_day and below is None
    window = sim if whole else window_run(fc, last, below)
    return ForecastAnswer("chance_below", last, first, chance=window.shortfall_probability,
                          below_cents=fc.buffer_cents if below is None else below,
                          futures=window.futures, below_is_buffer=below is None)  # fmt: skip


# --- what-if ---


@dataclass(frozen=True)
class WhatIfAnswer:
    low_now_cents: int  # the middle future's lowest balance, as things are now
    low_after_cents: int
    chance_now: float  # share of futures below the buffer on some day
    chance_after: float
    end_now_cents: int  # the middle future's balance on the last forecast day
    end_after_cents: int
    first_day: date
    last_day: date
    buffer_cents: int
    futures: int
    notes: tuple[str, ...] = ()

    def signature(self) -> tuple[Any, ...]:
        return ("whatif", self.low_now_cents, self.low_after_cents, round(self.chance_now, 4),
                round(self.chance_after, 4), self.end_after_cents)  # fmt: skip


def _series(phrase: str, fc: BuiltForecast, ctx: Context, out: Outcome) -> list[str]:
    """The keys of the recurring series a phrase names (one series, or one name's series)."""
    named = {s.name: s for s in fc.series if s.name and s.active and s.status != "rejected"}
    if phrase in ctx.choices and ctx.choices[phrase] in named:
        chosen = ctx.choices[phrase]
        out.matches.append(Match(phrase, "one", (chosen,), "your pick"))
        return [named[chosen].key]
    m = match(phrase, named, ctx.aliases())
    out.matches.append(m)
    if m.status == "none":
        raise Stop("not_found", NotFound(phrase, "recurring payment"))
    if m.status == "many":
        raise Stop("clarify", AskBack("which_one", m.names, phrase))
    return [named[m.names[0]].key]


def _category_cuts(
    change: Change, fc: BuiltForecast, ctx: Context, out: Outcome, days: int
) -> tuple[list[list[int]], str | None]:  # fmt: skip
    """Per future and day, how much less is spent (negative: more). A dollar amount less a month
    becomes a percent of the usual spending there (its average over the 13 weeks the futures draw
    from), so each drawn day is cut in proportion and a day with nothing spent is not cut."""
    inputs = fc.inputs
    assert inputs is not None
    data_date = fc.start.data_last_updated
    rows = ctx.cached("category_rows", lambda: category_rows(ctx.conn, ctx.account, data_date))
    category_of = {r.row_id: r.category for r in rows}
    want = change.category if change.kind == "category" else None
    per_future = [future_category_days(fc, category_of, f, days) for f in range(inputs.futures)]

    def spent(day: dict[str | None, int]) -> int:
        return sum(day.values()) if want is None else day.get(want, 0)

    note = None
    first = fc.start.first_day
    if change.percent is not None:
        share = change.percent / 100
    else:
        assert change.amount is not None
        monthly = _cents(change.amount, out)
        if change.direction == "more":  # spread evenly over each month's days
            cuts = [[-day_share(monthly, first + timedelta(days=k)) for k in range(days)]
                    for _ in range(inputs.futures)]  # fmt: skip
            return cuts, None
        pool_days = _pool_days(fc)
        pool = sum(v for d, v in _pool_spending(fc, category_of, want).items() if d in pool_days)
        usual_month = pool / max(len(pool_days), 1) * 30.4
        share = min(monthly / usual_month, 1.0) if usual_month > 0 else 0.0
        what = want or "everyday spending"
        if usual_month <= 0:
            note = f"Nothing to cut: no usual spending in {what} in the weeks the forecast uses."
        elif monthly >= usual_month:
            note = (f"That is all of your usual spending in {what} (about "
                    f"${usual_month / 100:,.0f} a month), so all of it is taken off.")  # fmt: skip
        else:
            note = (f"That is about {share:.0%} of your usual spending in {what} (about "
                    f"${usual_month / 100:,.0f} a month).")  # fmt: skip
    sign = 1 if change.direction == "less" else -1
    cuts = [[sign * round(spent(day) * share) for day in future] for future in per_future]
    return cuts, note


def _pool_spending(fc: BuiltForecast, category_of: dict[int, str], want: str | None
                   ) -> dict[date, int]:  # fmt: skip
    by_day: dict[date, int] = {}
    for row_id, day, cents in fc.spending_rows.kept:
        if want is None or category_of.get(row_id) == want:
            by_day[day] = by_day.get(day, 0) + cents
    return by_day


def _pool_days(fc: BuiltForecast) -> set[date]:
    """The days of the past weeks the futures draw from."""
    inputs = fc.inputs
    assert inputs is not None
    starts = pool_week_starts(inputs.start, inputs.history_start)
    return {w + timedelta(days=k) for w in starts for k in range(BLOCK)}


def _whatif(step: WhatIf, ctx: Context, out: Outcome) -> WhatIfAnswer:
    fc = forecast_of(ctx)
    inputs = fc.inputs
    assert inputs is not None
    first, last = inputs.start.first_day, inputs.start.last_day
    days = (last - first).days + 1
    items = list(inputs.items)
    cuts = [[0] * days for _ in range(inputs.futures)]
    notes: list[str] = []
    for n, change in enumerate(step.changes):
        if change.kind in ("cancel", "swap"):
            keys = _series(change.target or "", fc, ctx, out)
            if change.kind == "cancel":
                items = [i for i in items if i.series_key not in keys]
            else:
                assert change.amount is not None
                cents = _cents(change.amount, out)
                items = [replace(i, cents=-cents if i.cents < 0 else cents)
                         if i.series_key in keys else i for i in items]  # fmt: skip
        elif change.kind == "one_off":
            assert change.amount is not None
            day = first if change.day is None else _day_in_forecast(
                periods.future_day(change.day, ctx.today), fc)  # fmt: skip
            cents = _cents(change.amount, out) * (1 if change.direction == "in" else -1)
            items.append(ScheduledItem(day, cents, f"ask|{n}", "Ask", "entered", ASK_NOTE))
        else:
            add, note = _category_cuts(change, fc, ctx, out, days)
            cuts = [[a + b for a, b in zip(x, y, strict=True)]
                    for x, y in zip(cuts, add, strict=True)]  # fmt: skip
            if note:
                notes.append(note)
    items.sort(key=lambda i: (i.day, i.series_key))
    now = fc.simulation
    assert now is not None
    after = run(replace(inputs, items=tuple(items)), future_cuts=cuts)
    return WhatIfAnswer(min(now.p50), min(after.p50), now.shortfall_probability,
                        after.shortfall_probability, now.p50[-1], after.p50[-1], first, last,
                        inputs.buffer_cents, inputs.futures, tuple(notes))  # fmt: skip


# --- budget ---


@dataclass(frozen=True)
class BudgetLine:
    category: str
    limit_cents: int
    spent_cents: int
    likely_end_cents: int
    chance_over: float
    already_over: bool


@dataclass(frozen=True)
class BudgetAnswer:
    month: date
    data_date: date
    lines: tuple[BudgetLine, ...]
    asked: str | None  # the category asked about (None: all)

    def signature(self) -> tuple[Any, ...]:
        return ("budget", tuple((b.category, b.limit_cents, b.spent_cents, b.likely_end_cents,
                                 round(b.chance_over, 4)) for b in self.lines))  # fmt: skip


def _budget(step: Budget, ctx: Context, out: Outcome) -> BudgetAnswer:
    limits = ctx.limits if ctx.limits is not None else load_limits(ctx.conn, ctx.account)
    saved = {c: v for c, v in limits.items() if v is not None}
    if step.category is not None:
        if step.category not in saved:
            raise Stop("refused", Refusal("no_budget"))
        saved = {step.category: saved[step.category]}
    if not saved:
        raise Stop("refused", Refusal("no_budget"))
    fc = ctx.cached("budget_forecast", lambda: budget_forecast(ctx.conn, ctx.account, ctx.key))
    if fc is None:
        raise Stop("refused", Refusal("no_data"))
    rows = category_rows(ctx.conn, ctx.account, fc.start.data_last_updated)
    month = budget_month(fc, rows, saved)
    lines = tuple(BudgetLine(p.category, p.limit_cents, p.spent_cents, p.likely_end_cents,
                             p.chance_over, p.already_over) for p in month.paces)  # fmt: skip
    return BudgetAnswer(month.month, month.data_date, lines, step.category)


# --- safe to spend ---


@dataclass(frozen=True)
class SafeAnswer:
    cents_per_day: int | None
    sentence: str
    first_day: date
    last_day: date

    def signature(self) -> tuple[Any, ...]:
        return ("safe", self.cents_per_day, self.last_day)


def _safe(step: SafeToSpend, ctx: Context, out: Outcome) -> SafeAnswer:
    s = safe_to_spend_from(forecast_of(ctx))
    return SafeAnswer(s.cents_per_day, sentence(s), s.first_day, s.last_day)


# --- recurring ---


@dataclass(frozen=True)
class RecurringLine:
    key: str
    name: str
    direction: str
    cadence: str
    usual_cents: int
    next_day: date


@dataclass(frozen=True)
class RecurringAnswer:
    lines: tuple[RecurringLine, ...]

    def signature(self) -> tuple[Any, ...]:
        return ("recurring", tuple(sorted(r.key for r in self.lines)))


def _recurring(step: Recurring, ctx: Context, out: Outcome) -> RecurringAnswer:
    fc = forecast_of(ctx)
    series = [s for s in fc.series if s.active and s.status != "rejected"
              and (step.direction == "both" or s.direction == step.direction)]  # fmt: skip
    if step.find:
        named = {s.name for s in series if s.name}
        if step.find in ctx.choices and ctx.choices[step.find] in named:
            wanted = ctx.choices[step.find]
            out.matches.append(Match(step.find, "one", (wanted,), "your pick"))
        else:
            m = match(step.find, named, ctx.aliases())
            out.matches.append(m)
            if m.status == "none":
                raise Stop("not_found", NotFound(step.find, "recurring payment"))
            if m.status == "many":
                raise Stop("clarify", AskBack("which_one", m.names, step.find))
            wanted = m.names[0]
        series = [s for s in series if s.name == wanted]
    lines = tuple(RecurringLine(s.key, s.name or "Transfer", s.direction, s.cadence,
                                abs(s.expected_cents), s.next_expected)
                  for s in sorted(series, key=lambda s: (s.next_expected, s.key)))  # fmt: skip
    return RecurringAnswer(lines)


ENGINE_BLOCKS.update({
    "forecast": _forecast,
    "whatif": _whatif,
    "budget": _budget,
    "safe_to_spend": _safe,
    "recurring": _recurring,
})  # fmt: skip
