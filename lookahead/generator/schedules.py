"""Schedule engine for the synthetic generator.

Each schedule turns a rule ("rent, $750, on the 1st, shift later") into dated
transactions that carry their own ground-truth labels. The labels come from
these schedule objects, never from the detection rules we evaluate later.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import numpy as np

from lookahead.calendar_rules import (
    Direction,
    monthly_date,
    next_card_posting_day,
    shift_to_business_day,
)
from lookahead.taxonomy import check_labels

Cadence = Literal["weekly", "biweekly", "monthly", "semimonthly"]

# "earlier"/"later": business-day rule (payroll, bill payments).
# "card": card posting day rule (things the account holder initiates:
# card purchases, card-billed subscriptions, sent e-Transfers).
Shift = Direction | Literal["card"]

# A due date after the range end can still shift earlier into the range.
_SHIFT_MARGIN = timedelta(days=7)


def _post(due: date, shift: Shift) -> date:
    """The posting date for an item due on `due`."""
    if shift == "card":
        return next_card_posting_day(due)
    return shift_to_business_day(due, shift)


@dataclass(frozen=True)
class GeneratedTxn:
    """One synthetic transaction plus its ground truth. Description text is added later."""

    posted_date: date
    amount_cents: int
    txn_type: str  # the bank's "Description" column, e.g. "pos purchase"
    merchant: str  # canonical merchant name
    category: str
    flow_type: str
    series_id: str | None = None  # set only for recurring series
    one_off: bool = False  # large single item, excluded from everyday spending
    link_id: str | None = None  # shared by related rows (funding + payment, return + original)
    online: bool | None = None  # card purchases only: True = online, False = in person

    def __post_init__(self) -> None:
        check_labels(self.category, self.flow_type)


@dataclass(frozen=True)
class Amount:
    """A signed amount in cents: fixed, or drawn uniformly within ±spread."""

    typical_cents: int
    spread_cents: int = 0

    def __post_init__(self) -> None:
        if self.spread_cents < 0:
            raise ValueError("spread_cents must be >= 0")
        if self.spread_cents and self.spread_cents >= abs(self.typical_cents):
            raise ValueError(
                "spread_cents must be smaller than |typical_cents| (sign must not flip)"
            )

    def draw(self, rng: np.random.Generator) -> int:
        if self.spread_cents == 0:
            return self.typical_cents
        low = self.typical_cents - self.spread_cents
        high = self.typical_cents + self.spread_cents
        return int(rng.integers(low, high + 1))  # int(): numpy gives np.int64


@dataclass(frozen=True)
class RecurringSchedule:
    """A recurring series: paycheques, rent, bills, subscriptions, transfers to savings.

    `anchor` is the first due date. Weekly/biweekly repeat from it; monthly uses
    its day of month (clamped to short months); semimonthly is the 15th and the
    last day of each month from the anchor's month. `end` is the last due date.
    """

    series_id: str
    merchant: str
    category: str
    flow_type: str
    txn_type: str
    cadence: Cadence
    anchor: date
    amount: Amount
    shift: Shift
    end: date | None = None

    def __post_init__(self) -> None:
        check_labels(self.category, self.flow_type)

    def _due_dates(self, until: date) -> list[date]:
        """All due dates from the anchor up to `until`, before business-day shifts."""
        last = min(until, self.end) if self.end else until
        if self.cadence in ("weekly", "biweekly"):
            step = timedelta(weeks=1 if self.cadence == "weekly" else 2)
            due, out = self.anchor, []
            while due <= last:
                out.append(due)
                due += step
            return out
        days = (15, 31) if self.cadence == "semimonthly" else (self.anchor.day,)
        out = []
        year, month = self.anchor.year, self.anchor.month
        while date(year, month, 1) <= last:
            for day in days:
                due = monthly_date(year, month, day)
                if self.anchor <= due <= last:
                    out.append(due)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return out

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]:
        """Transactions whose posting date falls in [start, end]."""
        out = []
        for due in self._due_dates(end + _SHIFT_MARGIN):
            posted = _post(due, self.shift)
            if start <= posted <= end:
                out.append(
                    GeneratedTxn(
                        posted_date=posted,
                        amount_cents=self.amount.draw(rng),
                        txn_type=self.txn_type,
                        merchant=self.merchant,
                        category=self.category,
                        flow_type=self.flow_type,
                        series_id=self.series_id,
                    )
                )
        return out


@dataclass(frozen=True)
class OneOff:
    """A single unscheduled item, such as a returned e-Transfer. Flagged one_off."""

    due: date
    amount_cents: int
    merchant: str
    category: str
    flow_type: str
    txn_type: str
    shift: Shift = "later"
    link_id: str | None = None

    def __post_init__(self) -> None:
        check_labels(self.category, self.flow_type)

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]:
        """A one-item list, or empty if the posting date is outside [start, end]."""
        posted = _post(self.due, self.shift)
        if not start <= posted <= end:
            return []
        return [
            GeneratedTxn(
                posted_date=posted,
                amount_cents=self.amount_cents,
                txn_type=self.txn_type,
                merchant=self.merchant,
                category=self.category,
                flow_type=self.flow_type,
                one_off=True,
                link_id=self.link_id,
            )
        ]


@dataclass(frozen=True)
class IrregularIncome:
    """Random incoming transfers: a random count per month, random round amounts.

    Dates follow the card posting day rule: Saturdays post, a Sunday or holiday
    moves to the next posting day, and a date moved past the range end is dropped.
    """

    merchant: str
    category: str
    flow_type: str
    txn_type: str
    per_month: tuple[int, int]  # inclusive min, max count per month
    amount_range_cents: tuple[int, int]  # inclusive min, max
    round_to_cents: int = 100

    def __post_init__(self) -> None:
        check_labels(self.category, self.flow_type)

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]:
        """Transactions in [start, end], sorted by date."""
        out = []
        year, month = start.year, start.month
        while date(year, month, 1) <= end:
            first = max(start, date(year, month, 1))
            last = min(end, monthly_date(year, month, 31))
            n_days = (last - first).days + 1
            count = int(rng.integers(self.per_month[0], self.per_month[1] + 1))
            for _ in range(count):
                sent = first + timedelta(days=int(rng.integers(n_days)))
                low, high = (a // self.round_to_cents for a in self.amount_range_cents)
                amount = int(rng.integers(low, high + 1)) * self.round_to_cents
                posted = next_card_posting_day(sent)
                if posted > end:
                    continue
                out.append(
                    GeneratedTxn(
                        posted_date=posted,
                        amount_cents=amount,
                        txn_type=self.txn_type,
                        merchant=self.merchant,
                        category=self.category,
                        flow_type=self.flow_type,
                    )
                )
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return sorted(out, key=lambda t: t.posted_date)


@dataclass(frozen=True)
class DatedItem:
    """One occurrence of a DatedSeries."""

    due: date
    amount_cents: int
    link_id: str | None = None


@dataclass(frozen=True)
class DatedSeries:
    """A recurring series on explicit dates, e.g. tuition each semester.

    For series whose spacing is irregular but known in advance. Each item can
    carry its own link_id (e.g. to the transfer that funds it).
    """

    series_id: str
    merchant: str
    category: str
    flow_type: str
    txn_type: str
    shift: Shift
    items: tuple[DatedItem, ...]

    def __post_init__(self) -> None:
        check_labels(self.category, self.flow_type)

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]:
        """Items whose posting date falls in [start, end], sorted by date."""
        out = []
        for item in self.items:
            posted = _post(item.due, self.shift)
            if start <= posted <= end:
                out.append(
                    GeneratedTxn(
                        posted_date=posted,
                        amount_cents=item.amount_cents,
                        txn_type=self.txn_type,
                        merchant=self.merchant,
                        category=self.category,
                        flow_type=self.flow_type,
                        series_id=self.series_id,
                        link_id=item.link_id,
                    )
                )
        return sorted(out, key=lambda t: t.posted_date)


@dataclass(frozen=True)
class IrregularSeries:
    """A recurring source with irregular gaps, e.g. family credit memos every 4-8 weeks.

    Each gap is drawn uniformly from `gap_days`. A share `round_share` of amounts
    is rounded to `round_to_cents`; the rest are exact to the cent. Dates follow
    the card posting day rule.
    """

    series_id: str
    merchant: str
    category: str
    flow_type: str
    txn_type: str
    gap_days: tuple[int, int]  # inclusive min, max days between arrivals
    amount_range_cents: tuple[int, int]  # inclusive min, max
    round_to_cents: int = 100
    round_share: float = 1.0

    def __post_init__(self) -> None:
        check_labels(self.category, self.flow_type)
        if not 1 <= self.gap_days[0] <= self.gap_days[1]:
            raise ValueError("gap_days must be (min, max) with 1 <= min <= max")
        if not 0.0 <= self.round_share <= 1.0:
            raise ValueError("round_share must be between 0 and 1")

    def _amount(self, rng: np.random.Generator) -> int:
        low, high = self.amount_range_cents
        if rng.random() < self.round_share:
            step = self.round_to_cents
            return int(rng.integers(-(-low // step), high // step + 1)) * step
        return int(rng.integers(low, high + 1))

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]:
        """Arrivals in [start, end], sorted by date."""
        out = []
        # The first arrival is somewhere in the first gap, as if the series began earlier.
        arrival = start + timedelta(days=int(rng.integers(self.gap_days[1])))
        while arrival <= end:
            posted = next_card_posting_day(arrival)
            amount = self._amount(rng)
            if posted <= end:
                out.append(
                    GeneratedTxn(
                        posted_date=posted,
                        amount_cents=amount,
                        txn_type=self.txn_type,
                        merchant=self.merchant,
                        category=self.category,
                        flow_type=self.flow_type,
                        series_id=self.series_id,
                    )
                )
            gap = int(rng.integers(self.gap_days[0], self.gap_days[1] + 1))
            arrival += timedelta(days=gap)
        return out
