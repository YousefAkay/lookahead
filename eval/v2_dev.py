"""Forecaster v2, development stage only (pre-registration
docs/specs/2026-11-27-forecaster-v2-preregistration.md, commit 7e4a266).

    python -m eval.v2_dev simulate --set 12 --jobs 8
    python -m eval.v2_dev simulate --set long --jobs 8
    python -m eval.v2_dev simulate --set berka --jobs 8 --data ../lookahead-runs/data/berka
    python -m eval.v2_dev status                 # one line: units done per set
    python -m eval.v2_dev score                  # the five variants, cross-fitted; ledger first
    python -m eval.v2_dev freeze                 # the winner's final k(h) and w, all dev data
    python -m eval.v2_dev threshold              # the winner's alert threshold, re-picked

`simulate` runs, at every origin of every development unit, the forecaster three ways (v1, F,
F+a; 500 futures, seed 0, the account's own past only) and saves the futures, the realized
balances and the random walk's windows, one file per unit, written whole (resumable). Held-out
and fresh units are refused by the development-only checks. b and d are computed from these
saved futures with the same functions the forecaster uses (lookahead/forecast/v2.py), so a
stretched or mixed future here is the future the app would draw.

`score` cross-fits k(h) and w: each set is split into 5 groups by unit (sorted ids, every fifth);
for each group they are fitted on the other four groups of all three sets (equal weight per set)
and the variant is scored on the group. Every variant's numbers are written to the ledger before
any is printed. Metrics are `eval.berka_scores.summary_core` (the 2026-11-19 report's code).
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import HORIZON, METHOD, origins, run_origin_full
from eval.baselines import balance_series, crps_samples, dip_share, random_walk_paths
from eval.berka import build_db, dip_line_cents, origin_dates
from eval.berka_scores import summary_core
from eval.berka_split import assert_development_only as berka_development_only
from eval.berka_split import development_accounts
from eval.metrics import alert_metrics
from eval.personas_db import persona_db
from eval.thresholds_raw import choose, curve, leave_one_out
from lookahead.forecast.path import DEFAULT_OPTIONS, build_forecast
from lookahead.forecast.v2 import mix_walk, stretch
from lookahead.generator.archetypes import assert_development_only, development_ids

OUT = Path("data/backtest/v2dev")
LEDGER = Path("docs/results/v2_dev_ledger.jsonl")
FROZEN = Path("lookahead/forecast/v2_frozen.json")
REPORT = Path("docs/results/v2_dev_2026-11-27.md")
PREREG = "7e4a266"
FUTURES = 500
SETS = ("12", "long", "berka")
RAW = ("v1", "F", "Fa")
VARIANTS = ("F", "F+a", "F+b", "F+a+b", "F+a+b+d")
WALK_SHARES = tuple(round(0.1 * i, 1) for i in range(6))  # 0, 0.1, ..., 0.5
FOLDS = 5
MAX_WINDOWS = 336  # at most 365 - 30 + 1 windows of 30 days in the 365 days before
K_CAP = 50.0  # a numeric ceiling for the search only (see `fit_k`)


def options_of(raw: str) -> Any:
    from dataclasses import replace

    return {"v1": DEFAULT_OPTIONS, "F": replace(DEFAULT_OPTIONS, fee_dates=True),
            "Fa": replace(DEFAULT_OPTIONS, fee_dates=True, own_rate=True)}[raw]  # fmt: skip


# --- simulate ---


def _origin(conn: Any, unit: str, origin: Any) -> tuple[dict, dict[str, np.ndarray], np.ndarray]:
    record, paths, _ = run_origin_full(conn, unit, "synthetic", origin, FUTURES, "dev", False,
                                       keep_paths=True)  # fmt: skip
    out = {"v1": np.asarray(paths, dtype=np.int64)}
    for raw in ("F", "Fa"):
        f = build_forecast(conn, unit, METHOD, origin, None, HORIZON, FUTURES, keep_paths=True,
                           options=options_of(raw))  # fmt: skip
        assert f is not None and f.simulation is not None
        out[raw] = np.asarray(f.simulation.paths, dtype=np.int64)
    walk = random_walk_paths(balance_series(conn, unit, origin))
    history = len(balance_series(conn, unit, origin))
    record["history_days"] = history
    record["walk_windows"] = 0 if walk is None else len(walk)
    return record, out, (np.zeros((0, HORIZON)) if walk is None else walk)


def _save(path: Path, records: list[dict], paths: dict[str, list], walks: list[np.ndarray]) -> None:
    walk = np.zeros((len(walks), MAX_WINDOWS, HORIZON), dtype=np.int64)
    counts = np.zeros(len(walks), dtype=np.int32)
    for i, w in enumerate(walks):
        counts[i] = len(w)
        walk[i, : len(w)] = np.rint(w).astype(np.int64)
    tmp = path.with_name(path.stem + ".part.npz")
    np.savez_compressed(
        tmp, **{f"paths_{k}": np.stack(v).astype(np.int64) for k, v in paths.items()},
        walk=walk, walk_n=counts, records=np.array(json.dumps(records)),
    )  # fmt: skip
    tmp.replace(path)


def _synthetic_job(args: tuple[str, bool, str]) -> str:
    pid, long, folder = args
    path = Path(folder) / f"{pid}.npz"
    if path.exists():
        return f"{pid}: already done"
    assert_development_only([pid])
    records, paths, walks = [], {k: [] for k in RAW}, []
    with persona_db(pid, Path(folder) / "work") as db:
        for origin in origins():
            rec, p, w = _origin(db.conn, pid, origin)
            records.append(rec)
            walks.append(w)
            for k in RAW:
                paths[k].append(p[k])
    _save(path, records, paths, walks)
    return f"{pid}: {len(records)} origins"


def _berka_job(args: tuple[int, Any, str]) -> str:
    account, frame, folder = args
    path = Path(folder) / f"{account}.npz"
    if path.exists():
        return f"{account}: already done"
    berka_development_only([account])
    days = origin_dates(frame)
    work = Path(folder) / "work"
    work.mkdir(parents=True, exist_ok=True)
    db = work / f"{account}.db"
    conn = build_db(frame, account, db, dip_line_cents(frame, days[0]))
    records, paths, walks = [], {k: [] for k in RAW}, []
    try:
        for origin in days:
            rec, p, w = _origin(conn, str(account), origin)
            records.append(rec)
            walks.append(w)
            for k in RAW:
                paths[k].append(p[k])
    finally:
        conn.close()
        db.unlink(missing_ok=True)
    _save(path, records, paths, walks)
    return f"{account}: {len(records)} origins"


def simulate(which: str, jobs: int, data: Path | None) -> int:
    folder = OUT / which
    folder.mkdir(parents=True, exist_ok=True)
    if which == "berka":
        import eval.anchor as anchor

        if data is not None:
            anchor.DATA = data
        ids, frames = anchor._berka_frames()
        tasks: list[Any] = [(a, frames[a], str(folder)) for a in ids]
        job: Any = _berka_job
    else:
        ids = development_ids(long=which == "long")
        tasks = [(pid, which == "long", str(folder)) for pid in ids]
        job = _synthetic_job
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for line in pool.map(job, tasks):
            print(line, flush=True)
    return 0


def units(which: str) -> list[str]:
    if which == "berka":
        ids = development_accounts()
        berka_development_only(ids)
        return [str(a) for a in ids]
    ids = development_ids(long=which == "long")
    assert_development_only(ids)
    return ids


def status() -> int:
    parts = []
    for which in SETS:
        done = sum((OUT / which / f"{u}.npz").exists() for u in units(which))
        parts.append(f"{which} {done}/{len(units(which))}")
    print("v2 dev futures: " + ", ".join(parts))
    return 0


# --- load ---


def load_set(which: str) -> list[dict]:
    """Every origin of a set: its v1 record, the raw futures and the walk windows."""
    out = []
    for u in units(which):
        d = np.load(OUT / which / f"{u}.npz")
        records = json.loads(str(d["records"]))
        for i, rec in enumerate(records):
            out.append({
                "record": rec, "unit": u,
                "raw": {k: d[f"paths_{k}"][i] for k in RAW},
                "walk": d["walk"][i, : d["walk_n"][i]],
            })  # fmt: skip
    return out


def group_of(which: str) -> dict[str, int]:
    return {u: i % FOLDS for i, u in enumerate(sorted(units(which)))}


def scored(o: dict) -> bool:
    """The origins the 2026-11-19 report scores: not already below the line, walk defined."""
    r = o["record"]
    return not r["in_shortfall_at_origin"] and r["walk"] is not None


# --- b and d: fitting ---


def _rank(sorted_paths: np.ndarray, q: float) -> np.ndarray:
    return sorted_paths[round(q * (sorted_paths.shape[0] - 1))]


def needed_k(raw: np.ndarray, realized: np.ndarray) -> np.ndarray:
    """For each day, the smallest stretch that puts the realized balance inside the 80% band
    (1 when it is inside already; inf when the band has no width on that side)."""
    s = np.sort(raw, axis=0)
    lo, mid, hi = _rank(s, 0.1), _rank(s, 0.5), _rank(s, 0.9)
    y = realized.astype(float)
    k = np.ones(raw.shape[1])
    below, above = y < lo, y > hi
    with np.errstate(divide="ignore", invalid="ignore"):
        need_lo = np.where(mid - lo > 0, (mid - y) / (mid - lo), np.inf)
        need_hi = np.where(hi - mid > 0, (y - mid) / (hi - mid), np.inf)
    k = np.where(below, np.maximum(need_lo, 1.0), k)
    k = np.where(above, np.maximum(need_hi, 1.0), k)
    return k


def fit_k(sets: dict[str, list[dict]], raw: str) -> list[float]:
    """k(h): per day ahead, the smallest k >= 1 at which pooled 80% coverage reaches 80 percent,
    each set weighted equally (every origin weighs 1 / its set's origins), then made
    non-decreasing in h. Capped at K_CAP when no finite k reaches it (a band of no width)."""
    needs, weights = [], []
    for rows in sets.values():
        if not rows:
            continue
        for o in rows:
            needs.append(needed_k(o["raw"][raw], np.asarray(o["record"]["realized"])))
            weights.append(1.0 / len(rows))
    need, w = np.stack(needs), np.asarray(weights) / sum(weights)
    k = []
    for h in range(need.shape[1]):
        order = np.argsort(need[:, h])
        cum = np.cumsum(w[order])
        idx = int(np.searchsorted(cum, 0.8 - 1e-12))
        k.append(float(min(max(need[order[idx], h], 1.0), K_CAP)))
    return [float(x) for x in np.maximum.accumulate(k)]


def futures_of(o: dict, variant: str, k: list[float] | None, w: float) -> np.ndarray:
    """The variant's 500 futures at one origin, from the saved raw futures."""
    base = {"F": "F", "F+a": "Fa", "F+b": "F", "F+a+b": "Fa", "F+a+b+d": "Fa", "v1": "v1"}[variant]
    paths = o["raw"][base].tolist()
    if variant in ("F+b", "F+a+b", "F+a+b+d"):
        paths = stretch(paths, k or [])
    if variant == "F+a+b+d":
        history_ok = o["record"]["history_days"] >= 365
        windows = [tuple(int(x) for x in row) for row in o["walk"]] if history_ok else []
        paths = mix_walk(paths, windows, w, 0)
    return np.asarray(paths, dtype=np.int64)


def record_of(o: dict, paths: np.ndarray) -> dict:
    """The v1 record with the forecast's own numbers replaced by this variant's."""
    rec = dict(o["record"])
    s = np.sort(paths, axis=0)
    rec["p10"], rec["p50"], rec["p90"] = (_rank(s, q).tolist() for q in (0.1, 0.5, 0.9))
    lows = np.sort(paths.min(axis=1))
    rec["median_lowest_cents"] = int(lows[round(0.5 * (len(lows) - 1))])
    rec["shortfall_probability"] = dip_share(paths, rec["buffer_cents"])
    realized = np.asarray(rec["realized"], dtype=float)
    rec["crps_forecast"] = crps_samples(paths, realized).round(1).tolist()
    return rec


def crps_skill_vs_v1(recs: list[dict], v1: list[dict]) -> float:
    rows = [
        i for i, r in enumerate(v1) if not r["in_shortfall_at_origin"] and r["walk"] is not None
    ]
    f = np.mean([np.mean(recs[i]["crps_forecast"]) for i in rows])
    base = np.mean([np.mean(v1[i]["crps_forecast"]) for i in rows])
    return float(1 - f / base)


def pooled_skill(sets: dict[str, list[dict]], variant: str, k: list[float] | None,
                 w: float) -> float:  # fmt: skip
    vals = []
    for rows in sets.values():
        recs = [record_of(o, futures_of(o, variant, k, w)) for o in rows]
        vals.append(crps_skill_vs_v1(recs, [o["record"] for o in rows]))
    return float(np.mean(vals))


def fit(sets: dict[str, list[dict]], variant: str) -> tuple[list[float] | None, float]:
    """k(h) and w for a variant, fitted on `sets` (scored origins only)."""
    fit_rows = {s: [o for o in rows if scored(o)] for s, rows in sets.items()}
    k = None
    if variant in ("F+b", "F+a+b", "F+a+b+d"):
        k = fit_k(fit_rows, "F" if variant == "F+b" else "Fa")
    w = 0.0
    if variant == "F+a+b+d":
        w = max(WALK_SHARES, key=lambda share: (pooled_skill(fit_rows, variant, k, share),
                                                 -share))  # fmt: skip
    return k, w


# --- score ---


def cross_fitted(all_sets: dict[str, list[dict]], variant: str) -> tuple[dict, list]:
    """Each set's records of the variant, each origin scored by k and w fitted without its
    group; and the five folds' fitted values."""
    groups = {s: group_of(s) for s in all_sets}
    out: dict[str, list[dict]] = {s: [] for s in all_sets}
    fitted = []
    for g in range(FOLDS):
        train = {s: [o for o in rows if groups[s][o["unit"]] != g] for s, rows in all_sets.items()}
        k, w = fit(train, variant)
        fitted.append({"fold": g, "k": None if k is None else [round(x, 4) for x in k], "w": w})
        for s, rows in all_sets.items():
            for o in rows:
                if groups[s][o["unit"]] == g:
                    out[s].append(record_of(o, futures_of(o, variant, k, w)))
    return out, fitted


def numbers(recs: list[dict], v1: list[dict]) -> dict:
    core = summary_core(recs)
    return {
        "n_origins": core["n"], "n_units": core["units_n"],
        "coverage_all": core["coverage_all"], "coverage30": core["coverage30"],
        "skill_vs_v1": crps_skill_vs_v1(recs, v1), "skill_vs_walk": core["skill_r"],
        "skill_vs_stays": core["skill_n"],
    }  # fmt: skip


def eligible(per_set: dict[str, dict]) -> bool:
    return all(0.75 <= m["coverage_all"] <= 0.85 and m["coverage30"] >= 0.70
               for m in per_set.values())  # fmt: skip


def _ledger_write(entry: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _code() -> str:
    import subprocess

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()  # fmt: skip
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "lookahead", "eval"],
                           capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    if dirty:
        raise SystemExit("commit the code first: the ledger records the commit it ran")
    return head


def score() -> int:
    code = _code()
    all_sets = {s: load_set(s) for s in SETS}
    v1 = {s: [o["record"] for o in rows] for s, rows in all_sets.items()}
    results: dict[str, dict] = {"v1": {s: numbers(v1[s], v1[s]) for s in SETS}}
    entries = []
    for variant in VARIANTS:
        recs, fitted = cross_fitted(all_sets, variant)
        per_set = {s: numbers(recs[s], v1[s]) for s in SETS}
        results[variant] = per_set
        entries.append({"stage": "development, cross-fitted", "variant": variant,
                        "code": code, "prereg": PREREG, "per_set": per_set,
                        "eligible": eligible(per_set), "folds": fitted})  # fmt: skip
    pooled = {v: float(np.mean([results[v][s]["skill_vs_v1"] for s in SETS])) for v in VARIANTS}
    ok = [v for v in VARIANTS if eligible(results[v])]
    parts = {"F": 1, "F+a": 2, "F+b": 2, "F+a+b": 3, "F+a+b+d": 4}
    winner = None
    if ok:
        best = max(pooled[v] for v in ok)
        close = [v for v in ok if best - pooled[v] <= 0.001]
        winner = min(close, key=lambda v: (parts[v], -pooled[v]))
    for e in entries:  # every variant recorded before anything is shown
        e["pooled_skill_vs_v1"] = pooled[e["variant"]]
        e["winner"] = winner
        _ledger_write(e)
    _ledger_write({"stage": "development, v1 reference", "code": code, "prereg": PREREG,
                   "per_set": results["v1"]})  # fmt: skip
    REPORT.write_text(report(results, pooled, ok, winner, code), encoding="utf-8")
    print(report(results, pooled, ok, winner, code))
    return 0


def report(results: dict, pooled: dict, ok: list, winner: str | None, code: str) -> str:
    names = {"12": "12-month", "long": "24-month", "berka": "Berka"}
    lines = [
        f"# Forecaster v2, development stage, cross-fitted (code {code}, pre-registration "
        f"{PREREG})",
        "",
        "Coverage is the 80% band; skills are CRPS skill, mean of 30 days, in points; n is the "
        "scored origins (units). k(h) and w are cross-fitted: 5 groups per set, fitted on the "
        "other four groups of all three sets.",
        "",
        "| variant | set | n (units) | average coverage | day-30 coverage | vs v1 | vs walk | "
        "vs stays |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for v in ("v1", *VARIANTS):
        for s in SETS:
            m = results[v][s]
            lines.append(
                f"| {v} | {names[s]} | {m['n_origins']} ({m['n_units']}) | "
                f"{100 * m['coverage_all']:.1f}% | {100 * m['coverage30']:.1f}% | "
                f"{100 * m['skill_vs_v1']:+.1f} | {100 * m['skill_vs_walk']:+.1f} | "
                f"{100 * m['skill_vs_stays']:+.1f} |"
            )
    lines += [
        "",
        "Pooled CRPS skill against v1 (mean of the three sets): "
        + ", ".join(f"{v} {100 * pooled[v]:+.2f}" for v in VARIANTS),
        "",
        f"Eligible: {', '.join(ok) if ok else 'none'}. Winner: {winner or 'none (v1 stays)'}.",
    ]
    return "\n".join(lines) + "\n"


# --- freeze and threshold ---


def freeze() -> int:
    entries = [json.loads(x) for x in LEDGER.read_text().splitlines() if x]
    winner = next(e["winner"] for e in reversed(entries) if "winner" in e)
    if winner is None:
        print("no winner: nothing to freeze")
        return 1
    all_sets = {s: load_set(s) for s in SETS}
    k, w = fit(all_sets, winner)
    FROZEN.write_text(json.dumps({"variant": winner, "prereg": PREREG, "stretch": k,
                                  "walk_share": w}, indent=1) + "\n", encoding="utf-8")  # fmt: skip
    shown = {h: round(k[h - 1], 3) for h in (1, 7, 14, 30)} if k else None
    print(f"frozen {winner}: k(h) at days 1, 7, 14, 30 = {shown}; w = {w}")
    return 0


def threshold() -> int:
    frozen = json.loads(FROZEN.read_text())
    variant, k, w = frozen["variant"], frozen["stretch"], frozen["walk_share"]
    rows = load_set("12")
    recs = [record_of(o, futures_of(o, variant, k, w)) for o in rows]
    for r in recs:
        r["dataset"] = "synthetic"
    pick = choose(curve(recs))
    loo, folds = leave_one_out(recs)
    t = None if pick is None else pick["threshold"]
    out = {"variant": variant, "threshold": t,
           "leave_one_out": {"recall_on": loo["recall_on"], "precision": loo["precision"],
                             "caught_on": loo["caught_on"], "episodes": loo["shortfall_episodes"],
                             "false_per_30_days": loo["false_per_30_days"]},
           "fold_thresholds": sorted({v for v in folds.values() if v is not None})}  # fmt: skip
    per_set = {}
    for which in SETS:
        set_rows = rows if which == "12" else load_set(which)
        set_recs = recs if which == "12" else [record_of(o, futures_of(o, variant, k, w))
                                                 for o in set_rows]  # fmt: skip
        m = alert_metrics(set_recs, t if t is not None else 1.0, median_rule=False)
        per_set[which] = {"caught_on": m["caught_on"], "episodes": m["shortfall_episodes"],
                          "precision": m["precision"], "alert_episodes": m["alert_episodes"],
                          "false_alerts": m["false_alerts"]}  # fmt: skip
    out["dev_at_threshold"] = per_set
    _ledger_write({"stage": "development, threshold re-picked", "code": _code(),
                   "prereg": PREREG, **out})  # fmt: skip
    print(json.dumps(out, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.v2_dev",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("simulate")
    s.add_argument("--set", choices=SETS, required=True)
    s.add_argument("--jobs", type=int, default=8)
    s.add_argument("--data", type=Path)
    for name in ("status", "score", "freeze", "threshold"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    if args.command == "simulate":
        return simulate(args.set, args.jobs, args.data)
    return {"status": status, "score": score, "freeze": freeze, "threshold": threshold}[
        args.command
    ]()


if __name__ == "__main__":
    sys.exit(main())
