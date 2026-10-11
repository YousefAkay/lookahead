"""Where a real account's forecast error comes from, summary numbers only.

Method in DECISIONS.md (2026-10-11, "Where the real account's error comes from"). At each origin
of the real backtest the app's forecast is split into parts and set against what happened in the
30 days after, by the same parts. The 30-day error and the lowest-balance error are both split.
Nothing is written to disk and nothing printed from a row: no date, text or name, only counts,
shares and dollar summaries.

    python -m eval.diagnose_real                 # your database (data/private)
    python -m eval.diagnose_real --db some.db --limit 10 --futures 100   # a quick look
"""

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest import HORIZON, KEEPS_NAMES, METHOD, realized_balances
from eval.backtest_real import real_origins
from eval.fix_variants import FIXES, fix_options
from lookahead.db import connect_readonly
from lookahead.flows import Flow, Txn, flow_types
from lookahead.forecast.path import DEFAULT_OPTIONS, ForecastOptions, build_forecast
from lookahead.forecast.simulate import BLOCK, _renewal_arrivals, pool_week_starts
from lookahead.forecast.spending import (
    DEFAULT_KEEPS,
    FEE_REASONS,
    FEE_REVERSAL,
    KEEPS_EARLIER,
    RETURN_REASONS,
    KeepsComing,
    _similar_earlier,
    _streams,
    big_cutoffs,
)
from lookahead.recurring_store import series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.ui import private_db_path
from lookahead.views import accounts

MATCH_DAYS = 10  # a placed payment and a real row of one series this close are the same payment
PARTS = (
    "recurring", "everyday", "irregular", "big_deposits", "big_payments", "keeps_in", "keeps_out",
    "fees", "netted", "other_credits", "other_debits", "unexplained", "leftover",
)  # fmt: skip
NAMES = {
    "recurring": "Recurring items",
    "everyday": "Everyday spending",
    "irregular": "Irregular deposits under the big cutoff",
    "big_deposits": "Big deposits (set aside, not forecast)",
    "big_payments": "Big payments (set aside, not forecast)",
    "keeps_in": "Big deposits that keep coming (random arrivals; empty unless switched on)",
    "keeps_out": "Big payments that keep coming (random arrivals; empty unless switched on)",
    "fees": "Bank fees",
    "netted": "Netted holds",
    "other_credits": "Other credits (not forecast)",
    "other_debits": "Other debits (not forecast)",
    "unexplained": "Unexplained by rows",
    "leftover": "Leftover (date jitter in the futures, rounding)",
    "low_point": "The low point (lowest of the futures against their mean)",
}
RECURRING_SPLIT = ("missed", "extra", "amount", "timing")
BANDS = ("under $200", "$200 to $500", "$500 to the big cutoff", "at or above the big cutoff")


def day_index(day: date, origin: date) -> int:
    """1 for the day after the origin ... 30 for the last forecast day."""
    return (day - origin).days


def cumulate(by_day: Mapping[int, float]) -> list[float]:
    """Cumulative sum over days 1..HORIZON."""
    out, total = [], 0.0
    for k in range(1, HORIZON + 1):
        total += by_day.get(k, 0.0)
        out.append(total)
    return out


def pool_expectation(
    values_by_day: Mapping[date, float], weeks: Sequence[date]
) -> dict[int, float]:
    """The expected value of a stream drawn week by week from `weeks`: day i of the horizon takes
    day i % 7 of a week drawn at random, so its expectation is the average of that weekday."""
    if not weeks:
        return {}
    return {
        i + 1: sum(values_by_day.get(w + timedelta(days=i % BLOCK), 0) for w in weeks) / len(weeks)
        for i in range(HORIZON)
    }


def cutoffs(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: set[int], as_of: date
) -> tuple[int, int]:
    """The big-deposit and big-payment cutoffs the forecaster uses at `as_of` (cents)."""
    return big_cutoffs(txns, flows, series_rows, as_of)


