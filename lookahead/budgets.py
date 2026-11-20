"""Budgets that forecast (week 6; spec docs/specs/2026-11-20-budgets-that-forecast.md).

Monthly limits for a few discretionary categories, proposed from my history, and for each one:
spent so far this month, the chance of ending the month over the limit, and the likely end of the
month, read from the forecast's own simulated futures. Plus a what-if: my lowest balance if I stay
on budget. Every figure is an estimate; no accuracy is claimed.

- The month is the calendar month of the forecast's first day (the day after my data ends).
- Spent so far: the money-out rows of the category posted from the 1st of that month to the data
  date, less its refunds. Big one-time rows count here (they were spent), recurring payments too.
- The rest of the month: each future draws whole past weeks exactly as the forecast does
  (`week_draws`, the one shared function), and takes the category's part of each drawn day. A
  drawn week is cut by day, never scaled: with 3 days left, the first 3 days of the first drawn
  week. Big one-time rows and recurring payments are not in the drawn days (the forecast leaves
  them out of everyday spending), so they are never repeated.
- Rule zero: the drawn weeks come from the 13 weeks up to the data date, and spent so far stops at
  the data date. Nothing posted later is read.

Money is integer cents throughout; positive = spent.
"""

import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from statistics import median

from lookahead.forecast.path import Forecast, build_forecast
from lookahead.forecast.simulate import BLOCK, _percentile, pool_week_starts, run, week_draws
from lookahead.forecast.start import HORIZON_DAYS, forecast_start
from lookahead.taxonomy import CATEGORIES

DEFAULT_CATEGORIES: tuple[str, ...] = (
    "Food delivery",
    "Restaurants & cafés",
    "Entertainment",
    "Shopping",
)
OPTIONAL_CATEGORIES: tuple[str, ...] = (
    "Groceries",
    "Transport",
    "Personal care",
    "Health & pharmacy",
    "Gifts & donations",
    "Cash",
)
BUDGETABLE: tuple[str, ...] = DEFAULT_CATEGORIES + OPTIONAL_CATEGORIES
NEVER: tuple[str, ...] = tuple(c for c in CATEGORIES if c not in BUDGETABLE)

PROPOSAL_MONTHS = 3  # the median of the last 3 full calendar months ...
MIN_FULL_MONTHS = 2  # ... and no proposal with fewer than 2
ROUND_UP_CENTS = 500  # proposals are rounded up to the next $5
BIG_ONE_OFF_NOTE = (
    "This month's total includes a big one-time payment. It counts in what you spent, but the "
    "estimate never repeats it."
)
STALE_NOTE = (
    "Your data ends before this calendar month, so these budgets are for an earlier month. "
    "Import your latest export to see this month."
)


# --- Months ---


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    following = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def previous_month(first: date) -> date:
    return month_start(first - timedelta(days=1))


def month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def is_stale(budget_month: date, today: date) -> bool:
    """True when the budget month is not today's calendar month (my data ends earlier)."""
    return month_start(budget_month) != month_start(today)


# --- Rows by category ---


@dataclass(frozen=True)
class CategoryRow:
    row_id: int
    fingerprint: str
    day: date
    cents: int  # spent: money out positive, a refund negative
    category: str
    merchant: str | None


def category_rows(conn: sqlite3.Connection, account: str, last_day: date) -> list[CategoryRow]:
    """Rows posted up to `last_day` that count as spending in a category, with the category the
    app shows (the categorizer's, my corrections and a series' category included): money out,
    and money back only when it is a refund. Rows with no category are left out."""
    from lookahead.views import transactions

    df = transactions(conn, account, end=last_day)
    if df.empty:
        return []
    ids = dict(conn.execute("SELECT fingerprint, id FROM transactions WHERE account = ?",
                            (account,)).fetchall())  # fmt: skip
    out = []
    for r in df.itertuples(index=False):
        category = r.category if isinstance(r.category, str) else None
        if category is None:
            continue
        cents = -int(r.amount_cents)
        if cents < 0 and r.flow_type != "refund":
            continue  # money in that is not a refund is not spending (income, transfers in)
        merchant = r.merchant if isinstance(r.merchant, str) else None
        out.append(CategoryRow(int(ids[r.fingerprint]), r.fingerprint, r.date.date(), cents,
                               category, merchant))  # fmt: skip
    return out


