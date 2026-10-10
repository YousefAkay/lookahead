"""Alert threshold analysis on calibrated probabilities (development personas, synthetic).

Objective in DECISIONS.md (2026-10-10, "Alert threshold: objective"): the threshold with the
highest recall subject to precision >= 70 percent and at most 0.1 false alerts per 30 days,
ordinary origins only, chosen leave-one-persona-out. Reads the leave-one-persona-out records that
`python -m eval.calibrate` saved; changes nothing in the app.

    python -m eval.thresholds        # writes docs/results/thresholds_2026-10-10.md
"""

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from eval.calibrate import FIT_PATH, OUT
from eval.metrics import PROVISIONAL_THRESHOLD, alert_metrics, load_records
from lookahead.forecast.calibration import Calibration
from lookahead.generator.archetypes import assert_development_only

THRESHOLDS = (0.02, *[k / 100 for k in range(5, 100, 5)])
MIN_PRECISION = 0.70
MAX_FALSE_PER_30 = 0.10
REPORT = Path("docs/results/thresholds_2026-10-10.md")

Record = dict[str, Any]


def load_lopo(out_dir: Path, variant: str) -> list[Record]:
    rows = []
    with (out_dir / "lopo" / "lopo.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.pop("variant") == variant:
                rows.append(r)
    assert_development_only(sorted({r["persona"] for r in rows}))
    return rows


def curve(records: list[Record], thresholds: tuple[float, ...] = THRESHOLDS) -> list[Record]:
    """Alert metrics at each threshold (probability-only rule)."""
    out = []
    for t in thresholds:
        m = alert_metrics(records, t, median_rule=False)
        out.append({"threshold": t, **m})
    return out


def meets(m: Record) -> bool:
    return (
        m["precision"] is not None and m["precision"] >= MIN_PRECISION
        and m["false_per_30_days"] is not None and m["false_per_30_days"] <= MAX_FALSE_PER_30
    )  # fmt: skip


def choose(points: list[Record]) -> Record | None:
    """Highest recall among the thresholds that meet both constraints; ties: higher precision,
    then the higher threshold."""
    ok = [m for m in points if meets(m) and m["recall"] is not None]
    if not ok:
        return None
    return max(ok, key=lambda m: (m["recall"], m["precision"], m["threshold"]))


def leave_one_out(records: list[Record]) -> tuple[list[Record], dict[str, float | None]]:
    """For each persona: choose on the other 19, apply to it. Returns the pooled metrics of the
    held-back personas (as one metric dict) and the threshold chosen per persona."""
    personas = sorted({r["persona"] for r in records})
    chosen: dict[str, float | None] = {}
    held_back: list[Record] = []
    for pid in personas:
        train = [r for r in records if r["persona"] != pid]
        pick = choose(curve(train))
        chosen[pid] = None if pick is None else pick["threshold"]
        test = [r for r in records if r["persona"] == pid]
        t = 1.0 if pick is None else pick["threshold"]  # no threshold: no alerts
        held_back.append(alert_metrics(test, t, median_rule=False))
    return held_back, chosen


def pool(parts: list[Record]) -> Record:
    """Add up per-persona alert metrics."""
    keys = ("shortfall_episodes", "caught", "alert_episodes", "false_alerts", "ordinary_origins")
    total = {k: sum(p[k] for p in parts) for k in keys}
    leads = sorted(x for p in parts for x in p["lead_times"])
    alerts, episodes = total["alert_episodes"], total["shortfall_episodes"]
    return {
        **total, "lead_times": leads, "n_personas": sum(p["n_personas"] for p in parts),
        "recall": total["caught"] / episodes if episodes else None,
        "precision": (alerts - total["false_alerts"]) / alerts if alerts else None,
        "false_per_30_days": total["false_alerts"] / (total["ordinary_origins"] * 7) * 30
        if total["ordinary_origins"] else None,
    }  # fmt: skip


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _lead(leads: list[int]) -> str:
    if not leads:
        return "-"
    return f"{statistics.mean(leads):.1f} ({statistics.median(leads):.0f})"


def _row(label: str, m: Record) -> str:
    return (
        f"| {label} | {_pct(m['recall'])} ({m['caught']}/{m['shortfall_episodes']}) | "
        f"{_pct(m['precision'])} ({m['alert_episodes'] - m['false_alerts']}/"
        f"{m['alert_episodes']}) | {m['false_per_30_days']:.3f} | {_lead(m['lead_times'])} |"
    )


def report(records: list[Record], raw: list[Record], variant: str) -> str:
    points = curve(records)
    best = choose(points)
    held_back, chosen = leave_one_out(records)
    lopo = pool(held_back)
    provisional = alert_metrics(raw, PROVISIONAL_THRESHOLD, median_rule=True)
    n = (f"{len({r['persona'] for r in records})} development personas, "
         f"{sum(not r['in_shortfall_at_origin'] for r in records)} ordinary origins, "
         f"{points[0]['shortfall_episodes']} shortfall episodes")  # fmt: skip
    head = (
        "| Threshold | Recall (caught/episodes) | Precision (true/alerts) | False alerts per "
        "30 days | Lead time, days: mean (median) |"
    )
    out = [
        "# Alert threshold on calibrated probabilities (synthetic, development personas)",
        "",
        f"Made with `python -m eval.thresholds` from the leave-one-persona-out records of "
        f"`python -m eval.calibrate` (probability: widened futures"
        f"{' + ' + variant + ' map' if variant != 'widened' else ''}). {n}. Rule: alert when the "
        "calibrated chance is above the threshold (no median clause), through the app's alert "
        "engine. Objective (DECISIONS.md, 2026-10-10): highest recall with precision >= 70% and "
        "<= 0.1 false alerts per 30 days. The app's provisional rule is unchanged.",
        "",
        "## The curve (all 20 personas)",
        "",
        head,
        "|---|---|---|---|---|",
        *[
            _row(
                f"{m['threshold']:.0%}"
                + (
                    " **chosen**"
                    if best is not None and m["threshold"] == best["threshold"]
                    else ""
                ),
                m,
            )
            for m in points
        ],  # fmt: skip
        "",
        "For reference, the provisional rule on the raw (uncalibrated) chances, chance above 20% "
        "or median lowest below the buffer:",
        "",
        head,
        "|---|---|---|---|---|",
        _row("provisional, raw", provisional),
        "",
        "## Result",
        "",
    ]
    if best is None:
        prec_ok = [m for m in points if m["precision"] is not None
                   and m["precision"] >= MIN_PRECISION]  # fmt: skip
        false_ok = [m for m in points if m["false_per_30_days"] <= MAX_FALSE_PER_30]
        out.append("No threshold meets both constraints. Closest options:")
        if prec_ok:
            b = max(prec_ok, key=lambda m: (m["recall"] or 0, m["threshold"]))
            out.append(
                f"- precision >= 70% alone: {b['threshold']:.0%} (recall "
                f"{_pct(b['recall'])}, {b['false_per_30_days']:.3f} false per 30 days)"
            )
        if false_ok:
            b = max(false_ok, key=lambda m: (m["recall"] or 0, m["threshold"]))
            out.append(
                f"- <= 0.1 false per 30 days alone: {b['threshold']:.0%} (recall "
                f"{_pct(b['recall'])}, precision {_pct(b['precision'])})"
            )
    else:
        out.append(
            f"On all 20 development personas the objective picks **{best['threshold']:.0%}**"
            f": recall {_pct(best['recall'])} ({best['caught']} of "
            f"{best['shortfall_episodes']} episodes), precision {_pct(best['precision'])}, "
            f"{best['false_per_30_days']:.3f} false alerts per 30 days, lead time "
            f"{_lead(best['lead_times'])} days."
        )
    picks = sorted({t for t in chosen.values() if t is not None})
    out += [
        "",
        "## Leave one persona out (threshold chosen on 19, applied to the 20th)",
        "",
        f"Thresholds chosen across the 20 folds: {', '.join(f'{t:.0%}' for t in picks) or 'none'}"
        + (
            f" ({sum(t is None for t in chosen.values())} folds found none)"
            if any(t is None for t in chosen.values())
            else ""
        )
        + ".",
        "",
        head,
        "|---|---|---|---|---|",
        _row("held-back personas, added up", lopo),
        "",
        "n is small: the shortfall episodes come from the few personas that have any, and origins "
        "within a persona overlap.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.thresholds",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dir", type=Path, default=OUT)
    parser.add_argument("--calibration", type=Path, default=FIT_PATH)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    cal = Calibration.load(args.calibration)
    variant = cal.recalibration.kind if cal.recalibration.kind != "none" else "widened"
    records = load_lopo(args.dir, variant)
    raw = load_records(args.dir)
    args.report.write_text(report(records, raw, variant), encoding="utf-8")
    print(f"written: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
