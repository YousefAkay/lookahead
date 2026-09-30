"""Everyday spending for the synthetic generator: card purchases and cash withdrawals.

For each calendar day (Sundays included, since people shop on Sundays), the
number of purchases made is Poisson-distributed around `purchases_per_day`,
scaled by a weekday factor and the persona's activity that month. Each purchase
posts on the next card posting day, so Sunday purchases land on Monday.

`generate` ignores the balance; `simulate.py` adds the overdraft limit and
cutting back when the balance is low.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from lookahead.calendar_rules import next_card_posting_day
from lookahead.generator.schedules import GeneratedTxn
from lookahead.taxonomy import check_labels

_DAYS_PER_MONTH = 30.44

# CAD per unit of foreign currency, drawn uniformly per purchase, in plausible ranges;
# AED stands in for "a currency worth much less than CAD".
CURRENCY_RATES: dict[str, tuple[float, float]] = {
    "USD": (1.36, 1.45),
    "EUR": (1.55, 1.70),
    "GBP": (1.75, 1.90),
    "AED": (0.37, 0.40),
}


@dataclass(frozen=True)
class Merchant:
    """A place a persona spends at, with how often and how much (typical amount)."""

    name: str
    category: str
    online: bool
    weight: float  # relative frequency among this persona's purchases
    median_cents: int  # typical amount, in the merchant's currency (positive)
    currency: str = "CAD"

    def __post_init__(self) -> None:
        check_labels(self.category, "everyday")
        if self.currency != "CAD" and self.currency not in CURRENCY_RATES:
            raise ValueError(f"unknown currency {self.currency!r}")


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
        amount_cents = max(100, round(amount))  # at least 1.00 in the merchant's currency
        original = None
        if merchant.currency != "CAD":
            # The bank converts: CAD amount = foreign amount x that day's rate.
            original = amount_cents
            amount_cents = round(original * rng.uniform(*CURRENCY_RATES[merchant.currency]))
        return GeneratedTxn(
            posted_date=next_card_posting_day(made),
            amount_cents=-amount_cents,
            txn_type="pos purchase",
            merchant=merchant.name,
            category=merchant.category,
            flow_type="everyday",
            online=merchant.online,
            currency=merchant.currency,
            original_amount_cents=-original if original is not None else None,
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

    def day(self, made: date, rng: np.random.Generator, scale: float) -> list[GeneratedTxn]:
        """Purchases and cash withdrawals made on `made`, scaled by `scale`.

        Each row carries its posting date (the next card posting day), which can be
        later than `made` (Sunday purchases post Monday).
        """
        rate = self.purchases_per_day * self.weekday_factors[made.weekday()] * scale
        n_purchases = rng.poisson(rate)
        n_cash = rng.poisson(self.cash_per_month / _DAYS_PER_MONTH * scale)
        return [self._purchase(made, rng) for _ in range(n_purchases)] + [
            self._cash(made, rng) for _ in range(n_cash)
        ]

    def generate(
        self,
        start: date,
        end: date,
        rng: np.random.Generator,
        activity: Callable[[date], float],
    ) -> list[GeneratedTxn]:
        """Unconstrained everyday rows posted in [start, end], sorted by date.

        Ignores the balance: this is the persona's spending "wish". The pipeline uses
        `simulate`, which applies the overdraft limit and cutting back when low.
        `activity(d)` scales spending on day d (1.0 = normal, 0.1 = away).
        """
        out = []
        made = start
        while made <= end:
            out += self.day(made, rng, activity(made))
            made += timedelta(days=1)
        # A purchase made on the last day can post after the range end: drop it.
        return sorted((t for t in out if t.posted_date <= end), key=lambda t: t.posted_date)
