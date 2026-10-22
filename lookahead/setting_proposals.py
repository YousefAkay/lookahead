"""Proposals for recurring settings: the app suggests what I used to type by hand (week 5, item 2).

Prefilled from history, with one "Looks right" button; nothing is applied without a click. This is
not the lump-income / repeated-large-payment proposals of `lookahead.proposals` (retired).

- rent: a monthly e-Transfer sent by hand: the allowed amounts and the window of days of the month;
- link: one subscription under two merchant names (a "continues" link);
- possible: a monthly charge seen exactly twice.

What blocks a proposal depends on its kind (DECISIONS.md, 2026-10-20, narrowed before any scoring):
- rent: an existing rent rule on the series, "It ended" or "Not recurring", or "Not this";
  confirming, renaming or giving a category do not block it;
- link: an existing link involving either series, an end or a reject on either, or "Not this";
- possible subscription: any decision on it (it is a name I added, or its payments already belong
  to a series), or "Not this";
- a series added by hand never gets a proposal.
Rule zero: only rows posted on or before `as_of`. The method and the adopt rule are in
DECISIONS.md (2026-10-20).
"""

import sqlite3
from collections import defaultdict
from collections.abc import Collection, Sequence
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
RENT_STALE_DAYS = 50  # rent whose latest payment is older than this has stopped: no proposal
RENT_BAND = 0.15  # sizes within 15 percent of one another can be one rent (a rise of 5 to 12)


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


def _names(s: Stored) -> set[str]:
    return {s.key, s.id, *s.continues}


def standing(s: Stored, decisions: Sequence[Decision]) -> str | None:
    """The latest of confirm / reject / end I decided about this series (None: none). The log is
    in the order made, so a confirmation after an end puts it back."""
    names, latest = _names(s), None
    for d in decisions:
        if d.key in names and d.action in ("confirm", "reject", "end"):
            latest = d.action
    return latest


def has_link(s: Stored, decisions: Sequence[Decision]) -> bool:
    """True if a "continues" link involves this series (either side), or it already carries one."""
    names = _names(s)
    return bool(s.continues) or any(
        d.action == "continue" and (d.key in names or d.details.get("previous") in names)
        for d in decisions
    )


def rent_blocked(s: Stored, decisions: Sequence[Decision]) -> bool:
    """A rent proposal is blocked by a rent rule already on the series, by "It ended" or "Not
    recurring" (the latest of confirm / reject / end), or because I added the series by hand.
    Confirming, renaming and giving a category do not block it."""
    return (
        s.status == "user-added"
        or bool(s.allowed_cents)
        or standing(s, decisions) in ("reject", "end")
    )


