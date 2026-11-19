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
    python -m eval.baselines --tag 2026-11-19 --jobs 4   # a later rerun, in its own folder
    python -m eval.baselines --tag 2026-11-19 --report   # docs/results/baselines_2026-11-19.md

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
TAG_ARG = ""  # " --tag X" for a later rerun (in the report's first line)
REFERENCE: Path | None = None  # an earlier baselines run (folders 12/ and long/) to compare with
QUANTILES = tuple(k / 100 for k in range(5, 100, 5))  # 5, 10, ..., 95 percent
LOOKBACK_DAYS = 365
MIN_WINDOWS = 20  # fewer windows than this: no random walk for the origin
BINS = (0.0, 0.01, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 1.0001)  # the reliability curve
PLAN = "2026-10-17 experiment 1"
MIN_DIPS = 5  # a Brier skill needs at least this many dips in the archetype to be shown

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


def reference_name() -> str:
    return "the saved default (arm 0)" if REFERENCE is None else "the earlier baselines run"


def load_folder(folder: Path, long: bool) -> list[Record]:
    """An earlier baselines run's records (development only, one set), to compare with."""
    records = []
    for path in sorted(folder.glob("*.jsonl")):
        records += [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]
    if not records:
        raise ValueError(f"no records in {folder}")
    assert_development_only(sorted({r["persona"] for r in records}))
    assert all(is_long(r["persona"]) == long for r in records)
    return records


def usable(records: list[Record]) -> list[Record]:
    """Ordinary origins (not already in shortfall) where the random walk is defined."""
    return [r for r in records if not r["in_shortfall_at_origin"] and r["walk"] is not None]


def alert_pool(records: list[Record]) -> list[Record]:
    """The records the alert machinery is fed: every origin where the random walk exists, those
    already below the buffer included (an alert episode runs through them, as in the app)."""
    return [r for r in records if r["walk"] is not None]


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
        f"are on those {len(rows)}. This rerun equals {reference_name()} at "
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
        enough = s["events"] >= MIN_DIPS
        sn = skill(s["brier_f"], s["brier_n"]) if enough else None
        sr = skill(s["brier_f"], s["brier_r"]) if enough else None
        out.append(
            f"| {a} ({s['n']}, {s['events']}) | {s['brier_f']:.4f} | {s['brier_n']:.4f} | "
            f"{s['brier_r']:.4f} | {_pct(sn) if enough else 'too few dips'} | "
            f"{_pct(sr) if enough else 'too few dips'} |"
        )
        if sn is not None:
            bsk_n.append(sn)
        if sr is not None:
            bsk_r.append(sr)
    out.append(
        f"| **mean of the {len(bsk_n)} archetypes with {MIN_DIPS}+ dips** | | | | "
        f"{_pct(float(np.mean(bsk_n))) if bsk_n else '-'} | "
        f"{_pct(float(np.mean(bsk_r))) if bsk_r else '-'} |"
    )
    out += [
        "",
        "Origins are weekly and a dip lasts for weeks, so neighbouring origins share "
        "outcomes: the effective n is smaller than the n shown.",
        "",
    ]
    # dips caught at the same precision
    every = alert_pool(records)
    every_groups = by_archetype(every)
    picks = {}
    for label, recs in (("forecast", every), ("random walk", walk_records(every))):
        picks[label] = choose(curve(recs, THRESHOLDS))
    out += [
        "### Dips caught at the same precision (all origins, those already below the buffer "
        "included, as the alert engine sees them; threshold chosen on the whole set for each "
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
        f"| all archetypes | {alerts_row(every, chosen['forecast'])} | "
        f"{alerts_row(walk_records(every), chosen['random walk'])} | "
        f"{alerts_row(stays_records(every), 1.0)} |"
    )
    for a, g in every_groups.items():
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
        f"Made with `python -m eval.baselines{TAG_ARG} --report`. Method in DECISIONS.md "
        "(2026-10-17). "
        "20 development personas per set (never pooled), 40 weekly origins, 500 futures, "
        "seed 42, the default forecaster. The random walk uses the 365 days before each "
        "origin and only windows that ended on or before it. Nothing is adopted from this "
        "report; it fixes what the later experiments are scored against.",
        "",
    ]
    for long, title in ((False, "12-month set"), (True, "24-month set")):
        records = load(long)
        if REFERENCE is None:
            reference = load_records(current_default_dir(long))
        else:
            reference = load_folder(REFERENCE / ("long" if long else "12"), long)
        lines += section(title, records, reference)
    return "\n".join(lines) + "\n"


def use_tag(tag: str) -> None:
    """A later rerun keeps its own records and report; the 2026-10-17 ones are never touched."""
    global OUT, REPORT, TAG_ARG
    if not tag.replace("-", "").isalnum():
        raise ValueError("--tag: letters, digits and dashes only")
    if tag == "2026-10-17":
        raise ValueError("--tag 2026-10-17 is the original run: pick another tag")
    OUT = Path(f"data/backtest/exp1-{tag}")
    REPORT = Path(f"docs/results/baselines_{tag}.md")
    TAG_ARG = f" --tag {tag}"


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
    parser.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="with --report: an earlier baselines run (folders 12/ and long/) to compare with",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="a rerun: records in data/backtest/exp1-TAG, report baselines_TAG.md",
    )
    args = parser.parse_args(argv)
    if args.tag:
        use_tag(args.tag)
    if args.reference is not None:
        global REFERENCE, TAG_ARG
        REFERENCE = args.reference
        TAG_ARG += f" --reference {args.reference}"
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