def part_of(t: Txn, flow: Flow, in_series: bool, cut_in: int, cut_out: int) -> str:
    """The part a row posted after the origin belongs to (the first rule that fits)."""
    if flow.netted:
        return "netted"
    if flow.reason in FEE_REASONS or flow.reason == FEE_REVERSAL:
        return "fees"
    if in_series:
        return "recurring"
    if t.amount_cents > 0:
        if flow.flow_type in ("income", "transfer"):
            return "big_deposits" if t.amount_cents >= cut_in else "irregular"
        return "everyday" if flow.reason in RETURN_REASONS else "other_credits"
    spends = flow.flow_type in ("everyday", "transfer") or flow.reason == "bill payment"
    if not spends:
        return "other_debits"
    return "big_payments" if -t.amount_cents >= cut_out else "everyday"


def match_recurring(
    placed: list[tuple[int, int, str]], actual: list[tuple[int, int, str]]
) -> tuple[
    list[tuple[tuple[int, int], tuple[int, int]]], list[tuple[int, int]], list[tuple[int, int]]
]:
    """Pair placed payments with real rows of the same series, nearest day first, within
    MATCH_DAYS. Items are (day index, cents, series key). Returns the matched pairs
    ((placed day, cents), (real day, cents)), the missed placed payments and the extra real rows."""
    candidates = sorted(
        (abs(p[0] - a[0]), i, j)
        for i, p in enumerate(placed)
        for j, a in enumerate(actual)
        if p[2] == a[2] and abs(p[0] - a[0]) <= MATCH_DAYS
    )  # fmt: skip
    used_p: set[int] = set()
    used_a: set[int] = set()
    pairs = []
    for _, i, j in candidates:
        if i in used_p or j in used_a:
            continue
        used_p.add(i)
        used_a.add(j)
        pairs.append(((placed[i][0], placed[i][1]), (actual[j][0], actual[j][1])))
    missed = [(p[0], p[1]) for i, p in enumerate(placed) if i not in used_p]
    extra = [(a[0], a[1]) for j, a in enumerate(actual) if j not in used_a]
    return pairs, missed, extra


def recurring_split(
    pairs: list[tuple[tuple[int, int], tuple[int, int]]], missed: list[tuple[int, int]],
    extra: list[tuple[int, int]], k: int,
) -> dict[str, float]:  # fmt: skip
    """The recurring part's error (expected minus happened) in the change up to day `k`, split.
    The four add up to it exactly."""
    return {
        "missed": float(sum(c for d, c in missed if d <= k)),
        "extra": float(-sum(c for d, c in extra if d <= k)),
        "amount": float(sum(p[1] - a[1] for p, a in pairs if a[0] <= k)),
        "timing": float(sum(p[1] * ((p[0] <= k) - (a[0] <= k)) for p, a in pairs)),
    }