def link_blocked(s: Stored, decisions: Sequence[Decision]) -> bool:
    """A link proposal is blocked, for either series, by an existing link, by "It ended" or "Not
    recurring", or because I added the series by hand. Confirming, renaming and giving a
    category do not block it."""
    return (
        s.status == "user-added"
        or has_link(s, decisions)
        or standing(s, decisions) in ("reject", "end")
    )


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
    RECENT_PAYMENTS is dropped (the latest amount is the one used going forward). A size is
    one group of amounts within 3 percent (or $5) of one another; it is allowed at its median (what
    the history was paid at) **and at its latest amount**, so a small rise inside the group, right
    after the rise, when the new amount has few payments, is not lost."""
    ordered = sorted(payments)
    recent = ordered[-RECENT_PAYMENTS:]
    sizes: set[int] = set()
    for cluster in _size_clusters([a for _, a in payments]):
        low, high = min(cluster), max(cluster)
        if any(low <= a <= high for _, a in recent):
            sizes.add(int(median(cluster)))
            sizes.add([a for _, a in ordered if low <= a <= high][-1])  # the latest amount
    return sorted(sizes)


def _monthly(dates: Sequence[date]) -> bool:
    """About one payment a month: the typical gap is 25 to 38 days and most gaps 20 to 50."""
    ordered = sorted(dates)
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:], strict=False)]
    if not gaps or not 25 <= median(gaps) <= 38:
        return False
    return sum(20 <= g <= 50 for g in gaps) >= 0.8 * len(gaps)


REASONS = {
    "entry_not_found": "the entry (its series) is not in the account's series",
    "no_candidate_series": "no detected series (or recipient or merchant) to attach a proposal to",
    "guard_blocked_by_end_or_reject": "blocked: I ended it or called it not recurring",
    "guard_blocked_by_link": "blocked: a link already involves it",
    "guard_blocked_by_hand_added": "blocked: it is a series I added by hand",
    "guard_blocked_by_rent_rule": "blocked: a rent rule is already on it",
    "too_few_payments": "too few payments",
    "amount_too_small": "the amount is too small to be rent",
    "not_monthly": "the payments are not about monthly",
    "outside_freshness_limit": "the latest payment is too old (outside the freshness limit)",
    "window_or_amounts_unfit": "no window of two weeks or less, or more than three amounts",
    "an_ordinary_series_covers_it": "one amount on about one day: an ordinary series covers it",
    "amounts_not_equal": "a proposal exists but its amounts are not the same as mine",
    "window_not_covered": "a proposal exists but its window does not cover mine",
    "same_merchant": "both are the same merchant (a price change, not a new name)",
    "not_fixed_merchant_series": "not a fixed-amount merchant series",
    "different_cadence": "the two do not have the same cadence",
    "gap_not_in_range": "they overlap in time, or the new one starts too long after the old one",
    "amounts_differ": "the amounts differ too much",
    "not_one_to_one": "the match is ambiguous (more than one possible partner)",
    "not_two_payments": "the merchant does not have exactly two payments",
    "gap_not_monthly": "the two payments are not about a month apart",
    "card_not_a_month_apart": "paid by card: the two payments are not a calendar month apart",
    "already_in_a_series": "the payments already belong to a series",
    "name_added_by_hand": "the name was added by hand",
}


def _blocked_reason(s: Stored, decisions: Sequence[Decision], kind: str) -> str | None:
    """Why a series blocks a proposal of this kind (None: it does not). The same rule as
    `rent_blocked` / `link_blocked`, with the reason."""
    if s.status == "user-added":
        return "guard_blocked_by_hand_added"
    if standing(s, decisions) in ("reject", "end"):
        return "guard_blocked_by_end_or_reject"
    if kind == "rent" and s.allowed_cents:
        return "guard_blocked_by_rent_rule"
    if kind == "link" and has_link(s, decisions):
        return "guard_blocked_by_link"
    return None


def _rent_cluster(rows: Sequence[Txn]) -> tuple[list[Txn], str | None]:
    """The payments of a recipient that can be rent: at or above the floor, similar in amount
    (within RENT_BAND of one size, so a rent rise stays in), roughly monthly. Small e-Transfers
    to the same recipient, or to none, are left out and never pull the rent under the floor. The
    cluster with the most payments wins. Returns (cluster, None), or ([], the reason there is
    none)."""
    big = [t for t in rows if abs(t.amount_cents) >= MIN_RENT_CENTS]
    if not big:
        return [], "amount_too_small"
    best: tuple[tuple[int, float], list[Txn]] | None = None
    for anchor in _size_clusters([abs(t.amount_cents) for t in big]):
        centre = median(anchor)
        near = [t for t in big if abs(abs(t.amount_cents) - centre) <= RENT_BAND * centre]
        dates = [t.posted_date for t in near]
        months = {(d.year, d.month) for d in dates}
        if len(near) < MIN_PAYMENTS or len(months) < MIN_PAYMENTS or not _monthly(dates):
            continue
        if best is None or (len(near), centre) > best[0]:
            best = ((len(near), centre), near)
    if best is not None:
        return best[1], None
    months = {(t.posted_date.year, t.posted_date.month) for t in big}
    if len(big) < MIN_PAYMENTS or len(months) < MIN_PAYMENTS:
        return [], "too_few_payments"
    return [], "not_monthly"


def _rent_for_group(
    group_key: str, rows: Sequence[Txn], family: Sequence[Stored], decisions: Sequence[Decision],
    fingerprint: dict[int, str], as_of: date,
) -> "SettingProposal | str":  # fmt: skip
    """A rent proposal for one recipient's payments, or the reason there is none. It is judged on
    the rent cluster of the recipient (see `_rent_cluster`), not on the whole group."""
    if not family:
        return "no_candidate_series"
    for s in family:
        reason = _blocked_reason(s, decisions, "rent")
        if reason:
            return reason
    cluster, reason = _rent_cluster(rows)
    if reason:
        return reason
    payments = [(t.posted_date, abs(t.amount_cents)) for t in cluster]
    if (as_of - max(d for d, _ in payments)).days > RENT_STALE_DAYS:
        return "outside_freshness_limit"  # the rent stopped; the account went on
    sizes = _sizes_going_forward(payments)
    window = best_window([d.day for d, _ in payments])
    if window is None or not 1 <= len(sizes) <= MAX_SIZES:
        return "window_or_amounts_unfit"
    width = (window[1] - window[0]) % 31 + 1
    if len(sizes) < 2 and width < 4:
        return "an_ordinary_series_covers_it"
    live = [s for s in family if s.active]  # the live series if there is one
    target = max(live or family, key=lambda s: (len(s.evidence), s.key))
    rule = RentRule(tuple(sizes), window[0], window[1])
    amounts = " or ".join(f"${a / 100:,.2f}" for a in sizes)
    return SettingProposal(
        kind="rent",
        series_id=target.id or target.key,
        key=f"rent|{target.id or target.key}",
        title=(
            f"Looks like rent sent by e-Transfer: {amounts}, "
            f"between day {window[0]} and day {window[1]} of the month"
        ),
        evidence=tuple(fingerprint[t.row_id] for t in cluster),
        confidence="likely" if len(payments) >= 2 * MIN_PAYMENTS else "possible",
        rent=rule,
    )


def _rent_groups(
    series: Sequence[Stored], txns: Sequence[Txn], user: UserDecisions | None,
    key: bytes | None, as_of: date,
) -> list[tuple[str, list[Txn], list[Stored]]]:  # fmt: skip
    """(recipient group, its payments, the detected series of that recipient), for every
    outgoing e-Transfer recipient."""
    past = [t for t in txns if t.posted_date <= as_of]
    groups, _ = _groups(past, user, key)
    by_family: dict[str, list[Stored]] = defaultdict(list)
    for s in series:
        if s.kind == "e-transfer" and s.direction == "out":
            by_family[family_of(s.key)].append(s)
    return [
        (group_key, rows, by_family.get(group_key, []))
        for (group_key, _, kind, direction), rows in sorted(groups.items())
        if kind == "e-transfer" and direction == "out"
    ]


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
    four days or more), and whose series is not blocked (see `rent_blocked`)."""
    out = []
    for group_key, rows, family in _rent_groups(series, txns, user, key, as_of):
        result = _rent_for_group(group_key, rows, family, decisions, fingerprint, as_of)
        if isinstance(result, SettingProposal):
            out.append(result)
    return out


