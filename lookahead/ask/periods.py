"""Period and day words turned into dates, on this machine (the plan never carries today's date).

Past periods are relative to the app's "today" (the real date; in demo mode and in the test set,
the day after the data ends) and are then cut to the days my data covers, with a note when the
question reaches outside them. Written defaults (spec, "Ambiguity"):
- "last month" is the previous full calendar month, "this month" the 1st to today;
- weeks run Monday to Sunday; "last N months/weeks" are full ones before the current one;
- "last N days" are the N days before today;
- a month without a year ("in March") is the latest March that has started by today.

Future days are relative to today too; the forecast covers the 30 days after my data ends.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from lookahead.ask.plan import Day, Period


@dataclass(frozen=True)
class Span:
    start: date | None  # None: nothing is left once cut to the data
    end: date | None
    asked_start: date
    asked_end: date
    notes: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return self.start is None


def month_last(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def add_months(first: date, months: int) -> date:
    """The first day of the month `months` after the month of `first` (negative: before)."""
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def asked(period: Period | None, today: date, first: date) -> tuple[date, date]:
    """The days a period asks for, before cutting to the data. `first` is the data's first day
    (used by "all")."""
    if period is None or period.kind == "all":
        return first, today
    k = period.kind
    monday = today - timedelta(days=today.weekday())
    this_month = today.replace(day=1)
    if k == "today":
        return today, today
    if k == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if k == "this_week":
        return monday, today
    if k == "last_week":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if k == "this_month":
        return this_month, today
    if k == "last_month":
        start = add_months(this_month, -1)
        return start, this_month - timedelta(days=1)
    if k == "this_year":
        return date(today.year, 1, 1), today
    if k == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    n = period.n or 0
    if k == "last_n_days":
        return today - timedelta(days=n), today - timedelta(days=1)
    if k == "last_n_weeks":
        return monday - timedelta(days=7 * n), monday - timedelta(days=1)
    if k == "last_n_months":
        return add_months(this_month, -n), this_month - timedelta(days=1)
    if k == "month":
        assert period.year and period.month
        return date(period.year, period.month, 1), month_last(period.year, period.month)
    if k == "named_month":
        assert period.month
        year = today.year if period.month <= today.month else today.year - 1
        return date(year, period.month, 1), month_last(year, period.month)
    if k == "year":
        assert period.year
        return date(period.year, 1, 1), date(period.year, 12, 31)
    if k == "range":
        assert period.start and period.end
        return period.start, period.end
    assert period.start  # since
    return period.start, today


def span(period: Period | None, today: date, first: date, last: date) -> Span:
    """The period cut to the days my data covers (`first` to `last`), with notes saying what was
    cut. Nothing left: an empty span."""
    a, b = asked(period, today, first)
    notes = []
    if b > last:
        notes.append(f"Your data ends on {last}: later days are not in it yet.")
    if a < first and period is not None and period.kind != "all":
        notes.append(f"Your data starts on {first}: earlier days are not in it.")
    start, end = max(a, first), min(b, last)
    if start > end:
        return Span(None, None, a, b, tuple(notes))
    return Span(start, end, a, b, tuple(notes))


def future_day(day: Day, today: date) -> date:
    """A future day from its words. A day of the month past the month's end ("the 31st" in a
    30-day month) is that month's last day."""
    k = day.kind
    if k == "tomorrow":
        return today + timedelta(days=1)
    if k == "in_days":
        return today + timedelta(days=day.n or 0)
    if k == "date":
        assert day.date is not None
        return day.date
    if k == "day_of_month":
        assert day.day
        this = today.replace(day=min(day.day, monthrange(today.year, today.month)[1]))
        if this >= today:
            return this
        nxt = add_months(today.replace(day=1), 1)
        return nxt.replace(day=min(day.day, monthrange(nxt.year, nxt.month)[1]))
    if k == "named_date":
        assert day.month and day.day
        for year in (today.year, today.year + 1):
            d = date(year, day.month, min(day.day, monthrange(year, day.month)[1]))
            if d >= today:
                return d
        raise AssertionError("unreachable")
    if k == "end_of_week":
        return today + timedelta(days=6 - today.weekday())
    return month_last(today.year, today.month)  # end_of_month
