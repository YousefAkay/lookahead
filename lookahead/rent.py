"""Rent that is sent by hand: a few allowed amounts and a window of days (step 3a-iii).

You enter the amounts and the window in the app. A window is named by the month it starts
in. "From the 30th to the 7th" starts on the 30th and ends on the 7th of the next month, so
it wraps across a month end (and a year end). A from-day that a month does not have (the 30th
in February) is clamped to that month's last day.

How the forecast reads a rent series:
- the amount is always the larger allowed one (the cautious reading);
- where one date is needed, it is the earliest day of the window still ahead (`next_window_day`);
- in simulated paths, the date is drawn from inside the window, weighted by your own past rent
  dates posted on or before the forecast date (`draw_rent_date`), so nothing from the future
  leaks in (rule zero).
"""

import random
from calendar import monthrange
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class RentRule:
    """Allowed amounts (positive cents, sizes) and the window of days rent is sent in."""

    amounts_cents: tuple[int, ...]
    from_day: int
    to_day: int

    def __post_init__(self) -> None:
        sizes = tuple(sorted(set(self.amounts_cents)))
        if not sizes or sizes[0] <= 0:
            raise ValueError("give at least one amount above zero")
        if not (1 <= self.from_day <= 31 and 1 <= self.to_day <= 31):
            raise ValueError("days of the month go from 1 to 31")
        object.__setattr__(self, "amounts_cents", sizes)


def rent_cents(rule: RentRule) -> int:
    """The size the forecast uses: the larger allowed amount."""
    return rule.amounts_cents[-1]


def parse_dollars(text: str) -> tuple[int, ...]:
    """Dollar amounts typed as "820, 795.50" (commas or semicolons) -> integer cents."""
    parts = [p.strip() for p in text.replace(";", ",").split(",")]
    cents: list[int] = []
    for part in parts:
        try:
            value = Decimal(part)
        except InvalidOperation:
            raise ValueError(f"not an amount: {part!r}") from None
        if not value.is_finite() or value <= 0 or value != value.quantize(Decimal("0.01")):
            raise ValueError(f"an amount above zero with at most two decimals: {part!r}")
        cents.append(int(value * 100))
    return tuple(cents)


def _clamped(year: int, month: int, day: int) -> date:
    """`day` of the month, or the month's last day if it has fewer days."""
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return date(year, month, min(day, monthrange(year, month)[1]))


def window(rule: RentRule, year: int, month: int) -> tuple[date, date]:
    """The window that starts in this month: (first day, last day), both included."""
    start = _clamped(year, month, rule.from_day)
    wraps = rule.from_day > rule.to_day
    return start, _clamped(year, month + 1 if wraps else month, rule.to_day)


def window_days(rule: RentRule, year: int, month: int) -> list[date]:
    start, end = window(rule, year, month)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def earliest_day(rule: RentRule, year: int, month: int) -> date:
    return window(rule, year, month)[0]


def window_containing(rule: RentRule, on: date) -> tuple[date, date] | None:
    """The window a date falls in, or None if it is outside every window."""
    for months_back in (1, 0):
        start, end = window(rule, on.year, on.month - months_back)
        if start <= on <= end:
            return start, end
    return None


def next_window_day(rule: RentRule, as_of: date, last_paid: date | None) -> date:
    """The single date for the next rent: the earliest day of the first window that has not
    ended and has no payment yet, but never before `as_of` (the past cannot be expected)."""
    for back in (-1, 0, 1, 2):  # a window can start in the month before `as_of` (it wraps)
        start, end = window(rule, as_of.year, as_of.month + back)
        if end >= as_of and (last_paid is None or last_paid < start):
            return max(start, as_of)
    raise ValueError("no window found")  # a window always ends within a month of its start


def _key(rule: RentRule, day: date, start: date) -> int:
    """A day's weight key: its day of the month, except the first day of a window, which
    always stands for the from-day (a rent paid on Feb 28 stands in for "the 30th")."""
    return rule.from_day if day == start else day.day


def draw_rent_date(
    rule: RentRule, year: int, month: int, past: Sequence[date], as_of: date, rng: random.Random
) -> date:
    """One date in the window that starts in this month, for a simulated path.

    Each day weighs 1 plus the number of past rent payments on the same day of the month, so
    days you never paid on stay possible. Only payments dated on or before `as_of` count."""
    seen: Counter[int] = Counter()
    for paid in past:
        if paid > as_of:
            continue
        found = window_containing(rule, paid)
        if found is not None:
            seen[_key(rule, paid, found[0])] += 1
    days = window_days(rule, year, month)
    weights = [1 + seen[_key(rule, d, days[0])] for d in days]
    return rng.choices(days, weights=weights)[0]