def _best_rent_group(
    series: Sequence[Stored], txns: Sequence[Txn], fingerprint: dict[int, str],
    user: UserDecisions | None, key: bytes | None, as_of: date, evidence: Collection[str],
) -> tuple[int, str, list[Txn], list[Stored]] | None:  # fmt: skip
    """The outgoing e-Transfer recipient whose payments overlap `evidence` most, as (overlap,
    group key, its payments, its detected series). None if none overlaps."""
    mine = set(evidence)
    best: tuple[int, str, list[Txn], list[Stored]] | None = None
    for group_key, rows, family in _rent_groups(series, txns, user, key, as_of):
        overlap = len(mine & {fingerprint[t.row_id] for t in rows})
        if overlap > (best[0] if best else 0):
            best = (overlap, group_key, rows, family)
    return best if best and best[0] >= 0.5 * max(len(mine), 1) else None


def rent_reason(
    series: Sequence[Stored], decisions: Sequence[Decision], txns: Sequence[Txn],
    fingerprint: dict[int, str], user: UserDecisions | None, key: bytes | None, as_of: date,
    evidence: Collection[str],
) -> str:  # fmt: skip
    """Why there is no rent proposal for the recipient whose payments include `evidence` (the
    fingerprints of a rent series of mine): a reason code from REASONS, or "proposal_made"."""
    best = _best_rent_group(series, txns, fingerprint, user, key, as_of, evidence)
    if best is None:
        return "no_candidate_series"
    _, group_key, rows, family = best
    result = _rent_for_group(group_key, rows, family, decisions, fingerprint, as_of)
    return "proposal_made" if isinstance(result, SettingProposal) else result


