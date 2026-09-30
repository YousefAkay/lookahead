"""Event-driven bank fees: a stage after the quirks and before description text.

Fees follow events, not the calendar (DECISIONS.md):
- a service charge on the same day as every incoming credit memo;
- an ATM fee row on about 20% of ABM withdrawals ($0.00 or -$2.00);
- an overdrawn handling charge, once per day, when a scheduled payment posts and
  leaves the balance below zero ($5; $10 if two or more do that day);
- overdraft interest on each statement date (the 25th, or the next business day),
  from the negative evening balances in that cycle.

Fees are computed from the balance before fees (a fee that itself overdraws the
account causes no further fees; the effect is a few cents). Only adds rows.
"""

from datetime import date, timedelta

import numpy as np

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.generator.schedules import GeneratedTxn

TRANSFER_FEE_CENTS = -1_500
ATM_FEE_ROW_SHARE = 0.20  # share of withdrawals with a fee row
ATM_FEE_ZERO_SHARE = 0.70  # of those rows: the share at $0.00 (the rest at -$2.00)
ATM_FEE_CENTS = -200
HANDLING_FEE_CENTS = (-500, -1_000)  # one / two or more overdrawing payments that day
OVERDRAFT_RATE = 0.15  # per year (ASSUMPTION)
STATEMENT_DAY = 25

_SCHEDULED_FLOWS = {"recurring_outflow", "transfer"}


def _fee(d: date, cents: int, txn_type: str) -> GeneratedTxn:
    return GeneratedTxn(d, cents, txn_type, "Scotiabank", "Fees & interest", "everyday")


def _statement_date(year: int, month: int) -> date:
    return shift_to_business_day(monthly_date(year, month, STATEMENT_DAY), "later")


def add_bank_fees(
    rows: list[GeneratedTxn],
    opening_balance_cents: int,
    start: date,
    end: date,
    rng: np.random.Generator,
) -> list[GeneratedTxn]:
    """`rows` plus fee rows, sorted by date. `rows` must be sorted by date."""
    added: list[GeneratedTxn] = []

    # Event fees: one pass over the rows in date order.
    for t in rows:
        if t.txn_type == "credit memo":
            added.append(_fee(t.posted_date, TRANSFER_FEE_CENTS, "service charge"))
        elif t.txn_type == "abm withdrawal" and rng.random() < ATM_FEE_ROW_SHARE:
            cents = 0 if rng.random() < ATM_FEE_ZERO_SHARE else ATM_FEE_CENTS
            added.append(_fee(t.posted_date, cents, "service charge"))

    # Walk the balance day by day: handling charges and daily evening balances.
    by_day: dict[date, list[GeneratedTxn]] = {}
    for t in rows:
        by_day.setdefault(t.posted_date, []).append(t)
    balance = opening_balance_cents
    negative_by_day: dict[date, int] = {}  # cents below zero each evening
    day = start
    while day <= end:
        overdrawing = 0
        for t in by_day.get(day, []):
            balance += t.amount_cents
            if t.amount_cents < 0 and t.flow_type in _SCHEDULED_FLOWS and balance < 0:
                overdrawing += 1
        if overdrawing:
            cents = HANDLING_FEE_CENTS[0] if overdrawing == 1 else HANDLING_FEE_CENTS[1]
            added.append(_fee(day, cents, "overdrawn handling chg."))
        negative_by_day[day] = max(0, -balance)
        day += timedelta(days=1)

    # Overdraft interest per statement cycle: (previous 25th, this 25th].
    year, month = start.year, start.month
    cycle_start = start
    while True:
        cycle_end = monthly_date(year, month, STATEMENT_DAY)
        posted = _statement_date(year, month)
        if cycle_end >= start:
            days = [
                cycle_start + timedelta(days=i) for i in range((cycle_end - cycle_start).days + 1)
            ]
            owed = sum(negative_by_day.get(d, 0) for d in days) * OVERDRAFT_RATE / 365
            if round(owed) >= 1 and posted <= end:
                added.append(_fee(posted, -round(owed), "overdraft interest"))
            cycle_start = cycle_end + timedelta(days=1)
        if cycle_end >= end:
            break
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return sorted(rows + added, key=lambda t: t.posted_date)
