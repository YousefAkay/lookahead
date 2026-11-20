"""Berka scores with the full metric set, and the same metrics for the synthetic sets.

    python -m eval.berka_scores run --data ../lookahead-runs/data/berka --jobs 8
    python -m eval.berka_scores check-store --data ../lookahead-runs/data/berka
    python -m eval.berka_scores report       # docs/results/scores_development_2026-11-19.md

`run` makes the default forecast again at every origin of the 200 primary development accounts
(40 weekly origins each, 500 futures, the account's own past only) and adds what experiment 3
did not save: the CRPS of "balance stays" and of the random walk (the account's own 30-day
balance changes in the 365 days before the origin, windows that end on or before it), and the
walk's dip chance. Records go to data/backtest/berka-scores/<group>/ (not committed), one file per
account, resumable. Held-out accounts are refused: the held-out run waits for its committed
protocol and will use this same code.

`check-store` compares the recomputed forecasts with the futures saved for experiments 4 to 6
(the experiment 3 records), origin by origin.

`summary` gives one dataset's numbers, used unchanged for the synthetic sets and Berka:
- CRPS (mean over the 30 days and on day 30) of the forecast, "balance stays" and the random
  walk, and the forecast's skill against each (1 - its CRPS / theirs);
- 80 percent coverage: the share of days whose realized balance is between p10 and p90 (day 30,
  and all 30 days);
- the Brier score of the dip chance against "balance stays" (never dips) and the walk;
- dips caught at the same precision: the alert engine on all origins, threshold chosen on the
  dataset itself (highest recall with precision 70 percent or more and at most 0.1 false alerts
  per 30 days; in-sample and descriptive, as in experiment 1);
- the lowest-balance bias: the median lowest future against the realized lowest balance.

Berka amounts are koruna scaled to the forecaster's range (5 cents per koruna, DECISIONS
2026-10-17), not dollars: its CRPS and bias are in those scaled units, and only skills, coverage,
Brier and dips compare across datasets. Berka's line is "fell below one month's outflow", not a
personal buffer.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import FUTURES, LOGIC, run_origin_full
from eval.baselines import (
    alerts_row,
    balance_series,
    crps_samples,
    dip_share,
    load_folder,
    random_walk_paths,
)
from eval.berka import build_db, dip_line_cents, origin_dates
from eval.berka_gate import load as load_berka
from eval.berka_split import assert_development_only, development_accounts
from eval.thresholds import THRESHOLDS
from eval.thresholds_raw import choose, curve

OUT = Path("data/backtest/berka-scores")
REPORT = Path("docs/results/scores_development_2026-11-19.md")
SYNTHETIC = Path("data/backtest/exp1-2026-11-19")
Record = dict[str, Any]


# --- The run (development accounts only) ---


def account_records(account: int, frame: Any, work: Path, futures: int = FUTURES) -> list[Record]:
    """Every origin of one development account: the default forecast, with the baselines."""
    assert_development_only([account])
    days = origin_dates(frame)
    db = work / f"{account}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    conn = build_db(frame, account, db, dip_line_cents(frame, days[0]))
    out = []
    try:
        for origin in days:
            rec, paths, forecast = run_origin_full(
                conn, str(account), "synthetic", origin, futures, "berka", False, keep_paths=True,
            )  # fmt: skip
            draws = np.asarray(paths, dtype=float)
            realized = np.asarray(rec["realized"], dtype=float)
            rec["crps_forecast"] = crps_samples(draws, realized).round(1).tolist()
            rec["crps_stays"] = (
                np.abs(float(rec["start_balance_cents"]) - realized).round(1).tolist()
            )
            rec["zero_estimate"] = sum(d.spending_cents for d in forecast.days) == 0
            walk = random_walk_paths(balance_series(conn, str(account), origin))
            rec["walk_windows"] = 0 if walk is None else len(walk)
            if walk is None:
                rec["crps_walk"] = rec["walk"] = None
            else:
                rec["crps_walk"] = crps_samples(walk, realized).round(1).tolist()
                rec["walk"] = {
                    "shortfall_probability": dip_share(walk, rec["buffer_cents"]),
                    "p10": np.rint(np.quantile(walk, 0.10, axis=0)).astype(int).tolist(),
                    "p50": np.rint(np.median(walk, axis=0)).astype(int).tolist(),
                    "p90": np.rint(np.quantile(walk, 0.90, axis=0)).astype(int).tolist(),
                    "median_lowest_cents": int(np.rint(np.median(walk.min(axis=1)))),
                }
            out.append(rec)
    finally:
        conn.close()
        db.unlink(missing_ok=True)
    return out


def _job(args: tuple[int, Any, Path]) -> str:
    account, frame, folder = args
    path = folder / f"{account}.jsonl"
    if path.exists():
        return f"{account}: already done"
    records = account_records(account, frame, folder / "work")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    tmp.replace(path)
    return f"{account}: {len(records)} origins"


def run(data: Path, group: str, jobs: int, folder: Path) -> int:
    ids = development_accounts(group)
    assert_development_only(ids)
    t = load_berka(data)
    t = t[t["account_id"].isin(ids)].reset_index(drop=True)  # only development rows are kept
    frames = {a: g.reset_index(drop=True) for a, g in t.groupby("account_id")}
    folder.mkdir(parents=True, exist_ok=True)
    todo = [(a, frames[a], folder) for a in ids]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for n, line in enumerate(pool.map(_job, todo), 1):
            print(f"[{n}/{len(todo)}] {line}", flush=True)
    return 0


def load_records(folder: Path) -> list[Record]:
    records = []
    for path in sorted(folder.glob("*.jsonl")):
        records += [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]
    accounts = sorted({int(r["persona"]) for r in records})
    assert_development_only(accounts)
    return records


def check_store(data: Path, folder: Path) -> int:
    """Origins whose recomputed p10, p50, p90 and dip chance equal the saved experiment 3 ones."""
    load_records(folder)  # development accounts only
    checked = equal = 0
    for path in sorted(folder.glob("*.jsonl")):
        saved = data / "runs" / "exp3" / path.name
        if not saved.exists():
            continue
        old = {r["origin"]: r for r in map(json.loads, saved.read_text("utf-8").splitlines())}
        for r in map(json.loads, path.read_text("utf-8").splitlines()):
            o = old.get(r["origin"])
            checked += 1
            keys = ("p10", "p50", "p90", "shortfall_probability", "realized")
            equal += o is not None and all(o[k] == r[k] for k in keys)
    print(f"recomputed against experiment 3: {equal} of {checked} origins equal")
    return 0 if checked and equal == checked else 1


# --- The metrics, the same for every dataset ---


def _event(r: Record) -> float:
    return 0.0 if r["realized_first_below_buffer"] is None else 1.0


def _skill(score: float, reference: float) -> float | None:
    return None if not reference else 1 - score / reference


def summary(records: Sequence[Record]) -> Record:
    """One dataset's numbers (see the module docstring), with every n."""
    out = summary_core(records)
    pool = [r for r in records if r["walk"] is not None]
    for tag, recs in (("f", pool), ("r", [{**r, **r["walk"]} for r in pool])):
        pick = choose(curve(recs, THRESHOLDS))
        threshold = None if pick is None else pick["threshold"]
        out[f"alert_{tag}"] = f"threshold {threshold}: {alerts_row(recs, threshold)}"
    return out