def origin_record(
    conn: Any, account: str, origin: date, futures: int,
    keeps: KeepsComing | None = DEFAULT_KEEPS, options: ForecastOptions = DEFAULT_OPTIONS,
) -> dict[str, Any]:  # fmt: skip
    """The parts for one origin: expected and happened (cumulated by day), the two errors split.
    With `keeps` the forecast draws big money that keeps coming as random arrivals, and the rows
    that would have been such arrivals are counted in their own parts instead of as big."""
    forecast = build_forecast(conn, account, METHOD, origin, None, HORIZON, futures,
                              keep_paths=True, keeps=keeps, options=options)  # fmt: skip
    if forecast is None or forecast.simulation is None or forecast.inputs is None:
        raise ValueError("no forecast for this origin")
    sim, start = forecast.simulation, forecast.start
    if not sim.paths or len(sim.paths[0]) != HORIZON:
        raise ValueError("the futures were not kept")
    end = origin + timedelta(days=HORIZON)
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?", (account, end.isoformat()),
    ).fetchall()  # fmt: skip
    all_txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    row_of = {r[5]: r[0] for r in raw}
    txns = [t for t in all_txns if t.posted_date <= origin]
    user = load_user_decisions(conn, origin)
    flows = flow_types(txns, user)  # what the forecast at the origin saw
    flows_after = flow_types(all_txns, user)  # a label for what happened
    in_series = {
        row_of[fp] for s in forecast.series if s.status != "rejected" for fp in s.evidence
        if fp in row_of and row_of[fp] in {t.row_id for t in txns}
    }  # fmt: skip
    cut_in, cut_out = cutoffs(txns, flows, in_series, origin)
    streams = _streams(txns, flows, in_series, origin)
    weeks = pool_week_starts(start, forecast.inputs.history_start)
    spending: dict[date, float] = defaultdict(float)
    for t, _, cents in streams.spending:
        spending[t.posted_date] -= cents
    deposits: dict[date, float] = defaultdict(float)
    for t, _, cents in streams.deposits:
        deposits[t.posted_date] += cents
    placed_by_day: dict[int, float] = defaultdict(float)
    placed = []
    for item in forecast.items:
        k = day_index(item.day, origin)
        if 1 <= k <= HORIZON:
            placed_by_day[k] += item.cents
            placed.append((k, item.cents, item.series_key))
    expected = {
        "recurring": cumulate(placed_by_day),
        "everyday": cumulate(pool_expectation(spending, weeks)),
        "irregular": cumulate(pool_expectation(deposits, weeks)),
        "fees": cumulate({k: -forecast.fees_per_day for k in range(1, HORIZON + 1)}),
    }
    arrivals = forecast.arrivals
    if arrivals.in_sizes:
        mean_in = sum(arrivals.in_sizes) / len(arrivals.in_sizes)
        per_day = {k: arrivals.in_rate * mean_in for k in range(1, HORIZON + 1)}
        if arrivals.renewal == "draw" and arrivals.in_last is not None:
            # the renewal draw: the mean of the very draws the futures made (seed 0)
            draws = [_renewal_arrivals(0, f, arrivals.in_gaps, arrivals.in_last, start.first_day,
                                       arrivals.in_sizes, HORIZON)
                     for f in range(len(sim.paths))]  # fmt: skip
            per_day = {k: sum(d[k - 1] for d in draws) / len(draws)
                       for k in range(1, HORIZON + 1)}  # fmt: skip
        expected["keeps_in"] = cumulate(per_day)
    if arrivals.out_sizes:
        mean_out = sum(arrivals.out_sizes) / len(arrivals.out_sizes)
        expected["keeps_out"] = cumulate({k: -arrivals.out_rate * mean_out
                                          for k in range(1, HORIZON + 1)})  # fmt: skip
    for part in PARTS:
        expected.setdefault(part, [0.0] * HORIZON)
    series_after, _ = series_as_of(conn, account, end, None)
    key_of = {
        row_of[fp]: s.key for s in series_after if s.status != "rejected" for fp in s.evidence
        if fp in row_of
    }  # fmt: skip
    happened_by_day: dict[str, dict[int, float]] = {p: defaultdict(float) for p in PARTS}
    actual = []
    earlier_in: list[tuple[date, int]] = []
    earlier_out: list[tuple[date, int]] = []
    if keeps is not None:  # the rows a "keeps coming" test compares with, from all rows by then
        for t in all_txns:
            f = flows_after[t.row_id]
            if t.row_id in key_of or f.netted:
                continue
            if t.amount_cents > 0 and f.flow_type in ("income", "transfer"):
                if keeps.own_account or f.reason != "own-account transfer":
                    earlier_in.append((t.posted_date, t.amount_cents))
            elif t.amount_cents < 0 and (f.flow_type in ("everyday", "transfer")
                                         or f.reason == "bill payment"):  # fmt: skip
                earlier_out.append((t.posted_date, -t.amount_cents))
    for t in all_txns:
        if t.posted_date <= origin:
            continue
        flow = flows_after[t.row_id]
        part = part_of(t, flow, t.row_id in key_of, cut_in, cut_out)
        if keeps is not None and part in ("big_deposits", "big_payments"):
            own_ok = keeps.own_account or flow.reason != "own-account transfer"
            row = (t, flow, abs(t.amount_cents))
            if part == "big_deposits" and own_ok and arrivals.in_sizes:
                if _similar_earlier(row, earlier_in) >= KEEPS_EARLIER:
                    part = "keeps_in"
            elif part == "big_payments" and arrivals.out_sizes:
                if _similar_earlier(row, earlier_out) >= KEEPS_EARLIER:
                    part = "keeps_out"
        k = day_index(t.posted_date, origin)
        happened_by_day[part][k] += t.amount_cents
        if part == "recurring":
            actual.append((k, t.amount_cents, key_of[t.row_id]))
    happened = {p: cumulate(happened_by_day[p]) for p in PARTS}
    realized = realized_balances(conn, account, origin)
    mean_path = [sum(path[k] for path in sim.paths) / len(sim.paths) for k in range(HORIZON)]
    happened["unexplained"] = [
        (realized[k] - start.balance_cents)
        - sum(happened[p][k] for p in PARTS if p != "unexplained")
        for k in range(HORIZON)
    ]  # fmt: skip
    expected["leftover"] = [
        mean_path[k] - start.balance_cents - sum(expected[p][k] for p in PARTS if p != "leftover")
        for k in range(HORIZON)
    ]  # fmt: skip
    low_k = min(range(HORIZON), key=lambda k: (realized[k], k)) + 1
    pairs, missed, extra = match_recurring(placed, actual)
    at30 = HORIZON
    return {
        "in_shortfall": start.balance_cents < forecast.buffer_cents,
        "error_30": {p: expected[p][at30 - 1] - happened[p][at30 - 1] for p in PARTS},
        "error_low": {
            **{p: expected[p][low_k - 1] - happened[p][low_k - 1] for p in PARTS},
            "low_point": sim.median_lowest_cents - mean_path[low_k - 1],
        },
        "recurring_30": recurring_split(pairs, missed, extra, at30),
        "recurring_low": recurring_split(pairs, missed, extra, low_k),
        "total_30": mean_path[-1] - realized[-1],
        "median_30": sim.p50[-1] - realized[-1],
        "lowest_error": sim.median_lowest_cents - min(realized),
        "low_day": low_k,
        "n_placed": len(placed), "n_actual": len(actual), "n_matched": len(pairs),
        "n_missed": len(missed), "n_extra": len(extra),
        "date_off": [abs(p[0] - a[0]) for p, a in pairs if p[0] != a[0]],
        "expected_cum": {p: expected[p] for p in PARTS},
        "happened_cum": happened, "mean_path": mean_path,
        "realized": list(realized), "start_cents": start.balance_cents,
    }  # fmt: skip


