"""The three hand-written v0 personas: scheduled money and everyday spending.

Amounts are integer cents: 85_000 = $850.00. Signs: + into the account, - out.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np

from lookahead.generator.schedules import (
    Amount,
    DatedItem,
    DatedSeries,
    GeneratedTxn,
    IrregularIncome,
    IrregularSeries,
    OneOff,
    RecurringSchedule,
    Shift,
)
from lookahead.generator.spending import Merchant, SpendingProfile


class Source(Protocol):
    """Anything that can generate transactions: every schedule type has this method."""

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]: ...


@dataclass(frozen=True)
class Persona:
    """A synthetic account holder: opening balance, scheduled money, everyday spending."""

    persona_id: str
    description: str
    opening_balance_cents: int
    sources: tuple[Source, ...]
    spending: SpendingProfile | None = None
    quiet_months: frozenset[int] = frozenset()  # months with low everyday activity
    quiet_activity: float = 1.0  # everyday-spending multiplier in quiet months

    def activity(self, d: date) -> float:
        """Everyday-spending multiplier on day d: lower in quiet months."""
        return self.quiet_activity if d.month in self.quiet_months else 1.0

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]:
        """All scheduled transactions in [start, end], sorted by date (stable)."""
        rows = [t for source in self.sources for t in source.generate(start, end, rng)]
        return sorted(rows, key=lambda t: t.posted_date)


def _monthly(
    series_id: str,
    merchant: str,
    category: str,
    txn_type: str,
    day: int,
    amount: Amount,
    shift: Shift,
) -> RecurringSchedule:
    """A monthly outflow from June 2026. Flow type: `transfer` for Transfers, else
    `recurring_outflow`. Keeps the persona tables short."""
    return RecurringSchedule(
        series_id=series_id,
        merchant=merchant,
        category=category,
        flow_type="transfer" if category == "Transfers" else "recurring_outflow",
        txn_type=txn_type,
        cadence="monthly",
        anchor=date(2026, 6, day),
        amount=amount,
        shift=shift,
    )


def _m(name: str, category: str, online: bool, weight: float, median_dollars: float) -> Merchant:
    """Short merchant constructor for the tables below (median given in dollars)."""
    return Merchant(name, category, online, weight, round(median_dollars * 100))


_CAFE, _REST, _DELIV = "Restaurants & cafés", "Restaurants & cafés", "Food delivery"
_GROC, _SHOP, _TRANS = "Groceries", "Shopping", "Transport"

# A: student, lots of food delivery.
_SPENDING_A = SpendingProfile(
    purchases_per_day=1.3,
    amount_sigma=0.45,
    cash_per_month=1.0,
    merchants=(
        _m("Uber Eats", _DELIV, True, 12, 27),
        _m("DoorDash", _DELIV, True, 7, 25),
        _m("Tim Hortons", _CAFE, False, 9, 5),
        _m("Cafe Quorvel", _CAFE, False, 6, 6),
        _m("Maxi", _GROC, False, 6, 34),
        _m("Super C", _GROC, False, 4, 30),
        _m("Couche-Tard", _GROC, False, 6, 8),
        _m("STM", _TRANS, False, 4, 12),
        _m("Dollarama", _SHOP, False, 2, 10),
    ),
)

# B: young worker, groceries and restaurants.
_SPENDING_B = SpendingProfile(
    purchases_per_day=1.2,
    amount_sigma=0.45,
    cash_per_month=0.5,
    weekday_factors=(0.9, 0.9, 0.95, 1.0, 1.15, 1.25, 0.85),
    merchants=(
        _m("IGA", _GROC, False, 8, 48),
        _m("Metro", _GROC, False, 7, 42),
        _m("Restaurant Le Petit Orme", _REST, False, 6, 42),
        _m("Cafe Velisaro", _CAFE, False, 7, 6),
        _m("Bixi", _TRANS, True, 5, 4),
        _m("Pharmaprix", "Health & pharmacy", False, 4, 22),
        _m("Amazon.ca", _SHOP, True, 5, 38),
        _m("Winners", _SHOP, False, 2, 55),
        _m("SAQ", _GROC, False, 3, 24),
    ),
)

# C: a made-up student budget (all values invented).
_SPENDING_C = SpendingProfile(
    purchases_per_day=1.6,
    amount_sigma=0.5,
    cash_per_month=1.0,
    merchants=(
        _m("Tim Hortons", _CAFE, False, 6, 5),
        _m("Café Quorvel", _CAFE, False, 4, 6),
        _m("Couche-Tard", _GROC, False, 5, 9),
        _m("Metro", _GROC, False, 7, 34),
        _m("Super C", _GROC, False, 5, 41),
        _m("Restaurant Le Petit Orme", _REST, False, 2.5, 26),
        _m("Jean Coutu", "Health & pharmacy", False, 2, 24),
        _m("Dollarama", _SHOP, False, 3, 9),
        _m("STM", _TRANS, False, 6, 11),
        _m("Bixi", _TRANS, False, 2, 5),
        _m("Uber Eats", _DELIV, True, 6, 29),
        _m("Uber", _TRANS, True, 3, 22),
        _m("Lyft", _TRANS, True, 2, 19),
        _m("Amazon.ca", _SHOP, True, 4, 36),
        _m("Steam", "Entertainment", True, 1, 14),
        _m("Best Buy", _SHOP, False, 0.7, 140),  # the occasional larger purchase
    ),
)


PERSONA_A = Persona(
    persona_id="A",
    description="Student with a part-time job, paid biweekly; tight before payday",
    opening_balance_cents=60_000,
    spending=_SPENDING_A,
    sources=(
        RecurringSchedule(
            series_id="A-pay",
            merchant="Boutique Mile End",
            category="Income",
            flow_type="income",
            txn_type="deposit",
            cadence="biweekly",
            anchor=date(2026, 6, 5),
            amount=Amount(85_000, spread_cents=6_000),  # hours vary
            shift="earlier",
        ),
        _monthly("A-rent", "Interac e-Transfer", "Rent & housing", "withdrawal", 1,
                 Amount(-75_000), "card"),
        _monthly("A-fizz", "Fizz", "Phone, internet & utilities", "pos purchase", 12,
                 Amount(-3_500), "card"),
        _monthly("A-spotify", "Spotify", "Subscriptions", "pos purchase", 20,
                 Amount(-1_299), "card"),
    ),
)  # fmt: skip

PERSONA_B = Persona(
    persona_id="B",
    description="Young worker paid on the 15th and last day; saves monthly",
    opening_balance_cents=250_000,
    spending=_SPENDING_B,
    sources=(
        RecurringSchedule(
            series_id="B-pay",
            merchant="Groupe Conseil Plateau",
            category="Income",
            flow_type="income",
            txn_type="deposit",
            cadence="semimonthly",
            anchor=date(2026, 6, 1),
            amount=Amount(190_000),
            shift="earlier",
        ),
        _monthly("B-rent", "Gestion Immobiliere", "Rent & housing", "bill payment", 1,
                 Amount(-115_000), "later"),
        _monthly("B-hydro", "Hydro-Quebec", "Phone, internet & utilities", "bill payment", 20,
                 Amount(-9_000, spread_cents=3_000), "later"),
        _monthly("B-videotron", "Videotron", "Phone, internet & utilities", "bill payment", 8,
                 Amount(-8_500), "later"),
        _monthly("B-netflix", "Netflix", "Subscriptions", "pos purchase", 14,
                 Amount(-1_899), "card"),
        _monthly("B-savings", "Own savings account", "Transfers", "customer transfer dr.", 16,
                 Amount(-30_000), "later"),
    ),
)  # fmt: skip

_TUITION = DatedSeries(
    series_id="C-tuition",
    merchant="Concordia University",
    category="Education",
    flow_type="recurring_outflow",
    txn_type="bill payment",
    shift="later",
    items=(  # summer, fall, winter terms; only fall falls in Jun-Sep 2026
        DatedItem(date(2026, 5, 12), -725_000, "tuition-summer-2026"),
        DatedItem(date(2026, 8, 28), -725_000, "tuition-fall-2026"),
        DatedItem(date(2027, 1, 8), -690_000, "tuition-winter-2027"),
    ),
)

# Earmarked credit memos: same series as the living-money memos, a few days before
# each tuition payment, covering most of it, linked to it.
_EARMARKED_MEMOS = DatedSeries(
    series_id="C-family",
    merchant="Family",
    category="Income",
    flow_type="income",
    txn_type="credit memo",
    shift="card",
    items=(
        DatedItem(date(2026, 5, 6), 640_000, "tuition-summer-2026"),
        DatedItem(date(2026, 8, 24), 640_000, "tuition-fall-2026"),
        DatedItem(date(2027, 1, 4), 640_000, "tuition-winter-2027"),
    ),
)

PERSONA_C = Persona(
    persona_id="C",
    description="Student funded by family credit memos; tuition each term; quiet in August",
    opening_balance_cents=135_000,
    spending=_SPENDING_C,
    quiet_months=frozenset({8}),
    quiet_activity=0.25,
    sources=(
        IrregularSeries(  # main income: living money, every 3-6 weeks
            series_id="C-family",
            merchant="Family",
            category="Income",
            flow_type="income",
            txn_type="credit memo",
            gap_days=(21, 42),
            amount_range_cents=(120_000, 280_000),
            round_to_cents=5_000,
            round_share=0.5,
        ),
        _EARMARKED_MEMOS,
        _TUITION,
        IrregularIncome(  # secondary income: small e-Transfers
            merchant="Interac e-Transfer",
            category="Income",
            flow_type="income",
            txn_type="deposit",
            per_month=(1, 3),
            amount_range_cents=(4_000, 25_000),
            round_to_cents=500,
        ),
        _monthly("C-rent", "Interac e-Transfer", "Rent & housing", "withdrawal", 3,
                 Amount(-82_000), "card"),  # continues in the quiet month
        _monthly("C-fizz", "Fizz", "Phone, internet & utilities", "pos purchase", 8,
                 Amount(-4_200), "card"),
        _monthly("C-netflix", "Netflix", "Subscriptions", "pos purchase", 15,
                 Amount(-1_649), "card"),
        OneOff(  # an e-Transfer that comes back a few days later
            due=date(2026, 7, 9),
            amount_cents=-8_500,
            merchant="Interac e-Transfer",
            category="Transfers",
            flow_type="transfer",
            txn_type="withdrawal",
            shift="card",
            link_id="etransfer-return-2026-07",
        ),
        OneOff(
            due=date(2026, 7, 14),
            amount_cents=8_500,
            merchant="Interac e-Transfer",
            category="Transfers",
            flow_type="refund",
            txn_type="deposit",
            shift="card",
            link_id="etransfer-return-2026-07",
        ),
    ),
)  # fmt: skip

PERSONAS: dict[str, Persona] = {p.persona_id: p for p in (PERSONA_A, PERSONA_B, PERSONA_C)}
