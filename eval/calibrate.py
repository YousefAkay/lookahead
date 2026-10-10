"""The calibration step: rerun the development backtest, fit, evaluate leave-one-persona-out.

Method in DECISIONS.md (2026-10-10, "Calibration of the band and the shortfall probability").
Synthetic development personas only; held-out personas are refused. The app is not switched.

    python -m eval.calibrate                    # from scratch: backtest (with every future's
                                                # path), fit, evaluate, write the results
    python -m eval.calibrate --reuse            # fit and evaluate on the saved run
    python -m eval.calibrate apply --real data/private/backtest/paths
                                                # apply the saved calibration to a real backtest
                                                # folder: evaluation only, never fitting

Writes docs/results/calibration.json (the fitted calibration: 30 multipliers and the probability
map) and docs/results/calibration_2026-10-10.md (before and after, leave-one-persona-out). The
saved run and the leave-one-persona-out records go to data/backtest/calibration (not committed).
"""

import argparse
import json
import math
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import FUTURES, origins, run_origin_with_paths, version_settings
from eval.metrics import (
    BIN_EDGES,
    by_archetype,
    forecast_metrics,
    group_origins,
    load_records,
    real_results_dir,
    restate,
)
from lookahead.db import connect
from lookahead.forecast.calibration import (
    EPS,
    Calibration,
    Recalibration,
    calibrate_record,
    fit_multiplier,
    needed_multiplier,
)
from lookahead.generator.archetypes import (
    END,
    START,
    assert_development_only,
    build_persona,
    development_ids,
)
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

OUT = Path("data/backtest/calibration")
FIT_PATH = Path("docs/results/calibration.json")
REPORT = Path("docs/results/calibration_2026-10-10.md")
HORIZON = 30
KEEP_MARGIN = 0.002  # a recalibration must lower the Brier score by at least this much
VARIANTS = ("widened", "logistic", "isotonic")

Record = dict[str, Any]


# --- The development run, with paths ---


def run_persona_paths(pid: str, out_dir: Path, futures: int, seed: int) -> str:
    """Every origin of one development persona: records (jsonl) and paths (npz)."""
    assert_development_only([pid])
    persona = build_persona(pid)
    work = out_dir / "work" / pid
    work.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        export, _ = write_persona(persona.persona, work, seed, START, END)
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
    (out_dir / f"{pid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    np.savez_compressed(out_dir / f"{pid}.paths.npz", origins=np.array([r["origin"] for r in
                        records]), paths=np.array(paths, dtype=np.int64))  # fmt: skip
    return pid


def run_development(out_dir: Path, futures: int, seed: int, workers: int) -> None:
    """The 20 development personas from scratch (the folder is emptied first)."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    (out_dir / "settings.json").write_text(json.dumps(
        {"futures": futures, "seed": seed, "method": "weekday", **version_settings()}),
        encoding="utf-8")  # fmt: skip
    ids = development_ids()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(run_persona_paths, pid, out_dir, futures, seed) for pid in ids]
        for n, job in enumerate(jobs, start=1):
            print(f"[{n}/{len(ids)}] {job.result()}", flush=True)


def load_run(out_dir: Path) -> tuple[list[Record], dict[tuple[str, str], np.ndarray]]:
    """The saved records and, for each (persona, origin), its paths (futures x days)."""
    records = load_records(out_dir)
    assert_development_only(sorted({r["persona"] for r in records}))
    paths: dict[tuple[str, str], np.ndarray] = {}
    for f in sorted(out_dir.glob("*.paths.npz")):
        data = np.load(f)
        pid = f.name.removesuffix(".paths.npz")
        for origin, p in zip(data["origins"], data["paths"], strict=True):
            paths[(pid, str(origin))] = p
    return records, paths


# --- Fitting (array versions of lookahead/forecast/calibration.py, checked equal in tests) ---


def fit_multipliers(records: list[Record]) -> list[float]:
    """s(h) for h = 1..30: the 80th percentile of the multiplier each origin needed."""
    out = []
    for k in range(HORIZON):
        needed = [needed_multiplier(r["p10"][k], r["p50"][k], r["p90"][k], r["realized"][k])
                  for r in records]  # fmt: skip
        out.append(fit_multiplier(needed))
    return [m if math.isfinite(m) else 1000.0 for m in out]


def lows_array(paths: np.ndarray, p50: list[int], multipliers: list[float]) -> np.ndarray:
    """Each widened future's lowest balance, sorted (same rounding as the pure version)."""
    m = np.asarray(p50, dtype=np.float64)
    s = np.asarray(multipliers, dtype=np.float64)
    widened = np.round(m + s * (paths.astype(np.float64) - m))
    return np.sort(widened.min(axis=1)).astype(np.int64)