# --- Irregular deposits: what the income looks like to the forecaster ---


def deposit_evidence(conn: Any, account: str, origins: Sequence[date]) -> dict[str, Any]:
    """Counts of the irregular deposits by size band, type and regularity (no row is kept)."""
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ?", (account,),
    ).fetchall()  # fmt: skip
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    last = max(t.posted_date for t in txns)
    first = min(t.posted_date for t in txns)
    series, _ = series_as_of(conn, account, last, None)
    row_of = {r[5]: r[0] for r in raw}
    in_series = {
        row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence if fp in row_of
    }
    flows = flow_types(txns, load_user_decisions(conn, last))
    cut_in, _ = cutoffs(txns, flows, in_series, last)
    rows = [
        (t, flows[t.row_id])
        for t in txns
        if t.amount_cents > 0 and t.row_id not in in_series and not flows[t.row_id].netted
        and flows[t.row_id].flow_type in ("income", "transfer")
    ]  # fmt: skip

    def band(cents: int) -> int:
        return 0 if cents < 20000 else 1 if cents < 50000 else 2 if cents < cut_in else 3

    out: dict[str, Any] = {"cutoff_is_floor": cut_in == 50000, "windows": {}}
    for label, since in (("last 365 days", last - timedelta(days=364)), ("whole history", first)):
        inside = [(t, f) for t, f in rows if t.posted_date >= since]
        total = sum(t.amount_cents for t, _ in inside) or 1
        weeks = max(1, ((last - since).days + 1) // 7)
        per_band = []
        for b in range(4):
            mine = [t for t, _ in inside if band(t.amount_cents) == b]
            days = sorted({t.posted_date for t in mine})
            gaps = [(y - x).days for x, y in zip(days, days[1:], strict=False)]
            weeks_with = {(t.posted_date - since).days // 7 for t in mine}
            windows = [
                any(o < t.posted_date <= o + timedelta(days=HORIZON) for t in mine)
                for o in origins if o >= since
            ]  # fmt: skip
            per_band.append({
                "count": len(mine), "share_money": sum(t.amount_cents for t in mine) / total,
                "share_weeks": len(weeks_with) / weeks,
                "share_windows": sum(windows) / len(windows) if windows else None,
                "median_gap": statistics.median(gaps) if gaps else None,
            })  # fmt: skip
        out["windows"][label] = {
            "rows": len(inside),
            "bands": per_band,
            "by_type": dict(Counter(f.reason for _, f in inside)),
        }
    return out


# --- Summary ---


def _d(cents: float) -> str:
    x = cents / 100
    return f"-${-x:,.0f}" if round(x) < 0 else f"${x:,.0f}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def summarize(records: list[dict[str, Any]], key: str, parts: Sequence[str]) -> list[str]:
    """One line per part: mean, median and mean absolute error, and how often it is the
    biggest absolute part."""
    if not records:
        return ["    (no origins)"]
    biggest: Counter[str] = Counter()
    for r in records:
        biggest[max(parts, key=lambda p: (abs(r[key][p]), p))] += 1
    lines = []
    for p in parts:
        errs = [r[key][p] for r in records]
        share = _pct(biggest[p] / len(records))
        mean, median = _d(statistics.fmean(errs)), _d(statistics.median(errs))
        absolute = _d(statistics.fmean(abs(e) for e in errs))
        lines.append(
            f"    {NAMES[p]}: mean {mean}, median {median}, mean absolute {absolute}, "
            f"the biggest at {biggest[p]} of {len(records)} origins ({share})"
        )
    return lines


def report(records: list[dict[str, Any]], title: str) -> list[str]:
    lines = [f"  {title}: {len(records)} origins"]
    if not records:
        return lines
    totals = [r["total_30"] for r in records]
    lines.append(
        f"  30-day change, the mean future minus what happened: mean "
        f"{_d(statistics.fmean(totals))}, median {_d(statistics.median(totals))} "
        f"(the middle future: mean "
        f"{_d(statistics.fmean(r['median_30'] for r in records))})"
    )  # fmt: skip
    lines.append(
        "  Parts of the 30-day error (expected minus happened; negative = too pessimistic):"
    )
    lines += summarize(records, "error_30", PARTS)
    lines.append("  The recurring part at day 30, split (mean over origins):")
    for name in RECURRING_SPLIT:
        lines.append(
            f"    {name}: {_d(statistics.fmean(r['recurring_30'][name] for r in records))}"
        )
    lows = [r["lowest_error"] for r in records]
    lines.append(
        f"  Lowest-balance error (median lowest of the futures minus the real lowest): mean "
        f"{_d(statistics.fmean(lows))}, median {_d(statistics.median(lows))}"
    )  # fmt: skip
    lines.append("  Parts of the lowest-balance error (at the day of the real lowest):")
    lines += summarize(records, "error_low", (*PARTS, "low_point"))
    lines.append("  The recurring part at the day of the real lowest, split (mean):")
    for name in RECURRING_SPLIT:
        lines.append(
            f"    {name}: {_d(statistics.fmean(r['recurring_low'][name] for r in records))}"
        )
    placed = sum(r["n_placed"] for r in records)
    actual = sum(r["n_actual"] for r in records)
    off = [d for r in records for d in r["date_off"]]
    lines.append(
        f"  Recurring payments over all origins: {placed} placed, {actual} real, "
        f"{sum(r['n_matched'] for r in records)} matched, {sum(r['n_missed'] for r in records)} "
        f"missed, {sum(r['n_extra'] for r in records)} extra; {len(off)} matched ones on another "
        f"day (mean {statistics.fmean(off) if off else 0:.1f} days off)"
    )  # fmt: skip
    return lines


def evidence_lines(ev: dict[str, Any]) -> list[str]:
    lines = ["  Irregular deposits (money in of a kind the forecaster counts, in no series):"]
    if ev["cutoff_is_floor"]:
        lines.append("    the big cutoff is the $500 floor, so the third band is empty")
    for label, w in ev["windows"].items():
        lines.append(f"    {label}: {w['rows']} deposits")
        for name, b in zip(BANDS, w["bands"], strict=True):
            lines.append(
                f"      {name}: {b['count']} deposits, {_pct(b['share_money'])} of the money in, "
                f"in {_pct(b['share_weeks'])} of weeks, followed by one within 30 days at "
                f"{_pct(b['share_windows'])} of origins, median gap "
                f"{'-' if b['median_gap'] is None else f'{b['median_gap']:.0f} days'}"
            )  # fmt: skip
        types = ", ".join(f"{k}: {v}" for k, v in sorted(w["by_type"].items()))
        lines.append(f"      by type: {types or '-'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.diagnose_real", description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--futures", type=int, default=500)
    parser.add_argument(
        "--limit", type=int, default=None, help="only every n-th origin (quick look)"
    )
    parser.add_argument("--keeps-coming", choices=sorted(KEEPS_NAMES), default="with-own",
                        help="big money that keeps coming forecast as random arrivals (the "
                             "default, with transfers from my own accounts counted), without "
                             "them, or 'off' for the old forecaster")  # fmt: skip
    parser.add_argument("--recurring-fix", choices=sorted(FIXES), default="both",
                        help="off: no fix; b: possibly ended not placed; a: weekly card series not "
                             "placed; both (the default)")  # fmt: skip
    parser.add_argument("--renewal", action="store_true",
                        help="the renewal draw of the keeps-coming deposits (an option, off by "
                             "default; DECISIONS.md, 2026-10-12)")  # fmt: skip
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    keeps = KEEPS_NAMES[args.keeps_coming]
    options = replace(fix_options(args.recurring_fix), renewal=args.renewal)
    conn = connect_readonly(db_path)
    try:
        for number, account in enumerate(accounts(conn), start=1):
            span = conn.execute(
                "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
                (account,),
            ).fetchone()
            if span[0] is None:
                continue
            days = real_origins(date.fromisoformat(span[0]), date.fromisoformat(span[1]))
            if args.limit:
                days = days[:: args.limit]
            print(f"account-{number}: {len(days)} origins, {args.futures} futures", flush=True)
            records = []
            for n, origin in enumerate(days, start=1):
                records.append(origin_record(conn, account, origin, args.futures, keeps, options))
                if n % 10 == 0:
                    print(f"  ... {n} of {len(days)}", flush=True)
            for line in report(records, "All origins"):
                print(line)
            ordinary = [r for r in records if not r["in_shortfall"]]
            for line in report(ordinary, "Origins not already below the buffer"):
                print(line)
            for line in evidence_lines(deposit_evidence(conn, account, days)):
                print(line)
            expected = statistics.fmean(r["expected_cum"]["irregular"][-1] for r in records)
            arrived = statistics.fmean(r["happened_cum"]["irregular"][-1] for r in records)
            print(
                f"  Irregular deposits per 30 days: expected from the weekly draw "
                f"{_d(expected)}, arrived {_d(arrived)} (means over origins)"
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