def monthly_totals(rows: Iterable[CategoryRow], category: str) -> dict[date, int]:
    """Spent in `category` by month (keyed by the month's first day). Months with no rows are
    absent; `full_months` decides which months count."""
    totals: dict[date, int] = defaultdict(int)
    for r in rows:
        if r.category == category:
            totals[month_start(r.day)] += r.cents
    return dict(totals)


# --- Proposals ---


def full_months(first_posted: date, data_date: date, budget_month: date) -> list[date]:
    """The calendar months before the budget month that my data covers from their 1st to their
    last day, most recent first."""
    out = []
    month = previous_month(month_start(budget_month))
    while month >= month_start(first_posted):
        if month >= first_posted and month_end(month) <= data_date:
            out.append(month)
        month = previous_month(month)
    return out


@dataclass(frozen=True)
class Proposal:
    category: str
    limit_cents: int | None  # None: not enough history (or nothing spent): enter a limit
    months: int  # the full months it rests on


def round_up(cents: float) -> int:
    return math.ceil(cents / ROUND_UP_CENTS) * ROUND_UP_CENTS


def propose(
    rows: Sequence[CategoryRow], category: str, first_posted: date, data_date: date,
    budget_month: date,
) -> Proposal:  # fmt: skip
    """The median of the category's last 3 full months (a month with nothing spent counts as $0),
    rounded up to the next $5. None with fewer than 2 full months, or when the median is $0."""
    months = full_months(first_posted, data_date, budget_month)[:PROPOSAL_MONTHS]
    if len(months) < MIN_FULL_MONTHS:
        return Proposal(category, None, len(months))
    totals = monthly_totals(rows, category)
    middle = median(totals.get(m, 0) for m in months)
    return Proposal(category, round_up(middle) if middle > 0 else None, len(months))


# --- Stored limits ---


def load_limits(conn: sqlite3.Connection, account: str) -> dict[str, int | None]:
    """category -> limit in cents, or None for a budget I turned off. Absent: never decided."""
    try:
        rows = conn.execute(
            "SELECT category, limit_cents FROM budgets WHERE account = ?", (account,)
        ).fetchall()
    except sqlite3.OperationalError:  # a read-only database from before the table existed
        return {}
    return {r[0]: (None if r[1] is None else int(r[1])) for r in rows}


def check_limit(category: str, cents: int | None) -> None:
    if category not in BUDGETABLE:
        raise ValueError(f"{category!r} cannot have a budget")
    if cents is not None and cents <= 0:
        raise ValueError("a limit above $0")


def set_limit(
    conn: sqlite3.Connection, account: str, category: str, cents: int | None,
    when: date | None = None,
) -> None:  # fmt: skip
    """Save a limit (None turns the category's budget off)."""
    check_limit(category, cents)
    with conn:
        conn.execute(
            "INSERT INTO budgets (account, category, limit_cents, set_on) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (account, category) DO UPDATE SET limit_cents = excluded.limit_cents,"
            " set_on = excluded.set_on",
            (account, category, cents, (when or date.today()).isoformat()),
        )


def remove(conn: sqlite3.Connection, account: str, category: str) -> None:
    """Forget my decision about an optional category (it is offered again, not shown)."""
    with conn:
        conn.execute("DELETE FROM budgets WHERE account = ? AND category = ?", (account, category))


@dataclass(frozen=True)
class Shown:
    """One category on the Budgets tab."""

    category: str
    limit_cents: int | None  # the saved limit; None while only proposed or not entered
    proposal: Proposal | None  # shown while I have not decided


def shown_categories(
    stored: Mapping[str, int | None], proposals: Mapping[str, Proposal]
) -> list[Shown]:  # fmt: skip
    """The defaults I have not turned off, then the optional ones I added, in a fixed order."""
    out = []
    for category in BUDGETABLE:
        decided = category in stored
        if decided and stored[category] is None:
            continue  # turned off
        if not decided and category not in DEFAULT_CATEGORIES:
            continue  # an optional category I have not added
        out.append(Shown(category, stored.get(category), None if decided else
                         proposals.get(category)))  # fmt: skip
    return out


