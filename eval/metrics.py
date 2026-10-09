"""Backtest metrics (week 4, step 4), from the saved records of eval/backtest.py.

Definitions are in DECISIONS.md (2026-10-09, "the backtest metrics, defined before anything is
computed"). In short, for the development personas only (a held-out persona is refused unless it is
the final test): balance error at 7, 14 and 30 days against a naive baseline, lowest-balance
error, 80 percent band coverage and width (pointwise and whole path), the Brier score of the
shortfall probability, and alerts (caught, false per 30 days, lead time) from the app's own alert
engine. Origins already in shortfall are on their own row. Synthetic and real are separate columns,
and every section says its n. This module prints summary numbers and counts, never rows.

    python -m eval.metrics                                  # synthetic, from data/backtest/full
    python -m eval.metrics --real data/private/backtest     # beside the real column
    python -m eval.metrics --threshold 0.30 --no-median     # another alert rule (step 5 chooses)
"""

import argparse
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from eval.backtest import (
    DEFAULT_OUT,
    FORMAT,
    LOGIC,
    history_bucket,
    read_records,
    split_by_shortfall,
)
from lookahead.alerts import Snapshot, episodes, record_snapshot
from lookahead.db import SCHEMA
from lookahead.generator.archetypes import HELD_OUT

HORIZONS = (7, 14, 30)
BIN_EDGES = 5  # reliability bins: 0-20, 20-40, 40-60, 60-80, 80-100 percent
BUCKETS = ("under 8 weeks", "8-12 weeks", "12-26 weeks", "26+ weeks")
GROUPS = ("all", "ordinary", "already in shortfall")
PROVISIONAL_THRESHOLD = 0.20

Record = dict[str, Any]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


# --- Loading ---


def load_records(out_dir: Path, final_test: bool = False) -> list[Record]:
    """Every saved record. Refuses results from an older version, and held-out personas unless
    this is the final test."""
    records = read_records(out_dir)
    if any(r.get("format") != FORMAT or r.get("logic") != LOGIC for r in records):
        raise ValueError(f"the results in {out_dir} are from an older version: run them again")
    held = sorted({
        r["persona"] for r in records
        if r.get("held_out") or (r["dataset"] == "synthetic" and r["persona"] in HELD_OUT)
    })  # fmt: skip
    if held and not final_test:
        raise ValueError(f"held-out personas are only for the final test: {', '.join(held)}")
    return records


def restate(record: Record, buffer_cents: int) -> Record:
    """The record as it would read with another buffer (real data: $0). The kept lowest balance of
    every future gives the shortfall probability for any buffer; the observed dips and the
    already-in-shortfall flag are recomputed from the balances."""
    new = dict(record)
    lows = record["lowest_cents"]
    new["buffer_cents"] = buffer_cents
    new["shortfall_probability"] = sum(low < buffer_cents for low in lows) / len(lows)
    below = [d for d, b in zip(record["days"], record["realized"], strict=True) if b < buffer_cents]
    new["realized_first_below_buffer"] = below[0] if below else None
    new["in_shortfall_at_origin"] = record["start_balance_cents"] < buffer_cents
    return new


# --- Groups ---


def group_origins(records: list[Record]) -> dict[str, list[Record]]:
    ordinary, already = split_by_shortfall(records)
    return {"all": list(records), "ordinary": ordinary, "already in shortfall": already}


def by_history_bucket(records: list[Record]) -> dict[str, list[Record]]:
    out: dict[str, list[Record]] = {}
    for bucket in BUCKETS:
        rows = [r for r in records if history_bucket(r["history_days"]) == bucket]
        if rows:
            out[bucket] = rows
    return out


def by_archetype(records: list[Record]) -> dict[str, list[Record]]:
    out: dict[str, list[Record]] = {}
    for r in records:
        if r.get("archetype"):
            out.setdefault(r["archetype"], []).append(r)
    return dict(sorted(out.items()))


# --- Forecast metrics ---


