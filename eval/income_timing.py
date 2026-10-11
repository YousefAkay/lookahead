"""Does the timing of my big deposits depend on the time since the last one? Counts and shares only.

Method in DECISIONS.md (2026-10-12, "Does the timing of my big deposits depend on the time since
the last one?"). A read-only diagnostic: it changes no forecast. For two populations of deposits,
read the way the forecaster would at each weekly origin (rows posted by then, series and decisions
as of then): the recurring-size deposits keeps-coming draws as random arrivals, and all deposits of
$500 or more in no series. It prints the chance that the next one comes within 7 days by the days
since the previous one, next to the chance the current constant-rate model implies, and the gaps
between consecutive deposits. No row, date, name or amount is printed, and nothing is written.

    python -m eval.income_timing                 # your database (data/private)
    python -m eval.income_timing --db some.db --limit 3
"""

import argparse
import sqlite3
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from eval.backtest_real import real_origins
from eval.small_n import MIN_GROUP, MIN_STATISTIC, TOO_FEW
from lookahead.db import connect_readonly
from lookahead.flows import Txn, flow_types
from lookahead.forecast.spending import DEFAULT_KEEPS, keeps_coming_arrivals
from lookahead.merchants import is_private_path
from lookahead.recurring_store import series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.ui import private_db_path
from lookahead.views import accounts

WINDOW_DAYS = 7  # "the next deposit comes within 7 days"
MIN_CENTS = 50000  # population 2: $500 or more
RATE_DAYS = 365  # the constant rate is the deposits of the last 365 days over the days observed
MIN_GAPS = 6  # the renewal draw needs this many past gaps (the proposal's default)
MIN_LONGER = 2  # ... and this many longer than the days already waited
GROUP_EDGES = (7, 14, 21, 28, 35)  # days since the previous deposit: 0-7, 8-14, ..., 36+
GROUP_NAMES = ("0-7 days", "8-14 days", "15-21 days", "22-28 days", "29-35 days", "36+ days")
POPULATIONS = {
    "recurring-size deposits (what keeps-coming draws)": "keeps",
    "all deposits of $500 or more in no series": "all",
}
TYPES = ("incoming e-Transfer", "own-account transfer", "credit memo")
MIN_TYPE_DEPOSITS = 15


@dataclass(frozen=True, order=True)
class Dep:
    day: date
    row_id: int
    reason: str


@dataclass(frozen=True)
class Obs:
    days_since: int  # from the previous deposit of the population to the origin
    event: bool  # one came in the next 7 days
    implied: float  # the chance the constant-rate model gives for it
    renewal: float = (
        0.0  # the chance the renewal draw gives for it (the constant rate on a fallback)
    )
    fallback: bool = True  # the renewal draw had too few gaps and fell back to the constant rate


def group_of(days: int) -> int:
    """0 to 5: which group of days since the previous deposit."""
    return sum(days > edge for edge in GROUP_EDGES)


def implied_chance(rate: float, days: int = WINDOW_DAYS) -> float:
    """The chance of at least one arrival in `days` days, each day having one with chance `rate`."""
    return 1 - (1 - rate) ** days


def past_gaps(deps: Sequence[Dep], origin: date) -> list[int]:
    """The days between consecutive distinct posting days of the deposits of the last 365 days up
    to `origin` (several deposits on one day are one arrival day)."""
    start = origin - timedelta(days=RATE_DAYS - 1)
    days = sorted({d.day for d in deps if start <= d.day <= origin})
    return [(b - a).days for a, b in zip(days, days[1:], strict=False)]


def renewal_chance(
    gaps: Sequence[int], waited: int, constant: float, min_gaps: int = MIN_GAPS
) -> tuple[float, bool]:  # fmt: skip
    """The chance the renewal draw gives for an arrival in the next 7 days, and whether it fell back
    to the constant rate. After `waited` days without a deposit the next gap is drawn from the past
    gaps longer than `waited`; the arrival is within 7 days if that gap is at most waited + 7.
    Too few gaps (under `min_gaps`), or under 2 longer than `waited`: the constant rate."""
    longer = [g for g in gaps if g > waited]
    if len(gaps) < min_gaps or len(longer) < MIN_LONGER:
        return constant, True
    return sum(1 for g in longer if g <= waited + WINDOW_DAYS) / len(longer), False


