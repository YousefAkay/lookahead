"""Recurring payments (week 3, step 3a): calendar-aware detection by rules.

A series is a merchant (or a set of same-amount e-Transfers) paid on a regular calendar
pattern. For each group of rows the detector tries every cadence and keeps the best fit:
- weekly, biweekly:  a fixed number of days from an anchor date;
- semimonthly:       the 15th and the last day of the month, or the 1st and the 15th;
- monthly:           the same day each month, clamped to the month's length (31 -> 30, 28);
- yearly:            the same month and day.
The due date is then shifted like the bank does: income to the business day before a weekend
or holiday, payments to the one after. A row matches a due date within TOLERANCE business
days. A series needs MIN_PAYMENTS matches (2 for yearly), most due dates in its span paid,
and most of the group's rows explained. If a whole merchant does not fit, rows of one amount
are tried on their own (a subscription among other purchases, rent among e-Transfers).

Habits are not schedules. A weekly grocery trip, or three coffees of the same price that
happen to line up, would pass the rules above, so two stricter rules apply:
- card purchases only form a series if the amounts stay within 10% of the median and most
  payments post within a day of the expected date (a subscription is billed by a machine);
- a series found among other rows of the same group needs MIN_PAYMENTS_PART payments.

Left out: fully reversed pairs, refunds and bank fees (fees follow events, not dates).
e-Transfers have no merchant: they are grouped by recipient (when the bank gives one; as
the pseudonymizer's keyed PERSON token, never the name or a plain hash of it) and amount,
and stay candidates until you confirm them; they are never called rent here. One price
change (stable before, stable after) keeps a fixed series in one piece.
Transfers between your own accounts (a monthly move to savings) are found the same way, by
amount and date.
Rule zero: `detect(..., as_of=t)` uses only rows posted on or before t.

Counts-only check on a database (no names, no amounts):
    python -m lookahead.recurring report --db data/private/db/lookahead.db
"""

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Literal

import numpy as np

from lookahead.calendar_rules import (
    is_business_day,
    monthly_date,
    next_card_posting_day,
    shift_to_business_day,
)
from lookahead.flows import Txn, flow_types, merchant_groups
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key, token
from lookahead.resolve import UserDecisions, load_user_decisions

Cadence = Literal["weekly", "biweekly", "semimonthly", "monthly", "yearly"]
CADENCES: tuple[Cadence, ...] = ("weekly", "biweekly", "semimonthly", "monthly", "yearly")
TOLERANCE = 3  # business days between the expected and the actual posting date
MIN_PAYMENTS = 3  # payments before a series counts as detected ...
MIN_PAYMENTS_YEARLY = 2  # ... except yearly: two years of data hold 2 or 3 at most
MIN_PAYMENTS_PART = 4  # ... and a series found among other rows of its group (yearly: 3)
LOOSE_TOLERANCE = 7  # business days: only for same-amount, about-monthly e-Transfers
CARD_AMOUNT_SHARE = 0.10  # card purchases: amounts within 10% of the median
EXACT_DAYS, MIN_EXACT_SHARE = 1, 0.75  # card purchases: 75% within a day of the due date
MIN_HIT_RATIO = 0.75  # share of the due dates in the series' span that were paid
MIN_COVERAGE = 0.8  # share of the group's rows the series explains
ENDED_AFTER = 2  # due dates in a row without a payment
FIXED_SHARE, FIXED_CENTS = 0.03, 100  # "same amount": within 3% and within $1 of the median
MIN_RANGE_SHARE = 0.10  # a variable range is at least +/- 10% ...
FEW_PAYMENTS = 6  # ... while there are fewer payments than this
_NOT_SPENDING = {"bank fee", "fee reversal"}
_ETRANSFER = {"outgoing e-Transfer", "incoming e-Transfer"}


@dataclass(frozen=True)
class Series:
    key: str  # stable id of the group (and the amount, when found among other rows)
    merchant: str | None  # None for e-Transfers
    kind: Literal["merchant", "e-transfer", "own transfer"]
    direction: Literal["in", "out"]
    cadence: Cadence
    anchor: date  # a due date of the pattern, before any shift
    anchor_day: int | None  # day of the month (monthly, semimonthly, yearly)
    amount_type: Literal["fixed", "variable"]
    expected_cents: int  # the median amount, signed
    range_cents: tuple[int, int]  # normal range, signed (low, high); fixed: both the median
    rows: tuple[int, ...]  # the payments found, oldest first (the evidence)
    next_expected: date  # first expected date after the last payment found
    active: bool  # False: ended (ENDED_AFTER due dates passed without a payment)
    # A fixed amount that changed once (a price change): the amount before, and the first
    # payment at the new amount. `expected_cents` is then the new amount.
    previous_cents: int | None = None
    changed_row: int | None = None
    # Days between each payment and its expected date (negative = early), oldest first.
    # The forecast uses this spread to move the date around in its simulated paths.
    offsets: tuple[int, ...] = ()
    # True: e-Transfers of one amount, about monthly, sent by hand on varying days. Found
    # with LOOSE_TOLERANCE on the date; a candidate only, never used until you confirm it.
    loose: bool = False