def widened_probability(record: Record, paths: np.ndarray, multipliers: list[float]) -> float:
    lows = lows_array(paths, record["p50"], multipliers)
    return float(np.mean(lows < record["buffer_cents"]))


def outcome(record: Record) -> int:
    return 1 if record["realized_first_below_buffer"] is not None else 0


def fit_recalibration(probs: list[float], outcomes: list[int], kind: str) -> Recalibration:
    """A logistic map on the log-odds, or an isotonic one. With one class only, no map."""
    if kind == "widened" or len(set(outcomes)) < 2:
        return Recalibration("none")
    p = np.clip(np.asarray(probs), EPS, 1 - EPS)
    y = np.asarray(outcomes)
    if kind == "logistic":
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(C=1e6, max_iter=1000)
        model.fit(np.log(p / (1 - p)).reshape(-1, 1), y)
        return Recalibration("logistic", float(model.coef_[0][0]), float(model.intercept_[0]))
    from sklearn.isotonic import IsotonicRegression

    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(np.asarray(probs), y)
    return Recalibration("isotonic", x=tuple(float(v) for v in iso.X_thresholds_),
                         y=tuple(float(v) for v in iso.y_thresholds_))  # fmt: skip


def fit(records: list[Record], paths: dict[tuple[str, str], np.ndarray], kind: str
        ) -> Calibration:  # fmt: skip
    """Bands on every origin of these records; the probability map on their ordinary origins."""
    s = fit_multipliers(records)
    ordinary = [r for r in records if not r["in_shortfall_at_origin"]]
    probs = [widened_probability(r, paths[(r["persona"], r["origin"])], s) for r in ordinary]
    recal = fit_recalibration(probs, [outcome(r) for r in ordinary], kind)
    return Calibration(tuple(s), recal, {
        "personas": len({r["persona"] for r in records}), "origins": len(records),
        "ordinary_origins": len(ordinary), "data": "synthetic development personas only",
    })  # fmt: skip


def apply(record: Record, paths: np.ndarray | None, cal: Calibration) -> Record:
    """`calibrate_record`, with the widened lows from the array version (faster)."""
    if paths is None:
        return calibrate_record(record, None, cal)
    banded = calibrate_record(record, None, cal)
    lows = lows_array(paths, record["p50"], list(cal.multipliers))
    raw = float(np.mean(lows < record["buffer_cents"]))
    banded["lowest_cents"] = [int(x) for x in lows]
    banded["median_lowest_cents"] = int(lows[round(0.5 * (len(lows) - 1))])
    banded["widened_probability"] = raw
    banded["shortfall_probability"] = cal.recalibration(raw)
    banded["calibrated"] = "full"
    return banded


# --- Leave one persona out ---


