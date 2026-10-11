"""Proposals for the forecaster (DECISIONS.md, 2026-10-11, step 2): an option, off by default.

a) Lump income as proposals. Lumps (big deposits in no series, not paid straight back out, in the
   last 365 days) are grouped by sender, as the pseudonymizer's keyed token. Two or more from one
   sender give a proposal: "expecting about $X from <sender> around <date>?", with a typical size
   (the median), a typical gap (the median), an expected date and a window.
b) Repeated large outflows. Two or more big payments to one payee, 60 to 400 days apart, give a
   placement of the next one on its expected date, by default, labelled "detected, not
   confirmed". One payment only gives an offer for a planned item prefilled from it.

Both carry their evidence (fingerprints) and a key. What I decide about them ("looks right",
"not this time") is a dated row in `proposal_decisions`, replayed as of a date like the recurring
decisions. Everything uses only rows posted on or before the date (rule zero).
"""

import re
import sqlite3
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Literal

from lookahead.calendar_rules import shift_to_business_day
from lookahead.flows import Flow, Txn, merchant_groups
from lookahead.forecast.spending import (
    LARGE_WINDOW_DAYS,
    TYPICAL_MIN_ROWS,
    _counted,
    _money_in,
    large_cutoff,
    lump_rows,
)
from lookahead.pseudonymize import token
from lookahead.resolve import UserDecisions

DETECTED = "detected, not confirmed"
CONFIRMED = "confirmed"
LOOKS_RIGHT = "looks right"
NOT_THIS_TIME = "not this time"
ACTIONS = (LOOKS_RIGHT, NOT_THIS_TIME)

# a) lump proposals
MIN_LUMPS = 2
LIKELY_LUMPS = 3
MIN_HALF_WIDTH_DAYS = 10
# b) repeated large outflows
OUTFLOW_LOOKBACK_DAYS = 730
MIN_GAP_DAYS, MAX_GAP_DAYS = 60, 400
JITTER_DAYS = 5
YEAR_DAYS = 365  # the calendar rule: a payment proposes itself plus 365 days
TERM_MATCH_DAYS = 21  # a payment this close to a proposed day is that term's payment, already made

_BANK_WORDS = frozenset({"free", "interac", "e-transfer", "etransfer", "e-transfert", "from"})
_SLOT = re.compile(r"^\s*\d+/")  # a credit memo's text starts with a slot such as "1/"


def sender_of(text: str) -> str:
    """The sender name in a deposit's text, case-folded; empty if the bank prints none. Only
    letters decide: card-style codes, slots like "1/" and the fixed e-Transfer words are no name."""
    words = [w for w in _SLOT.sub("", text.casefold()).split() if w not in _BANK_WORDS]
    name = " ".join(words)
    return name if re.search(r"[^\W\d_]", name) else ""


def _median_int(values: Sequence[int]) -> int:
    return round(median(values))


# --- a) Lump income as proposals ---


@dataclass(frozen=True)
class LumpProposal:
    key: str  # "lump|<sender token>|<date of the last lump>"
    sender: str  # the keyed token, never the name
    typical_cents: int  # the median lump size
    gap_days: int  # the median gap between lumps
    expected: date
    first: date  # the window: expected date plus or minus the half width
    last: date
    evidence: tuple[str, ...]  # fingerprints of the lumps, oldest first
    confidence: Literal["possible", "likely"]
    status: str = DETECTED


def lump_proposals(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    first_day: date, last_day: date, key: bytes | None, fingerprints: Mapping[int, str],
) -> tuple[list[LumpProposal], list[tuple[Txn, Flow, int]]]:  # fmt: skip
    """The proposals whose expected date is a forecast day, and every lump of the window (the
    pool of the random arrivals). Without a pseudonym key there are no proposals: a sender is
    only ever grouped under its keyed token."""
    lumps = lump_rows(txns, flows, series_rows, as_of)
    if key is None:
        return [], lumps
    by_sender: dict[str, list[tuple[Txn, Flow, int]]] = defaultdict(list)
    for row in lumps:
        name = sender_of(row[0].text)
        if name:
            by_sender[token("PERSON", name, key)].append(row)
    out = []
    for sender, rows in sorted(by_sender.items()):
        if len(rows) < MIN_LUMPS:
            continue
        days = [t.posted_date for t, _, _ in rows]
        gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
        gap = _median_int(gaps)
        if gap <= 0:
            continue  # two lumps on one day: no gap to go by
        expected = shift_to_business_day(days[-1] + timedelta(days=gap), "earlier")
        if not first_day <= expected <= last_day:
            continue
        half = max(MIN_HALF_WIDTH_DAYS, round(gap / 4))
        out.append(LumpProposal(
            f"lump|{sender}|{days[-1].isoformat()}", sender,
            _median_int([c for _, _, c in rows]), gap, expected,
            expected - timedelta(days=half), expected + timedelta(days=half),
            tuple(fingerprints[t.row_id] for t, _, _ in rows),
            "likely" if len(rows) >= LIKELY_LUMPS else "possible",
        ))  # fmt: skip
    return out, lumps


