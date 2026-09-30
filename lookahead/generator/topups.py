"""Balance-triggered top-ups: stage 3 of the generator.

A generic income type: when the running balance ends a day below a threshold, a
top-up posts a few business days later. Any persona can use it; nothing here is
specific to credit memos or students. The app itself must NOT assume this income
pattern (see DECISIONS.md, scope rule).
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from lookahead.calendar_rules import is_business_day
from lookahead.generator.schedules import GeneratedTxn
from lookahead.taxonomy import check_labels


def _check_range(name: str, pair: tuple[int, int], minimum: int) -> None:
    if not minimum <= pair[0] <= pair[1]:
        raise ValueError(f"{name} must be (min, max) with {minimum} <= min <= max")


def _add_business_days(d: date, n: int) -> date:
    """The n-th business day after d (n >= 1)."""
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


@dataclass(frozen=True)
class BalanceTopUp:
    """When the evening balance is below `threshold_cents`, money arrives later.

    Delay: usually `delay_business_days`; with probability `late_chance`,
    `late_delay_business_days` instead (the balance can dip below zero first).
    Amount: usually `amount_range_cents`; with probability `large_chance`,
    `large_range_cents`. A share `round_share` of amounts is rounded.
    At most one top-up is pending at a time, and arrivals are at least
    `min_gap_days` apart.

    `lookahead_days`: the trigger also counts scheduled (non-everyday) rows due in
    the next N days, e.g. rent on the 1st. People ask for money before a known bill,
    not after it bounces. Everyday spending is not foreseen.
    """

    series_id: str
    merchant: str
    category: str
    flow_type: str
    txn_type: str
    threshold_cents: int
    delay_business_days: tuple[int, int]
    late_chance: float
    late_delay_business_days: tuple[int, int]
    amount_range_cents: tuple[int, int]
    large_chance: float = 0.0
    large_range_cents: tuple[int, int] | None = None
    round_to_cents: int = 100
    round_share: float = 1.0
    min_gap_days: int = 0
    lookahead_days: int = 0

    def __post_init__(self) -> None:
        check_labels(self.category, self.flow_type)
        _check_range("delay_business_days", self.delay_business_days, 1)
        _check_range("late_delay_business_days", self.late_delay_business_days, 1)
        _check_range("amount_range_cents", self.amount_range_cents, 1)
        for name in ("late_chance", "large_chance", "round_share"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.large_chance > 0:
            if self.large_range_cents is None:
                raise ValueError("large_chance > 0 needs large_range_cents")
            _check_range("large_range_cents", self.large_range_cents, 1)

    def draw_arrival(self, trigger: date, rng: np.random.Generator) -> date:
        """Posting date for a top-up triggered on the evening of `trigger`."""
        late = rng.random() < self.late_chance
        low, high = self.late_delay_business_days if late else self.delay_business_days
        return _add_business_days(trigger, int(rng.integers(low, high + 1)))

    def draw_amount(self, rng: np.random.Generator) -> int:
        large = self.large_range_cents is not None and rng.random() < self.large_chance
        low, high = self.large_range_cents if large else self.amount_range_cents  # type: ignore[misc]
        if rng.random() < self.round_share:
            step = self.round_to_cents
            return int(rng.integers(-(-low // step), high // step + 1)) * step
        return int(rng.integers(low, high + 1))


class TopUpRunner:
    """Runs top-up rules day by day, for a caller that tracks the posted balance.

    Each day: call `morning(day)` (returns top-ups posting today), apply the day's
    other rows, then call `evening(day, balance)` with the posted balance.
    `scheduled` rows (everything but everyday spending) feed the look-ahead.
    """

    def __init__(
        self,
        topups: tuple[BalanceTopUp, ...],
        scheduled: list[GeneratedTxn],
        rng: np.random.Generator,
    ) -> None:
        self.topups = topups
        self.rng = rng
        self.scheduled_by_day: dict[date, int] = {}
        for t in scheduled:
            if t.flow_type != "everyday":
                self.scheduled_by_day[t.posted_date] = (
                    self.scheduled_by_day.get(t.posted_date, 0) + t.amount_cents
                )
        self.pending: dict[int, tuple[date, int]] = {}  # rule index -> (arrival, amount)
        self.last_arrival: dict[int, date] = {}

    def morning(self, day: date) -> list[GeneratedTxn]:
        """Top-ups that post today."""
        out = []
        for i, rule in enumerate(self.topups):
            if i in self.pending and self.pending[i][0] == day:
                _, amount = self.pending.pop(i)
                self.last_arrival[i] = day
                out.append(
                    GeneratedTxn(
                        posted_date=day,
                        amount_cents=amount,
                        txn_type=rule.txn_type,
                        merchant=rule.merchant,
                        category=rule.category,
                        flow_type=rule.flow_type,
                        series_id=rule.series_id,
                    )
                )
        return out

    def evening(self, day: date, balance: int) -> None:
        """Check each rule against the evening balance; schedule a top-up if needed."""
        for i, rule in enumerate(self.topups):
            last = self.last_arrival.get(i)
            gap_ok = last is None or (day - last).days >= rule.min_gap_days
            upcoming = sum(
                self.scheduled_by_day.get(day + timedelta(days=k), 0)
                for k in range(1, rule.lookahead_days + 1)
            )
            if balance + upcoming < rule.threshold_cents and i not in self.pending and gap_ok:
                self.pending[i] = (rule.draw_arrival(day, self.rng), rule.draw_amount(self.rng))


def apply_topups(
    rows: list[GeneratedTxn],
    opening_balance_cents: int,
    topups: tuple[BalanceTopUp, ...],
    start: date,
    end: date,
    rng: np.random.Generator,
) -> list[GeneratedTxn]:
    """Walk the running balance of fixed `rows` day by day; return the top-ups it triggers."""
    runner = TopUpRunner(topups, rows, rng)
    by_day: dict[date, int] = {}
    for t in rows:
        by_day[t.posted_date] = by_day.get(t.posted_date, 0) + t.amount_cents
    out: list[GeneratedTxn] = []
    balance = opening_balance_cents
    day = start
    while day <= end:
        arrivals = runner.morning(day)
        out += arrivals
        balance += sum(t.amount_cents for t in arrivals) + by_day.get(day, 0)
        runner.evening(day, balance)
        day += timedelta(days=1)
    return out