def forecast_metrics(records: list[Record]) -> dict[str, Any]:
    """Error, coverage, width and Brier score for these records (a group chosen by the caller)."""
    if not records:
        return {"n_origins": 0, "n_personas": 0}
    n = len(records)
    out: dict[str, Any] = {"n_origins": n, "n_personas": len({r["persona"] for r in records})}
    error: dict[int, dict[str, float | None]] = {}
    for h in HORIZONS:
        i = h - 1
        p50 = [abs(r["p50"][i] - r["realized"][i]) / 100 for r in records]
        point = [abs(r["point"][i] - r["realized"][i]) / 100 for r in records]
        naive = [abs(r["start_balance_cents"] - r["realized"][i]) / 100 for r in records]
        error[h] = {
            "mean_p50": _mean(p50), "median_p50": statistics.median(p50),
            "mean_point": _mean(point), "mean_naive": _mean(naive),
            "skill_p50": None if _mean(naive) == 0 else 1 - _mean(p50) / _mean(naive),
        }  # fmt: skip
    out["error"] = error
    misses = [(r["median_lowest_cents"] - r["realized_lowest_cents"]) / 100 for r in records]
    out["lowest"] = {"bias": _mean(misses), "mae": _mean([abs(m) for m in misses])}

    def inside(r: Record, i: int) -> bool:
        return r["p10"][i] <= r["realized"][i] <= r["p90"][i]

    out["coverage"] = {h: sum(inside(r, h - 1) for r in records) / n for h in HORIZONS}
    out["coverage"]["all"] = sum(inside(r, i) for r in records for i in range(30)) / (30 * n)
    out["width"] = {h: _mean([(r["p90"][h - 1] - r["p10"][h - 1]) / 100 for r in records])
                    for h in HORIZONS}  # fmt: skip
    out["path_coverage"] = sum(all(inside(r, i) for i in range(30)) for r in records) / n
    happened = [1.0 if r["realized_first_below_buffer"] is not None else 0.0 for r in records]
    prob = [r["shortfall_probability"] for r in records]
    brier = _mean([(p - o) ** 2 for p, o in zip(prob, happened, strict=True)])
    base = _mean(happened)
    reference = base * (1 - base)
    bins = []
    for b in range(BIN_EDGES):
        members = [(p, o) for p, o in zip(prob, happened, strict=True)
                   if min(int(p * BIN_EDGES), BIN_EDGES - 1) == b]  # fmt: skip
        bins.append(
            {
                "n": len(members),
                "mean_forecast": _mean([p for p, _ in members]) if members else None,
                "observed": _mean([o for _, o in members]) if members else None,
            }
        )
    out["brier"] = {
        "brier": brier, "base_rate": base, "reference": reference,
        "skill": None if reference == 0 else 1 - brier / reference, "reliability": bins,
    }  # fmt: skip
    return out


# --- Observed daily balances and shortfall episodes ---


def daily_realized(records: list[Record]) -> dict[date, int]:
    """One persona's observed daily balances, joined from the overlapping 30-day windows."""
    daily: dict[date, int] = {}
    for r in sorted(records, key=lambda x: x["origin"]):
        for day, balance in zip(r["days"], r["realized"], strict=True):
            d = date.fromisoformat(day)
            if daily.setdefault(d, balance) != balance:
                raise ValueError(f"the windows disagree about {d}")
    return daily


def shortfall_episodes(records: list[Record]) -> list[date]:
    """The first day of each shortfall episode (a run of days below the buffer) of one persona.
    An episode that began on or before the first origin is not counted."""
    ordered = sorted(records, key=lambda x: x["origin"])
    buffer = ordered[0]["buffer_cents"]
    daily = daily_realized(ordered)
    previous_below = ordered[0]["start_balance_cents"] < buffer
    starts = []
    for day in sorted(daily):
        below = daily[day] < buffer
        if below and not previous_below:
            starts.append(day)
        previous_below = below
    return starts


# --- Alerts ---


def alert_condition(record: Record, threshold: float, median_rule: bool) -> bool:
    """The alert rule: the chance of dipping below the buffer above `threshold`, or (if
    `median_rule`) the median lowest balance below the buffer."""
    return record["shortfall_probability"] > threshold or (
        median_rule and record["median_lowest_cents"] < record["buffer_cents"]
    )


