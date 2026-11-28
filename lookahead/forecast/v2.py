"""Forecaster v2's parts (pre-registration docs/specs/2026-11-27-forecaster-v2-preregistration.md).
All off by default: with every option off the forecaster is v1 exactly.

- F, fees on their real dates (`fee_plan`): a fee charged within 2 days of the same day of the
  month (counted from the start or from the end of the month) in at least two of the last three
  calendar months is placed on that day in the months ahead, at its latest amount, in every
  future. Every other fee joins the everyday spending of its own day, so it is drawn with its
  past week. Only rows posted on or
  before the forecast date are read (rule zero).
- a, its own spending rate per future, lives in `simulate.week_draws` (each future first
  resamples the pool of past weeks).
- b, a stretch per day ahead (`stretch`): every future moves away from that day's median by a
  factor k(h) >= 1, the same for every account; k is fitted on development data.
- d, random-walk futures (`walk_windows`, `mix_walk`): a fixed share of the futures is replaced,
  from day 1, by the account's own balance changes over 1 to 30 days in the 365 days before the
  forecast date. Fewer than 365 days of history: no walk futures.
"""

import random
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from lookahead.flows import Flow, Txn
from lookahead.one_time import FEE_REASONS

FEE_MONTHS_BACK = 2  # the forecast date's month and the two calendar months before it
FEE_DAY_TOLERANCE = 2
FEE_MONTHS_NEEDED = 2
WALK_LOOKBACK_DAYS = 365
WALK_MIN_WINDOWS = 20  # as eval.baselines.MIN_WINDOWS


def _month_end(day: date) -> date:
    following = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def _days_from_end(day: date) -> int:
    return (_month_end(day) - day).days


def _same_day(a: date, b: date, way: str) -> bool:
    """Two dates within the tolerance of the same day of the month, counted from the start
    ("start") or from the end ("end") of the month."""
    if way == "start":
        return abs(a.day - b.day) <= FEE_DAY_TOLERANCE
    return abs(_days_from_end(a) - _days_from_end(b)) <= FEE_DAY_TOLERANCE


@dataclass(frozen=True)
class FeePlan:
    cumulative: tuple[int, ...]  # cents of placed fees from the first forecast day through day k
    extra_daily: Mapping[date, int] = field(default_factory=dict)  # fees drawn with their week
    placed: tuple[tuple[date, int], ...] = ()  # (day, cents) of each placed fee


def _fee_text(t: Txn) -> str:
    """The kind of a fee: its type and text, upper case, digits and extra spaces dropped (so a
    date or reference number in the text does not split one fee into many)."""
    text = f"{t.txn_type} {t.text}".upper()
    return " ".join("".join(c for c in text if not c.isdigit()).split())


def _window_start(as_of: date) -> date:
    """The first day of the calendar month two months before `as_of`'s month."""
    month = as_of.replace(day=1)
    for _ in range(FEE_MONTHS_BACK):
        month = (month - timedelta(days=1)).replace(day=1)
    return month


def _pattern(rows: Sequence[Txn], window_from: date) -> tuple[Txn, str, list[Txn]] | None:
    """The best repeating day among `rows` (one fee kind): for every fee in the window as an
    anchor, counted from the start and from the end of the month, the fees within the tolerance
    of it; it repeats if they fall in at least FEE_MONTHS_NEEDED calendar months of the window.
    Most months wins, then the latest anchor. Returns (the latest matched fee, the way, every
    matched fee of the kind at any date) or None."""
    recent = [t for t in rows if t.posted_date >= window_from]
    best: tuple[int, date, Txn, str, list[Txn]] | None = None
    for anchor in recent:
        for way in ("start", "end"):
            in_window = [t for t in recent if _same_day(t.posted_date, anchor.posted_date, way)]
            months = len({(t.posted_date.year, t.posted_date.month) for t in in_window})
            if months < FEE_MONTHS_NEEDED:
                continue
            latest = max(in_window, key=lambda t: (t.posted_date, t.row_id))
            key = (months, latest.posted_date)
            if best is None or key > best[:2]:
                matched = [t for t in rows if _same_day(t.posted_date, anchor.posted_date, way)]
                best = (months, latest.posted_date, latest, way, matched)
    return None if best is None else (best[2], best[3], best[4])


