"""Planted card quirks: pre-authorization holds, returns, duplicates.

Runs after the simulation and before description text. It only ADDS rows, and may
set `link_id` / `quirk` on the existing rows it pairs with; nothing else changes.
Shares and timings are set by hand (see DECISIONS.md).
"""

from dataclasses import replace
from datetime import date, timedelta

import numpy as np

from lookahead.calendar_rules import is_card_posting_day, next_card_posting_day
from lookahead.generator.schedules import GeneratedTxn

# Share of purchases that come with a hold + same-day correction.
HOLD_SHARE: dict[str, float] = {"Uber": 0.39, "Uber Eats": 0.39, "Lyft": 0.15}
# Final charge minus hold: usually a few cents, sometimes far off.
_CLOSE_SHARE = 0.85
_FAR_RANGE_CENTS = (-3_700, 1_500)

_RETURN_DELAY_DAYS = (3, 20)
_MAX_POSTING_SHIFT = 2  # Sunday + holiday Monday


def _eligible(t: GeneratedTxn) -> bool:
    """A plain everyday card purchase not already part of a planted pattern."""
    return (
        t.txn_type == "pos purchase"
        and t.flow_type == "everyday"
        and not t.one_off
        and t.quirk is None
        and t.link_id is None
    )


def _hold_rows(
    t: GeneratedTxn, link: str, rng: np.random.Generator
) -> tuple[GeneratedTxn, GeneratedTxn, GeneratedTxn]:
    """(hold, final charge, correction) for purchase t; all post the same day."""
    if rng.random() < _CLOSE_SHARE:
        diff = round(rng.normal(6, 20))  # final - hold, in cents
    else:
        diff = int(rng.integers(_FAR_RANGE_CENTS[0], _FAR_RANGE_CENTS[1] + 1))
    hold_cents = max(100, -t.amount_cents - diff)
    hold = replace(t, amount_cents=-hold_cents, link_id=link, quirk="preauth_hold")
    final = replace(t, link_id=link)
    correction = replace(
        t,
        amount_cents=hold_cents,
        txn_type="correction",
        flow_type="refund",
        link_id=link,
        quirk="preauth_correction",
    )
    return hold, final, correction


def _return_row(orig: GeneratedTxn, link: str, end: date, rng: np.random.Generator) -> GeneratedTxn:
    """A full return of `orig`, 3-20 days later, in the same card format and currency."""
    latest = (end - orig.posted_date).days - _MAX_POSTING_SHIFT
    delay = int(rng.integers(_RETURN_DELAY_DAYS[0], min(_RETURN_DELAY_DAYS[1], latest) + 1))
    original = orig.original_amount_cents
    return replace(
        orig,
        posted_date=next_card_posting_day(orig.posted_date + timedelta(days=delay)),
        amount_cents=-orig.amount_cents,
        txn_type="pos return",
        flow_type="refund",
        original_amount_cents=-original if original is not None else None,
        link_id=link,
    )


def plant_card_quirks(
    rows: list[GeneratedTxn],
    persona_id: str,
    start: date,
    end: date,
    rng: np.random.Generator,
) -> list[GeneratedTxn]:
    """`rows` plus planted holds, 1-2 returns, one double charge and one identical pair."""
    out: list[GeneratedTxn] = []
    n_holds = 0
    for t in rows:
        share = HOLD_SHARE.get(t.merchant, 0.0)
        if _eligible(t) and share and rng.random() < share:
            n_holds += 1
            out += _hold_rows(t, f"{persona_id}-preauth-{n_holds}", rng)
        else:
            out.append(t)

    def pick(candidates: list[int]) -> int:
        return candidates[int(rng.integers(len(candidates)))]

    # Returns: shopping purchases, or foreign-currency ones; one foreign first if any.
    min_gap = _RETURN_DELAY_DAYS[0] + _MAX_POSTING_SHIFT
    returnable = [
        i
        for i, t in enumerate(out)
        if _eligible(t)
        and (end - t.posted_date).days >= min_gap
        and (t.category == "Shopping" or t.currency != "CAD")
    ]
    foreign = [i for i in returnable if out[i].currency != "CAD"]
    domestic = [i for i in returnable if out[i].currency == "CAD" and out[i].category == "Shopping"]
    chosen = [pick(foreign)] if foreign else []
    for _ in range(int(rng.integers(1, 3)) - len(chosen)):
        options = [i for i in domestic if i not in chosen]
        if options:
            chosen.append(pick(options))
    for n, i in enumerate(chosen, start=1):
        link = f"{persona_id}-return-{n}"
        out.append(_return_row(out[i], link, end, rng))
        out[i] = replace(out[i], link_id=link)

    # One genuine double charge: same merchant and amount, same or next posting day.
    candidates = [i for i, t in enumerate(out) if _eligible(t) and t.posted_date < end]
    if candidates:
        i = pick(candidates)
        link = f"{persona_id}-duplicate-1"
        later = out[i].posted_date + timedelta(days=int(rng.integers(0, 2)))
        when = later if is_card_posting_day(later) and later <= end else out[i].posted_date
        out.append(replace(out[i], posted_date=when, link_id=link, quirk="duplicate_charge"))
        out[i] = replace(out[i], link_id=link)

    # One pair of identical legitimate purchases (say, two coffees), same day.
    cafes = [i for i, t in enumerate(out) if _eligible(t) and t.category == "Restaurants & cafés"]
    candidates = cafes or [i for i, t in enumerate(out) if _eligible(t)]
    if candidates:
        i = pick(candidates)
        twin = replace(out[i], link_id=f"{persona_id}-identical-1", quirk="identical_legit")
        out[i] = twin
        out.append(twin)

    return sorted(out, key=lambda t: t.posted_date)
