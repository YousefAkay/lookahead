"""Recurring payments placed on the forecast days (3b step 2).

Every active, non-rejected series is used as it stands, detected or confirmed or entered: the
forecast needs no confirmations. Each item says which of the three it rests on, because the
page shows every assumption that way.

Rent (a series with a rent rule) is placed as follows: always the larger allowed amount; one
payment per cycle; the date is the earliest day of the window that is still ahead; and the most
recent window that ended with no payment (and no later payment) is expected on the first
forecast day, because the data may simply be stale or the payment late. Older gaps are treated
as skipped months. Uses only rows posted by the forecast start (rule zero).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from lookahead.forecast.start import ForecastStart
from lookahead.recurring import expected_dates
from lookahead.recurring_store import Stored, rent_rule_of
from lookahead.rent import RentRule, assign_cycles, rent_cents, window


@dataclass(frozen=True)
class ScheduledItem:
    day: date
    cents: int  # signed: money in is positive, money out is negative
    series_key: str
    name: str | None
    basis: str  # "detected", "confirmed" or "entered" (typed by me, or my own date or amount)
    note: str


def _basis(s: Stored) -> str:
    if s.edited or s.status == "user-added":
        return "entered"
    return "confirmed" if s.status == "confirmed" else "detected"


def scheduled_items(
    series: Sequence[Stored], posted: Mapping[str, date], start: ForecastStart
) -> list[ScheduledItem]:
    """The payments expected on each forecast day. `posted` maps a payment's fingerprint to its
    posting date; payments posted after the data ends are ignored."""
    known = {fp: day for fp, day in posted.items() if day <= start.data_last_updated}
    items: list[ScheduledItem] = []
    for s in series:
        if not s.active or s.status == "rejected":
            continue
        rule = rent_rule_of(s)
        if rule is not None and s.direction == "out":
            paid = [known[fp] for fp in s.evidence if fp in known]
            items += _rent_items(s, rule, paid, start)
        else:
            items += _pattern_items(s, start)
    return sorted(items, key=lambda i: (i.day, i.series_key))


def _pattern_items(s: Stored, start: ForecastStart) -> list[ScheduledItem]:
    """Its next expected date, then every later date of its pattern inside the horizon."""
    first = max(s.next_expected, start.first_day)
    if first > start.last_day:
        return []
    note = "from its detected pattern"
    if s.next_expected < start.first_day:
        note = "due by the last day of the data and not seen yet: expected on the first day"
    later = expected_dates(
        s.cadence, s.anchor, first + timedelta(days=1), start.last_day, s.direction,
        card=s.kind == "e-transfer",
    )  # fmt: skip
    return [
        ScheduledItem(day, s.expected_cents, s.key, s.name, _basis(s), note if day == first else
                      "from its detected pattern")
        for day in [first, *later]
    ]  # fmt: skip


def _rent_items(
    s: Stored, rule: RentRule, paid: list[date], start: ForecastStart
) -> list[ScheduledItem]:  # fmt: skip
    cycles = {cycle for _, cycle in assign_cycles(rule, paid)}
    cents = -rent_cents(rule)
    basis = _basis(s)
    ended: list[tuple[date, date]] = []  # windows over before the first forecast day
    items: list[ScheduledItem] = []
    first_month = start.first_day.year * 12 + start.first_day.month - 1 - 2  # 2 months back
    last_month = start.last_day.year * 12 + start.last_day.month - 1
    for n in range(first_month, last_month + 1):
        begins, ends = window(rule, n // 12, n % 12 + 1)
        if ends < start.first_day:
            ended.append((begins, ends))
        elif begins not in cycles and begins <= start.last_day:
            items.append(ScheduledItem(
                max(begins, start.first_day), cents, s.key, s.name, basis,
                "rent: the larger allowed amount, on the earliest day of the window still ahead",
            ))  # fmt: skip
    if ended:
        begins, _ = max(ended)
        if begins not in cycles and all(begins > c for c in cycles):
            items.append(ScheduledItem(
                start.first_day, cents, s.key, s.name, basis,
                "rent: the window ended with no payment and the data may be out of date or the "
                "payment late (not seen yet): the larger allowed amount, on the first day",
            ))  # fmt: skip
    return items