def fee_plan(
    txns: Sequence[Txn], flows: Mapping[int, Flow], as_of: date, first_day: date, horizon: int
) -> FeePlan:
    """F: which fees repeat on a day of the month (placed ahead at their latest amount), and the
    rest (drawn with their week). Fees are the rows whose reason is a bank fee; a fee reversal
    (money back) is drawn with its week as negative spending. The three months are calendar
    months: `as_of`'s month and the two before it (pre-registration, section 8)."""
    fees = [t for t in txns if t.posted_date <= as_of and t.amount_cents < 0
            and flows[t.row_id].reason in FEE_REASONS]  # fmt: skip
    reversals = [t for t in txns if t.posted_date <= as_of and t.amount_cents > 0
                 and flows[t.row_id].reason == "fee reversal"]  # fmt: skip
    window_from = _window_start(as_of)
    groups: dict[str, list[Txn]] = defaultdict(list)
    for t in fees:
        groups[_fee_text(t)].append(t)
    patterns: list[tuple[Txn, str]] = []
    drawn: list[Txn] = []
    for rows in groups.values():
        left = list(rows)
        while (found := _pattern(left, window_from)) is not None:  # a kind may repeat twice
            latest, way, matched = found
            patterns.append((latest, way))
            taken = {t.row_id for t in matched}
            left = [t for t in left if t.row_id not in taken]
        drawn += left
    placed: list[tuple[date, int]] = []
    last_day = first_day + timedelta(days=horizon - 1)
    for latest, way in patterns:
        month = first_day.replace(day=1)
        while month <= last_day:
            end = _month_end(month)
            if way == "start":
                day = month.replace(day=min(latest.posted_date.day, end.day))
            else:
                day = end - timedelta(days=_days_from_end(latest.posted_date))
            if first_day <= day <= last_day and day > latest.posted_date:
                placed.append((day, -latest.amount_cents))
            month = end + timedelta(days=1)
    per_day = [0] * horizon
    for day, cents in placed:
        per_day[(day - first_day).days] += cents
    cumulative, total = [], 0
    for cents in per_day:
        total += cents
        cumulative.append(total)
    extra: dict[date, int] = defaultdict(int)
    for t in drawn:
        extra[t.posted_date] += -t.amount_cents
    for t in reversals:
        extra[t.posted_date] -= t.amount_cents
    return FeePlan(tuple(cumulative), dict(extra), tuple(sorted(placed)))


# --- b: the stretch per day ahead ---


def _median(values: Sequence[int]) -> int:
    ordered = sorted(values)
    return ordered[round(0.5 * (len(ordered) - 1))]  # the simulator's nearest-rank percentile


def stretch(paths: list[list[int]], k: Sequence[float]) -> list[list[int]]:
    """Each future moved away from that day's median by k(h): m + k(h) (x - m), rounded to the
    cent. k(h) below 1 is refused (b only widens); an empty `k` changes nothing."""
    if not k or not paths:
        return paths
    if any(x < 1 for x in k):
        raise ValueError("a stretch factor is never below 1")
    horizon = len(paths[0])
    if len(k) < horizon:
        raise ValueError(f"{len(k)} stretch factors for {horizon} days")
    medians = [_median([p[h] for p in paths]) for h in range(horizon)]
    return [[round(medians[h] + k[h] * (p[h] - medians[h])) for h in range(horizon)]
            for p in paths]  # fmt: skip


# --- d: random-walk futures ---


def closing_balances(conn: sqlite3.Connection, account: str, as_of: date) -> list[int]:
    """The closing balance of each day from the first posting to `as_of` (as
    eval.baselines.balance_series; rows after `as_of` are never read)."""
    rows = conn.execute(
        "SELECT posted_date, balance_after_cents FROM transactions WHERE account = ?"
        " AND posted_date <= ? ORDER BY posted_date, id",
        (account, as_of.isoformat()),
    ).fetchall()
    if not rows:
        return []
    closing: dict[date, int] = {}
    for day, balance in rows:  # the first row stored for a day is its last posting
        closing.setdefault(date.fromisoformat(day), int(balance))
    first = min(closing)
    out, carried = [], closing[first]
    for k in range((as_of - first).days + 1):
        carried = closing.get(first + timedelta(days=k), carried)
        out.append(carried)
    return out


def walk_windows(
    closing: Sequence[int], horizon: int, history_days: int
) -> tuple[tuple[int, ...], ...]:  # fmt: skip
    """The walk's paths: the forecast date's balance (the last of `closing`, one closing balance a
    day up to that date) plus each past window's change since its first day, for windows in the
    365 days before the date (as eval.baselines.random_walk_paths). Empty with fewer than 365
    days of history or too few windows."""
    if history_days < WALK_LOOKBACK_DAYS or not closing:
        return ()
    n = len(closing)
    first_start = max(0, n - 1 - WALK_LOOKBACK_DAYS)
    starts = list(range(first_start, n - 1 - horizon + 1))
    if len(starts) < WALK_MIN_WINDOWS:
        return ()
    origin = closing[-1]
    return tuple(tuple(origin + closing[s + h] - closing[s] for h in range(1, horizon + 1))
                 for s in starts)  # fmt: skip


def mix_walk(
    paths: list[list[int]], windows: Sequence[Sequence[int]], share: float, seed: int
) -> list[list[int]]:  # fmt: skip
    """The first round(share x futures) futures replaced from day 1 by walk paths, each drawn
    from its own stream (the seed and the future's number). No windows or a share of 0: no
    change."""
    count = round(share * len(paths))
    if not windows or count <= 0:
        return paths
    out = list(paths)
    for f in range(count):
        stream = random.Random(f"{seed}|{f}|walk")
        out[f] = list(windows[stream.randrange(len(windows))][: len(paths[f])])
    return out