def _snapshot(record: Record) -> Snapshot:
    p50 = record["p50"]
    k = min(range(len(p50)), key=lambda i: (p50[i], i))
    return Snapshot(
        data_date=date.fromisoformat(record["origin"]),
        low_day=date.fromisoformat(record["days"][k]), low_cents=p50[k],
        probability=record["shortfall_probability"], buffer_cents=record["buffer_cents"],
        median_lowest_cents=record["median_lowest_cents"],
    )  # fmt: skip


def _alert_episodes(records: list[Record], threshold: float, median_rule: bool) -> list[date]:
    """The dates (origins) at which the app's alert engine opened an alert episode, feeding it
    this persona's records in order."""
    store = sqlite3.connect(":memory:")
    store.executescript(SCHEMA)
    for r in sorted(records, key=lambda x: x["origin"]):
        snap = _snapshot(r) if alert_condition(r, threshold, median_rule) else None
        record_snapshot(store, "p", snap, date.fromisoformat(r["origin"]), {} if snap else None)
    opened = [e.opened_on for e in episodes(store, "p")]
    store.close()
    return sorted(opened)


def alert_metrics(
    records: list[Record], threshold: float = PROVISIONAL_THRESHOLD, median_rule: bool = True
) -> dict[str, Any]:
    """Caught, missed, false alert episodes, lead time, precision, recall and false alerts per 30
    days, over origins not already in shortfall; alerts raised while already in shortfall are
    counted on their own."""
    totals: Counter[str] = Counter()
    leads: list[int] = []
    by_bucket: dict[str, Counter[str]] = {}
    personas: dict[str, list[Record]] = {}
    for r in records:
        personas.setdefault(r["persona"], []).append(r)
    for rows in personas.values():
        rows = sorted(rows, key=lambda x: x["origin"])
        at = {date.fromisoformat(r["origin"]): r for r in rows}
        totals["origins"] += len(rows)
        totals["ordinary_origins"] += sum(not r["in_shortfall_at_origin"] for r in rows)
        totals["already"] += sum(r["in_shortfall_at_origin"] for r in rows)
        opened_ordinary: list[date] = []
        for opened in _alert_episodes(rows, threshold, median_rule):
            origin = at[opened]
            if origin["in_shortfall_at_origin"]:
                totals["alerts_while_in_shortfall"] += 1
                continue
            opened_ordinary.append(opened)
            totals["alert_episodes"] += 1
            false = origin["realized_first_below_buffer"] is None
            totals["false"] += false
            bucket = by_bucket.setdefault(history_bucket(origin["history_days"]), Counter())
            bucket["false" if false else "true"] += 1
        for first_day in shortfall_episodes(rows):
            totals["shortfall"] += 1
            ahead = [o for o in opened_ordinary if o < first_day and (first_day - o).days <= 30]
            if ahead:
                totals["caught"] += 1
                leads.append((first_day - min(ahead)).days)
    ordinary = totals["ordinary_origins"]
    alerts = totals["alert_episodes"]
    return {
        "n_personas": len(personas), "n_origins": totals["origins"],
        "ordinary_origins": ordinary, "already_in_shortfall_origins": totals["already"],
        "shortfall_episodes": totals["shortfall"], "caught": totals["caught"],
        "missed": totals["shortfall"] - totals["caught"], "lead_times": sorted(leads),
        "alert_episodes": alerts, "false_alerts": totals["false"],
        "alerts_while_in_shortfall": totals["alerts_while_in_shortfall"],
        "recall": totals["caught"] / totals["shortfall"] if totals["shortfall"] else None,
        "precision": (alerts - totals["false"]) / alerts if alerts else None,
        "false_per_30_days": totals["false"] / (ordinary * 7) * 30 if ordinary else None,
        "by_bucket": {b: dict(c) for b, c in by_bucket.items()},
    }  # fmt: skip


# --- The report ---


