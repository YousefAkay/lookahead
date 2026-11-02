"""The scenario calculator: "what if I spend X a day" (a plain product feature, 2026-11-02).

Not a forecast. It adds up exactly what is entered, day by day, in integer cents: a starting
balance, recurring flows, a daily spend and one-off items, to an end date. No randomness and no
band; the page says so under the chart.

Recurring dates come from the detector's own cadence rules (`lookahead.recurring.expected_dates`):
income moves to the business day before a weekend or holiday, a payment to the one after, and an
e-Transfer or card payment posts Monday to Saturday. This module imports nothing from the
forecaster or the proposers (a test checks it); prefilling from the database is
`lookahead.scenario_prefill`.
"""

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from lookahead.recurring import expected_dates

SHIFT_DAYS = 7  # a due date this many days before the start can still post after it


@dataclass(frozen=True)
class Flow:
    """A recurring flow: `cents` each time (money in positive), on the dates of its pattern."""

    name: str
    cents: int
    cadence: str  # weekly / biweekly / semimonthly / monthly / yearly
    anchor: date  # a due date of the pattern (monthly: only its day is used)
    card: bool = False  # an e-Transfer or card payment: posts Monday to Saturday
    on: bool = True
    key: str = ""  # the stored series it came from, if any
    spread: bool = False  # monthly only: a little each day instead of on one day (`day_share`)

    @property
    def direction(self) -> str:
        return "in" if self.cents > 0 else "out"


@dataclass(frozen=True)
class OneOff:
    day: date
    cents: int  # money in positive
    name: str = ""


@dataclass(frozen=True)
class Inputs:
    balance_cents: int
    start: date  # the balance is at the end of this day; the scenario runs from the next day
    end: date
    flows: tuple[Flow, ...] = ()
    daily_spend_cents: int = 0  # a size, taken off every day
    one_offs: tuple[OneOff, ...] = ()
    buffer_cents: int | None = None


@dataclass(frozen=True)
class Result:
    days: tuple[date, ...]  # start + 1 to end
    balances: tuple[int, ...]  # at the end of each day
    end_balance: int
    first_below: date | None  # the first day below the buffer, if a buffer was given


def default_end(today: date) -> date:
    """December 31 of the current year."""
    return date(today.year, 12, 31)


def day_share(monthly_cents: int, day: date) -> int:
    """A monthly amount spread over the days of its calendar month, in whole cents: every day gets
    the same share and the cents left over go one each to the first days, so a whole month adds up
    to the monthly amount exactly."""
    days = monthrange(day.year, day.month)[1]
    base, extra = divmod(abs(monthly_cents), days)
    share = base + (1 if day.day <= extra else 0)
    return share if monthly_cents >= 0 else -share


def flow_dates(flow: Flow, first: date, last: date) -> list[date]:
    """The posting dates of a flow in [first, last], weekend and holiday shifts included."""
    dates = expected_dates(
        flow.cadence, flow.anchor, first - timedelta(days=SHIFT_DAYS), last, flow.direction,
        card=flow.card,
    )  # fmt: skip
    return sorted(d for d in set(dates) if first <= d <= last)


def run(inputs: Inputs) -> Result:
    """The balance at the end of every day from the day after `start` to `end`."""
    if inputs.end <= inputs.start:
        return Result((), (), inputs.balance_cents, None)
    first = inputs.start + timedelta(days=1)
    change: defaultdict[date, int] = defaultdict(int)
    for flow in inputs.flows:
        if not flow.on:
            continue
        if flow.spread:
            day = first
            while day <= inputs.end:
                change[day] += day_share(flow.cents, day)
                day += timedelta(days=1)
        else:
            for day in flow_dates(flow, first, inputs.end):
                change[day] += flow.cents
    for item in inputs.one_offs:
        if first <= item.day <= inputs.end:
            change[item.day] += item.cents
    days, balances = [], []
    balance = inputs.balance_cents
    below: date | None = None
    day = first
    while day <= inputs.end:
        balance += change[day] - inputs.daily_spend_cents
        days.append(day)
        balances.append(balance)
        if below is None and inputs.buffer_cents is not None and balance < inputs.buffer_cents:
            below = day
        day += timedelta(days=1)
    return Result(tuple(days), tuple(balances), balance, below)