def rent_profile(
    series: Sequence[Stored], txns: Sequence[Txn], fingerprint: dict[int, str],
    user: UserDecisions | None, key: bytes | None, as_of: date, evidence: Collection[str],
) -> tuple[int, int, int] | None:  # fmt: skip
    """Counts only, for the recipient that `evidence` points to: (payments, of them at or above
    the rent floor, different months with such a payment). No amounts, dates or names."""
    best = _best_rent_group(series, txns, fingerprint, user, key, as_of, evidence)
    if best is None:
        return None
    rows = best[2]
    big = [t for t in rows if abs(t.amount_cents) >= MIN_RENT_CENTS]
    return len(rows), len(big), len({(t.posted_date.year, t.posted_date.month) for t in big})


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


def _link_pair_reason(
    old: Stored, new: Stored, day_of: dict[str, date], decisions: Sequence[Decision], as_of: date
) -> str | None:  # fmt: skip
    """Why `new` cannot be `old` under a new name (None: it can, before the one-to-one test)."""
    for s in (old, new):
        if not (
            s.kind == "merchant" and s.direction == "out" and s.amount_type == "fixed"
            and s.evidence and s.last_paid is not None and s.last_paid <= as_of
        ):  # fmt: skip
            return "not_fixed_merchant_series"
    for s in (old, new):
        reason = _blocked_reason(s, decisions, "link")
        if reason:
            return reason
    period = LINK_PERIOD_DAYS.get(old.cadence)
    first = _first_day(new, day_of)
    if period is None or first is None or new.cadence != old.cadence:
        return "different_cadence"
    if family_of(new.key) == family_of(old.key):
        return "same_merchant"
    gap = (first - old.last_paid).days  # type: ignore[operator]
    if not 0 < gap <= LINK_MAX_GAP_PERIODS * period:
        return "gap_not_in_range"
    a, b = abs(old.expected_cents), abs(new.expected_cents)
    if abs(a - b) > LINK_AMOUNT_TOLERANCE * max(a, b):
        return "amounts_differ"
    return None


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

    No proposal when either series is blocked (see `link_blocked`), when they overlap in time (two
    real subscriptions), when they are the same merchant (a price change, not a new name), or
    when the match is not one to one (a new series that could continue two old ones, or the
    other way round): prefer no proposal over a doubtful one."""
    day_of = {fingerprint[t.row_id]: t.posted_date for t in txns if t.posted_date <= as_of}
    candidates: list[tuple[Stored, Stored, int]] = []
    for old in series:
        for new in series:
            if new is old or _link_pair_reason(old, new, day_of, decisions, as_of) is not None:
                continue
            first = _first_day(new, day_of)
            candidates.append((old, new, (first - old.last_paid).days))  # type: ignore[operator]
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


def link_reason(
    series: Sequence[Stored], decisions: Sequence[Decision], txns: Sequence[Txn],
    fingerprint: dict[int, str], as_of: date, evidence: Collection[str],
) -> str:  # fmt: skip
    """Why there is no link proposal for the two names whose payments are `evidence` (a series of
    mine that a link joined, now separate again): a code from REASONS, or "proposal_made"."""
    mine = set(evidence)
    parts = [
        s for s in series if s.evidence and len(mine & set(s.evidence)) >= 0.5 * len(s.evidence)
    ]
    if len(parts) < 2:
        return "no_candidate_series"  # detection does not see two separate series there
    parts.sort(key=lambda s: (s.last_paid or date.min, s.key))
    old, new = parts[0], parts[-1]
    day_of = {fingerprint[t.row_id]: t.posted_date for t in txns if t.posted_date <= as_of}
    reason = _link_pair_reason(old, new, day_of, decisions, as_of)
    if reason is not None:
        return reason
    made = propose_links(series, decisions, txns, fingerprint, as_of)
    pair = {new.id, old.id}
    return (
        "proposal_made"
        if any({p.series_id, p.other_id} == pair for p in made)
        else "not_one_to_one"
    )


# --- possible: a monthly charge seen exactly twice ---

POSSIBLE_MIN_GAP, POSSIBLE_MAX_GAP = 25, 38  # days between the two charges
POSSIBLE_STALE_DAYS = 45  # the latest charge is no older than this
POSSIBLE_SAME_AMOUNT = 0.05  # the two amounts differ by at most 5 percent of the larger one
CARD_MIN_GAP, CARD_MAX_GAP = 28, 34  # paid by card: a calendar month apart


def _possible_for_group(
    merchant: str, rows: Sequence[Txn], card_rows: Collection[int], covered: Collection[str],
    added: Collection[str], fingerprint: dict[int, str], as_of: date,
) -> "SettingProposal | str":  # fmt: skip
    """A possible-subscription proposal for one merchant's payments, or the reason there is none."""
    if len(rows) != 2:
        return "not_two_payments"
    if merchant.casefold() in added:
        return "name_added_by_hand"
    first, second = sorted(rows, key=lambda t: (t.posted_date, t.row_id))
    a, b = abs(first.amount_cents), abs(second.amount_cents)
    gap = (second.posted_date - first.posted_date).days
    if min(a, b) < 100 or abs(a - b) > POSSIBLE_SAME_AMOUNT * max(a, b):
        return "amounts_differ"
    if not POSSIBLE_MIN_GAP <= gap <= POSSIBLE_MAX_GAP:
        return "gap_not_monthly"
    if all(t.row_id in card_rows for t in rows) and not CARD_MIN_GAP <= gap <= CARD_MAX_GAP:
        return "card_not_a_month_apart"  # paid by card: a calendar month, not just "about a month"
    if (as_of - second.posted_date).days > POSSIBLE_STALE_DAYS:
        return "outside_freshness_limit"
    prints = tuple(fingerprint[t.row_id] for t in (first, second))
    if any(fp in covered for fp in prints):
        return "already_in_a_series"
    return SettingProposal(
        kind="possible",
        series_id=f"possible|{prints[0]}",
        key=f"possible|{prints[0]}",
        title=f"{merchant} may be a monthly subscription (${b / 100:,.2f}, seen twice so far)",
        evidence=prints,
        confidence="possible",
        name=merchant,
        amount_cents=second.amount_cents,
        anchor=second.posted_date,
        cadence="monthly",
    )


