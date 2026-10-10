"""Two forecasters side by side, each raw and calibrated (synthetic development personas).

Reads two folders written by `python -m eval.calibrate` (the saved run and its
leave-one-persona-out records) and prints one table: error, skill, band coverage and width,
path-level coverage, lowest-balance bias, Brier, and alerts. Raw alerts use the app's provisional
rule; calibrated alerts use the threshold the objective picks on that run's own curve.

    python -m eval.compare_runs --a data/backtest/calibration --a-name current \\
        --b data/backtest/calibration-income --b-name "income prototype" --out <file.md>
"""

import argparse
import statistics
import sys
from pathlib import Path
from typing import Any

from eval.metrics import (
    PROVISIONAL_THRESHOLD,
    alert_metrics,
    by_archetype,
    forecast_metrics,
    group_origins,
    load_records,
)
from eval.thresholds import choose, curve, load_lopo
from lookahead.forecast.calibration import Calibration

Record = dict[str, Any]


def _d(x: float | None) -> str:
    if x is None:
        return "-"
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def column(records: list[Record], alerts: Record, threshold: str) -> list[str]:
    """One column of the table (ordinary origins)."""
    m = forecast_metrics(group_origins(records)["ordinary"])
    e = m["error"]
    leads = alerts["lead_times"]
    return [
        f"{m['n_origins']} origins, {m['n_personas']} personas",
        " / ".join(_d(e[h]["mean_p50"]) for h in (7, 14, 30)),
        " / ".join(f"{e[h]['skill_p50']:.2f}" for h in (7, 14, 30)),
        " / ".join(_pct(m["coverage"][h]) for h in (7, 14, 30, "all")),
        " / ".join(_d(m["width"][h]) for h in (7, 14, 30)),
        _pct(m["path_coverage"]),
        f"{_d(m['lowest']['bias'])} / {_d(m['lowest']['mae'])}",
        f"{m['brier']['brier']:.3f}",
        threshold,
        f"{_pct(alerts['recall'])} ({alerts['caught']}/{alerts['shortfall_episodes']})",
        f"{_pct(alerts['precision'])} ({alerts['alert_episodes'] - alerts['false_alerts']}/"
        f"{alerts['alert_episodes']})",
        f"{alerts['false_per_30_days']:.3f}",
        "-" if not leads else f"{statistics.mean(leads):.1f}",
    ]


ROWS = (
    "n (ordinary origins)",
    "Error, middle future, mean: 7 / 14 / 30 days",
    "Skill vs balance-stays: 7 / 14 / 30 days",
    "80% band coverage: 7 / 14 / 30 days / all",
    "Band width: 7 / 14 / 30 days",
    "Path-level coverage",
    "Lowest balance: bias / typical miss",
    "Brier",
    "Alert rule",
    "Recall",
    "Precision",
    "False alerts per 30 days",
    "Lead time, days (mean)",
)


def run_columns(folder: Path, name: str) -> list[tuple[str, list[str], list[Record], list[Record]]]:
    raw = load_records(folder)
    cal = Calibration.load(folder / "calibration.json")
    variant = cal.recalibration.kind if cal.recalibration.kind != "none" else "widened"
    calibrated = load_lopo(folder, variant)
    best = choose(curve(calibrated))
    t = 1.0 if best is None else best["threshold"]
    return [
        (f"{name}, raw", column(raw, alert_metrics(raw, PROVISIONAL_THRESHOLD, True),
                                "provisional (20% or median)"), raw, raw),
        (f"{name}, calibrated", column(calibrated, alert_metrics(calibrated, t, False),
                                       f"calibrated chance > {t:.0%}"), calibrated, raw),
    ]  # fmt: skip


def table(cols: list[tuple[str, list[str], list[Record], list[Record]]]) -> list[str]:
    lines = ["| Metric | " + " | ".join(c[0] for c in cols) + " |",
             "|---|" + "---|" * len(cols)]  # fmt: skip
    for i, label in enumerate(ROWS):
        lines.append(f"| {label} | " + " | ".join(c[1][i] for c in cols) + " |")
    return lines


def archetype_table(cols: list[tuple[str, list[str], list[Record], list[Record]]]) -> list[str]:
    """30-day error, all-day coverage, lowest-balance bias and Brier per archetype."""
    lines = ["Per archetype (ordinary origins): 30-day error / all-day coverage / lowest-balance "
             "bias / Brier.", "", "| Archetype | " + " | ".join(c[0] for c in cols) + " |",
             "|---|" + "---|" * len(cols)]  # fmt: skip
    groups = [by_archetype(group_origins(c[2])["ordinary"]) for c in cols]
    for arch in groups[0]:
        cells = []
        for g in groups:
            m = forecast_metrics(g[arch])
            cells.append(f"{_d(m['error'][30]['mean_p50'])} / {_pct(m['coverage']['all'])} / "
                         f"{_d(m['lowest']['bias'])} / {m['brier']['brier']:.3f}")  # fmt: skip
        n = forecast_metrics(groups[0][arch])
        lines.append(f"| {arch} ({n['n_origins']}, {n['n_personas']}) | " + " | ".join(cells)
                     + " |")  # fmt: skip
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.compare_runs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--a", type=Path, required=True)
    parser.add_argument("--a-name", default="A")
    parser.add_argument("--b", type=Path, required=True)
    parser.add_argument("--b-name", default="B")
    parser.add_argument("--title", default="Two forecasters, raw and calibrated")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    cols = run_columns(args.a, args.a_name) + run_columns(args.b, args.b_name)
    text = "\n".join([f"# {args.title}", "", *table(cols), "", *archetype_table(cols), ""])
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"written: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
