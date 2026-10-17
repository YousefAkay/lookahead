"""Step 6 of the 2026-10-12 run (Yousef's pre-registration): blending with "the balance stays".

Method in DECISIONS.md (2026-10-12, "Step 6: blending with the balance stays the same"). For each
horizon day h, blended balance = naive + w(h) x (forecast - naive), where naive is the balance at
the origin. The same blend is applied to every simulated future, so the band, each future's
lowest balance and the chance of dipping below the buffer come from the blended futures. w(h) is
between 0 and 1, fitted per day to minimize the mean absolute error of the middle future on the
ordinary origins of the development personas, then made monotone (non-increasing in h).

    python -m eval.blend                 # both sets: paths runs, leave one persona out, the fit
    python -m eval.blend --set 12        # one set only
    python -m eval.blend apply --real data/private/backtest/keeps-coming-with-own/paths
                                         # the saved weights on a real backtest folder with
                                         # paths: evaluation only, never fitting; prints
                                         # summary numbers, writes nothing

Synthetic development personas only (held-out ids are refused). The app does not use it.
"""

import argparse
import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import (
    FUTURES,
    current_default_dir,
    format_of,
    origins,
    run_origin_with_paths,
)
from eval.metrics import (
    HORIZONS,
    PROVISIONAL_THRESHOLD,
    alert_metrics,
    by_archetype,
    forecast_metrics,
    load_records,
    restate,
)
from lookahead.db import connect
from lookahead.generator.archetypes import (
    END,
    assert_development_only,
    build_persona,
    development_ids,
)
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.merchants import is_private_path
from lookahead.settings import set_safety_buffer_cents

OUT = Path("data/backtest/blend")
WEIGHTS = Path("docs/results/blend_weights.json")
REPORT = Path("docs/results/blend_2026-10-12.md")
HORIZON = 30
GRID = tuple(k / 100 for k in range(101))  # w(h) is searched on a 0.01 grid from 0 to 1
SETS = {"12": False, "long": True}

Record = dict[str, Any]
Paths = dict[tuple[str, str], np.ndarray]


# --- The development runs, with every future's path ---


def run_persona_paths(pid: str, out_dir: Path, futures: int = FUTURES, seed: int = 42) -> str:
    """Every origin of one development persona (the merged forecaster, options off): records
    (jsonl) and every future's daily balance (npz). Skipped when both are saved already."""
    assert_development_only([pid])
    rec_path, npz_path = out_dir / f"{pid}.jsonl", out_dir / f"{pid}.paths.npz"
    if rec_path.exists() and npz_path.exists():
        return pid
    persona = build_persona(pid)
    work = out_dir / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        export, _ = write_persona(persona.persona, work, seed, persona.start, END)
        conn = connect(work / "run.db")
        store_exports(conn, pid, [(export.name, parse_export(export))], datetime(2026, 10, 1))
        set_safety_buffer_cents(conn, persona.buffer_cents)
        records, paths = [], []
        for origin in origins():
            record, p = run_origin_with_paths(conn, pid, "synthetic", origin, futures,
                                              persona.archetype, persona.held_out)  # fmt: skip
            records.append(record)
            paths.append(p)
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(work, ignore_errors=True)
    np.savez_compressed(npz_path, origins=np.array([r["origin"] for r in records]),
                        paths=np.array(paths, dtype=np.int64))  # fmt: skip
    rec_path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return pid