def sender_rows(lumps: Sequence[tuple[Txn, Flow, int]], sender: str, key: bytes) -> set[int]:
    """The row ids of the lumps from one sender (by token)."""
    return {
        t.row_id for t, _, _ in lumps
        if (name := sender_of(t.text)) and token("PERSON", name, key) == sender
    }  # fmt: skip


# --- b) Repeated large outflows ---


@dataclass(frozen=True)
class OutflowPlacement:
    key: str  # "outflow|<payee>|<date of the latest payment>"
    payee: str  # a merchant group, "own accounts", or a keyed token for a person
    cents: int  # signed: money out is negative; the latest payment
    day: date  # the expected date (moved to a business day)
    evidence: tuple[str, ...]  # fingerprints of the big payments, oldest first
    rule: str  # "a year after the latest" or "the typical gap after the latest"
    status: str = DETECTED


@dataclass(frozen=True)
class FirstTimeOffer:
    """One big payment to a payee: no placement; a planned item prefilled from it is offered."""

    payee: str
    cents: int
    day: date  # the same day one year on
    evidence: tuple[str, ...]


def _a_year_after(day: date) -> date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:  # February 29
        return day.replace(year=day.year + 1, day=28)


def _payee(t: Txn, flow: Flow, merchants: Mapping[int, str | None], key: bytes | None) -> str:
    if flow.reason == "outgoing e-Transfer":
        name = " ".join(w for w in t.text.casefold().split()
                        if w not in ("free", "interac", "e-transfer"))  # fmt: skip
        return token("PERSON", name, key) if name and key is not None else ""
    if flow.reason == "own-account transfer":
        return "own accounts"
    return merchants.get(t.row_id) or ""


def big_outflows(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    key: bytes | None, user: UserDecisions | None = None,
) -> dict[str, list[tuple[Txn, int]]]:  # fmt: skip
    """payee -> its big payments of the last 730 days, oldest first. A big payment is a row of
    the everyday-spending stream (in no series, not a fee, not netted) at least the big-row cutoff
    at `as_of`. Rows with no payee are left out."""
    known = [t for t in txns if t.posted_date <= as_of]
    out_rows = _counted(known, flows, series_rows, as_of)
    in_rows = _money_in(known, flows, series_rows, as_of)
    recent = as_of - timedelta(days=LARGE_WINDOW_DAYS - 1)
    own = [c for t, _, c in out_rows if c > 0 and t.posted_date >= recent]
    everything = own + [c for t, _, c in in_rows if c > 0 and t.posted_date >= recent]
    cutoff = large_cutoff(own if len(own) >= TYPICAL_MIN_ROWS else everything)
    first = as_of - timedelta(days=OUTFLOW_LOOKBACK_DAYS - 1)
    merchants = merchant_groups(known, user)
    groups: dict[str, list[tuple[Txn, int]]] = defaultdict(list)
    for t, flow, cents in sorted(out_rows, key=lambda r: (r[0].posted_date, r[0].row_id)):
        if cents >= cutoff and t.posted_date >= first:
            payee = _payee(t, flow, merchants, key)
            if payee:
                groups[payee].append((t, cents))
    return dict(groups)