def addable(shown: Sequence[Shown]) -> list[str]:
    """Categories I can add a budget for: every budgetable one not on the tab, which includes a
    default category I turned off (so turning one off is never final)."""
    on_tab = {s.category for s in shown}
    return [c for c in BUDGETABLE if c not in on_tab]


def waiting_proposals(shown: Sequence[Shown]) -> dict[str, int]:
    """What one "Looks right" accepts: every undecided category that has a proposed limit."""
    return {s.category: s.proposal.limit_cents for s in shown
            if s.limit_cents is None and s.proposal is not None
            and s.proposal.limit_cents is not None}  # fmt: skip


# --- The forecast the budgets read ---


def budget_forecast(
    conn: sqlite3.Connection, account: str, key: bytes | None = None, as_of: date | None = None,
    futures: int | None = None,
) -> Forecast | None:  # fmt: skip
    """The default forecast (weekday estimate), long enough to reach the month's last day: 30
    days, or 31 for a 31-day month seen from its 1st. Its first 30 days are the usual forecast's."""
    start = forecast_start(conn, account, as_of)
    if start is None:
        return None
    need = (month_end(start.first_day) - start.first_day).days + 1
    extra = {} if futures is None else {"futures": futures}
    return build_forecast(conn, account, "weekday", as_of, key,
                          horizon_days=max(HORIZON_DAYS, need), **extra)  # fmt: skip


def days_left(forecast: Forecast) -> int:
    """Forecast days from the first one through the month's last day."""
    first = forecast.start.first_day
    return (month_end(first) - first).days + 1


def _row_categories(rows: Iterable[CategoryRow]) -> dict[int, str]:
    return {r.row_id: r.category for r in rows}


