"""Experiment 1 of the 2026-10-17 plan: proper baselines and scores (synthetic development only).

Method in DECISIONS.md (2026-10-17, "Plan to improve the forecaster"). For every origin of every
development persona the default forecast is made again, this time keeping every future's path, so
that three things can be saved with the record: 19 quantiles per day (5 to 95 percent), the exact
CRPS of the forecast on each of the 30 days, and the same two numbers for two baselines:

* "balance stays": the balance at the origin, as a single value (a point mass);
* the random walk: the balance at the origin plus the 30-day paths of balance change in the
  windows of the 365 days before the origin that ended on or before it (rule zero: no window
  reaches past the origin), giving a band and a dip chance. It is defined only when at least 20
  windows exist.

CRPS (continuous ranked probability score) is the average distance of the forecast distribution
from what happened, in cents: lower is better, and for a single value it is the absolute error.

    python -m eval.baselines --quick              # 2 personas, 4 origins, 100 futures
    python -m eval.baselines --set 12 --jobs 2    # the 20 development personas, resumable
    python -m eval.baselines --set long --jobs 2
    python -m eval.baselines --report             # writes docs/results/baselines_2026-10-17.md

Records go to data/backtest/exp1/<set>/ (not committed). Held-out personas are refused.
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import (
    FUTURES,
    HORIZON,
    METHOD,
    QUICK_FUTURES,
    _check_settings,
    current_default_dir,
    origins,
    quick_origins,
    run_origin_with_paths,
    version_settings,
)
from eval.metrics import alert_metrics, load_records
from eval.personas_db import persona_db
from eval.thresholds import THRESHOLDS
from eval.thresholds_raw import choose, curve
from lookahead.forecast.path import DEFAULT_OPTIONS, forecaster_settings
from lookahead.generator.archetypes import assert_development_only, development_ids, is_long

OUT = Path("data/backtest/exp1")
REPORT = Path("docs/results/baselines_2026-10-17.md")
QUANTILES = tuple(k / 100 for k in range(5, 100, 5))  # 5, 10, ..., 95 percent
LOOKBACK_DAYS = 365
MIN_WINDOWS = 20  # fewer windows than this: no random walk for the origin
BINS = (0.0, 0.01, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 1.0001)  # the reliability curve
PLAN = "2026-10-17 experiment 1"

Record = dict[str, Any]


# --- Scores ---


def crps_samples(samples: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """Exact CRPS of an equally weighted sample at each column.

    `samples` is (n, d): n draws for each of d days; `observed` has d values. CRPS = mean |x - y|
    minus half the mean |x - x'| over pairs of draws; with the draws sorted the second term is
    sum((2i - n + 1) x_i) / n^2. One draw makes it the absolute error."""
    x = np.sort(np.asarray(samples, dtype=float), axis=0)
    n = x.shape[0]
    y = np.asarray(observed, dtype=float)
    first = np.abs(x - y).mean(axis=0)
    weights = (2 * np.arange(n) - n + 1)[:, None]
    second = (weights * x).sum(axis=0) / n**2
    return first - second


def balance_series(conn: sqlite3.Connection, account: str, origin: date) -> list[int]:
    """The closing balance of each day from the first posting to the origin (days without
    postings keep the balance before them). Rows after the origin are never read."""
    rows = conn.execute(
        "SELECT posted_date, balance_after_cents FROM transactions WHERE account = ?"
        " AND posted_date <= ? ORDER BY posted_date, id",
        (account, origin.isoformat()),
    ).fetchall()
    if not rows:
        return []
    closing: dict[date, int] = {}
    for day, balance in rows:  # the first row stored for a day is its last posting
        closing.setdefault(date.fromisoformat(day), int(balance))
    first = min(closing)
    out, carried = [], closing[first]
    for k in range((origin - first).days + 1):
        carried = closing.get(first + timedelta(days=k), carried)
        out.append(carried)
    return out


def random_walk_paths(
    series: Sequence[int], horizon: int = HORIZON, lookback: int = LOOKBACK_DAYS,
    minimum: int = MIN_WINDOWS,
) -> np.ndarray | None:  # fmt: skip
    """The random walk's draws: (windows, horizon) balances, the last balance of `series` (the
    origin's) plus each past window's change since the window's first day. A window starts on a
    day s and covers days s+1 .. s+horizon, all on or before the origin (the last element of
    `series`); only the `lookback` days before the origin are used. None if fewer than
    `minimum` windows fit."""
    n = len(series)  # series[n - 1] is the origin
    first_start = max(0, n - 1 - lookback)
    starts = list(range(first_start, n - 1 - horizon + 1))  # s + horizon <= n - 1
    if len(starts) < minimum:
        return None
    values = np.asarray(series, dtype=float)
    deltas = np.stack([values[s + 1 : s + 1 + horizon] - values[s] for s in starts])
    return values[-1] + deltas


def dip_share(paths: np.ndarray, buffer_cents: int) -> float:
    """The share of draws that are below the buffer on some day."""
    return float((paths.min(axis=1) < buffer_cents).mean())


# --- The run ---


def scored_record(
    conn: sqlite3.Connection, pid: str, origin: date, futures: int, archetype: str
) -> Record:  # fmt: skip
    """The default forecast at `origin` with its paths, and the extra numbers of this experiment."""
    record, paths = run_origin_with_paths(
        conn, pid, "synthetic", origin, futures, archetype, False, keep_paths=True,
        options=DEFAULT_OPTIONS,
    )  # fmt: skip
    draws = np.asarray(paths, dtype=float)  # (futures, days)
    realized = np.asarray(record["realized"], dtype=float)
    start = float(record["start_balance_cents"])
    record["quantiles"] = np.rint(np.quantile(draws, QUANTILES, axis=0)).astype(int).tolist()
    record["crps_forecast"] = crps_samples(draws, realized).round(1).tolist()
    record["crps_stays"] = np.abs(start - realized).round(1).tolist()
    walk = random_walk_paths(balance_series(conn, pid, origin))
    record["walk_windows"] = 0 if walk is None else len(walk)
    if walk is None:
        record["crps_walk"] = None
        record["walk"] = None
    else:
        record["crps_walk"] = crps_samples(walk, realized).round(1).tolist()
        record["walk"] = {
            "shortfall_probability": dip_share(walk, record["buffer_cents"]),
            "p50": np.rint(np.median(walk, axis=0)).astype(int).tolist(),
            "median_lowest_cents": int(np.rint(np.median(walk.min(axis=1)))),
            "p10": np.rint(np.quantile(walk, 0.10, axis=0)).astype(int).tolist(),
            "p90": np.rint(np.quantile(walk, 0.90, axis=0)).astype(int).tolist(),
        }
    return record


def run_persona(
    pid: str, out_dir: Path, days: list[date], futures: int, seed: int = 42,
    work: Path | None = None,
) -> int:  # fmt: skip
    """The origins `days` of one development persona, saved as they are made (resumable)."""
    assert_development_only([pid])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{pid}.jsonl"
    settings = {"futures": futures, "seed": seed, "method": METHOD, "plan": PLAN,
                "quantiles": list(QUANTILES), "lookback": LOOKBACK_DAYS,
                "min_windows": MIN_WINDOWS, **version_settings(pid),
                "forecaster": forecaster_settings(DEFAULT_OPTIONS)}  # fmt: skip
    _check_settings(out_dir / f"{pid}.meta.json", settings)
    done: set[str] = set()
    if path.exists():
        done = {json.loads(line)["origin"] for line in path.read_text("utf-8").splitlines() if line}
    todo = [d for d in days if d.isoformat() not in done]
    if not todo:
        return 0
    with persona_db(pid, work or out_dir / "work", seed) as db, path.open("a") as out:
        for origin in todo:
            record = scored_record(db.conn, pid, origin, futures, db.persona.archetype)
            out.write(json.dumps(record) + "\n")
            out.flush()
    return len(todo)


def _job(args: tuple[str, Path, bool, int]) -> str:
    pid, out_dir, quick, futures = args
    days = quick_origins() if quick else origins()
    n = run_persona(pid, out_dir, days, futures)
    return f"{pid}: {n} new origins"


# --- The report ---


def load(long: bool) -> list[Record]:
    """Every record of a set; refuses held-out personas and mixed sets."""
    folder = OUT / ("long" if long else "12")
    records = []
    for path in sorted(folder.glob("*.jsonl")):
        records += [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]
    assert_development_only(sorted({r["persona"] for r in records}))
    assert all(is_long(r["persona"]) == long for r in records)
    return records


def usable(records: list[Record]) -> list[Record]:
    """Ordinary origins (not already in shortfall) where the random walk is defined."""
    return [r for r in records if not r["in_shortfall_at_origin"] and r["walk"] is not None]


def event(r: Record) -> float:
    return 0.0 if r["realized_first_below_buffer"] is None else 1.0


def brier(pairs: list[tuple[float, float]]) -> float | None:
    return float(np.mean([(p - o) ** 2 for p, o in pairs])) if pairs else None


def skill(score: float | None, reference: float | None) -> float | None:
    if score is None or not reference:
        return None
    return 1 - score / reference


def by_archetype(records: list[Record]) -> dict[str, list[Record]]:
    out: dict[str, list[Record]] = {}
    for r in records:
        out.setdefault(r["archetype"], []).append(r)
    return dict(sorted(out.items()))


def archetype_scores(rows: list[Record]) -> Record:
    """CRPS (mean over the 30 days, and on day 30) of the three methods, and Brier scores."""

    def mean_crps(key: str, day: int | None = None) -> float:
        if day is None:
            return float(np.mean([np.mean(r[key]) for r in rows]))
        return float(np.mean([r[key][day] for r in rows]))

    out: Record = {"n": len(rows), "personas": len({r["persona"] for r in rows}),
                   "events": int(sum(event(r) for r in rows))}  # fmt: skip
    for tag, key in (("f", "crps_forecast"), ("n", "crps_stays"), ("r", "crps_walk")):
        out[f"crps_{tag}"] = mean_crps(key) / 100
        out[f"crps_{tag}_30"] = mean_crps(key, HORIZON - 1) / 100
    out["brier_f"] = brier([(r["shortfall_probability"], event(r)) for r in rows])
    out["brier_n"] = brier([(0.0, event(r)) for r in rows])  # balance stays: never dips
    out["brier_r"] = brier([(r["walk"]["shortfall_probability"], event(r)) for r in rows])
    return out


def reliability(rows: list[Record], key: str) -> list[Record]:
    """Per bin of the predicted dip chance: n, the mean prediction, the share that dipped."""
    pairs = [(r["shortfall_probability"] if key == "f" else r["walk"]["shortfall_probability"],
              event(r)) for r in rows]  # fmt: skip
    out = []
    for lo, hi in zip(BINS[:-1], BINS[1:], strict=True):
        part = [(p, o) for p, o in pairs if lo <= p < hi]
        out.append({"lo": lo, "hi": min(hi, 1.0), "n": len(part),
                    "mean_p": float(np.mean([p for p, _ in part])) if part else None,
                    "seen": float(np.mean([o for _, o in part])) if part else None})  # fmt: skip
    return out


def walk_records(rows: list[Record]) -> list[Record]:
    """The records as the random walk would have made them, for the alert machinery."""
    return [{**r, **r["walk"]} for r in rows]


def stays_records(rows: list[Record]) -> list[Record]:
    """Balance stays: the dip chance is 0 at every origin that is not already in shortfall."""
    return [{**r, "shortfall_probability": 0.0, "median_lowest_cents": r["start_balance_cents"]}
            for r in rows]  # fmt: skip


def alerts_row(rows: list[Record], threshold: float | None) -> str:
    if threshold is None:
        return "no threshold meets the bar"
    m = alert_metrics(rows, threshold, median_rule=False)
    recall = "-" if not m["shortfall_episodes"] else f"{m['caught_on']}/{m['shortfall_episodes']}"
    precision = "-" if m["precision"] is None else f"{m['precision']:.0%}"
    return (
        f"recall on ahead {recall}; precision {precision} "
        f"({m['alert_episodes'] - m['false_alerts']}"
        f"/{m['alert_episodes']}); {m['false_per_30_days'] or 0:.3f} false per 30 days"
    )


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1%}" if abs(x) < 10 else "n/a"


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def section(title: str, records: list[Record], reference: list[Record]) -> list[str]:
    ordinary = [r for r in records if not r["in_shortfall_at_origin"]]
    rows = usable(records)
    out = [f"## {title}", ""]
    same = 0
    ref = {(r["persona"], r["origin"]): r for r in reference}
    for r in records:
        b = ref.get((r["persona"], r["origin"]))
        same += bool(b) and all(b[k] == r[k] for k in ("p10", "p50", "p90", "shortfall_probability",
                                                       "realized"))  # fmt: skip
    out += [
        f"Origins: {len(records)}; ordinary (not already below the buffer): {len(ordinary)}; "
        f"with a random walk (at least {MIN_WINDOWS} windows): {len(rows)}. All scores below "
        f"are on those {len(rows)}. This rerun equals the saved default (arm 0) at "
        f"{same} of {len(records)} records (p10, p50, p90, dip chance, realized).",
        "",
    ]
    groups = by_archetype(rows)
    scores = {a: archetype_scores(g) for a, g in groups.items()}
    out += [
        "### CRPS (mean over the 30 days, in dollars; lower is better) and skill",
        "",
        "| Archetype (n origins, personas) | forecast | balance stays | random walk | "
        "skill vs stays | skill vs walk | day 30: forecast / stays / walk |",
        "|---|---|---|---|---|---|---|",
    ]
    for a, s in scores.items():
        out.append(
            f"| {a} ({s['n']}, {s['personas']}) | {_usd(s['crps_f'])} | {_usd(s['crps_n'])} | "
            f"{_usd(s['crps_r'])} | {_pct(skill(s['crps_f'], s['crps_n']))} | "
            f"{_pct(skill(s['crps_f'], s['crps_r']))} | {_usd(s['crps_f_30'])} / "
            f"{_usd(s['crps_n_30'])} / {_usd(s['crps_r_30'])} |"
        )
    sk_n = [skill(s["crps_f"], s["crps_n"]) for s in scores.values()]
    sk_r = [skill(s["crps_f"], s["crps_r"]) for s in scores.values()]
    out.append(
        f"| **mean of archetypes (equal weight)** | | | | {_pct(float(np.mean(sk_n)))} | "
        f"{_pct(float(np.mean(sk_r)))} | |"
    )
    out += [
        "",
        "### Brier score of the dip chance (lower is better) and skill",
        "",
        "| Archetype (n, dips) | forecast | balance stays | random walk | skill vs stays | "
        "skill vs walk |",
        "|---|---|---|---|---|---|",
    ]
    bsk_n, bsk_r = [], []
    for a, s in scores.items():
        sn, sr = skill(s["brier_f"], s["brier_n"]), skill(s["brier_f"], s["brier_r"])
        out.append(
            f"| {a} ({s['n']}, {s['events']}) | {s['brier_f']:.4f} | {s['brier_n']:.4f} | "
            f"{s['brier_r']:.4f} | {_pct(sn)} | {_pct(sr)} |"
        )
        if sn is not None:
            bsk_n.append(sn)
        if sr is not None:
            bsk_r.append(sr)
    out.append(
        f"| **mean of archetypes** | | | | {_pct(float(np.mean(bsk_n)))} | "
        f"{_pct(float(np.mean(bsk_r)))} |"
    )
    out += [
        "",
        "(An archetype with no dips scores 0 for balance stays; its skill is not defined "
        "and it is left out of the mean.)",
        "",
        "### Reliability of the dip chance, all archetypes of the set together",
        "",
        "| Predicted chance | forecast: n | mean predicted | dipped | random walk: n | "
        "mean predicted | dipped |",
        "|---|---|---|---|---|---|---|",
    ]
    rf, rw = reliability(rows, "f"), reliability(rows, "r")
    for a, b in zip(rf, rw, strict=True):

        def cell(x: Record) -> str:
            if not x["n"]:
                return "0 | - | -"
            return f"{x['n']} | {x['mean_p']:.1%} | {x['seen']:.1%}"

        out.append(f"| {a['lo']:.0%} to {a['hi']:.0%} | {cell(a)} | {cell(b)} |")
    out += [
        "",
        "Origins are weekly and a dip lasts for weeks, so neighbouring origins share "
        "outcomes: the effective n is smaller than the n shown.",
        "",
    ]
    # dips caught at the same precision
    picks = {}
    for label, recs in (("forecast", rows), ("random walk", walk_records(rows))):
        picks[label] = choose(curve(recs, THRESHOLDS))
    out += [
        "### Dips caught at the same precision (threshold chosen on the whole set for each "
        "method: highest recall on ahead with precision 70 percent or more and at most 0.1 "
        "false alerts per 30 days; in-sample and descriptive)",
        "",
        "| | forecast | random walk | balance stays |",
        "|---|---|---|---|",
    ]
    chosen = {k: (None if v is None else v["threshold"]) for k, v in picks.items()}
    out.append(
        f"| chosen threshold | {chosen['forecast']} | {chosen['random walk']} | "
        "none (never alerts) |"
    )
    out.append(
        f"| all archetypes | {alerts_row(rows, chosen['forecast'])} | "
        f"{alerts_row(walk_records(rows), chosen['random walk'])} | "
        f"{alerts_row(stays_records(rows), 1.0)} |"
    )
    for a, g in groups.items():
        out.append(
            f"| {a} | {alerts_row(g, chosen['forecast'])} | "
            f"{alerts_row(walk_records(g), chosen['random walk'])} | "
            f"{alerts_row(stays_records(g), 1.0)} |"
        )
    out.append("")
    return out


def report() -> str:
    lines = [
        "# Experiment 1: baselines and scores (synthetic, development personas)",
        "",
        "Made with `python -m eval.baselines --report`. Method in DECISIONS.md (2026-10-17). "
        "20 development personas per set (never pooled), 40 weekly origins, 500 futures, "
        "seed 42, the default forecaster. The random walk uses the 365 days before each "
        "origin and only windows that ended on or before it. Nothing is adopted from this "
        "report; it fixes what the later experiments are scored against.",
        "",
    ]
    for long, title in ((False, "12-month set"), (True, "24-month set")):
        records = load(long)
        reference = load_records(current_default_dir(long))
        lines += section(title, records, reference)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.baselines",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quick", action="store_true", help="2 personas, 4 origins, 100 futures")
    parser.add_argument("--set", choices=("12", "long", "both"), default="both")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    if args.report:
        REPORT.write_text(report(), encoding="utf-8")
        print(f"written: {REPORT}")
        return 0
    sets = {"12": [False], "long": [True], "both": [False, True]}[args.set]
    futures = QUICK_FUTURES if args.quick else FUTURES
    jobs = []
    for long in sets:
        ids = args.only or development_ids(long=long)
        if args.quick and not args.only:
            ids = ids[:2]
        folder = OUT / (("quick-" if args.quick else "") + ("long" if long else "12"))
        jobs += [(pid, folder, args.quick, futures) for pid in ids]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for n, line in enumerate(pool.map(_job, jobs), 1):
            print(f"[{n}/{len(jobs)}] {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
