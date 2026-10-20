"""Proposals for recurring settings: the app suggests what I used to type by hand (week 5, item 2).

Prefilled from history, with one "Looks right" button; nothing is applied without a click. This is
not the lump-income / repeated-large-payment proposals of `lookahead.proposals` (retired).

- rent: a monthly e-Transfer sent by hand: the allowed amounts and the window of days of the month;
- link: one subscription under two merchant names (a "continues" link);
- possible: a monthly charge seen exactly twice.

A proposal is made only for a series I have made **no decision about** (not confirmed, rent rule,
linked, ended, rejected, renamed, categorised or hand-added). Rule zero: only rows posted on or
before `as_of`. The method and the adopt rule are in DECISIONS.md (2026-10-20).
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import ceil
from statistics import median

from lookahead.flows import Txn
from lookahead.recurring import _groups
from lookahead.recurring_store import Decision, Stored
from lookahead.rent import RentRule
from lookahead.resolve import UserDecisions
from lookahead.series_identity import family_of

# rent
MIN_PAYMENTS = 3
MIN_RENT_CENTS = 10_000  # below $100 an e-Transfer is not rent
MAX_SIZES = 3
MAX_WINDOW_DAYS = 14
COVER = 0.8  # the window covers at least this share of past payments
SIZE_TOLERANCE = 0.03  # amounts within 3 percent (or $5) are one size
RECENT_PAYMENTS = 6  # the sizes of the latest payments are the ones used going forward


@dataclass(frozen=True)
class SettingProposal:
    """One thing the app suggests. `series_id` is the series a "Looks right" decision is written
    against; `key` identifies the proposal for "not this" (kind plus series)."""

    kind: str  # rent / link / possible
    series_id: str
    key: str
    title: str
    evidence: tuple[str, ...]  # fingerprints of the payments it is based on
    confidence: str  # likely / possible
    rent: RentRule | None = None
    other_id: str | None = None  # link: the older series


def has_decision(s: Stored, decisions: Sequence[Decision]) -> bool:
    """True if I have decided anything about this series (by id, by key, or by an earlier key it
    carries on). A hand-added series always has one (its own "add")."""
    names = {s.key, s.id, *s.continues}
    return s.status == "user-added" or any(d.key in names for d in decisions)


def best_window(days: Sequence[int], cover: float = COVER) -> tuple[int, int] | None:
    """The shortest window of days of the month (it may wrap a month end) that holds at least
    `cover` of the given send days, as (from_day, to_day). None if it needs more than
    MAX_WINDOW_DAYS days. Ties: more payments inside, then the earlier from-day."""
    if not days:
        return None
    need = ceil(cover * len(days))
    best: tuple[int, int, int] | None = None  # (width, -inside, start)
    for start in range(1, 32):
        for width in range(1, MAX_WINDOW_DAYS + 1):
            inside = {(start - 1 + k) % 31 + 1 for k in range(width)}
            count = sum(d in inside for d in days)
            if count >= need:
                candidate = (width, -count, start)
                if best is None or candidate < best:
                    best = candidate
                break
    if best is None:
        return None
    width, _, start = best
    return start, (start + width - 2) % 31 + 1


def _size_clusters(amounts: Sequence[int]) -> list[list[int]]:
    """Amounts (positive cents, sorted) grouped into sizes: within 3 percent or $5 of the first."""
    out: list[list[int]] = []
    for a in sorted(amounts):
        if out and a - out[-1][0] <= max(SIZE_TOLERANCE * out[-1][0], 500):
            out[-1].append(a)
        else:
            out.append([a])
    return out


def _sizes_going_forward(payments: Sequence[tuple[date, int]]) -> list[int]:
    """The sizes of the latest payments: a size that no longer appears among the last
    RECENT_PAYMENTS is dropped (the latest amount is the one used going forward)."""
    clusters = _size_clusters([a for _, a in payments])
    recent = sorted(payments)[-RECENT_PAYMENTS:]
    sizes = []
    for cluster in clusters:
        if any(lo <= a <= hi for _, a in recent for lo, hi in [(min(cluster), max(cluster))]):
            sizes.append(int(median(cluster)))
    return sorted(sizes)


def _monthly(dates: Sequence[date]) -> bool:
    """About one payment a month: the typical gap is 25 to 38 days and most gaps 20 to 50."""
    ordered = sorted(dates)
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:], strict=False)]
    if not gaps or not 25 <= median(gaps) <= 38:
        return False
    return sum(20 <= g <= 50 for g in gaps) >= 0.8 * len(gaps)


def propose_rent(
    series: Sequence[Stored],
    decisions: Sequence[Decision],
    txns: Sequence[Txn],
    fingerprint: dict[int, str],
    user: UserDecisions | None,
    key: bytes | None,
    as_of: date,
) -> list[SettingProposal]:
    """Rent sent by e-Transfer: allowed amounts and a window, for a recipient whose payments are
    about monthly, are not already one plain series (two or more sizes, or send days spread over
    four days or more), and whose series I have not decided anything about."""
    past = [t for t in txns if t.posted_date <= as_of]
    groups, _ = _groups(past, user, key)
    by_family: dict[str, list[Stored]] = defaultdict(list)
    for s in series:
        if s.kind == "e-transfer" and s.direction == "out":
            by_family[family_of(s.key)].append(s)
    out: list[SettingProposal] = []
    for (group_key, _, kind, direction), rows in sorted(groups.items()):
        family = by_family.get(group_key)
        if kind != "e-transfer" or direction != "out" or not family:
            continue
        if any(has_decision(s, decisions) for s in family):
            continue
        payments = [(t.posted_date, abs(t.amount_cents)) for t in rows]
        months = {(d.year, d.month) for d, _ in payments}
        if len(payments) < MIN_PAYMENTS or len(months) < MIN_PAYMENTS:
            continue
        if median(a for _, a in payments) < MIN_RENT_CENTS:
            continue
        if not _monthly([d for d, _ in payments]):
            continue
        sizes = _sizes_going_forward(payments)
        window = best_window([d.day for d, _ in payments])
        if window is None or not 1 <= len(sizes) <= MAX_SIZES:
            continue
        width = (window[1] - window[0]) % 31 + 1
        if len(sizes) < 2 and width < 4:
            continue  # one amount on about one day: an ordinary series already covers it
        target = max(family, key=lambda s: (len(s.evidence), s.key))
        rule = RentRule(tuple(sizes), window[0], window[1])
        amounts = " or ".join(f"${a / 100:,.2f}" for a in sizes)
        out.append(
            SettingProposal(
                kind="rent",
                series_id=target.id or target.key,
                key=f"rent|{target.id or target.key}",
                title=(
                    f"Looks like rent sent by e-Transfer: {amounts}, "
                    f"between day {window[0]} and day {window[1]} of the month"
                ),
                evidence=tuple(fingerprint[t.row_id] for t in rows),
                confidence="likely" if len(payments) >= 2 * MIN_PAYMENTS else "possible",
                rent=rule,
            )
        )
    return out