def _possible_context(
    series: Sequence[Stored], decisions: Sequence[Decision], txns: Sequence[Txn],
    user: UserDecisions | None, key: bytes | None, as_of: date,
) -> tuple[list[tuple[str, list[Txn]]], set[int], set[str], set[str]]:  # fmt: skip
    past = [t for t in txns if t.posted_date <= as_of]
    groups, card_rows = _groups(past, user, key)
    covered = {fp for s in series for fp in s.evidence}
    added = {(s.name or "").casefold() for s in series if s.status == "user-added"}
    added |= {str(d.details.get("name", "")).casefold() for d in decisions if d.action == "add"}
    merchants = [
        (merchant, rows)
        for (_, merchant, kind, direction), rows in sorted(groups.items(), key=lambda kv: kv[0])
        if kind == "merchant" and direction == "out" and merchant is not None
    ]
    return merchants, card_rows, covered, added


def propose_possible(
    series: Sequence[Stored],
    decisions: Sequence[Decision],
    txns: Sequence[Txn],
    fingerprint: dict[int, str],
    user: UserDecisions | None,
    key: bytes | None,
    as_of: date,
) -> list[SettingProposal]:
    """A merchant with exactly two payments, about a month apart, of about the same amount (within
    5 percent of the larger, so a currency conversion passes), the latest still recent: a
    subscription that detection cannot call a series yet (it needs three). Always "possible",
    never confirmed by itself: two one-off purchases look the same. Nothing for a merchant that
    has any other payment, for payments that already belong to a series, or for a name I added by
    hand. Paid by card (most subscriptions are), the two must be a calendar month apart."""
    merchants, card_rows, covered, added = _possible_context(
        series, decisions, txns, user, key, as_of
    )
    out = []
    for merchant, rows in merchants:
        result = _possible_for_group(merchant, rows, card_rows, covered, added, fingerprint, as_of)
        if isinstance(result, SettingProposal):
            out.append(result)
    return out


