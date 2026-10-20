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

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import ceil
from statistics import median

from lookahead import recurring_store as rs
from lookahead.flows import Txn
from lookahead.proposals import LOOKS_RIGHT, NOT_THIS_TIME
from lookahead.proposals import decide as record_proposal_decision
from lookahead.proposals import decisions_as_of as proposal_decisions_as_of
from lookahead.recurring import _groups
from lookahead.recurring_store import Decision, Stored
from lookahead.rent import RentRule
from lookahead.resolve import UserDecisions, load_user_decisions
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
    # possible: what "Looks right" hands to `add_series` (there is no series yet)
    name: str | None = None
    amount_cents: int | None = None  # signed, money out is negative
    anchor: date | None = None  # the date of the latest charge
    cadence: str | None = None


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


# --- link: one subscription under two merchant names ---

LINK_AMOUNT_TOLERANCE = 0.05  # the two amounts differ by at most 5 percent
LINK_PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "semimonthly": 15, "monthly": 31, "yearly": 366}
LINK_MAX_GAP_PERIODS = 2


def _first_day(s: Stored, day_of: dict[str, date]) -> date | None:
    return day_of.get(s.evidence[0]) if s.evidence else None


def _share_a_word(a: str | None, b: str | None) -> bool:
    def words(text: str | None) -> set[str]:
        return {w for w in (text or "").casefold().split() if len(w) > 2}

    return bool(words(a) & words(b))


def propose_links(
    series: Sequence[Stored],
    decisions: Sequence[Decision],
    txns: Sequence[Txn],
    fingerprint: dict[int, str],
    as_of: date,
) -> list[SettingProposal]:
    """The same fixed payment under a new merchant name: an older series that stopped, and a
    newer one of another merchant that starts after its last payment, within two periods, with the
    same cadence and about the same amount. "Likely" when the new one starts about one period
    after the last old payment (or the names share a word); otherwise "possible".

    No proposal when either series carries a decision of mine, when they overlap in time (two
    real subscriptions), when they are the same merchant (a price change, not a new name), or
    when the match is not one to one (a new series that could continue two old ones, or the
    other way round): prefer no proposal over a doubtful one."""
    day_of = {fingerprint[t.row_id]: t.posted_date for t in txns if t.posted_date <= as_of}
    pool = [
        s for s in series
        if s.kind == "merchant" and s.direction == "out" and s.amount_type == "fixed"
        and s.evidence and s.last_paid is not None and s.last_paid <= as_of
        and not has_decision(s, decisions)
    ]  # fmt: skip
    candidates: list[tuple[Stored, Stored, int]] = []
    for old in pool:
        period = LINK_PERIOD_DAYS.get(old.cadence)
        if period is None:
            continue
        for new in pool:
            first = _first_day(new, day_of)
            if new is old or first is None or new.cadence != old.cadence:
                continue
            if family_of(new.key) == family_of(old.key):
                continue
            gap = (first - old.last_paid).days  # type: ignore[operator]
            if not 0 < gap <= LINK_MAX_GAP_PERIODS * period:
                continue
            a, b = abs(old.expected_cents), abs(new.expected_cents)
            if abs(a - b) > LINK_AMOUNT_TOLERANCE * max(a, b):
                continue
            candidates.append((old, new, gap))
    out: list[SettingProposal] = []
    for old, new, gap in candidates:
        if sum(c[0] is old for c in candidates) > 1 or sum(c[1] is new for c in candidates) > 1:
            continue  # ambiguous: not one to one
        period = LINK_PERIOD_DAYS[old.cadence]
        on_time = abs(gap - period) <= max(5, 0.25 * period)
        likely = on_time or _share_a_word(old.name, new.name)
        amount = f"${abs(new.expected_cents) / 100:,.2f}"
        out.append(
            SettingProposal(
                kind="link",
                series_id=new.id or new.key,
                other_id=old.id or old.key,
                key=f"link|{new.id or new.key}|{old.id or old.key}",
                title=(
                    f"{new.name} looks like {old.name} under a new name ({amount} {new.cadence})"
                ),
                evidence=old.evidence + new.evidence,
                confidence="likely" if likely else "possible",
            )
        )
    return sorted(out, key=lambda p: p.key)


# --- possible: a monthly charge seen exactly twice ---

POSSIBLE_MIN_GAP, POSSIBLE_MAX_GAP = 25, 38  # days between the two charges
POSSIBLE_STALE_DAYS = 45  # the latest charge is no older than this
POSSIBLE_SAME_AMOUNT = 0.01  # the two amounts are the same to within 1 percent