def run_set(long: bool, out_dir: Path, workers: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = development_ids(long=long)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, pid in enumerate(pool.map(run_persona_paths, ids, [out_dir] * len(ids)), 1):
            print(f"[{n}/{len(ids)}] {pid}", flush=True)


def load_paths(folder: Path) -> Paths:
    out: Paths = {}
    for f in sorted(folder.glob("*.paths.npz")):
        data = np.load(f)
        name = f.name.removesuffix(".paths.npz")
        for origin, p in zip(data["origins"], data["paths"], strict=True):
            out[(name, str(origin))] = p
    return out


INFORMATIONAL = {"version", "arm", "confirmed", "placements"}  # not part of a forecast


def differs_from_arm0(records: list[Record], arm0: list[Record]) -> list[str]:
    """(persona origin) of records that are not arm 0's, field for field (apart from the commit
    stamp and the arm runner's own fields); a missing record counts too."""
    at = {(r["persona"], r["origin"]): r for r in arm0}
    out = []
    for r in records:
        b = at.get((r["persona"], r["origin"]))
        if b is None or any(r.get(k) != b.get(k) for k in (set(r) | set(b)) - INFORMATIONAL):
            out.append(f"{r['persona']} {r['origin']}")
    return out


# --- The blend ---


def _blend(values: list[int], naive: int, w: list[float]) -> list[int]:
    return [round(naive + wk * (v - naive)) for v, wk in zip(values, w, strict=True)]


def blend_record(record: Record, paths: np.ndarray | None, w: list[float]) -> Record:
    """The record as the blended forecast would have read. Rounding a monotone map keeps the
    order of the futures on each day, so the blended 10th, 50th and 90th percentiles are the
    blended percentiles. Each future's lowest balance and the chance need the paths; without them
    only the band, the middle future and the single path change (and the record says so)."""
    naive = record["start_balance_cents"]
    new = dict(record)
    for key in ("p10", "p50", "p90", "point"):
        new[key] = _blend(record[key], naive, w)
    new["blend"] = list(w)
    if paths is None:
        new["blended"] = "band only"
        return new
    weights = np.asarray(w, dtype=np.float64)
    blended = np.round(naive + weights * (paths.astype(np.float64) - naive))
    lows = np.sort(blended.min(axis=1)).astype(np.int64)
    new["lowest_cents"] = [int(x) for x in lows]
    new["median_lowest_cents"] = int(lows[round(0.5 * (len(lows) - 1))])
    new["shortfall_probability"] = float(np.mean(lows < record["buffer_cents"]))
    new["blended"] = "full"
    return new


def raw_weights(records: list[Record]) -> list[float]:
    """w(h) for h = 1..30 on these records' ordinary origins: the grid value with the lowest mean
    absolute error of the blended middle future (ties: the larger w, closer to the forecast)."""
    ordinary = [r for r in records if not r["in_shortfall_at_origin"]]
    if not ordinary:
        return [1.0] * HORIZON
    naive = np.array([r["start_balance_cents"] for r in ordinary], dtype=np.float64)
    out = []
    for k in range(HORIZON):
        p50 = np.array([r["p50"][k] for r in ordinary], dtype=np.float64)
        real = np.array([r["realized"][k] for r in ordinary], dtype=np.float64)
        errors = [float(np.mean(np.abs(naive + w * (p50 - naive) - real))) for w in GRID]
        best = min(range(len(GRID)), key=lambda i: (errors[i], -GRID[i]))
        out.append(GRID[best])
    return out


def non_increasing(values: list[float]) -> list[float]:
    """The closest non-increasing sequence in squared error (pool adjacent violators, equal
    weights), clipped to [0, 1]."""
    blocks: list[list[float]] = []  # [sum, count]
    for v in values:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    out = []
    for s, c in blocks:
        out += [min(1.0, max(0.0, s / c))] * int(c)
    return out


def fit_weights(records: list[Record]) -> list[float]:
    return non_increasing(raw_weights(records))


def leave_one_out(records: list[Record], paths: Paths) -> tuple[list[Record], list[Record]]:
    """For each persona: weights fitted on the other personas, applied to all its origins.
    Returns the held-back blended records and the weights of each fold."""
    personas = sorted({r["persona"] for r in records})
    assert_development_only(personas)
    held, folds = [], []
    for pid in personas:
        w = fit_weights([r for r in records if r["persona"] != pid])
        folds.append({"persona": pid, "weights": w})
        held += [blend_record(r, paths[(r["persona"], r["origin"])], w)
                 for r in records if r["persona"] == pid]  # fmt: skip
    return held, folds


# --- Reporting ---


def _d(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _p(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def summary_rows(records: list[Record]) -> list[tuple[str, str]]:
    """The comparison on ordinary origins, with the alerts of the provisional rule (both
    definitions of caught)."""
    ordinary = [r for r in records if not r["in_shortfall_at_origin"]]
    m = forecast_metrics(ordinary)
    if not m.get("n_origins"):
        return [("n (ordinary origins)", "0")]
    e = m["error"]
    a = alert_metrics(records, PROVISIONAL_THRESHOLD, True)
    return [
        ("n (ordinary origins, personas or accounts)", f"{m['n_origins']}, {m['n_personas']}"),
        ("Error, middle future, mean: 7 / 14 / 30 days",
         " / ".join(_d(e[h]["mean_p50"]) for h in HORIZONS)),
        ("Skill vs balance stays: 7 / 14 / 30 days",
         " / ".join(f"{e[h]['skill_p50']:.2f}" if e[h]["skill_p50"] is not None else "-"
                    for h in HORIZONS)),
        ("Lowest balance: bias / typical miss",
         f"{_d(m['lowest']['bias'])} / {_d(m['lowest']['mae'])}"),
        ("80% band coverage: 7 / 14 / 30 / all days",
         " / ".join(_p(m["coverage"][h]) for h in (*HORIZONS, "all"))),
        ("80% band width: 7 / 14 / 30 days", " / ".join(_d(m["width"][h]) for h in HORIZONS)),
        ("Path-level coverage", _p(m["path_coverage"])),
        ("Brier (reference)", f"{m['brier']['brier']:.3f} ({m['brier']['reference']:.3f})"),
        ("Alerts, provisional rule: recall on ahead (caught of episodes)",
         f"{_p(a['recall_on'])} ({a['caught_on']} of {a['shortfall_episodes']})"),
        ("Alerts: recall opened ahead (caught)", f"{_p(a['recall'])} ({a['caught']})"),
        ("Alerts: precision (true of alert episodes); false per 30 days",
         f"{_p(a['precision'])} ({a['alert_episodes'] - a['false_alerts']} of "
         f"{a['alert_episodes']}); " + ("-" if a["false_per_30_days"] is None
                                       else f"{a['false_per_30_days']:.3f}")),
    ]  # fmt: skip


def _table(columns: dict[str, list[Record]]) -> list[str]:
    rows = {name: dict(summary_rows(recs)) for name, recs in columns.items()}
    labels = list(next(iter(rows.values())))
    out = ["| | " + " | ".join(columns) + " |", "|---|" + "---|" * len(columns)]
    for label in labels:
        out.append(f"| {label} | " + " | ".join(rows[n].get(label, "-") for n in columns) + " |")
    return out


def _archetype_table(base: list[Record], blended: list[Record]) -> list[str]:
    out = ["| Archetype (ordinary origins, personas) | 30-day error: arm 0 / blended | skill at "
           "30 days | lowest-balance bias | coverage all days | Brier | recall on ahead "
           "(provisional rule) |", "|---|---|---|---|---|---|---|"]  # fmt: skip
    b_by = by_archetype([r for r in base if not r["in_shortfall_at_origin"]])
    x_by = by_archetype([r for r in blended if not r["in_shortfall_at_origin"]])
    for code in b_by:
        mb, mx = forecast_metrics(b_by[code]), forecast_metrics(x_by[code])
        ab = alert_metrics([r for r in base if r["archetype"] == code], PROVISIONAL_THRESHOLD)
        ax = alert_metrics([r for r in blended if r["archetype"] == code], PROVISIONAL_THRESHOLD)

        def skill(m: dict[str, Any]) -> str:
            s = m["error"][30]["skill_p50"]
            return "-" if s is None else f"{s:.2f}"

        out.append(
            f"| {code} ({mb['n_origins']}, {mb['n_personas']}) | "
            f"{_d(mb['error'][30]['mean_p50'])} / {_d(mx['error'][30]['mean_p50'])} | "
            f"{skill(mb)} / {skill(mx)} | {_d(mb['lowest']['bias'])} / "
            f"{_d(mx['lowest']['bias'])} | {_p(mb['coverage']['all'])} / "
            f"{_p(mx['coverage']['all'])} | {mb['brier']['brier']:.3f} / "
            f"{mx['brier']['brier']:.3f} | {_p(ab['recall_on'])} ({ab['caught_on']}/"
            f"{ab['shortfall_episodes']}) / {_p(ax['recall_on'])} ({ax['caught_on']}/"
            f"{ax['shortfall_episodes']}) |"
        )
    return out


def _w(ws: list[float]) -> str:
    return ", ".join(f"{w:.2f}" for w in ws)


def set_report(name: str, records: list[Record], held: list[Record], folds: list[Record],
               final: list[float], raw: list[float]) -> list[str]:  # fmt: skip
    days = (1, 7, 14, 30)
    spread = {d: (min(f["weights"][d - 1] for f in folds), max(f["weights"][d - 1] for f in folds))
              for d in days}  # fmt: skip
    return [
        f"## {name}", "",
        f"Weights fitted on all {len({r['persona'] for r in records})} development personas "
        f"(the ones carried forward), non-increasing in h, days 1 to 30: {_w(final)}.", "",
        f"Per-day fit before smoothing: {_w(raw)}.", "",
        "Across the leave-one-out folds, w at day " + "; ".join(
            f"{d}: {lo:.2f} to {hi:.2f}" for d, (lo, hi) in spread.items()) + ".", "",
        "Leave one persona out (weights fitted on the other personas, applied to the held-back "
        "one), against arm 0:", "",
        *_table({"arm 0 (merged forecaster)": records, "blended (leave one persona out)": held}),
        "", f"### {name}, by archetype (arm 0 / blended)", "",
        *_archetype_table(records, held), "",
    ]  # fmt: skip


# --- Real data: evaluation only ---


def apply_real(folder: Path, weights_path: Path = WEIGHTS) -> int:
    """The saved weights (fitted on synthetic development personas) on a real backtest folder
    with paths. Prints summary numbers only (own buffer and $0), writes nothing, fits nothing."""
    if not is_private_path(folder):
        print("Refusing a real-data folder outside data/private/. Nothing was read.")
        return 1
    if not any(folder.glob("*.paths.npz")):
        print("No saved paths in that folder: run `python -m eval.backtest_real --paths` first.")
        return 1
    saved = json.loads(weights_path.read_text(encoding="utf-8"))
    records = load_records(folder)
    paths = load_paths(folder)
    with_paths = [r for r in records if (r["persona"], r["origin"]) in paths]
    print(f"{len({r['persona'] for r in with_paths})} account(s), {len(with_paths)} origins with "
          "every future's path; weights fitted on synthetic development personas only")  # fmt: skip
    columns: dict[str, list[Record]] = {"Before (merged forecaster)": with_paths}
    for name in ("12", "long"):
        if name in saved:
            w = saved[name]["weights"]
            columns[f"Blended, {name}-month weights" if name == "12" else
                    "Blended, 24-month weights"] = [
                blend_record(r, paths[(r["persona"], r["origin"])], w) for r in with_paths
            ]  # fmt: skip
    for buffer_name, restated in (("own buffer", False), ("buffer $0", True)):
        print()
        print(f"Real, {buffer_name} (summary numbers only):")
        cols = {k: [restate(r, 0) for r in v] if restated else v for k, v in columns.items()}
        for line in _table(cols):
            print(line)
    return 0


def arm0_folder(long: bool) -> Path:
    """Arm 0: the records of the current default forecaster."""
    return current_default_dir(long)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.blend", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("action", nargs="?", default="fit", choices=("fit", "apply"))
    parser.add_argument("--set", choices=("12", "long", "both"), default="both")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--real", type=Path, default=None, help="a real folder with paths")
    parser.add_argument("--weights", type=Path, default=WEIGHTS)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    if args.action == "apply":
        if args.real is None:
            parser.error("apply needs --real <folder>")
        return apply_real(args.real, args.weights)
    names = ["12", "long"] if args.set == "both" else [args.set]
    saved = json.loads(args.weights.read_text(encoding="utf-8")) if args.weights.exists() else {}
    lines = ['# Step 6: blending with "the balance stays the same" (synthetic, development)', "",
             "Made with `python -m eval.blend`. Method in DECISIONS.md (2026-10-12, step 6). 20 "
             "development personas per set, 40 weekly origins, 500 futures, seed 42, the merged "
             "forecaster (keeps-coming on, options off). Forecast metrics on ordinary origins; "
             "alerts through the app's engine with the provisional rule, ordinary origins. Each "
             "set on its own. Not used by the app.", ""]  # fmt: skip
    for name in names:
        long = SETS[name]
        folder = OUT / name
        run_set(long, folder, args.workers)
        records = load_records(folder)
        assert all(r["format"] == format_of(r["persona"]) for r in records)
        arm0_dir = arm0_folder(long)
        if arm0_dir.exists():  # the check fixed before computing: these are arm 0's forecasts
            bad = differs_from_arm0(records, load_records(arm0_dir))
            if bad:
                print(f"{name}: {len(bad)} records differ from arm 0 (e.g. {bad[0]}): stopped")
                return 1
            print(f"{name}: all {len(records)} records equal arm 0's")
        paths = load_paths(folder)
        held, folds = leave_one_out(records, paths)
        raw = raw_weights(records)
        final = non_increasing(raw)
        saved[name] = {"weights": final, "raw": raw, "smoothing": "non-increasing (PAVA)",
                       "fitted_on": {"personas": len({r["persona"] for r in records}),
                                     "origins": len(records),
                                     "data": "synthetic development personas only"}}  # fmt: skip
        lines += set_report("24-month set" if long else "12-month set", records, held, folds,
                            final, raw)  # fmt: skip
    args.weights.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {args.weights}, {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
