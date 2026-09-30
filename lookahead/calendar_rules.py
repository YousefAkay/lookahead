"""Calendar rules for posting dates: month-end clamping, holidays, business-day shifts.

The holiday list is an ASSUMPTION about when Canadian banks do not post payments
(see DECISIONS.md). It will be checked against real Scotiabank posting dates.
"""

import calendar
from datetime import date, timedelta
from functools import cache
from typing import Literal

Direction = Literal["earlier", "later"]


def monthly_date(year: int, month: int, day: int) -> date:
    """Return day `day` of the month, clamped to the month's last day (31 -> 30, 28 or 29)."""
    if not 1 <= day <= 31:
        raise ValueError(f"day must be between 1 and 31, got {day}")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm, a.k.a. Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_monday(year: int, month: int, n: int) -> date:
    """The n-th Monday of a month (n=1 for the first)."""
    first = date(year, month, 1)
    days_to_monday = (7 - first.weekday()) % 7  # weekday(): Monday=0 ... Sunday=6
    return first + timedelta(days=days_to_monday + 7 * (n - 1))


def _monday_before_may_25(year: int) -> date:
    """Victoria Day (also Journée nationale des patriotes in Quebec)."""
    may_24 = date(year, 5, 24)
    return may_24 - timedelta(days=may_24.weekday())


@cache
def holidays(year: int) -> frozenset[date]:
    """Dates in `year` when payments are assumed not to post (observed dates).

    Fixed-date holidays that fall on a weekend move to the next free weekday,
    so Christmas on a Saturday is observed Monday and Boxing Day Tuesday.
    """
    moving = {
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _monday_before_may_25(year),  # Victoria Day
        _nth_monday(year, 9, 1),  # Labour Day
        _nth_monday(year, 10, 2),  # Thanksgiving
    }
    fixed = [
        date(year, 1, 1),  # New Year's Day
        date(year, 7, 1),  # Canada Day
        date(year, 9, 30),  # National Day for Truth and Reconciliation
        date(year, 11, 11),  # Remembrance Day
        date(year, 12, 25),  # Christmas Day
        date(year, 12, 26),  # Boxing Day
    ]
    observed = set(moving)
    for d in fixed:  # in date order, so Christmas is placed before Boxing Day
        while d.weekday() >= 5 or d in observed:
            d += timedelta(days=1)
        observed.add(d)
    return frozenset(observed)


def is_business_day(d: date) -> bool:
    """True if `d` is a weekday that is not a holiday."""
    return d.weekday() < 5 and d not in holidays(d.year)


def shift_to_business_day(d: date, direction: Direction) -> date:
    """Move `d` to the nearest business day in `direction`; business days stay put.

    Convention (an assumption, see DECISIONS.md): income moves "earlier",
    bills move "later".
    """
    if direction not in ("earlier", "later"):
        raise ValueError(f"direction must be 'earlier' or 'later', got {direction!r}")
    step = timedelta(days=-1 if direction == "earlier" else 1)
    while not is_business_day(d):
        d += step
    return d