def rate_at(deps: Sequence[Dep], origin: date, first_posted: date) -> float:
    """Deposits of the last 365 days up to `origin`, over the days observed in that window."""
    start = max(first_posted, origin - timedelta(days=RATE_DAYS - 1))
    days = (origin - start).days + 1
    return sum(1 for d in deps if start <= d.day <= origin) / days


def populations_at(
    conn: sqlite3.Connection, account: str, txns_all: Sequence[Txn], row_of: dict[str, int],
    day: date,
) -> dict[str, list[Dep]]:  # fmt: skip
    """The two populations as the forecaster sees them on `day`: only rows posted by then."""
    txns = [t for t in txns_all if t.posted_date <= day]
    series, _ = series_as_of(conn, account, day, None)
    in_series = {
        row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence if fp in row_of
    }
    flows = flow_types(txns, load_user_decisions(conn, day))
    arrivals = keeps_coming_arrivals(txns, flows, in_series, day, DEFAULT_KEEPS)
    by_id = {t.row_id: t for t in txns}
    keeps = [Dep(by_id[i].posted_date, i, flows[i].reason)
             for i in arrivals.members if by_id[i].amount_cents > 0]  # fmt: skip
    everything = [
        Dep(t.posted_date, t.row_id, flows[t.row_id].reason)
        for t in txns
        if t.amount_cents >= MIN_CENTS and t.row_id not in in_series
        and not flows[t.row_id].netted and flows[t.row_id].flow_type in ("income", "transfer")
    ]  # fmt: skip
    return {"keeps": sorted(keeps), "all": sorted(everything)}


def observations(
    pops: dict[date, dict[str, list[Dep]]], origins: Sequence[date], first_posted: date,
    name: str, kind: str | None = None, min_gaps: int = MIN_GAPS,
) -> tuple[list[Obs], int]:  # fmt: skip
    """One observation per origin that has a previous deposit, and the number that have none."""
    out, without = [], 0
    for t in origins:
        now = [d for d in pops[t][name] if kind is None or d.reason == kind]
        later = [d for d in pops[t + timedelta(days=WINDOW_DAYS)][name]
                 if kind is None or d.reason == kind]  # fmt: skip
        if not now:
            without += 1
            continue
        event = any(t < d.day <= t + timedelta(days=WINDOW_DAYS) for d in later)
        waited = (t - max(d.day for d in now)).days
        constant = implied_chance(rate_at(now, t, first_posted))
        renewal, fallback = renewal_chance(past_gaps(now, t), waited, constant, min_gaps)
        out.append(Obs(waited, event, constant, renewal, fallback))
    return out, without


def chance_lines(obs: list[Obs], without: int) -> list[str]:
    lines = [f"      origins with a previous deposit: {len(obs)}; with none yet: {without}"]
    if not obs:
        return lines
    on_draw = sum(not o.fallback for o in obs)
    lines.append(f"      origins on the renewal draw: {on_draw}; on the constant-rate fallback: "
                 f"{len(obs) - on_draw}")  # fmt: skip
    lines.append("      days since the previous deposit: n / came within 7 days / the constant "
                 "rate implies / the renewal draw implies")  # fmt: skip
    miss_constant = miss_renewal = 0.0
    for g, label in enumerate(GROUP_NAMES):
        mine = [o for o in obs if group_of(o.days_since) == g]
        if len(mine) < MIN_GROUP:
            lines.append(f"        {label}: {TOO_FEW}")
            continue
        share = sum(o.event for o in mine) / len(mine)
        constant = statistics.fmean(o.implied for o in mine)
        renewal = statistics.fmean(o.renewal for o in mine)
        miss_constant += len(mine) * abs(constant - share)
        miss_renewal += len(mine) * abs(renewal - share)
        lines.append(
            f"        {label}: n {len(mine)} / {share:.0%} / {constant:.0%} / {renewal:.0%}"
        )
    if len(obs) < MIN_GROUP:
        return [*lines, f"        all: {TOO_FEW}"]
    share = sum(o.event for o in obs) / len(obs)
    lines.append(f"        all: n {len(obs)} / {share:.0%} / "
                 f"{statistics.fmean(o.implied for o in obs):.0%} / "
                 f"{statistics.fmean(o.renewal for o in obs):.0%}")  # fmt: skip
    lines.append(f"      mean absolute difference from the real share, over the groups weighted by "
                 f"n: constant rate {miss_constant / len(obs):.0%}, renewal draw "
                 f"{miss_renewal / len(obs):.0%}")  # fmt: skip
    return lines