def leave_one_out(records: list[Record], paths: dict[tuple[str, str], np.ndarray]
                  ) -> tuple[dict[str, list[Record]], list[dict[str, Any]]]:  # fmt: skip
    """For each persona: fit on the others (each variant of the probability map), apply to it.
    Returns the held-back records per variant, and the multipliers of each fold."""
    personas = sorted({r["persona"] for r in records})
    out: dict[str, list[Record]] = {v: [] for v in VARIANTS}
    folds = []
    for pid in personas:
        train = [r for r in records if r["persona"] != pid]
        test = [r for r in records if r["persona"] == pid]
        s = fit_multipliers(train)
        ordinary = [r for r in train if not r["in_shortfall_at_origin"]]
        probs = [widened_probability(r, paths[(r["persona"], r["origin"])], s) for r in ordinary]
        ys = [outcome(r) for r in ordinary]
        folds.append({"persona": pid, "multipliers": s})
        for v in VARIANTS:
            cal = Calibration(tuple(s), fit_recalibration(probs, ys, v))
            out[v] += [apply(r, paths[(r["persona"], r["origin"])], cal) for r in test]
    return out, folds


def raw_map_scores(records: list[Record]) -> dict[str, dict[str, float]]:
    """Comparison only, added after seeing the first results (DECISIONS.md): a probability map on
    the raw, unwidened chance, leave-one-persona-out. Brier on ordinary origins, overall and per
    archetype. Never chosen by `choose_variant`."""
    personas = sorted({r["persona"] for r in records})
    held: dict[str, list[tuple[float, int, str]]] = {"raw": [], "raw + logistic": [],
                                                     "raw + isotonic": []}  # fmt: skip
    for pid in personas:
        train = [r for r in records if r["persona"] != pid and not r["in_shortfall_at_origin"]]
        test = [r for r in records if r["persona"] == pid and not r["in_shortfall_at_origin"]]
        probs = [r["shortfall_probability"] for r in train]
        ys = [outcome(r) for r in train]
        for name, kind in (("raw", "widened"), ("raw + logistic", "logistic"),
                           ("raw + isotonic", "isotonic")):  # fmt: skip
            recal = fit_recalibration(probs, ys, kind)
            held[name] += [(recal(r["shortfall_probability"]), outcome(r), r["archetype"])
                           for r in test]  # fmt: skip
    scores: dict[str, dict[str, float]] = {}
    for name, rows in held.items():
        by: dict[str, list[float]] = {"all": []}
        for p, y, arch in rows:
            by["all"].append((p - y) ** 2)
            by.setdefault(arch, []).append((p - y) ** 2)
        scores[name] = {k: sum(v) / len(v) for k, v in by.items()}
    return scores


def brier(records: list[Record]) -> float:
    return sum((r["shortfall_probability"] - outcome(r)) ** 2 for r in records) / len(records)


def choose_variant(lopo: dict[str, list[Record]]) -> tuple[str, dict[str, float]]:
    """The rule fixed in DECISIONS.md before fitting: a map is kept only if it lowers the
    leave-one-persona-out Brier score (ordinary origins) by at least 0.002; isotonic only if it
    beats logistic by more than 0.002."""
    scores = {v: brier([r for r in rows if not r["in_shortfall_at_origin"]])
              for v, rows in lopo.items()}  # fmt: skip
    best_map = "isotonic" if scores["isotonic"] < scores["logistic"] - KEEP_MARGIN else "logistic"
    if scores[best_map] <= scores["widened"] - KEEP_MARGIN:
        return best_map, scores
    return "widened", scores


# --- The report ---