def propose_possible(
    series: Sequence[Stored],
    decisions: Sequence[Decision],
    txns: Sequence[Txn],
    fingerprint: dict[int, str],
    user: UserDecisions | None,
    key: bytes | None,
    as_of: date,
) -> list[SettingProposal]:
    """A merchant with exactly two payments, about a month apart, of the same amount, the latest
    still recent: a subscription that detection cannot call a series yet (it needs three).
    Always "possible". Nothing for a merchant that has any other payment, for payments that
    already belong to a series, for a name I added by hand, or for card purchases only."""
    past = [t for t in txns if t.posted_date <= as_of]
    groups, card_rows = _groups(past, user, key)
    covered = {fp for s in series for fp in s.evidence}
    added = {(s.name or "").casefold() for s in series if s.status == "user-added"}
    added |= {str(d.details.get("name", "")).casefold() for d in decisions if d.action == "add"}
    out: list[SettingProposal] = []
    for (_, merchant, kind, direction), rows in sorted(groups.items(), key=lambda kv: kv[0]):
        if kind != "merchant" or direction != "out" or merchant is None or len(rows) != 2:
            continue
        if merchant.casefold() in added or all(t.row_id in card_rows for t in rows):
            continue
        first, second = sorted(rows, key=lambda t: (t.posted_date, t.row_id))
        a, b = abs(first.amount_cents), abs(second.amount_cents)
        gap = (second.posted_date - first.posted_date).days
        if min(a, b) < 100 or abs(a - b) > POSSIBLE_SAME_AMOUNT * max(a, b):
            continue
        if not POSSIBLE_MIN_GAP <= gap <= POSSIBLE_MAX_GAP:
            continue
        if (as_of - second.posted_date).days > POSSIBLE_STALE_DAYS:
            continue
        prints = tuple(fingerprint[t.row_id] for t in (first, second))
        if any(fp in covered for fp in prints):
            continue
        out.append(
            SettingProposal(
                kind="possible",
                series_id=f"possible|{prints[0]}",
                key=f"possible|{prints[0]}",
                title=(
                    f"{merchant} may be a monthly subscription (${b / 100:,.2f}, seen twice so far)"
                ),
                evidence=prints,
                confidence="possible",
                name=merchant,
                amount_cents=second.amount_cents,
                anchor=second.posted_date,
                cadence="monthly",
            )
        )
    return out


# --- showing, accepting and dismissing ---


def current_proposals(
    conn: sqlite3.Connection, account: str, key: bytes | None = None, as_of: date | None = None
) -> list[SettingProposal]:
    """Every proposal on `as_of` (default: the account's last posting date), from the rows posted
    by then, the recurring decisions made by then and the dismissals made by then. Rent first,
    then links, then possible subscriptions."""
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, (as_of or date.max).isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    if not txns:
        return []
    day = as_of or max(t.posted_date for t in txns)
    fingerprint = {r[0]: r[5] for r in raw}
    # rows are cut at `day`; decisions at `as_of` (None: a live call uses every decision made)
    series, _ = rs.series_as_of(conn, account, as_of, key)
    decisions = rs.load_decisions(conn, account, as_of)
    user = load_user_decisions(conn, as_of)
    found = [
        *propose_rent(series, decisions, txns, fingerprint, user, key, day),
        *propose_links(series, decisions, txns, fingerprint, day),
        *propose_possible(series, decisions, txns, fingerprint, user, key, day),
    ]
    dismissed = proposal_decisions_as_of(conn, account, as_of)
    return [p for p in found if dismissed.get(p.key) != NOT_THIS_TIME]


def dismiss(
    conn: sqlite3.Connection, account: str, proposal: SettingProposal, when: date | None = None
) -> None:
    """ "Not this": a dated row, so it stays dismissed and an earlier replay still shows it."""
    record_proposal_decision(conn, account, proposal.key, NOT_THIS_TIME, when or date.today())


def accept(
    conn: sqlite3.Connection, account: str, proposal: SettingProposal, series: Sequence[Stored],
    when: date | None = None,
) -> None:  # fmt: skip
    """ "Looks right": write the real recurring decision (only through `recurring_store`, by stable
    id), then log the click. Rent: a rent rule on the series; link: a "continues" link; possible:
    a series added by hand."""
    day = when or date.today()
    by_id = {s.id or s.key: s for s in series}
    if proposal.kind == "rent" and proposal.rent is not None:
        rs.set_rent_rule(conn, account, by_id[proposal.series_id], proposal.rent, day)
    elif proposal.kind == "link" and proposal.other_id is not None:
        rs.link_continuation(
            conn, account, by_id[proposal.series_id], by_id[proposal.other_id], day
        )
    elif proposal.kind == "possible" and proposal.name and proposal.amount_cents:
        rs.add_series(
            conn, account, proposal.name, "out", proposal.cadence or "monthly",
            proposal.anchor or day, abs(proposal.amount_cents), day,
        )  # fmt: skip
    else:
        raise ValueError(f"cannot apply a {proposal.kind!r} proposal")
    record_proposal_decision(conn, account, proposal.key, LOOKS_RIGHT, day)