def _d(x: float | None) -> str:
    return "-" if x is None else f"${x:,.2f}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _num(x: float | None, digits: int = 2) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def _forecast_rows(m: dict[str, Any]) -> list[tuple[str, str]]:
    if not m.get("n_origins"):
        return [(label, "-") for label in _FORECAST_LABELS]
    e, cov, w, b = m["error"], m["coverage"], m["width"], m["brier"]
    three = lambda f, key: " / ".join(f(e[h][key]) for h in HORIZONS)  # noqa: E731
    return [
        (_FORECAST_LABELS[0], f"{m['n_origins']} origins, {m['n_personas']} persona(s)"),
        (
            _FORECAST_LABELS[1],
            " / ".join(f"{_d(e[h]['mean_p50'])} ({_d(e[h]['median_p50'])})" for h in HORIZONS),
        ),  # fmt: skip
        (_FORECAST_LABELS[2], three(_d, "mean_point")),
        (_FORECAST_LABELS[3], three(_d, "mean_naive")),
        (_FORECAST_LABELS[4], three(_num, "skill_p50")),
        (_FORECAST_LABELS[5], f"{_d(m['lowest']['bias'])} / {_d(m['lowest']['mae'])}"),
        (_FORECAST_LABELS[6], " / ".join(_pct(cov[h]) for h in (*HORIZONS, "all"))),
        (_FORECAST_LABELS[7], " / ".join(_d(w[h]) for h in HORIZONS)),
        (_FORECAST_LABELS[8], _pct(m["path_coverage"])),
        (
            _FORECAST_LABELS[9],
            f"{_num(b['brier'], 3)} (reference {_num(b['reference'], 3)}, "
            f"skill {_num(b['skill'])})",
        ),  # fmt: skip
    ]


_FORECAST_LABELS = (
    "n",
    "Error, middle future: mean (median), 7 / 14 / 30 days",
    "Error, single path: mean, 7 / 14 / 30 days",
    "Error, naive baseline (balance stays): mean, 7 / 14 / 30 days",
    "Skill vs naive, middle future, 7 / 14 / 30 days",
    "Lowest balance: bias / typical miss",
    "80% band coverage, 7 / 14 / 30 days / all days (nominal 80%)",
    "80% band width: mean, 7 / 14 / 30 days",
    "Path-level coverage (all 30 days inside the band)",
    "Brier score of the shortfall probability",
)


def _alert_rows(m: dict[str, Any]) -> list[tuple[str, str]]:
    leads = m["lead_times"]
    return [
        (
            "n",
            f"{m['n_personas']} persona(s), {m['n_origins']} origins "
            f"({m['ordinary_origins']} ordinary)",
        ),  # fmt: skip
        ("Shortfall episodes (ordinary origins)", str(m["shortfall_episodes"])),
        ("Caught / missed", f"{m['caught']} / {m['missed']}"),
        ("Recall (caught / shortfall episodes)", _pct(m["recall"])),
        ("Alert episodes / false", f"{m['alert_episodes']} / {m['false_alerts']}"),
        ("Precision (alert episodes that were not false)", _pct(m["precision"])),
        ("False alerts per 30 days", _num(m["false_per_30_days"])),
        (
            "Lead time, days: mean (median)",
            "-" if not leads else f"{_mean(leads):.1f} ({statistics.median(leads):.1f})",
        ),  # fmt: skip
        ("Already in shortfall at the origin: origins", str(m["already_in_shortfall_origins"])),
        ("Alerts raised while already in shortfall", str(m["alerts_while_in_shortfall"])),
    ]


def _table(title: str, columns: list[tuple[str, list[tuple[str, str]]]]) -> list[str]:
    """Rows of (label, value) per column, side by side."""
    lines = [title]
    labels = [label for label, _ in columns[0][1]]
    width = max(len(label) for label in labels)
    lines.append(f"  {'':{width}}  " + "  |  ".join(name for name, _ in columns))
    for k, label in enumerate(labels):
        lines.append(f"  {label:{width}}  " + "  |  ".join(rows[k][1] for _, rows in columns))
    return lines


def _columns(synthetic: list[Record], real: list[Record]) -> list[tuple[str, list[Record]]]:
    cols = [("Synthetic (development)", synthetic), ("Real", real),
            ("Real, buffer $0", [restate(r, 0) for r in real])]  # fmt: skip
    return cols