def _d(x: float | None) -> str:
    if x is None:
        return "-"
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def comparison_rows(before: list[Record], after: list[Record]) -> list[str]:
    lines = ["| Metric | Before | After |", "|---|---|---|"]
    for group in ("ordinary", "all"):
        b = forecast_metrics(group_origins(before)[group])
        a = forecast_metrics(group_origins(after)[group])
        label = "origins not already in shortfall" if group == "ordinary" else "all origins"
        lines.append(f"| **{label}** (n) | {b['n_origins']} origins, {b['n_personas']} personas "
                     f"| {a['n_origins']} origins, {a['n_personas']} personas |")  # fmt: skip
        for h in (7, 14, 30, "all"):
            lines.append(f"| 80% band coverage, {h} {'days' if h != 'all' else ''} | "
                         f"{_pct(b['coverage'][h])} | {_pct(a['coverage'][h])} |")  # fmt: skip
        for h in (7, 14, 30):
            lines.append(f"| band width (mean), {h} days | {_d(b['width'][h])} | "
                         f"{_d(a['width'][h])} |")  # fmt: skip
        lines.append(f"| path-level coverage | {_pct(b['path_coverage'])} | "
                     f"{_pct(a['path_coverage'])} |")  # fmt: skip
        lines.append(f"| lowest balance: bias / typical miss | {_d(b['lowest']['bias'])} / "
                     f"{_d(b['lowest']['mae'])} | {_d(a['lowest']['bias'])} / "
                     f"{_d(a['lowest']['mae'])} |")  # fmt: skip
        bb, ab = b["brier"], a["brier"]
        lines.append(f"| Brier (reference, skill) | {bb['brier']:.3f} ({bb['reference']:.3f}, "
                     f"{bb['skill']:.2f}) | {ab['brier']:.3f} ({ab['reference']:.3f}, "
                     f"{ab['skill']:.2f}) |")  # fmt: skip
    return lines


def archetype_rows(before: list[Record], after: list[Record]) -> list[str]:
    lines = ["| Archetype | Origins (personas) | Brier before | Brier after | band coverage, all "
             "days, before / after | lowest-balance bias before / after |",
             "|---|---|---|---|---|---|"]  # fmt: skip
    b_groups = by_archetype(group_origins(before)["ordinary"])
    a_groups = by_archetype(group_origins(after)["ordinary"])
    for arch, rows in b_groups.items():
        b, a = forecast_metrics(rows), forecast_metrics(a_groups[arch])
        lines.append(f"| {arch} | {b['n_origins']} ({b['n_personas']}) | "
                     f"{b['brier']['brier']:.3f} | {a['brier']['brier']:.3f} | "
                     f"{_pct(b['coverage']['all'])} / {_pct(a['coverage']['all'])} | "
                     f"{_d(b['lowest']['bias'])} / {_d(a['lowest']['bias'])} |")  # fmt: skip
    return lines


def reliability_rows(named: list[tuple[str, list[Record]]]) -> list[str]:
    head = "| Forecast bin | " + " | ".join(n for n, _ in named) + " |"
    lines = ["Cell: origins, mean forecast, observed share (origins not already in shortfall).",
             "", head, "|---|" + "---|" * len(named)]  # fmt: skip
    tables = [forecast_metrics(group_origins(rows)["ordinary"])["brier"]["reliability"]
              for _, rows in named]  # fmt: skip
    for i in range(BIN_EDGES):
        cells = [f"{t[i]['n']}, {_pct(t[i]['mean_forecast'])}, {_pct(t[i]['observed'])}"
                 for t in tables]  # fmt: skip
        lines.append(f"| {i * 20}-{i * 20 + 20}% | " + " | ".join(cells) + " |")
    return lines


