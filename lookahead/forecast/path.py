"""The projected balance (3b step 4): where it starts, the recurring payments placed on their
days, and everyday spending from a baseline.

    balance(day) = balance at the start + scheduled money through that day
                   - cumulative everyday spending through that day

This is a point forecast (an estimate, one path). Ranges from simulated spending paths come
later. Everything uses only rows posted by the forecast date and the decisions made by then.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from lookahead.flows import Txn, flow_types
from lookahead.forecast.schedule import ScheduledItem, scheduled_items
from lookahead.forecast.spending import (
    BigEvent,
    BreakdownRow,
    big_events,
    constant_path,
    recent_average_path,
    recent_fee_rate,
    spending_breakdown,
    spending_by_day,
    weekday_average_path,
)
from lookahead.forecast.start import HORIZON_DAYS, ForecastStart, forecast_start
from lookahead.recurring_store import Stored, series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.settings import DEFAULT_BUFFER_CENTS, safety_buffer_cents

BASELINES = {
    "recent": "average of the last 28 days",
    "weekday": "average for each weekday over the last 8 weeks",
}


@dataclass(frozen=True)
class ForecastDay:
    day: date
    balance_cents: int  # projected end-of-day balance
    scheduled_cents: int  # recurring money in (+) or out (-) that day
    spending_cents: int  # everyday spending that day
    fees_cents: int = 0  # bank fees that day (their recent daily average)


@dataclass(frozen=True)
class Forecast:
    start: ForecastStart
    method: str  # how everyday spending was estimated
    days: list[ForecastDay]
    items: list[ScheduledItem]
    has_income: bool  # an income series exists (if not, no income is assumed)
    buffer_cents: int = DEFAULT_BUFFER_CENTS
    series: tuple[Stored, ...] = ()
    breakdown: tuple[BreakdownRow, ...] = ()  # what the weekday baseline's history is made of
    big_events: tuple[BigEvent, ...] = ()  # big one-time rows of the last 13 weeks: not forecast
    fees_per_day: float = 0.0  # cents a day, from the last 28 days

    @property
    def lowest(self) -> tuple[date, int]:
        """The day with the lowest projected balance, and that balance."""
        low = min(self.days, key=lambda d: (d.balance_cents, d.day))
        return low.day, low.balance_cents

    @property
    def first_below_buffer(self) -> date | None:
        """The first day the projected balance is below the safety buffer, if any."""
        return next((d.day for d in self.days if d.balance_cents < self.buffer_cents), None)


def project_balance(
    start: ForecastStart, items: Sequence[ScheduledItem], spending_cumulative: Sequence[int],
    fees_cumulative: Sequence[int] | None = None,
) -> list[ForecastDay]:  # fmt: skip
    """One entry per forecast day. `spending_cumulative[k]` is the everyday spending, and
    `fees_cumulative[k]` the bank fees, from the first forecast day through day k."""
    count = len(spending_cumulative)
    fees = list(fees_cumulative) if fees_cumulative is not None else [0] * count
    scheduled = [0] * count
    for it in items:
        k = (it.day - start.first_day).days
        if 0 <= k < count:
            scheduled[k] += it.cents
    out, money, spent_before, fees_before = [], 0, 0, 0
    for k, spent_through in enumerate(spending_cumulative):
        money += scheduled[k]
        out.append(
            ForecastDay(
                start.first_day + timedelta(days=k),
                start.balance_cents + money - spent_through - fees[k],
                scheduled[k],
                spent_through - spent_before,
                fees[k] - fees_before,
            )
        )
        spent_before, fees_before = spent_through, fees[k]
    return out


def build_forecast(
    conn: sqlite3.Connection, account: str, method: str = "weekday", as_of: date | None = None,
    key: bytes | None = None, horizon_days: int = HORIZON_DAYS,
) -> Forecast | None:  # fmt: skip
    """A forecast from what is stored, with no confirmations needed: every active series is used
    as detected unless I changed it. `as_of=None` is a live forecast (all rows, all decisions);
    a date replays it as it would have looked then. None if there are no rows by then."""
    if method not in BASELINES:
        raise ValueError(f"unknown baseline {method!r}; choose from {sorted(BASELINES)}")
    start = forecast_start(conn, account, as_of, horizon_days)
    if start is None:
        return None
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, start.data_last_updated.isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]
    posted = {r[5]: date.fromisoformat(r[1]) for r in rows}
    row_of = {r[5]: r[0] for r in rows}
    series, _ = series_as_of(conn, account, as_of, key)
    flows = flow_types(txns, load_user_decisions(conn, as_of))
    in_series = {
        row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence if fp in row_of
    }
    daily = spending_by_day(txns, flows, in_series, start.data_last_updated)
    first_posted = min(t.posted_date for t in txns)
    horizon = (start.last_day - start.first_day).days + 1
    if method == "recent":
        spending = recent_average_path(daily, start.data_last_updated, first_posted, horizon)
    else:
        spending = weekday_average_path(
            daily, start.data_last_updated, first_posted, start.first_day, horizon
        )
    items = scheduled_items(series, posted, start)
    fee_rate = recent_fee_rate(txns, flows, start.data_last_updated, first_posted)
    fees = constant_path(fee_rate, horizon)
    return Forecast(
        start, BASELINES[method], project_balance(start, items, spending, fees), items,
        any(s.direction == "in" and s.active and s.status != "rejected" for s in series),
        safety_buffer_cents(conn), tuple(series),
        tuple(spending_breakdown(txns, flows, in_series, start.data_last_updated, first_posted)),
        tuple(big_events(txns, flows, in_series, start.data_last_updated)),
        fee_rate,
    )  # fmt: skip