def summary_core(records: Sequence[Record]) -> Record:
    """Everything but dips caught: CRPS and skill, coverage, Brier, lowest-balance bias."""
    units = len({r["persona"] for r in records})
    rows = [r for r in records if not r["in_shortfall_at_origin"] and r["walk"] is not None]
    out: Record = {"origins": len(records), "units": units, "n": len(rows),
                   "units_n": len({r["persona"] for r in rows}),
                   "dips": int(sum(_event(r) for r in rows))}  # fmt: skip
    for tag, key in (("f", "crps_forecast"), ("n", "crps_stays"), ("r", "crps_walk")):
        out[f"crps_{tag}"] = float(np.mean([np.mean(r[key]) for r in rows]))
        out[f"crps30_{tag}"] = float(np.mean([r[key][-1] for r in rows]))
    for day in ("", "30"):
        out[f"skill{day}_n"] = _skill(out[f"crps{day}_f"], out[f"crps{day}_n"])
        out[f"skill{day}_r"] = _skill(out[f"crps{day}_f"], out[f"crps{day}_r"])
    inside = [[lo <= y <= hi for lo, y, hi in zip(r["p10"], r["realized"], r["p90"], strict=True)]
              for r in rows]  # fmt: skip
    out["coverage30"] = float(np.mean([d[-1] for d in inside]))
    out["coverage_all"] = float(np.mean([np.mean(d) for d in inside]))
    for tag, prob in (("f", lambda r: r["shortfall_probability"]), ("n", lambda r: 0.0),
                      ("r", lambda r: r["walk"]["shortfall_probability"])):  # fmt: skip
        out[f"brier_{tag}"] = float(np.mean([(prob(r) - _event(r)) ** 2 for r in rows]))
    out["brier_skill_n"] = _skill(out["brier_f"], out["brier_n"])
    out["brier_skill_r"] = _skill(out["brier_f"], out["brier_r"])
    bias = [(r["median_lowest_cents"] - r["realized_lowest_cents"]) / 100 for r in rows]
    out["bias"] = float(np.mean(bias))
    out["abs_bias"] = float(np.mean(np.abs(bias)))
    return out


# --- The report ---


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1%}"