def gap_lines(deps: Sequence[Dep]) -> list[str]:
    days = sorted(d.day for d in deps)
    gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
    if len(gaps) < MIN_STATISTIC:
        return [f"      gaps between consecutive deposits: {TOO_FEW}"]
    q1, q2, q3 = statistics.quantiles(gaps, n=4)
    within = sum(1 for g in gaps if 20 <= g <= 35) / len(gaps)
    return [f"      gaps between consecutive deposits: n {len(gaps)}; median {q2:.0f} days, "
            f"quartiles {q1:.0f} and {q3:.0f}; {within:.0%} of them 20 to 35 days"]  # fmt: skip


def report(
    pops: dict[date, dict[str, list[Dep]]], origins: Sequence[date], first_posted: date,
    min_gaps: int = MIN_GAPS,
) -> list[str]:  # fmt: skip
    lines = []
    for label, name in POPULATIONS.items():
        lines.append(f"    {label}:")
        seen: dict[int, Dep] = {}
        for by_name in pops.values():
            for d in by_name[name]:
                seen[d.row_id] = d
        union = sorted(seen.values())
        lines.append("      deposits ever in the population: "
                     f"{len(union) if len(union) >= MIN_GROUP else TOO_FEW}")  # fmt: skip
        obs, without = observations(pops, origins, first_posted, name, None, min_gaps)
        lines += chance_lines(obs, without)
        lines += gap_lines(union)
        for kind in TYPES:
            mine = [d for d in union if d.reason == kind]
            lines.append(
                f"      by type, {kind}: "
                f"{f'{len(mine)} deposits' if len(mine) >= MIN_GROUP else TOO_FEW}"
            )
            if len(mine) < MIN_TYPE_DEPOSITS:
                lines.append("        too few for a table")
                continue
            t_obs, t_without = observations(pops, origins, first_posted, name, kind, min_gaps)
            lines += [f"  {line}" for line in chance_lines(t_obs, t_without)]
            lines += [f"  {line}" for line in gap_lines(mine)]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.income_timing", description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="only every n-th origin")
    parser.add_argument("--min-gaps", type=int, default=MIN_GAPS,
                        help="past gaps the renewal draw needs (synthetic databases only; my own "
                             "account always uses the default)")  # fmt: skip
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    if args.min_gaps != MIN_GAPS and is_private_path(db_path):
        print(f"A minimum other than {MIN_GAPS} is for synthetic databases only: it is not tuned "
              f"on a real account.")  # fmt: skip
        return 1
    conn = connect_readonly(db_path)
    try:
        for number, account in enumerate(accounts(conn), start=1):
            raw = conn.execute(
                "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
                " FROM transactions WHERE account = ?", (account,),
            ).fetchall()  # fmt: skip
            if not raw:
                continue
            txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
            row_of = {r[5]: r[0] for r in raw}
            first = min(t.posted_date for t in txns)
            origins = real_origins(first, max(t.posted_date for t in txns))
            if args.limit:
                origins = origins[:: args.limit]
            dates = sorted({*origins, *(o + timedelta(days=WINDOW_DAYS) for o in origins)})
            print(f"account-{number}: {len(origins)} origins ({len(dates)} dates read)", flush=True)
            pops = {}
            for n, day in enumerate(dates, start=1):
                pops[day] = populations_at(conn, account, txns, row_of, day)
                if n % 25 == 0:
                    print(f"  ... {n}", flush=True)
            for line in report(pops, origins, first, args.min_gaps):
                print(line)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
