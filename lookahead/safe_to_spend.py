"""Safe to spend: about how much everyday spending a day keeps you above your buffer.

Method in DECISIONS.md (2026-10-11, "Safe-to-spend"). The largest constant everyday spending a
day for which at least 80 percent of the simulated futures stay at or above the safety buffer on
every day of the window (from the first forecast day to the day before the next expected income,
or 30 days). Each level is a what-if in the existing scenario engine, with the same random draws
as the forecast: the drawn everyday spending is cut by 100 percent over the window and a purchase
of that level is added on every day. This module only calls the forecaster; it changes nothing
in it, and it reads only what the forecast read (rule zero holds as it does for the forecast).

It is an estimate from the person's own past weeks, never a promise.
"""

import sqlite3
from dataclasses import dataclass, replace
from datetime import date, timedelta

from lookahead.forecast.path import Forecast, build_forecast
from lookahead.forecast.scenario import ExtraItem, Scenario, SpendingChange, run_scenario
from lookahead.forecast.start import HORIZON_DAYS

CONFIDENCE = 0.80  # at least this share of futures never below the buffer in the window
FIRST_TRY_CENTS = 5_000  # the search's first upper end: $50 a day, doubled until not safe
MAX_CENTS = 10_000_000  # $100,000 a day: the search never goes above it
PLENTY_RATIO = 3  # above 3 times the usual everyday spending: "plenty of room" (presentation)


@dataclass(frozen=True)
class SafeToSpend:
    cents_per_day: int | None  # the largest safe level, exact to the cent; None: no safe amount
    first_day: date  # the window: first forecast day ...
    last_day: date  # ... to this day (inclusive)
    income_day: date | None  # the next expected income (the window ends the day before it)
    usual_cents_per_day: int  # the forecast's own everyday spending, averaged over the window
    buffer_cents: int
    dip_by: date | None  # no safe amount: more than 20% of futures have dipped by this day
    futures: int
    start_balance_cents: int = 0  # the balance the forecast starts from
    capped: bool = False  # the search reached MAX_CENTS

    @property
    def days(self) -> int:
        return (self.last_day - self.first_day).days + 1

    @property
    def plenty(self) -> bool:
        """Far above the usual spending: shown as room to spare, not as a big daily figure."""
        return (self.cents_per_day is not None and self.usual_cents_per_day > 0
                and self.cents_per_day > PLENTY_RATIO * self.usual_cents_per_day)  # fmt: skip

    @property
    def dollars_per_day(self) -> int | None:
        """Shown rounded down to whole dollars, so rounding never moves it to the unsafe side."""
        return None if self.cents_per_day is None else self.cents_per_day // 100


def next_income_day(forecast: Forecast) -> date | None:
    """The first money-in payment of an active income series placed after the first forecast day
    (planned deposits are not income here), or None."""
    income = {s.key for s in forecast.series
              if s.direction == "in" and s.active and s.status != "rejected"}  # fmt: skip
    first = forecast.start.first_day
    days = sorted(i.day for i in forecast.items
                  if i.cents > 0 and i.series_key in income and i.day > first)  # fmt: skip
    return days[0] if days else None


def window_forecast(forecast: Forecast, last_day: date, buffer_cents: int | None = None
                    ) -> Forecast:  # fmt: skip
    """The forecast's futures over the window only, optionally against another buffer. Every
    future's draws are named per future and drawn in day order, so the window's days are exactly
    the first days of the forecast's own futures."""
    inputs = forecast.inputs
    if inputs is None:
        raise ValueError("this forecast has no simulated futures")
    start = replace(inputs.start, last_day=last_day)
    buffer = inputs.buffer_cents if buffer_cents is None else buffer_cents
    return replace(forecast, inputs=replace(inputs, start=start, buffer_cents=buffer))


def _allowed(futures: int) -> int:
    """How many futures may dip: at most 20 percent of them."""
    return int(futures * (1 - CONFIDENCE) + 1e-9)


def dipping(window: Forecast, cents_per_day: int) -> int:
    """How many futures go below the buffer on some window day when everyday spending is exactly
    `cents_per_day` every day (a what-if with the forecast's own draws)."""
    assert window.inputs is not None
    first, last = window.inputs.start.first_day, window.inputs.start.last_day
    days = [first + timedelta(days=k) for k in range((last - first).days + 1)]
    extras = () if cents_per_day == 0 else tuple(
        ExtraItem(d, -cents_per_day, "everyday spending") for d in days
    )  # fmt: skip
    scenario = Scenario(spending=SpendingChange(first, last, percent=100), extras=extras)
    result = run_scenario(window, scenario)
    return round(result.modified.shortfall_probability * result.modified.futures)


