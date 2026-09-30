"""Everyday spending for the synthetic generator: card purchases and cash withdrawals.

For each calendar day (Sundays included, since people shop on Sundays), the
number of purchases made is Poisson-distributed around `purchases_per_day`,
scaled by a weekday factor and the persona's activity that month. Each purchase
posts on the next card posting day, so Sunday purchases land on Monday.

Spending does not react to the balance (known v0 limit, see DECISIONS.md).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from lookahead.calendar_rules import next_card_posting_day
from lookahead.generator.schedules import GeneratedTxn
from lookahead.taxonomy import check_labels

_DAYS_PER_MONTH = 30.44


@dataclass(frozen=True)
class Merchant:
    """A place a persona spends at, with how often and how much (typical amount)."""

    name: str
    category: str
    online: bool
    weight: float  # relative frequency among this persona's purchases
    median_cents: int  # typical purchase amount (positive; the sign is applied later)

    def __post_init__(self) -> None:
        check_labels(self.category, "everyday")


@dataclass(frozen=True)
class SpendingProfile:
    """How a persona spends day to day.

    Purchase amounts are log-normal around each merchant's median: most are near
    it, a few are much larger (the "occasional larger purchase").
    """

    purchases_per_day: float
    amount_sigma: float  # spread of log-amounts; 0.5 means ~90% within 0.43x-2.3x the median
    merchants: tuple[Merchant, ...]
    weekday_factors: tuple[float, ...] = (1.0,) * 7  # Monday..Sunday, day the purchase is made
    cash_per_month: float = 0.0
    cash_amounts_cents: tuple[int, ...] = (2_000, 4_000, 6_000, 8_000, 10_000)

    def __post_init__(self) -> None:
        if len(self.weekday_factors) != 7:
            raise ValueError("weekday_factors needs 7 values, Monday to Sunday")
        if not self.merchants:
            raise ValueError("at least one merchant is required")

    def _purchase(self, made: date, rng: np.random.Generator) -> GeneratedTxn:
        weights = np.array([m.weight for m in self.merchants], dtype=float)
        merchant = self.merchants[int(rng.choice(len(self.merchants), p=weights / weights.sum()))]
        amount = merchant.median_cents * float(np.exp(self.amount_sigma * rng.standard_normal()))
        return GeneratedTxn(
            posted_date=next_card_posting_day(made),
            amount_cents=-max(100, round(amount)),  # at least $1.00
            txn_type="pos purchase",
            merchant=merchant.name,
            category=merchant.category,
            flow_type="everyday",
            online=merchant.online,
        )

    def _cash(self, made: date, rng: np.random.Generator) -> GeneratedTxn:
        amount = int(rng.choice(self.cash_amounts_cents))
        return GeneratedTxn(
            posted_date=next_card_posting_day(made),
            amount_cents=-amount,
            txn_type="abm withdrawal",
            merchant="ABM",
            category="Cash",
            flow_type="everyday",
        )

    def generate(
        self,
        start: date,
        end: date,
        rng: np.random.Generator,
        activity: Callable[[date], float],
    ) -> list[GeneratedTxn]:
        """Everyday rows posted in [start, end], sorted by date.

        `activity(d)` scales spending on day d (1.0 = normal, 0.1 = away).
        """
        out = []
        cash_rate = self.cash_per_month / _DAYS_PER_MONTH
        made = start
        while made <= end:
            scale = activity(made)
            n_purchases = rng.poisson(
                self.purchases_per_day * self.weekday_factors[made.weekday()] * scale
            )
            n_cash = rng.poisson(cash_rate * scale)
            out += [self._purchase(made, rng) for _ in range(n_purchases)]
            out += [self._cash(made, rng) for _ in range(n_cash)]
            made += timedelta(days=1)
        # A purchase made on the last day can post after the range end: drop it.
        return sorted((t for t in out if t.posted_date <= end), key=lambda t: t.posted_date)