def report(records: list[Record], lopo: dict[str, list[Record]], folds: list[dict[str, Any]],
           chosen: str, scores: dict[str, float], final: Calibration,
           raw_scores: dict[str, dict[str, float]] | None = None) -> str:  # fmt: skip
    after = lopo[chosen]
    s = final.multipliers
    fold_range = {
        h: (
            min(fold["multipliers"][h - 1] for fold in folds),
            max(fold["multipliers"][h - 1] for fold in folds),
        )
        for h in (7, 14, 30)
    }
    n_people = len({r["persona"] for r in records})
    out = [
        "# Calibration: before and after (leave-one-persona-out, synthetic development personas)",
        "",
        f"Made with `python -m eval.calibrate`: {n_people} development personas, {len(records)} "
        f"origins, {records[0]['futures']} futures, seed 42, weekday method. Method: "
        "DECISIONS.md, 2026-10-10. Each persona is scored with a calibration fitted on the other "
        "19. Held-out personas were not used. The app is not switched to it.",
        "",
        "## Band multipliers (fitted on all 20 development personas)",
        "",
        "s(h) by horizon day: " + ", ".join(f"{h}: {s[h - 1]:.2f}" for h in range(1, 31)),
        "",
        "Across the 20 leave-one-out fits: "
        + "; ".join(f"day {h} from {lo:.2f} to {hi:.2f}" for h, (lo, hi) in fold_range.items())
        + ".",
        "",
        "## Probability map",
        "",
        "Leave-one-persona-out Brier score, origins not already in shortfall "
        f"({sum(not r['in_shortfall_at_origin'] for r in records)} origins): "
        + ", ".join(
            f"widened only {scores['widened']:.4f}"
            if v == "widened"
            else f"widened + {v} {scores[v]:.4f}"
            for v in VARIANTS
        )
        + ".",
        "",
        f"Chosen by the rule fixed before fitting: **{chosen if chosen != 'widened' else 'no map'}"
        "**." + (f" Final map: {final.recalibration.kind}." if chosen != "widened" else ""),
        "",
        f"## Before and after ({'widened + ' + chosen if chosen != 'widened' else 'widened'})",
        "",
        *comparison_rows(records, after),
        "",
        "## By archetype (origins not already in shortfall)",
        "",
        *archetype_rows(records, after),
        "",
        "## Reliability of the shortfall probability",
        "",
        *reliability_rows([("Before", records)] + [(f"After: {v}", lopo[v]) for v in VARIANTS]),
        "",
    ]
    if raw_scores:
        archs = sorted(k for k in raw_scores["raw"] if k != "all")
        out += [
            "## Comparison only: a map on the raw chance (added after seeing the results above)",
            "",
            "Not part of the method written before fitting and never chosen by it. Brier score, "
            "leave-one-persona-out, origins not already in shortfall.",
            "",
            "| Probability | all | " + " | ".join(archs) + " |",
            "|---|---|" + "---|" * len(archs),
            *[f"| {name} | {sc['all']:.4f} | " + " | ".join(f"{sc[a]:.3f}" for a in archs) + " |"
              for name, sc in raw_scores.items()],
            f"| widened + {chosen} (the method) | {scores[chosen]:.4f} | " + " | ".join(
                f"{forecast_metrics(rows)['brier']['brier']:.3f}" for rows in by_archetype(
                    group_origins(after)["ordinary"]).values()) + " |",
            "",
        ]  # fmt: skip
    return "\n".join(out)


# --- Applying to a real folder (evaluation only) ---


def load_real_paths(folder: Path) -> dict[tuple[str, str], np.ndarray]:
    paths: dict[tuple[str, str], np.ndarray] = {}
    for f in sorted(folder.glob("*.paths.npz")):
        data = np.load(f)
        account = f.name.removesuffix(".paths.npz")
        for origin, p in zip(data["origins"], data["paths"], strict=True):
            paths[(account, str(origin))] = p
    return paths


def restate_calibrated(record: Record, buffer_cents: int, cal: Calibration) -> Record:
    """A calibrated record at another buffer (real data: $0): the widened lows give the chance,
    which then goes through the same probability map."""
    new = restate(record, buffer_cents)
    if record.get("calibrated") == "full":
        new["shortfall_probability"] = cal.recalibration(new["shortfall_probability"])
    return new


