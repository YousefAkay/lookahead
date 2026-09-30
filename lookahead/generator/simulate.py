"""Day-by-day simulation: everyday spending that reacts to the balance, plus top-ups.

Replaces the separate "spending" and "top-up" stages, because both now need the
balance while rows are being generated. Each day:

1. morning: top-ups due today post;
2. scheduled rows due today post (pay, rent, bills: always, even past the limit);
3. card purchases authorized earlier that post today reach the posted balance;
4. everyday purchases are made: rate x monthly activity x cut-back factor (if low);
   a purchase that would take the AVAILABLE balance past the overdraft limit is
   declined (not generated);
5. evening: top-up rules check the POSTED balance.

Available balance = posted balance minus card purchases authorized but not yet
posted (a Sunday purchase uses up money on Sunday, posts Monday).
"""

from datetime import date, timedelta

import numpy as np

from lookahead.generator.personas import Persona
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.topups import TopUpRunner


def cutback_applies(available_cents: int, upcoming_outflows_cents: int, below_cents: int) -> bool:
    """True if the person cuts back: available money after known upcoming bills is low.

    `upcoming_outflows_cents` is the (negative) sum of scheduled outflows due soon, so
    someone with $300 and $750 rent due in 4 days cuts back now, before rent.
    """
    return available_cents + upcoming_outflows_cents < below_cents


def simulate(
    persona: Persona,
    scheduled: list[GeneratedTxn],
    start: date,
    end: date,
    spending_rng: np.random.Generator,
    topup_rng: np.random.Generator,
) -> list[GeneratedTxn]:
    """Everyday rows and top-ups posted in [start, end], sorted by date.

    `scheduled` are the persona's stage-1 rows; they are read, never changed.
    """
    scheduled_by_day: dict[date, int] = {}
    outflows_by_day: dict[date, int] = {}  # known outflows, for the cut-back look-ahead
    for t in scheduled:
        scheduled_by_day[t.posted_date] = scheduled_by_day.get(t.posted_date, 0) + t.amount_cents
        if t.amount_cents < 0:
            outflows_by_day[t.posted_date] = outflows_by_day.get(t.posted_date, 0) + t.amount_cents

    runner = TopUpRunner(persona.topups, scheduled, topup_rng) if persona.topups else None
    posts_later: dict[date, int] = {}  # authorized card purchases, by future posting date
    posted = available = persona.opening_balance_cents
    out: list[GeneratedTxn] = []

    day = start
    while day <= end:
        if runner:
            for t in runner.morning(day):
                out.append(t)
                posted += t.amount_cents
                available += t.amount_cents
        today = scheduled_by_day.get(day, 0)
        posted += today + posts_later.pop(day, 0)  # later purchases were already in available
        available += today

        if persona.spending is not None:
            upcoming = sum(
                outflows_by_day.get(day + timedelta(days=k), 0)
                for k in range(1, persona.cutback_lookahead_days + 1)
            )
            scale = persona.activity(day)
            if cutback_applies(available, upcoming, persona.cutback_below_cents):
                scale *= persona.cutback_factor
            for t in persona.spending.day(day, spending_rng, scale):
                if available + t.amount_cents < persona.overdraft_limit_cents:
                    continue  # declined
                available += t.amount_cents
                if t.posted_date > end:
                    continue  # authorized now, posts after the range
                out.append(t)
                if t.posted_date == day:
                    posted += t.amount_cents
                else:
                    posts_later[t.posted_date] = posts_later.get(t.posted_date, 0) + t.amount_cents

        if runner:
            runner.evening(day, posted)
        day += timedelta(days=1)

    return sorted(out, key=lambda t: t.posted_date)