@dataclass(frozen=True)
class Override:
    flow_type: str
    one_off: bool = False
    series_key: str | None = None


def business_days_apart(a: date, b: date) -> int:
    """Business days after the earlier date, up to and including the later one."""
    first, last = min(a, b), max(a, b)
    return sum(
        is_business_day(first + timedelta(days=i)) for i in range(1, (last - first).days + 1)
    )


def _due_dates(cadence: str, anchor: date, start: date, end: date) -> list[date]:
    """Due dates of the pattern in [start, end], before any weekend or holiday shift."""
    if cadence in ("weekly", "biweekly"):
        step = 7 if cadence == "weekly" else 14
        first = anchor + timedelta(days=-(-(start - anchor).days // step) * step)
        return [first + timedelta(days=step * k) for k in range((end - first).days // step + 1)]
    if cadence == "yearly":
        dues = [monthly_date(y, anchor.month, anchor.day) for y in range(start.year, end.year + 1)]
        return [d for d in dues if start <= d <= end]
    if cadence == "monthly":
        days: tuple[int, ...] = (anchor.day,)
    elif cadence == "semimonthly":
        days = (1, 15) if anchor.day == 1 else (15, 31)
    else:
        raise ValueError(f"unknown cadence {cadence!r}")
    out = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        out += [d for d in (monthly_date(year, month, day) for day in days) if start <= d <= end]
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def expected_dates(
    cadence: str, anchor: date, start: date, end: date, direction: str, card: bool = False
) -> list[date]:
    """Expected posting dates for due dates in [start, end]: income moves to the business
    day before a weekend or holiday, payments to the one after. `card`: card purchases and
    e-Transfers post Monday to Saturday, so only Sundays and holidays move them (later)."""
    dues = _due_dates(cadence, anchor, start, end)
    if card and direction == "out":
        return [next_card_posting_day(d) for d in dues]
    shift = "earlier" if direction == "in" else "later"
    return [shift_to_business_day(d, shift) for d in dues]  # type: ignore[arg-type]


def _anchors(cadence: str, days: list[date]) -> list[date]:
    """Every pattern worth trying for these posting dates."""
    first = days[0]
    if cadence in ("weekly", "biweekly"):
        return [first + timedelta(days=r) for r in range(7 if cadence == "weekly" else 14)]
    if cadence == "monthly":
        return [date(2024, 1, d) for d in range(1, 32)]  # only the day is used
    if cadence == "semimonthly":
        return [date(2024, 1, 1), date(2024, 1, 15)]
    return sorted({d + timedelta(days=k) for d in days for k in range(-4, 5)})  # yearly


@dataclass(frozen=True)
class _Fit:
    cadence: Cadence
    anchor: date
    matched: tuple[tuple[date, int], ...]  # (expected date, index of the row), oldest first
    hit_ratio: float
    coverage: float
    deviation: int  # total calendar days between expected and actual dates
    exact_share: float  # share of the payments within EXACT_DAYS of the expected date
    expected: tuple[date, ...]  # every expected date tried, up to well after as_of


def _needed(cadence: str, part: bool) -> int:
    if part:
        return MIN_PAYMENTS_PART - 1 if cadence == "yearly" else MIN_PAYMENTS_PART
    return MIN_PAYMENTS_YEARLY if cadence == "yearly" else MIN_PAYMENTS


def _best_fit(
    days: list[date], direction: str, as_of: date, part: bool = False, exact: bool = False,
    card_days: bool = False, tolerance: int = TOLERANCE, min_hit: float = MIN_HIT_RATIO,
    min_payments: int | None = None, cadences: Sequence[Cadence] = CADENCES,
) -> _Fit | None:  # fmt: skip
    """The best pattern for these posting dates (sorted), or None if none is good enough.
    `part`: the rows are only some of their group's. `exact`: timing must be machine-like.
    `tolerance`, `min_hit` and `min_payments` are only loosened by the "why" diagnostic."""
    best: _Fit | None = None
    start, end = days[0] - timedelta(days=10), as_of + timedelta(days=400)
    for cadence in cadences:
        needed = min_payments or _needed(cadence, part)
        if len(days) < needed:
            continue
        for anchor in _anchors(cadence, days):
            expected = expected_dates(cadence, anchor, start, end, direction, card_days)
            matched: list[tuple[date, int]] = []
            used: set[int] = set()
            gaps: list[int] = []
            for due in expected:
                if due > days[-1] + timedelta(days=7):
                    break
                near = [
                    (abs((d - due).days), i)
                    for i, d in enumerate(days)
                    if i not in used
                    and abs((d - due).days) <= 2 * tolerance + 1
                    and business_days_apart(d, due) <= tolerance
                ]
                if near:
                    gap, i = min(near)
                    used.add(i)
                    matched.append((due, i))
                    gaps.append(gap)
            if len(matched) < needed:
                continue
            span = [e for e in expected if matched[0][0] <= e <= matched[-1][0]]
            fit = _Fit(cadence, anchor, tuple(matched), len(matched) / len(span),
                       len(matched) / len(days), sum(gaps),
                       sum(g <= EXACT_DAYS for g in gaps) / len(gaps), tuple(expected))  # fmt: skip
            if fit.hit_ratio < min_hit or fit.coverage < MIN_COVERAGE:
                continue
            if exact and fit.exact_share < MIN_EXACT_SHARE:
                continue
            if best is None or _rank(fit) > _rank(best):
                best = fit
    return best


def _rank(fit: _Fit) -> tuple[float, int, int]:
    return (round(fit.hit_ratio * fit.coverage, 6), len(fit.matched), -fit.deviation)


def _is_fixed(sizes: list[int]) -> bool:
    middle = median(sizes)
    return all(abs(s - middle) <= max(FIXED_SHARE * middle, FIXED_CENTS) for s in sizes)


def _step(sizes: list[int]) -> int | None:
    """Index where a fixed amount changes ONCE to another fixed amount (a price change):
    stable before, stable after. None if there is no such single step."""
    for k in range(1, len(sizes)):
        if _is_fixed(sizes[:k]) and _is_fixed(sizes[k:]) and not _is_fixed(sizes):
            return k
    return None


def _amounts(cents: list[int]) -> tuple[str, int, tuple[int, int], int | None, int | None]:
    """(fixed or variable, expected amount, normal range, amount before a price change,
    index of the first payment at the new price) for a series' amounts, oldest first.
    After one price change the series is still fixed, at the new amount."""
    sign = -1 if cents[0] < 0 else 1
    sizes = [abs(c) for c in cents]
    middle = median(sizes)
    if _is_fixed(sizes):
        value = sign * round(middle)
        return "fixed", value, (value, value), None, None
    step = _step(sizes)
    if step is not None:
        value = sign * round(median(sizes[step:]))
        return "fixed", value, (value, value), sign * round(median(sizes[:step])), step
    low, high = (float(v) for v in np.percentile(sizes, [10, 90]))
    if len(sizes) < FEW_PAYMENTS:
        low = min(low, middle * (1 - MIN_RANGE_SHARE))
        high = max(high, middle * (1 + MIN_RANGE_SHARE))
    bounds = sorted((sign * round(low), sign * round(high)))
    return "variable", sign * round(middle), (bounds[0], bounds[1]), None, None


def _clusters(rows: list[Txn]) -> list[list[Txn]]:
    """Rows of (nearly) one amount, for groups that do not fit as a whole."""
    out: list[list[Txn]] = []
    for t in sorted(rows, key=lambda t: abs(t.amount_cents)):
        first = abs(out[-1][0].amount_cents) if out else None
        if first is not None and abs(t.amount_cents) - first <= max(FIXED_SHARE * first, 100):
            out[-1].append(t)
        else:
            out.append([t])
    return [sorted(c, key=lambda t: (t.posted_date, t.row_id)) for c in out]


def _series(
    key: str, merchant: str | None, kind: str, direction: str, rows: list[Txn], as_of: date,
    part: bool = False, card: bool = False, loose: bool = False,
) -> Series | None:  # fmt: skip
    days = [t.posted_date for t in rows]
    tolerance = LOOSE_TOLERANCE if loose else TOLERANCE
    if loose and (len(days) < MIN_PAYMENTS_PART or _gap(days) != GAPS[2]):
        return None  # loose timing is only for 4+ payments about a month apart
    fit = _best_fit(days, direction, as_of, part=part, exact=card,
                    card_days=card or kind == "e-transfer", tolerance=tolerance,
                    cadences=("monthly",) if loose else CADENCES)  # fmt: skip
    if fit is None:
        return None
    paid = [rows[i] for _, i in fit.matched]
    amount_type, expected_cents, amount_range, previous, step = _amounts(
        [t.amount_cents for t in paid]
    )
    if (
        card
        and step is None
        and any(
            abs(abs(t.amount_cents) - abs(expected_cents)) > CARD_AMOUNT_SHARE * abs(expected_cents)
            for t in paid
        )
    ):
        return None  # a shopping habit, not a bill: the amounts move too much
    last_due = fit.matched[-1][0]
    later = [e for e in fit.expected if e > last_due]
    # A due date counts as missed once its tolerance window is over.
    missed = sum(e < as_of and business_days_apart(e, as_of) > tolerance for e in later)
    return Series(
        key=key, merchant=merchant, kind=kind, direction=direction,  # type: ignore[arg-type]
        cadence=fit.cadence, anchor=fit.anchor,
        anchor_day=None if fit.cadence in ("weekly", "biweekly") else fit.anchor.day,
        amount_type=amount_type, expected_cents=expected_cents,  # type: ignore[arg-type]
        range_cents=amount_range, rows=tuple(t.row_id for t in paid),
        next_expected=later[0], active=missed < ENDED_AFTER,
        previous_cents=previous, changed_row=None if step is None else paid[step].row_id,
        offsets=tuple((days[i] - due).days for due, i in fit.matched), loose=loose,
    )  # fmt: skip


def _recipient(text: str) -> str:
    """What the bank prints after the fixed e-Transfer words (usually nothing)."""
    words = [w for w in text.casefold().split() if w not in ("free", "interac", "e-transfer")]
    return " ".join(words)


def _groups(
    txns: Sequence[Txn], user: UserDecisions | None, key: bytes | None = None
) -> tuple[dict[tuple[str, str | None, str, str], list[Txn]], set[int]]:
    """(key, merchant, kind, direction) -> rows that may form a series; and the row ids
    that are card purchases."""
    flows = flow_types(txns, user)
    merchants = merchant_groups(txns, user)
    out: dict[tuple[str, str | None, str, str], list[Txn]] = {}
    card = {t.row_id for t in txns if flows[t.row_id].reason == "card purchase"}
    for t in txns:
        flow = flows[t.row_id]
        if flow.netted or flow.flow_type == "refund" or flow.reason in _NOT_SPENDING:
            continue
        direction = "in" if t.amount_cents > 0 else "out"
        if flow.reason in _ETRANSFER:
            who = _recipient(t.text)
            if who and key is None:
                continue  # a named recipient is only ever grouped under its keyed token
            # The pseudonymizer's keyed token: no name, and no guessable hash, in a series key.
            tag = token("PERSON", who, key) if who and key is not None else "unnamed"
            group = (f"e-transfer|{direction}|{tag}", None, "e-transfer", direction)
        elif flow.reason == "own-account transfer":
            group = (f"own transfer|{direction}", None, "own transfer", direction)
        elif merchants[t.row_id] is not None and t.amount_cents != 0:
            name = merchants[t.row_id]
            group = (f"merchant|{direction}|{name}", name, "merchant", direction)
        else:
            continue
        out.setdefault(group, []).append(t)
    return out, card


def detect(
    txns: Sequence[Txn],
    user: UserDecisions | None = None,
    as_of: date | None = None,
    key: bytes | None = None,
) -> list[Series]:
    """Every recurring series visible on `as_of` (default: the last posting date), using
    only rows posted on or before it. Sorted by key. `key` is the pseudonym key: e-Transfers
    that name a recipient are grouped under its keyed token, and are skipped without it."""
    if as_of is None:
        as_of = max((t.posted_date for t in txns), default=date.min)
    past = [t for t in txns if t.posted_date <= as_of]
    found: list[Series] = []
    groups, card_rows = _groups(past, user, key)
    for (key, merchant, kind, direction), rows in groups.items():
        rows = sorted(rows, key=lambda t: (t.posted_date, t.row_id))
        card = all(t.row_id in card_rows for t in rows)
        whole = (
            None
            if kind != "merchant"  # no merchant: only rows of one amount can be a series
            else _series(key, merchant, kind, direction, rows, as_of, card=card)
        )
        if whole is not None:
            found.append(whole)
            continue
        for cluster in _clusters(rows):  # rows of one amount, tried on their own
            centre = round(median(abs(t.amount_cents) for t in cluster))
            part = _series(f"{key}|{centre}", merchant, kind, direction, cluster, as_of,
                           part=True, card=card)  # fmt: skip
            if part is None and kind == "e-transfer" and direction == "out":
                # Sent by hand, so the day moves: same amount, about monthly, failing only
                # on timing. A candidate for the Recurring page; nothing uses it unconfirmed.
                part = _series(f"{key}|{centre}", merchant, kind, direction, cluster, as_of,
                               part=True, loose=True)  # fmt: skip
            if part is not None:
                found.append(part)
    return sorted(found, key=lambda s: s.key)


def flow_overrides(
    txns: Sequence[Txn],
    series: Sequence[Series],
    confirmed: frozenset[str] | set[str] = frozenset(),
    user: UserDecisions | None = None,
) -> dict[int, Override]:
    """Where recurring detection changes a row's flow type (the per-row rules stay as they
    are; the forecaster and the pages read these on top):
    - payments of a detected merchant series -> recurring_outflow (a card subscription);
    - payments of an e-Transfer series -> recurring_outflow only once you confirmed it;
    - a bill payment to a payee paid exactly once -> a one-off, out of the recurring list.
    A regular transfer to your own account stays a transfer: it is scheduled, not spending.
    """
    out: dict[int, Override] = {}
    flows = flow_types(txns, user)
    for s in series:
        if s.direction != "out" or s.kind == "own transfer":
            continue
        if s.kind == "e-transfer" and s.key not in confirmed:
            continue
        for row_id in s.rows:
            # Only rows whose type changes: an everyday card purchase, or a confirmed
            # e-Transfer. A bill payment already is a recurring outflow, and a payment to
            # my own account stays a transfer.
            base = flows[row_id].flow_type if row_id in flows else None
            if base == "everyday" or (s.kind == "e-transfer" and base == "transfer"):
                out[row_id] = Override("recurring_outflow", series_key=s.key)
    merchants = merchant_groups(txns, user)
    bills: dict[str | None, list[int]] = {}
    for t in txns:
        if flows[t.row_id].reason == "bill payment":
            bills.setdefault(merchants[t.row_id], []).append(t.row_id)
    for payee, row_ids in bills.items():
        if payee is not None and len(row_ids) == 1 and row_ids[0] not in out:
            out[row_ids[0]] = Override("everyday", one_off=True)
    return out


# --- Counts-only report on a database ---


@dataclass
class RecurringCounts:
    rows: int
    series: int
    by_kind: Counter[str]
    by_payment: Counter[str]  # what a series is paid by: card, bill payment, ...
    own_account_rows: int  # bill payments to my own accounts (transfers)
    by_cadence: Counter[str]
    by_direction: Counter[str]
    by_amount: Counter[str]
    active: int
    price_changed: int
    rows_in_series: int
    upgraded_rows: int  # card purchases that become recurring outflows
    one_off_bills: int  # bill payments to a payee paid once
    candidates: int  # e-Transfer series waiting for confirmation
    # for each loose-timing candidate: (payments, median days off, most days off)
    loose: tuple[tuple[int, int, int], ...]
    # same-amount payments left out of a loose candidate: (days from the nearest expected
    # date, True if a payment of the set posted in the same calendar month)
    left_out: tuple[tuple[int, bool], ...]
    named_recipients_skipped: bool  # e-Transfers name a recipient but no key was given


PAID_BY = ("card", "bill payment", "e-Transfer", "transfer to my own account", "deposit",
           "other")  # fmt: skip


def _paid_by(reason: str) -> str:
    if reason == "card purchase":
        return "card"
    if reason == "bill payment":
        return "bill payment"
    if reason in _ETRANSFER:
        return "e-Transfer"
    if reason in ("own-account transfer", "payment to my own account"):
        return "transfer to my own account"
    return "deposit" if reason in ("deposit", "credit memo") else "other"


def count_recurring(
    txns: Sequence[Txn], user: UserDecisions | None = None, key: bytes | None = None
) -> RecurringCounts:
    series = detect(txns, user, key=key)
    overrides = flow_overrides(txns, series, user=user)
    flows = flow_types(txns, user)
    named = any(flows[t.row_id].reason in _ETRANSFER and _recipient(t.text) for t in txns)
    return RecurringCounts(
        rows=len(txns),
        series=len(series),
        by_kind=Counter(s.kind for s in series),
        by_payment=Counter(_paid_by(flows[s.rows[0]].reason) for s in series),
        own_account_rows=sum(f.reason == "payment to my own account" for f in flows.values()),
        by_cadence=Counter(s.cadence for s in series),
        by_direction=Counter("money in" if s.direction == "in" else "money out" for s in series),
        by_amount=Counter(s.amount_type for s in series),
        active=sum(s.active for s in series),
        price_changed=sum(s.previous_cents is not None for s in series),
        rows_in_series=sum(len(s.rows) for s in series),
        upgraded_rows=sum(o.flow_type == "recurring_outflow" for o in overrides.values()),
        one_off_bills=sum(o.one_off for o in overrides.values()),
        candidates=sum(s.kind == "e-transfer" for s in series),
        loose=tuple(
            (len(s.rows), round(median(abs(d) for d in s.offsets)), max(abs(d) for d in s.offsets))
            for s in series
            if s.loose
        ),
        named_recipients_skipped=named and key is None,
        left_out=tuple(pair for s in series if s.loose for pair in _left_out(txns, s, flows)),
    )


def _left_out(txns: Sequence[Txn], s: Series, flows: dict[int, object]) -> list[tuple[int, bool]]:
    """Outgoing e-Transfers of the series' amount that are not among its payments: how far
    each is from the nearest expected date, and whether a payment of the series posted in
    the same calendar month (a second payment that month) or not (alone in its month)."""
    by_id = {t.row_id: t for t in txns}
    paid = [by_id[r] for r in s.rows]
    centre = abs(s.expected_cents)
    start, end = paid[0].posted_date - timedelta(days=45), paid[-1].posted_date + timedelta(days=45)
    expected = expected_dates(s.cadence, s.anchor, start, end, s.direction, card=True)
    out = []
    for t in txns:
        reason = getattr(flows[t.row_id], "reason", "")
        same = abs(abs(t.amount_cents) - centre) <= max(FIXED_SHARE * centre, FIXED_CENTS)
        if t.row_id in s.rows or reason != "outgoing e-Transfer" or not same or not expected:
            continue
        nearest = min(expected, key=lambda e: abs((t.posted_date - e).days))
        month = (t.posted_date.year, t.posted_date.month)
        near = any((p.posted_date.year, p.posted_date.month) == month for p in paid)
        out.append(((t.posted_date - nearest).days, near))
    return out


# --- Why a regular-looking group is not a series (counts only) ---

WHY_NO_SERIES = (
    "is a series",
    "named recipient, no pseudonym key",
    "fewer than 4 payments of this amount (one amount among others needs 4; a payee "
    "always paid about the same amount needs 3)",
    "timing: payments more than 3 business days from any pattern (a loose-timing "
    "candidate if these are e-Transfers about a month apart)",
    "gaps: too many expected dates without a payment",
    "amount changed: fits once amounts within 10% are taken together",
    "no calendar pattern even with looser rules",
)
_FLOWS_TO_EXPLAIN = {
    "outgoing e-Transfer": "outgoing e-Transfers",
    "bill payment": "bill payments",
    "withdrawal": "other withdrawals",
    "no rule (fallback)": "rows with no flow rule",
    "own-account transfer": "transfers to my own accounts",
}


@dataclass(frozen=True)
class AmountGroup:
    """Rows of one flow type and (nearly) one amount, described without names or amounts."""

    flow: str  # one of the _FLOWS_TO_EXPLAIN labels
    payments: int
    gap: str  # one of GAPS
    why: str  # one of WHY_NO_SERIES


def _loose_clusters(rows: list[Txn], share: float) -> list[list[Txn]]:
    out: list[list[Txn]] = []
    for t in sorted(rows, key=lambda t: abs(t.amount_cents)):
        first = abs(out[-1][0].amount_cents) if out else None
        if first is not None and abs(t.amount_cents) - first <= share * first:
            out[-1].append(t)
        else:
            out.append([t])
    return [sorted(c, key=lambda t: (t.posted_date, t.row_id)) for c in out]


def explain_amount_groups(
    txns: Sequence[Txn], user: UserDecisions | None = None, key: bytes | None = None,
    as_of: date | None = None, min_rows: int = 3,
) -> list[AmountGroup]:  # fmt: skip
    """For money going out without a merchant-level series: every set of `min_rows`+ rows of
    one flow type and one amount, and the first reason it is not a series. This is how a
    regular payment the detector missed (rent by e-Transfer, say) can be explained without
    showing it. The detector itself is not changed by anything here."""
    if as_of is None:
        as_of = max((t.posted_date for t in txns), default=date.min)
    past = [t for t in txns if t.posted_date <= as_of]
    flows = flow_types(past, user)
    payees = merchant_groups(past, user)
    by_flow: dict[tuple[str, str | None], list[Txn]] = {}
    for t in past:
        flow = flows[t.row_id]
        if t.amount_cents < 0 and not flow.netted and flow.reason in _FLOWS_TO_EXPLAIN:
            # one payee at a time: amounts of different payees are never put together
            by_flow.setdefault((flow.reason, payees[t.row_id]), []).append(t)
    out: list[AmountGroup] = []
    for (reason, payee), rows in by_flow.items():
        rows = sorted(rows, key=lambda t: (t.posted_date, t.row_id))
        if payee is not None and _series("", payee, "merchant", "out", rows, as_of) is not None:
            if len(rows) >= min_rows:  # the whole payee is one series (needs 3 payments)
                gap = _gap([t.posted_date for t in rows])
                out.append(AmountGroup(_FLOWS_TO_EXPLAIN[reason], len(rows), gap, WHY_NO_SERIES[0]))
            continue
        card_days = reason == "outgoing e-Transfer"
        named = card_days and key is None

        def fits(group: list[Txn], card_days: bool = card_days, **loosen: object) -> bool:
            days = [t.posted_date for t in group]
            return _best_fit(days, "out", as_of, part=True, card_days=card_days,
                             **loosen) is not None  # type: ignore[arg-type]  # fmt: skip

        for cluster in _clusters(rows):
            if len(cluster) < min_rows:
                continue
            wider = next(c for c in _loose_clusters(rows, CARD_AMOUNT_SHARE)
                         if cluster[0] in c)  # fmt: skip
            if named and any(_recipient(t.text) for t in cluster):
                why = WHY_NO_SERIES[1]
            elif fits(cluster):
                why = WHY_NO_SERIES[0]
            elif len(cluster) < MIN_PAYMENTS_PART:
                why = WHY_NO_SERIES[2]
            elif fits(cluster, tolerance=7):
                why = WHY_NO_SERIES[3]
            elif fits(cluster, min_hit=0.5) or fits(cluster, tolerance=7, min_hit=0.5):
                why = WHY_NO_SERIES[4]
            elif len(wider) > len(cluster) and fits(wider, tolerance=7, min_hit=0.5):
                why = WHY_NO_SERIES[5]
            else:
                why = WHY_NO_SERIES[6]
            out.append(AmountGroup(_FLOWS_TO_EXPLAIN[reason], len(cluster),
                                   _gap([t.posted_date for t in cluster]), why))  # fmt: skip
    return sorted(out, key=lambda g: (g.flow, -g.payments, g.why))


WHY_NOT = (
    "is a series",
    "some of its payments form a series",
    "too few payments",
    "irregular timing",
    "no payee name",
)
GAPS = ("one payment", "under 20 days", "20-45 days", "46-120 days", "over 120 days")


@dataclass(frozen=True)
class BillGroup:
    """One bill-payment payee, described without its name or amounts."""

    payments: int
    why: str  # one of WHY_NOT
    gap: str  # one of GAPS: the median number of days between payments


def _gap(days: list[date]) -> str:
    if len(days) < 2:
        return GAPS[0]
    middle = median((b - a).days for a, b in zip(days, days[1:], strict=False))
    return (
        GAPS[1]
        if middle < 20
        else GAPS[2]
        if middle <= 45
        else GAPS[3]
        if middle <= 120
        else GAPS[4]
    )


def bill_payment_groups(
    txns: Sequence[Txn], user: UserDecisions | None = None, as_of: date | None = None
) -> list[BillGroup]:
    """Every bill-payment payee (money out), and why it is or is not a recurring series.
    Bill payments have no amount rule: only the number of payments and the timing count."""
    if as_of is None:
        as_of = max((t.posted_date for t in txns), default=date.min)
    past = [t for t in txns if t.posted_date <= as_of]
    flows = flow_types(past, user)
    merchants = merchant_groups(past, user)
    by_payee: dict[str | None, list[Txn]] = {}
    for t in past:
        if flows[t.row_id].reason == "bill payment" and not flows[t.row_id].netted:
            by_payee.setdefault(merchants[t.row_id], []).append(t)
    out = []
    for payee, rows in by_payee.items():
        rows = sorted(rows, key=lambda t: (t.posted_date, t.row_id))
        days = [t.posted_date for t in rows]
        if payee is None:
            why = "no payee name"
        elif _series("", payee, "merchant", "out", rows, as_of) is not None:
            why = "is a series"
        elif any(_series("", payee, "merchant", "out", c, as_of, part=True) is not None
                 for c in _clusters(rows)):  # fmt: skip
            why = "some of its payments form a series"
        elif len(rows) < MIN_PAYMENTS:
            why = "too few payments"
        else:
            why = "irregular timing"
        out.append(BillGroup(len(rows), why, _gap(days)))
    return sorted(out, key=lambda g: (-g.payments, g.why, g.gap))


def _line(counts: Counter[str], order: Sequence[str]) -> str:
    return ", ".join(f"{name} {counts.get(name, 0)}" for name in order)


def _report(db_path: Path, account: str, env_path: Path) -> int:
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    txns = [
        Txn(row_id, date.fromisoformat(day), cents, kind, text)
        for row_id, day, cents, kind, text in conn.execute(
            "SELECT id, posted_date, amount_cents, description, sub_description"
            " FROM transactions WHERE account = ?",
            (account,),
        )
    ]
    user = load_user_decisions(conn)
    conn.close()
    try:
        key: bytes | None = pseudonym_key(env_path)
    except PseudonymKeyError:
        key = None
    c = count_recurring(txns, user, key)
    print(f"Recurring payments: {c.rows} rows, account {account}")
    print(f"series detected: {c.series} (still active {c.active}, ended {c.series - c.active})")
    print(f"  by kind: {_line(c.by_kind, ('merchant', 'e-transfer', 'own transfer'))}")
    print(f"  paid by: {_line(c.by_payment, PAID_BY)}")
    print(f"  by cadence: {_line(c.by_cadence, CADENCES)}")
    print(f"  by direction: {_line(c.by_direction, ('money in', 'money out'))}")
    print(f"  by amount: {_line(c.by_amount, ('fixed', 'variable'))}; with one price change "
          f"{c.price_changed}")  # fmt: skip
    print(f"rows inside a series: {c.rows_in_series}")
    print(f"card purchases that become recurring outflows: {c.upgraded_rows} rows "
          "(bill payments already are)")  # fmt: skip
    print(f"bill payments to my own accounts (transfers, not spending): {c.own_account_rows}")
    print(f"bill payments to a payee paid once (one-offs): {c.one_off_bills}")
    print(f"e-Transfer series waiting for your confirmation: {c.candidates} "
          f"({len(c.loose)} with loose timing)")  # fmt: skip
    for payments, typical, most in c.loose:
        print(f"  loose-timing candidate: {payments} payments; days off its typical day of "
              f"the month: median {typical}, maximum {most}")  # fmt: skip
    for days_off, shared in c.left_out:
        where = (
            "another payment of the set posted in the same month (a second one)"
            if shared
            else "no other payment of the set that month (alone in its month, too far off)"
        )
        side = "before" if days_off < 0 else "after"
        print(f"  same amount, left out: {abs(days_off)} days {side} the nearest expected "
              f"date; {where}")  # fmt: skip
    bills = bill_payment_groups(txns, user)
    sizes = Counter(
        "1 payment" if g.payments == 1 else "2 payments" if g.payments == 2
        else "3-5 payments" if g.payments <= 5 else "6+ payments"
        for g in bills
    )  # fmt: skip
    print(f"bill payments: {sum(g.payments for g in bills)} rows to {len(bills)} payees")
    order = ("1 payment", "2 payments", "3-5 payments", "6+ payments")
    print(f"  payees by number of payments: {_line(sizes, order)}")
    for why in WHY_NOT:
        part = [g for g in bills if g.why == why]
        if part:
            gaps = Counter(g.gap for g in part)
            print(f"  {why}: {len(part)} payees, {sum(g.payments for g in part)} rows; "
                  f"median gap between payments: "
                  f"{', '.join(f'{gap} {gaps[gap]}' for gap in GAPS if gaps[gap])}")  # fmt: skip
    if c.named_recipients_skipped:
        print("e-Transfers that name a recipient were skipped: PSEUDONYM_KEY is missing")
    groups = explain_amount_groups(txns, user, key)
    print("money out, by flow type: sets of 3+ payments of one amount, and why each is or is "
          "not a series")  # fmt: skip
    if not groups:
        print("  none")
    for g in groups:
        print(f"  {g.flow}: {g.payments} payments, median gap {g.gap}: {g.why}")
    print("Stored series and your decisions are on the Recurring page.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.recurring")
    commands = parser.add_subparsers(dest="command", required=True)
    rep = commands.add_parser("report", help="counts-only: recurring series found")
    rep.add_argument("--db", type=Path, required=True)
    rep.add_argument("--account", default="scotia-chequing")
    rep.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    return _report(args.db, args.account, args.env)


if __name__ == "__main__":
    sys.exit(main())