def future_category_days(
    forecast: Forecast, category_of: Mapping[int, str], future: int, days: int
) -> list[dict[str | None, int]]:  # fmt: skip
    """Future `future`'s everyday spending on its first `days` forecast days, split by category
    (None: rows with no budget category, so the parts always add up to the day). Forecast day k
    is day k % 7 of drawn week k // 7, the forecast's own mapping."""
    inputs = forecast.inputs
    if inputs is None:
        raise ValueError("this forecast has no simulated futures")
    by_day: dict[date, dict[str | None, int]] = defaultdict(lambda: defaultdict(int))
    for row_id, day, cents in forecast.spending_rows.kept:
        by_day[day][category_of.get(row_id)] += cents
    starts = pool_week_starts(inputs.start, inputs.history_start)
    weeks = week_draws(inputs.seed, future, len(starts), -(-days // BLOCK))
    out: list[dict[str | None, int]] = []
    for k in range(days):
        if not weeks:
            out.append({})
            continue
        drawn = starts[weeks[k // BLOCK]] + timedelta(days=k % BLOCK)
        out.append(dict(by_day.get(drawn, {})))
    return out


# --- Pace ---


@dataclass(frozen=True)
class Pace:
    category: str
    limit_cents: int
    spent_cents: int  # this month, posted up to the data date
    likely_end_cents: int  # the middle future's month total
    chance_over: float  # share of futures that end the month above the limit
    futures: int
    big_one_off: bool  # spent so far includes a big one-time payment
    rows: tuple[CategoryRow, ...]  # the rows behind spent so far, newest first
    remaining: tuple[tuple[int, ...], ...] = ()  # each future's spending on each day left

    @property
    def likely_over_cents(self) -> int:
        return max(self.likely_end_cents - self.limit_cents, 0)


@dataclass(frozen=True)
class BudgetMonth:
    month: date  # its first day
    data_date: date
    days_left: int  # forecast days through the month's last day
    paces: tuple[Pace, ...]


def budget_month(
    forecast: Forecast, rows: Sequence[CategoryRow], limits: Mapping[str, int]
) -> BudgetMonth:  # fmt: skip
    """Pace for each category with a limit. `rows` are the category rows up to the forecast's
    data date (later rows are ignored, so nothing after the data date can count)."""
    inputs = forecast.inputs
    if inputs is None:
        raise ValueError("this forecast has no simulated futures")
    data_date = forecast.start.data_last_updated
    first = month_start(forecast.start.first_day)
    left = days_left(forecast)
    if forecast.start.last_day < month_end(first):
        raise ValueError("the forecast does not reach the end of the month: use budget_forecast")
    rows = [r for r in rows if r.day <= data_date]
    category_of = _row_categories(rows)
    set_aside = forecast.spending_rows.set_aside
    per_future = [future_category_days(forecast, category_of, f, left)
                  for f in range(inputs.futures)]  # fmt: skip
    paces = []
    for category, limit in limits.items():
        this_month = sorted((r for r in rows if r.category == category and r.day >= first),
                            key=lambda r: (r.day, r.row_id), reverse=True)  # fmt: skip
        spent = sum(r.cents for r in this_month)
        remaining = tuple(tuple(day.get(category, 0) for day in days) for days in per_future)
        totals = [spent + sum(days) for days in remaining]
        paces.append(Pace(
            category, limit, spent,
            _percentile(totals, 0.5) if totals else spent,
            sum(t > limit for t in totals) / len(totals) if totals else float(spent > limit),
            len(totals),
            any(r.row_id in set_aside for r in this_month),
            tuple(this_month),
            remaining,
        ))  # fmt: skip
    return BudgetMonth(first, data_date, left, tuple(paces))


# --- Staying on budget: the what-if ---


@dataclass(frozen=True)
class OnBudget:
    """The first 30 forecast days (the Forecast page's window), as things are now and on budget."""

    low_now_cents: int  # the middle future's lowest balance
    low_on_budget_cents: int
    chance_now: float  # share of futures below the buffer on some day
    chance_on_budget: float
    never_lower: bool  # on budget is at or above as-now in every future, on every day
    futures: int


def on_budget_cuts(month: BudgetMonth, horizon: int, futures: int) -> list[list[int]]:
    """Per future and day, what staying on budget takes off the everyday spending: each budgeted
    category is capped at its limit less spent so far (never below $0 left) for the rest of the
    month. A day's cut is the growth of the amount above the cap, so the running total of the
    cuts is exactly that amount, which is never negative: the balance on budget is never lower."""
    cuts = [[0] * horizon for _ in range(futures)]
    for pace in month.paces:
        allowed = max(pace.limit_cents - pace.spent_cents, 0)
        for f, days in enumerate(pace.remaining):
            running = before = 0
            for k, cents in enumerate(days[:horizon]):
                running += cents
                above = max(running - allowed, 0)
                cuts[f][k] += above - before
                before = above
    return cuts


def on_budget(forecast: Forecast, month: BudgetMonth) -> OnBudget:
    """The what-if on the forecast's own futures, draw for draw (the same week draws, payment
    dates and deposits), over the first 30 forecast days."""
    inputs = forecast.inputs
    if inputs is None:
        raise ValueError("this forecast has no simulated futures")
    last = min(inputs.start.last_day, inputs.start.first_day + timedelta(days=HORIZON_DAYS - 1))
    window = replace(inputs, start=replace(inputs.start, last_day=last))
    horizon = (last - inputs.start.first_day).days + 1
    cuts = on_budget_cuts(month, horizon, inputs.futures)
    now = run(window, keep_paths=True)
    after = run(window, keep_paths=True, future_cuts=cuts)
    never_lower = all(b >= a for p, q in zip(now.paths, after.paths, strict=True)
                      for a, b in zip(p, q, strict=True))  # fmt: skip
    return OnBudget(min(now.p50), min(after.p50), now.shortfall_probability,
                    after.shortfall_probability, never_lower, inputs.futures)  # fmt: skip


def first_posted(conn: sqlite3.Connection, account: str) -> date | None:
    row = conn.execute("SELECT MIN(posted_date) FROM transactions WHERE account = ?",
                       (account,)).fetchone()  # fmt: skip
    return date.fromisoformat(row[0]) if row and row[0] else None


def proposals_for(
    conn: sqlite3.Connection, account: str, rows: Sequence[CategoryRow], forecast: Forecast
) -> dict[str, Proposal]:  # fmt: skip
    """A proposal for every category that can have a budget."""
    first = first_posted(conn, account)
    data_date = forecast.start.data_last_updated
    if first is None:
        return {}
    return {c: propose(rows, c, first, data_date, forecast.start.first_day) for c in BUDGETABLE}