def possible_reason(
    series: Sequence[Stored], decisions: Sequence[Decision], txns: Sequence[Txn],
    fingerprint: dict[int, str], user: UserDecisions | None, key: bytes | None, as_of: date,
    name: str,
) -> str:  # fmt: skip
    """Why there is no possible-subscription proposal for the merchant called `name`: a code from
    REASONS, or "proposal_made"."""
    merchants, card_rows, covered, added = _possible_context(
        series, decisions, txns, user, key, as_of
    )
    for merchant, rows in merchants:
        if merchant.casefold() == name.casefold():
            result = _possible_for_group(
                merchant, rows, card_rows, covered, added, fingerprint, as_of
            )
            return "proposal_made" if isinstance(result, SettingProposal) else result
    return "no_candidate_series"


# --- showing, accepting and dismissing ---


@dataclass(frozen=True)
class Inputs:
    """What the proposers read on one day: the rows, the series, the decisions made by then."""

    series: Sequence[Stored]
    decisions: Sequence[Decision]
    txns: Sequence[Txn]
    fingerprint: dict[int, str]
    user: UserDecisions | None
    day: date


def proposal_inputs(
    conn: sqlite3.Connection, account: str, key: bytes | None = None, as_of: date | None = None,
    series: Sequence[Stored] | None = None,
) -> Inputs | None:  # fmt: skip
    """The proposers' inputs on `as_of` (default: the account's last posting date): rows posted
    by then, the series and recurring decisions as of `as_of` (None: a live call uses every
    decision made). `series` (the live series, if the caller has them) saves a second detection;
    it is only valid for a live call. None if nothing was posted."""
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, (as_of or date.max).isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    if not txns:
        return None
    if series is None or as_of is not None:
        series, _ = rs.series_as_of(conn, account, as_of, key)
    return Inputs(
        series, rs.load_decisions(conn, account, as_of), txns, {r[0]: r[5] for r in raw},
        load_user_decisions(conn, as_of), as_of or max(t.posted_date for t in txns),
    )  # fmt: skip


def current_proposals(
    conn: sqlite3.Connection, account: str, key: bytes | None = None, as_of: date | None = None,
    series: Sequence[Stored] | None = None,
) -> list[SettingProposal]:  # fmt: skip
    """Every proposal on `as_of` (default: the account's last posting date), from the rows posted
    by then, the recurring decisions made by then and the dismissals made by then. Rent first,
    then links, then possible subscriptions. `series` (the live series, if the caller has them)
    saves a second detection; it is only valid for a live call (`as_of=None`)."""
    inputs = proposal_inputs(conn, account, key, as_of, series)
    if inputs is None:
        return []
    i = inputs
    found = [
        *propose_rent(i.series, i.decisions, i.txns, i.fingerprint, i.user, key, i.day),
        *propose_links(i.series, i.decisions, i.txns, i.fingerprint, i.day),
        *propose_possible(i.series, i.decisions, i.txns, i.fingerprint, i.user, key, i.day),
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


def describe(proposal: SettingProposal, hide_names: bool = False) -> str:
    """The sentence shown to me. With `hide_names` (the page is replacing names) no merchant
    name appears: only what kind of suggestion it is and the amounts."""
    if not hide_names:
        return proposal.title
    if proposal.kind == "link":
        return "One payment looks like another one under a new name"
    if proposal.kind == "possible" and proposal.amount_cents:
        amount = f"${abs(proposal.amount_cents) / 100:,.2f}"
        return f"A payment of {amount} may be a monthly subscription (seen twice so far)"
    return proposal.title