def real_summary(name: str, rows: list[Record]) -> list[str]:
    m = forecast_metrics(group_origins(rows)["ordinary"])
    if not m.get("n_origins"):
        return [f"  {name}: no ordinary origins"]
    cov = " / ".join(_pct(m["coverage"][h]) for h in (7, 14, 30, "all"))
    rel = "; ".join(f"{i * 20}-{i * 20 + 20}%: {b['n']}, {_pct(b['observed'])}"
                    for i, b in enumerate(m["brier"]["reliability"]))  # fmt: skip
    return [
        f"  {name}: {m['n_origins']} ordinary origins, {m['n_personas']} account(s)",
        f"    80% band coverage 7 / 14 / 30 days / all: {cov}; path-level "
        f"{_pct(m['path_coverage'])}",
        "    width 7 / 14 / 30 days: " + " / ".join(_d(m["width"][h]) for h in (7, 14, 30)),
        f"    lowest balance bias / typical miss: {_d(m['lowest']['bias'])} / "
        f"{_d(m['lowest']['mae'])}",
        f"    Brier {m['brier']['brier']:.3f} (reference {m['brier']['reference']:.3f})",
        f"    reliability (bin: n, observed): {rel}",
    ]


def apply_real(folder: Path, fit_path: Path) -> int:
    """Summary numbers only, before and after, at the account's buffer and at $0. Never fits."""
    folder = real_results_dir(folder)
    cal = Calibration.load(fit_path)
    records = load_records(folder)
    paths = load_real_paths(folder)
    after = [apply(r, paths.get((r["persona"], r["origin"])), cal) for r in records]
    full = sum(r.get("calibrated") == "full" for r in after)
    print("REAL BACKTEST, CALIBRATION APPLIED (evaluation only; summary numbers, no rows)")
    print(
        f"  calibration: {fit_path} (fitted on {cal.fitted_on.get('data', '?')}), "
        f"probability map: {cal.recalibration.kind}"
    )
    print(f"  {len(records)} origins; {full} with every future's path (the rest: band only)")
    if full < len(records):
        print(
            "  For the shortfall chance and the lowest balance, rerun the real backtest with "
            "paths: python -m eval.backtest_real --paths"
        )
    zero_before = [restate(r, 0) for r in records]
    zero_after = [restate_calibrated(r, 0, cal) for r in after]
    for title, rows in (
        ("Before, own buffer", records),
        ("After, own buffer", after),
        ("Before, buffer $0", zero_before),
        ("After, buffer $0", zero_after),
    ):
        print("\n".join(real_summary(title, rows)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.calibrate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", nargs="?", default="fit", choices=("fit", "apply"))
    parser.add_argument("--reuse", action="store_true", help="fit on the saved run")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--futures", type=int, default=FUTURES)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--real", type=Path, default=None, help="a real backtest folder (apply)")
    parser.add_argument("--calibration", type=Path, default=FIT_PATH)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    if args.action == "apply":
        if args.real is None:
            parser.error("apply needs --real <folder>")
        return apply_real(args.real, args.calibration)
    if not args.reuse:
        run_development(args.out, args.futures, args.seed, args.workers)
    records, paths = load_run(args.out)
    lopo, folds = leave_one_out(records, paths)
    chosen, scores = choose_variant(lopo)
    final = fit(records, paths, chosen)
    args.calibration.parent.mkdir(parents=True, exist_ok=True)
    args.calibration.write_text(final.to_json(), encoding="utf-8")
    (args.out / "lopo").mkdir(exist_ok=True)
    with (args.out / "lopo" / "lopo.jsonl").open("w", encoding="utf-8") as f:
        for v, rows in lopo.items():
            for r in rows:
                slim = {k: r[k] for k in ("persona", "archetype", "origin", "buffer_cents",
                                          "in_shortfall_at_origin", "history_days",
                                          "realized_first_below_buffer", "shortfall_probability",
                                          "median_lowest_cents", "p50", "days", "realized",
                                          "start_balance_cents")}  # fmt: skip
                f.write(json.dumps({"variant": v, **slim}) + "\n")
    raw_scores = raw_map_scores(records)
    args.report.write_text(report(records, lopo, folds, chosen, scores, final, raw_scores),
                           encoding="utf-8")  # fmt: skip
    print(f"written: {args.calibration}, {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