def is_safe(window: Forecast, cents_per_day: int) -> bool:
    assert window.inputs is not None
    return dipping(window, cents_per_day) <= _allowed(window.inputs.futures)


def largest_safe(window: Forecast) -> tuple[int | None, bool]:
    """(the largest safe level in cents a day, whether the cap was reached); None if even no
    everyday spending is not safe. Safety can only switch from yes to no as the level rises
    (more spending lowers every future on every day), so bisection on whole cents is exact."""
    if not is_safe(window, 0):
        return None, False
    lo, hi = 0, FIRST_TRY_CENTS
    while is_safe(window, hi):
        if hi >= MAX_CENTS:
            return MAX_CENTS, True
        lo, hi = hi, min(hi * 2, MAX_CENTS)
    while hi - lo > 1:  # lo is safe, hi is not
        mid = (lo + hi) // 2
        if is_safe(window, mid):
            lo = mid
        else:
            hi = mid
    return lo, False


def dip_day(forecast: Forecast, last_day: date, buffer_cents: int | None = None) -> date:
    """With no everyday spending at all: the first day by which more than 20 percent of the
    futures have dipped below the buffer (bisection on the window's length)."""
    first = forecast.start.first_day
    lo, hi = 0, (last_day - first).days  # the window ending on day `hi` is not safe
    while lo < hi:
        mid = (lo + hi) // 2
        if is_safe(window_forecast(forecast, first + timedelta(days=mid), buffer_cents), 0):
            lo = mid + 1
        else:
            hi = mid
    return first + timedelta(days=lo)


def safe_to_spend_from(forecast: Forecast, buffer_cents: int | None = None) -> SafeToSpend:
    """Safe-to-spend for an existing forecast (it needs its simulated futures)."""
    if forecast.inputs is None:
        raise ValueError("this forecast has no simulated futures")
    first = forecast.start.first_day
    income = next_income_day(forecast)
    last = income - timedelta(days=1) if income is not None else forecast.start.last_day
    if income is None:
        last = min(last, first + timedelta(days=HORIZON_DAYS - 1))
    window = window_forecast(forecast, last, buffer_cents)
    assert window.inputs is not None
    in_window = [d.spending_cents for d in forecast.days if first <= d.day <= last]
    usual = round(sum(in_window) / len(in_window)) if in_window else 0
    cents, capped = largest_safe(window)
    dip = dip_day(forecast, last, buffer_cents) if cents is None else None
    return SafeToSpend(cents, first, last, income, usual, window.inputs.buffer_cents, dip,
                       window.inputs.futures, forecast.start.balance_cents, capped)  # fmt: skip


def safe_to_spend(
    conn: sqlite3.Connection, account: str, method: str = "weekday", as_of: date | None = None,
    key: bytes | None = None, buffer_cents: int | None = None,
) -> SafeToSpend | None:  # fmt: skip
    """Safe-to-spend as of a date (None: live), from the forecast the app makes then. None if
    there is no forecast (no rows by then)."""
    forecast = build_forecast(conn, account, method, as_of, key)
    if forecast is None or forecast.inputs is None:
        return None
    return safe_to_spend_from(forecast, buffer_cents)


def exact_figure(s: SafeToSpend) -> str:
    """The exact safe amount in words, for the small print under "plenty of room"."""
    if s.cents_per_day is None:
        return ""
    more = " or more" if s.capped else ""
    return f"Exact figure: about ${s.dollars_per_day:,}/day{more} (80% confidence)."


def sentence(s: SafeToSpend) -> str:
    """The plain-words line the pages show (dollar signs plain; the page escapes them)."""
    buffer = f"${s.buffer_cents / 100:,.0f}"
    until = (f"until {s.income_day:%b} {s.income_day.day} (the next expected income)"
             if s.income_day is not None else f"for the next {s.days} days")  # fmt: skip
    if s.cents_per_day is None:
        assert s.dip_by is not None
        if s.start_balance_cents < s.buffer_cents:
            return f"No safe amount: your balance is already below your {buffer} buffer."
        before = s.dip_by + timedelta(days=1)  # dipped by `dip_by`, so before the day after it
        return (f"No safe amount: a dip below your {buffer} buffer is likely before "
                f"{before:%b} {before.day}, even with no everyday spending.")  # fmt: skip
    more = "or more " if s.capped else ""
    if s.plenty:
        usual = f"${s.usual_cents_per_day / 100:,.0f}/day"
        return (f"Plenty of room: you could spend well above your usual {usual} {until} and stay "
                f"above your {buffer} buffer (80% confidence, an estimate).")  # fmt: skip
    return (f"About ${s.dollars_per_day:,}/day {more}for everyday spending {until} keeps you "
            f"above your {buffer} buffer, with 80% confidence (an estimate).")  # fmt: skip
