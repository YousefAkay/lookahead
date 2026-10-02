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
e-Transfers have no merchant: they are grouped by recipient (when the bank gives one) and
amount, and stay candidates until you confirm them; they are never called rent here.
Transfers between your own accounts (a monthly move to savings) are found the same way, by
amount and date.
Rule zero: `detect(..., as_of=t)` uses only rows posted on or before t.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Literal

import numpy as np

from lookahead.calendar_rules import is_business_day, monthly_date, shift_to_business_day
from lookahead.flows import Txn, flow_types, merchant_groups
from lookahead.resolve import UserDecisions

Cadence = Literal["weekly", "biweekly", "semimonthly", "monthly", "yearly"]
CADENCES: tuple[Cadence, ...] = ("weekly", "biweekly", "semimonthly", "monthly", "yearly")
TOLERANCE = 3  # business days between the expected and the actual posting date
MIN_PAYMENTS = 3  # payments before a series counts as detected ...
MIN_PAYMENTS_YEARLY = 2  # ... except yearly: two years of data hold 2 or 3 at most
MIN_PAYMENTS_PART = 4  # ... and a series found among other rows of its group (yearly: 3)
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
    cadence: str, anchor: date, start: date, end: date, direction: str
) -> list[date]:
    """Expected posting dates for due dates in [start, end]: income moves to the business
    day before a weekend or holiday, payments to the one after."""
    shift = "earlier" if direction == "in" else "later"
    return [shift_to_business_day(d, shift) for d in _due_dates(cadence, anchor, start, end)]  # type: ignore[arg-type]


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
    days: list[date], direction: str, as_of: date, part: bool = False, exact: bool = False
) -> _Fit | None:
    """The best pattern for these posting dates (sorted), or None if none is good enough.
    `part`: the rows are only some of their group's. `exact`: timing must be machine-like."""
    best: _Fit | None = None
    start, end = days[0] - timedelta(days=10), as_of + timedelta(days=400)
    for cadence in CADENCES:
        needed = _needed(cadence, part)
        if len(days) < needed:
            continue
        for anchor in _anchors(cadence, days):
            expected = expected_dates(cadence, anchor, start, end, direction)
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
                    and abs((d - due).days) <= 7
                    and business_days_apart(d, due) <= TOLERANCE
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
            if fit.hit_ratio < MIN_HIT_RATIO or fit.coverage < MIN_COVERAGE:
                continue
            if exact and fit.exact_share < MIN_EXACT_SHARE:
                continue
            if best is None or _rank(fit) > _rank(best):
                best = fit
    return best


def _rank(fit: _Fit) -> tuple[float, int, int]:
    return (round(fit.hit_ratio * fit.coverage, 6), len(fit.matched), -fit.deviation)


def _amounts(cents: list[int]) -> tuple[str, int, tuple[int, int]]:
    """(fixed or variable, median, normal range) for a series' amounts (all one sign)."""
    sign = -1 if cents[0] < 0 else 1
    sizes = [abs(c) for c in cents]
    middle = median(sizes)
    if all(abs(s - middle) <= max(FIXED_SHARE * middle, FIXED_CENTS) for s in sizes):
        value = sign * round(middle)
        return "fixed", value, (value, value)
    low, high = (float(v) for v in np.percentile(sizes, [10, 90]))
    if len(sizes) < FEW_PAYMENTS:
        low = min(low, middle * (1 - MIN_RANGE_SHARE))
        high = max(high, middle * (1 + MIN_RANGE_SHARE))
    bounds = sorted((sign * round(low), sign * round(high)))
    return "variable", sign * round(middle), (bounds[0], bounds[1])


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
    part: bool = False, card: bool = False,
) -> Series | None:  # fmt: skip
    days = [t.posted_date for t in rows]
    fit = _best_fit(days, direction, as_of, part=part, exact=card)
    if fit is None:
        return None
    paid = [rows[i] for _, i in fit.matched]
    amount_type, expected_cents, amount_range = _amounts([t.amount_cents for t in paid])
    if card and any(
        abs(abs(t.amount_cents) - abs(expected_cents)) > CARD_AMOUNT_SHARE * abs(expected_cents)
        for t in paid
    ):
        return None  # a shopping habit, not a bill: the amounts move too much
    last_due = fit.matched[-1][0]
    later = [e for e in fit.expected if e > last_due]
    # A due date counts as missed once its tolerance window is over.
    missed = sum(e < as_of and business_days_apart(e, as_of) > TOLERANCE for e in later)
    return Series(
        key=key, merchant=merchant, kind=kind, direction=direction,  # type: ignore[arg-type]
        cadence=fit.cadence, anchor=fit.anchor,
        anchor_day=None if fit.cadence in ("weekly", "biweekly") else fit.anchor.day,
        amount_type=amount_type, expected_cents=expected_cents,  # type: ignore[arg-type]
        range_cents=amount_range, rows=tuple(t.row_id for t in paid),
        next_expected=later[0], active=missed < ENDED_AFTER,
    )  # fmt: skip


def _recipient(text: str) -> str:
    """What the bank prints after the fixed e-Transfer words (usually nothing)."""
    words = [w for w in text.casefold().split() if w not in ("free", "interac", "e-transfer")]
    return " ".join(words)


def _groups(
    txns: Sequence[Txn], user: UserDecisions | None
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
            # A short hash, so a person's name is never part of a stored key.
            tag = hashlib.sha256(who.encode()).hexdigest()[:12] if who else "unnamed"
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
    txns: Sequence[Txn], user: UserDecisions | None = None, as_of: date | None = None
) -> list[Series]:
    """Every recurring series visible on `as_of` (default: the last posting date), using
    only rows posted on or before it. Sorted by key."""
    if as_of is None:
        as_of = max((t.posted_date for t in txns), default=date.min)
    past = [t for t in txns if t.posted_date <= as_of]
    found: list[Series] = []
    groups, card_rows = _groups(past, user)
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
    for s in series:
        if s.direction != "out" or s.kind == "own transfer":
            continue
        if s.kind == "e-transfer" and s.key not in confirmed:
            continue
        for row_id in s.rows:
            out[row_id] = Override("recurring_outflow", series_key=s.key)
    flows = flow_types(txns, user)
    merchants = merchant_groups(txns, user)
    bills: dict[str | None, list[int]] = {}
    for t in txns:
        if flows[t.row_id].reason == "bill payment":
            bills.setdefault(merchants[t.row_id], []).append(t.row_id)
    for payee, row_ids in bills.items():
        if payee is not None and len(row_ids) == 1 and row_ids[0] not in out:
            out[row_ids[0]] = Override("everyday", one_off=True)
    return out
