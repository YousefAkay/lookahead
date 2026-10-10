"""Alert threshold on raw chances with the new "caught" definition (step 1 of the 2026-10-11 plan).

Method in DECISIONS.md (2026-10-11, "Step 1: c) caught when an alert is on ahead of the dip"): the
threshold with the highest recall ("on ahead (new)") subject to precision >= 70 percent and at most
0.1 false alerts per 30 days, ordinary origins only, chosen leave one persona out, on the raw
(uncalibrated) chances of the development backtest of the current forecaster. Both definitions
are printed side by side; the provisional rule is its own row. The stop conditions fixed before
computing are checked and printed; if one holds, no threshold is chosen. Changes nothing in the
app; the calibrated results (`eval.thresholds`) are not overwritten.

    python -m eval.backtest                    # the development backtest (data/backtest/full)
    python -m eval.thresholds_raw              # writes docs/results/thresholds_raw_2026-10-11.md
"""

import argparse
import statistics
import sys
from pathlib import Path
from typing import Any

from eval.backtest import DEFAULT_OUT
from eval.metrics import PROVISIONAL_THRESHOLD, alert_metrics, load_records
from eval.thresholds import MAX_FALSE_PER_30, MIN_PRECISION, THRESHOLDS
from lookahead.generator.archetypes import assert_development_only

REPORT = Path("docs/results/thresholds_raw_2026-10-11.md")
HIGH = 0.80  # recall at thresholds from here up should hardly move
HIGH_RISE = 0.05  # more than 5 points is unexpected
MAX_FOLD_THRESHOLDS = 3  # more different thresholds than this across the folds is unexpected

Record = dict[str, Any]


def curve(records: list[Record], thresholds: tuple[float, ...] = THRESHOLDS) -> list[Record]:
    """Alert metrics at each threshold (probability-only rule), both definitions."""
    return [{"threshold": t, **alert_metrics(records, t, median_rule=False)} for t in thresholds]


def meets(m: Record) -> bool:
    return (
        m["precision"] is not None and m["precision"] >= MIN_PRECISION
        and m["false_per_30_days"] is not None and m["false_per_30_days"] <= MAX_FALSE_PER_30
    )  # fmt: skip


def choose(points: list[Record], recall_key: str = "recall_on") -> Record | None:
    """Highest recall (of the given definition) among the thresholds that meet both constraints;
    ties: higher precision, then the higher threshold."""
    ok = [m for m in points if meets(m) and m[recall_key] is not None]
    if not ok:
        return None
    return max(ok, key=lambda m: (m[recall_key], m["precision"], m["threshold"]))


def pool(parts: list[Record]) -> Record:
    """Add up per-persona alert metrics, both definitions."""
    keys = ("shortfall_episodes", "caught", "caught_on", "alert_episodes", "false_alerts",
            "ordinary_origins")  # fmt: skip
    total = {k: sum(p[k] for p in parts) for k in keys}
    alerts, episodes = total["alert_episodes"], total["shortfall_episodes"]
    return {
        **total, "n_personas": sum(p["n_personas"] for p in parts),
        "lead_times": sorted(x for p in parts for x in p["lead_times"]),
        "lead_times_on": sorted(x for p in parts for x in p["lead_times_on"]),
        "recall": total["caught"] / episodes if episodes else None,
        "recall_on": total["caught_on"] / episodes if episodes else None,
        "precision": (alerts - total["false_alerts"]) / alerts if alerts else None,
        "false_per_30_days": total["false_alerts"] / (total["ordinary_origins"] * 7) * 30
        if total["ordinary_origins"] else None,
    }  # fmt: skip


def leave_one_out(records: list[Record]) -> tuple[Record, dict[str, float | None]]:
    """For each persona: choose on the others with the new definition, apply to it."""
    personas = sorted({r["persona"] for r in records})
    chosen: dict[str, float | None] = {}
    parts = []
    for pid in personas:
        pick = choose(curve([r for r in records if r["persona"] != pid]))
        chosen[pid] = None if pick is None else pick["threshold"]
        t = 1.0 if pick is None else pick["threshold"]  # no threshold: no alerts
        parts.append(alert_metrics([r for r in records if r["persona"] == pid], t, False))
    return pool(parts), chosen


def stop_conditions(points: list[Record], chosen: dict[str, float | None]) -> list[str]:
    """The conditions fixed before computing under which line c) stops (empty: continue)."""
    out = []
    for m in points:
        if m["recall_on"] is not None and m["recall"] is not None and m["recall_on"] < m["recall"]:
            out.append(f"recall under the new definition is lower at {m['threshold']:.0%}")
        if not set(m["caught_episodes"]) <= set(m["caught_on_episodes"]):
            out.append(f"the new caught set does not contain the old at {m['threshold']:.0%}")
        if m["threshold"] >= HIGH and (m["recall_on"] or 0) - (m["recall"] or 0) > HIGH_RISE:
            out.append(f"recall rises by more than 5 points at {m['threshold']:.0%}")
    picks = {t for t in chosen.values() if t is not None}
    if len(picks) > MAX_FOLD_THRESHOLDS:
        out.append(f"the folds chose {len(picks)} different thresholds (more than 3)")
    return out


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _lead(leads: list[int]) -> str:
    return "-" if not leads else f"{statistics.mean(leads):.1f} ({statistics.median(leads):.0f})"