def repeated_outflows(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    first_day: date, last_day: date, key: bytes | None, fingerprints: Mapping[int, str],
    rule: Literal["earliest", "year", "gap", "calendar"] = "earliest",
    user: UserDecisions | None = None,
) -> tuple[list[OutflowPlacement], list[FirstTimeOffer]]:  # fmt: skip
    """The placements of the next repeated large payment that fall on a forecast day, and the
    first-time offers. `rule`: the default takes the earliest of a year after the latest payment
    and the typical gap after it that is after `as_of` and inside the horizon; "year" and "gap"
    use one of them alone (scored for comparison). "calendar" (DECISIONS.md, 2026-10-12, step 1):
    every past payment proposes itself plus 365 days, and the earliest proposed date after
    `as_of` and inside the horizon is placed, with the amount of the payment that proposed it
    (the latest of them if several propose the same date)."""
    placements, offers = [], []
    for payee, rows in sorted(big_outflows(txns, flows, series_rows, as_of, key, user).items()):
        days = [t.posted_date for t, _ in rows]
        evidence = tuple(fingerprints[t.row_id] for t, _ in rows)
        latest_t, latest_cents = rows[-1]
        if len(rows) == 1:
            offers.append(FirstTimeOffer(payee, -latest_cents, _a_year_after(days[0]), evidence))
            continue
        if rule == "calendar":
            found = _same_term(rows, as_of, first_day, last_day)
            if found is not None:
                day, source_day, cents = found
                placements.append(OutflowPlacement(
                    f"outflow|{payee}|{source_day.isoformat()}", payee, -cents, day, evidence,
                    "a year after the same-term payment",
                ))  # fmt: skip
            continue
        repeats = [g for a, b in zip(days, days[1:], strict=False)
                   if MIN_GAP_DAYS <= (g := (b - a).days) <= MAX_GAP_DAYS]  # fmt: skip
        if not repeats:
            continue
        candidates = []
        if rule in ("earliest", "year"):
            candidates.append((_a_year_after(days[-1]), "a year after the latest"))
        if rule in ("earliest", "gap"):
            gap = _median_int(repeats)
            candidates.append((days[-1] + timedelta(days=gap), "the typical gap after the latest"))
        ahead = sorted(
            (shift_to_business_day(d, "later"), why) for d, why in candidates
            if as_of < shift_to_business_day(d, "later") <= last_day
        )  # fmt: skip
        ahead = [(d, why) for d, why in ahead if d >= first_day]
        if not ahead:
            continue
        day, why = ahead[0]
        placements.append(OutflowPlacement(
            f"outflow|{payee}|{days[-1].isoformat()}", payee, -latest_cents, day, evidence, why,
        ))  # fmt: skip
    return placements, offers


def _same_term(
    rows: Sequence[tuple[Txn, int]], as_of: date, first_day: date, last_day: date
) -> tuple[date, date, int] | None:  # fmt: skip
    """The calendar rule: (placed day, the payment that proposed it, its cents), or None. Each
    past payment proposes itself plus 365 days, moved to the next business day; the earliest
    proposed day after `as_of` and on a forecast day wins, and among the payments that propose
    that day the latest one gives the amount."""
    proposed = []
    for t, cents in rows:  # oldest first
        day = shift_to_business_day(t.posted_date + timedelta(days=YEAR_DAYS), "later")
        if as_of < day <= last_day and day >= first_day:
            proposed.append((day, t.posted_date, cents))
    # A term already paid this year is not placed again: each payment made within 3 weeks of a
    # proposed day belongs to the nearest such day (one payment, one day), and that day is dropped.
    # Installments a month apart stay apart, because each payment takes only its nearest day.
    pairs = sorted(
        (abs((t.posted_date - day).days), t.posted_date, day)
        for t, _ in rows for day in {d for d, _, _ in proposed}
        if abs((t.posted_date - day).days) <= TERM_MATCH_DAYS
    )  # fmt: skip
    paid_days: set[date] = set()
    used: set[date] = set()
    for _, paid_on, day in pairs:
        if paid_on not in used and day not in paid_days:
            used.add(paid_on)
            paid_days.add(day)
    proposed = [p for p in proposed if p[0] not in paid_days]
    if not proposed:
        return None
    day = min(d for d, _, _ in proposed)
    return [p for p in proposed if p[0] == day][-1]


# --- Decisions: a dated log, replayed as of a date ---


def decide(
    conn: sqlite3.Connection, account: str, proposal_key: str, action: str, when: date
) -> None:  # fmt: skip
    """Record what I decided about a proposal or placement ("looks right" or "not this time")."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    with conn:
        conn.execute(
            "INSERT INTO proposal_decisions (account, proposal_key, action, decided_at)"
            " VALUES (?, ?, ?, ?)",
            (account, proposal_key, action, when.isoformat()),
        )


def decisions_as_of(
    conn: sqlite3.Connection, account: str, as_of: date | None = None
) -> dict[str, str]:
    """proposal key -> the latest action decided on or before `as_of` (None: every decision).
    A database made before the table existed has none."""
    limit = (as_of or date.max).isoformat()
    try:
        rows = conn.execute(
            "SELECT proposal_key, action FROM proposal_decisions WHERE account = ?"
            " AND decided_at <= ? ORDER BY decided_at, decision_id",
            (account, limit),
        ).fetchall()
    except sqlite3.OperationalError:  # an older database opened read-only
        return {}
    return {r[0]: r[1] for r in rows}