# The rows of the development report that the held-out dry run must reproduce exactly
# (protocol of 2026-11-19): label and the formatting of one summary_core result.
CORE_ROWS: list[tuple[str, Any]] = [
    ("origins (units)", lambda x: f"{x['origins']:,} ({x['units']})"),
    ("scored origins n (units)", lambda x: f"{x['n']:,} ({x['units_n']})"),
    ("dips among them", lambda x: f"{x['dips']:,}"),
    ("CRPS skill vs balance stays, mean of 30 days", lambda x: _pct(x["skill_n"])),
    ("CRPS skill vs random walk, mean of 30 days", lambda x: _pct(x["skill_r"])),
    ("CRPS skill vs balance stays, day 30", lambda x: _pct(x["skill30_n"])),
    ("CRPS skill vs random walk, day 30", lambda x: _pct(x["skill30_r"])),
    (
        "CRPS forecast / stays / walk, mean of 30 days",
        lambda x: (
            f"{x['crps_f'] / 100:,.0f} / {x['crps_n'] / 100:,.0f} / {x['crps_r'] / 100:,.0f}"
        ),
    ),
    ("80% band coverage, day 30", lambda x: f"{x['coverage30']:.1%}"),
    ("80% band coverage, all 30 days", lambda x: f"{x['coverage_all']:.1%}"),
    (
        "Brier of the dip chance: forecast / stays / walk",
        lambda x: f"{x['brier_f']:.4f} / {x['brier_n']:.4f} / {x['brier_r']:.4f}",
    ),
    (
        "Brier skill vs stays / vs walk",
        lambda x: f"{_pct(x['brier_skill_n'])} / {_pct(x['brier_skill_r'])}",
    ),
    (
        "lowest-balance bias: mean (mean absolute)",
        lambda x: f"{x['bias']:+,.0f} ({x['abs_bias']:,.0f})",
    ),
]


def synthetic_records(long: bool) -> list[Record]:
    """The 2026-11-19 baselines records: development personas of one set, current logic only."""
    records = load_folder(SYNTHETIC / ("long" if long else "12"), long)
    if any(r.get("logic") != LOGIC for r in records):
        raise ValueError("synthetic records from another forecaster version")
    return records


def report(berka_folder: Path, group: str = "primary") -> str:
    columns = [("Synthetic, 12-month set", "personas", synthetic_records(False)),
               ("Synthetic, 24-month set", "personas", synthetic_records(True)),
               (f"Berka, {group} development", "accounts", load_records(berka_folder))]  # fmt: skip
    zero = [r for r in columns[2][2] if r.get("zero_estimate")]
    columns.append(("Berka, zero-estimate origins (only)", "accounts", zero))
    s = [(name, unit, summary(recs)) for name, unit, recs in columns]

    def row(label: str, fn: Any) -> str:
        return f"| {label} | " + " | ".join(fn(x) for _, _, x in s) + " |"

    lines = [
        "# Development scores, synthetic and Berka (2026-11-19)",
        "",
        "Made with `python -m eval.berka_scores report`. The default forecaster (final, "
        "DECISIONS 2026-11-19), 40 weekly origins per persona or account, 500 futures, seed 42. "
        "Development data only: nothing held out was opened. Scores are on ordinary origins "
        "(not already below the line) where the random walk is defined; dips caught use all "
        "origins, as the alert engine sees them. Synthetic: the 2026-11-19 baselines rerun. "
        "Berka amounts are in scaled units (1 unit = 20 koruna: 5 cents per koruna, shown in "
        "hundreds of cents), not dollars, and its line is "
        '"fell below one month\'s outflow".',
        "",
        "| | " + " | ".join(name for name, _, _ in s) + " |",
        "|---|" + "---|" * len(s),
        *[row(label, fn) for label, fn in CORE_ROWS[:-1]],
        row("dips caught, forecast (in-sample threshold)", lambda x: x["alert_f"]),
        row("dips caught, random walk (in-sample threshold)", lambda x: x["alert_r"]),
        "| (zero-estimate column: its dips caught are computed on non-consecutive origins, "
        "so episodes are cut or joined; not comparable with the other columns) | | | | |",
        row(*CORE_ROWS[-1]),
        "",
        "All numbers are pooled over origins, so larger balances weigh more in CRPS and bias "
        "(the 2026-11-19 baselines report gives the synthetic per-archetype averages). The "
        "zero-estimate column is the origins where the forecast's everyday spending was zero, "
        "reported apart as committed on 2026-10-17; it can only veto, never decide. "
        "CRPS and bias are in dollars for the synthetic sets and in scaled units for Berka; "
        "compare skills, coverage, Brier and dips across datasets, not amounts. A positive bias "
        "means the forecast's median lowest balance was above the realized lowest (too "
        "optimistic). Origins are weekly and a dip lasts for weeks, so neighbouring origins "
        "share outcomes: the effective n is smaller than the n shown.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.berka_scores",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["run", "check-store", "report"])
    parser.add_argument("--data", type=Path, default=Path("data/berka"))
    parser.add_argument("--group", choices=["primary", "sensitivity"], default="primary")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    folder = OUT / args.group
    if args.command == "run":
        return run(args.data, args.group, args.jobs, folder)
    if args.command == "check-store":
        return check_store(args.data, folder)
    REPORT.write_text(report(folder, args.group), encoding="utf-8")
    print(f"written: {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