def report_text(
    synthetic: list[Record], real: list[Record], threshold: float = PROVISIONAL_THRESHOLD,
    median_rule: bool = True,
) -> str:  # fmt: skip
    """The summary: forecast metrics for three groups, alerts, and breakdowns, in separate
    columns for synthetic and real data, with n beside every number."""
    cols = _columns(synthetic, real)
    out = ["BACKTEST SUMMARY (summary numbers and counts only)"]
    for name, rows in cols:
        people = len({r["persona"] for r in rows})
        out.append(f"  {name}: {people} persona(s), {len(rows)} origins" if rows
                   else f"  {name}: no real data yet" if name.startswith("Real")
                   else f"  {name}: none")  # fmt: skip
    rule = f"chance above {threshold:.0%}" + (", or median lowest balance below the buffer"
                                              if median_rule else "")  # fmt: skip
    out.append(f"  Alert rule used: {rule} (provisional until the threshold is chosen)")
    shown = [(n, rows) for n, rows in cols if rows] or [(cols[0][0], [])]
    for group, label in (("all", "All origins"), ("ordinary", "Origins not already in shortfall"),
                         ("already in shortfall", "Origins already in shortfall")):  # fmt: skip
        out.append("")
        columns = [(n, _forecast_rows(forecast_metrics(group_origins(rows)[group])))
                   for n, rows in shown]  # fmt: skip
        out += _table(f"{label}: forecast", columns)
    out.append("")
    alerts = [(n, _alert_rows(alert_metrics(rows, threshold, median_rule))) for n, rows in shown]
    out += _table("Alerts (ordinary origins; already-in-shortfall on their own rows)", alerts)
    out.append("")
    out.append("Reliability of the shortfall probability, origins not already in shortfall "
               "(bin: n, mean forecast, observed)")  # fmt: skip
    for name, rows in shown:
        bins = forecast_metrics(group_origins(rows)["ordinary"]).get("brier", {}).get("reliability")
        if bins:
            cells = [f"{i * 20}-{i * 20 + 20}%: {b['n']}, {_pct(b['mean_forecast'])}, "
                     f"{_pct(b['observed'])}" for i, b in enumerate(bins)]  # fmt: skip
            out.append(f"  {name}: " + "; ".join(cells))
    for name, rows in shown:
        out.append("")
        out += _breakdown(f"{name}: by history length (all origins)", by_history_bucket(rows))
        kinds = by_archetype(rows)
        if kinds:
            out.append("")
            out += _breakdown(f"{name}: by archetype (all origins)", kinds)
        out.append("")
        out += _alert_by_bucket(f"{name}: alert episodes by history length of the opening origin",
                                alert_metrics(rows, threshold, median_rule))  # fmt: skip
    return "\n".join(out)


def _breakdown(title: str, groups: dict[str, list[Record]]) -> list[str]:
    lines = [title, "  group | origins, personas | error p50 7/14/30 days | band coverage all days "
                    "| path coverage | Brier"]  # fmt: skip
    for key, rows in groups.items():
        m = forecast_metrics(rows)
        e = m["error"]
        lines.append(
            f"  {key} | {m['n_origins']}, {m['n_personas']} | "
            + " / ".join(_d(e[h]["mean_p50"]) for h in HORIZONS)
            + f" | {_pct(m['coverage']['all'])} | {_pct(m['path_coverage'])} | "
            + _num(m["brier"]["brier"], 3)
        )
    return lines


def _alert_by_bucket(title: str, m: dict[str, Any]) -> list[str]:
    lines = [title]
    if not m["by_bucket"]:
        return lines + ["  no alert episodes"]
    for bucket in BUCKETS:
        if bucket in m["by_bucket"]:
            c = m["by_bucket"][bucket]
            lines.append(f"  {bucket}: {c.get('true', 0)} not false, {c.get('false', 0)} false")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.metrics", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--dir", type=Path, default=DEFAULT_OUT / "full", help="synthetic results")
    parser.add_argument("--real", type=Path, default=None, help="real results (private folder)")
    parser.add_argument("--no-synthetic", action="store_true")
    parser.add_argument("--threshold", type=float, default=PROVISIONAL_THRESHOLD)
    parser.add_argument("--no-median", action="store_true", help="drop the median-lowest rule")
    parser.add_argument("--final-test", action="store_true", help="allow held-out personas")
    args = parser.parse_args(argv)
    synthetic = [] if args.no_synthetic else load_records(args.dir, args.final_test)
    real = load_records(args.real) if args.real else []
    print(report_text(synthetic, real, args.threshold, not args.no_median))
    return 0


if __name__ == "__main__":
    sys.exit(main())