HEAD = (
    "| Rule | Recall, opened ahead (original) | Recall, on ahead (new) | Precision "
    "(true/alerts) | False alerts per 30 days | Lead time, original: mean (median) | Lead time, "
    "new: mean (median) |"
)
LINE = "|---|---|---|---|---|---|---|"


def row(label: str, m: Record) -> str:
    return (
        f"| {label} | {_pct(m['recall'])} ({m['caught']}/{m['shortfall_episodes']}) | "
        f"{_pct(m['recall_on'])} ({m['caught_on']}/{m['shortfall_episodes']}) | "
        f"{_pct(m['precision'])} ({m['alert_episodes'] - m['false_alerts']}/"
        f"{m['alert_episodes']}) | {m['false_per_30_days']:.3f} | {_lead(m['lead_times'])} | "
        f"{_lead(m['lead_times_on'])} |"
    )


def by_persona_gain(records: list[Record], threshold: float) -> list[str]:
    """Which personas gain caught episodes under the new definition at this threshold."""
    out = []
    for pid in sorted({r["persona"] for r in records}):
        m = alert_metrics([r for r in records if r["persona"] == pid], threshold, False)
        if m["caught_on"] != m["caught"]:
            out.append(f"{pid} +{m['caught_on'] - m['caught']}")
    return out


def report(records: list[Record]) -> str:
    personas = sorted({r["persona"] for r in records})
    assert_development_only(personas)
    points = curve(records)
    provisional = alert_metrics(records, PROVISIONAL_THRESHOLD, median_rule=True)
    lopo, chosen = leave_one_out(records)
    stops = stop_conditions(points, chosen)
    best = None if stops else choose(points)
    n = (f"{len(personas)} development personas, "
         f"{sum(not r['in_shortfall_at_origin'] for r in records)} ordinary origins "
         f"(of {len(records)}), {points[0]['shortfall_episodes']} shortfall episodes")  # fmt: skip
    out = [
        '# Alert threshold on raw chances, both "caught" definitions (synthetic, development)',
        "",
        f"Made with `python -m eval.thresholds_raw` from the development backtest of the current "
        f"forecaster (`python -m eval.backtest`: 500 futures, seed 42, weekday method). {n}. "
        "Rule: alert when the raw chance is above the threshold (no median clause), through the "
        "app's alert engine. Objective (DECISIONS.md, 2026-10-11, step 1): highest recall "
        "*on ahead (new)* with precision >= 70% and <= 0.1 false alerts per 30 days. Nothing "
        "changes in the app.",
        "",
        "## The curve (all development personas)",
        "",
        HEAD,
        LINE,
        *[
            row(
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
        row("provisional: above 20% or median lowest below the buffer", provisional),
        "",
        "Precision, false alerts per 30 days, alert episodes and shortfall episodes are the same "
        "for both definitions: only the caught count and the lead time depend on the definition.",
        "",
        "## Where the new definition adds caught episodes",
        "",
        *[
            f"- at {t:.0%}: {', '.join(by_persona_gain(records, t)) or 'none'}"
            for t in (0.05, 0.20, 0.50, 0.95)
        ],  # fmt: skip
        "- provisional rule: "
        + (
            ", ".join(
                f"{pid} +{m['caught_on'] - m['caught']}"
                for pid in personas
                if (
                    m := alert_metrics(
                        [r for r in records if r["persona"] == pid], PROVISIONAL_THRESHOLD, True
                    )
                )["caught_on"]
                != m["caught"]
            )
            or "none"
        ),  # fmt: skip
        "",
        "## Stop conditions (fixed before computing)",
        "",
    ]
    if stops:
        out += ["**Line c) stops; no threshold is chosen.**", ""] + [f"- {s}" for s in stops]
    else:
        out.append("None holds: line c) continues.")
    picks = sorted({t for t in chosen.values() if t is not None})
    out += [
        "",
        "## Result",
        "",
        "No threshold chosen (see above)."
        if best is None and stops
        else "No threshold meets both constraints."
        if best is None
        else f"On all development personas the objective picks **{best['threshold']:.0%}**: recall "
        f"(new) {_pct(best['recall_on'])} ({best['caught_on']} of {best['shortfall_episodes']}), "
        f"precision {_pct(best['precision'])}, {best['false_per_30_days']:.3f} false alerts per "
        f"30 days, lead time {_lead(best['lead_times_on'])} days.",
        "",
        "## Leave one persona out (threshold chosen on the others, applied to the held-back one)",
        "",
        f"Thresholds chosen across the {len(chosen)} folds: "
        f"{', '.join(f'{t:.0%}' for t in picks) or 'none'}"
        + (
            f" ({sum(t is None for t in chosen.values())} folds found none)"
            if any(t is None for t in chosen.values())
            else ""
        )
        + ".",  # fmt: skip
        "",
        HEAD,
        LINE,
        row("held-back personas, added up", lopo),
        "",
        "n is small: the shortfall episodes come from the few personas that have any, and origins "
        "within a persona overlap.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.thresholds_raw", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--dir", type=Path, default=DEFAULT_OUT / "full")
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    records = [r for r in load_records(args.dir) if r["dataset"] == "synthetic"]
    args.report.write_text(report(records), encoding="utf-8")
    print(f"written: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
