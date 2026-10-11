"""The numbers behind the Model report page.

Synthetic: `python -m eval.report_data` reads the saved development run of the calibration step
(data/backtest/calibration: raw records and leave-one-persona-out records) and writes
docs/results/report_synthetic.json, which the page reads. Development personas only.

Real: the page calls `real_summary` at runtime on the current default forecaster's folder,
data/private/backtest/keeps-coming-with-own (made by `python -m eval.backtest_real`), when it
exists. Nothing real is ever written by this module; it returns summary numbers and counts only.

Categorizer results are copied from DECISIONS.md (step 7b, holdout run 1) into
docs/results/categorizer_results.json by hand; they are not recomputed here.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from eval.metrics import (
    BUCKETS,
    PROVISIONAL_THRESHOLD,
    alert_metrics,
    by_archetype,
    by_history_bucket,
    forecast_metrics,
    group_origins,
    load_records,
    real_results_dir,
    restate,
)

SYNTHETIC_OUT = Path("docs/results/report_synthetic.json")
CATEGORIZER = Path("docs/results/categorizer_results.json")
# The folder of the current default forecaster (keeps-coming on, own-account transfers counted).
# `data/private/backtest/full` is the old forecaster's.
REAL_DEFAULT = Path("data/private/backtest/keeps-coming-with-own")

Record = dict[str, Any]


def _plain(m: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe: horizon keys as strings."""
    out = dict(m)
    for key in ("error", "coverage", "width"):
        if key in out:
            out[key] = {str(k): v for k, v in out[key].items()}
    return out


def _alerts(m: dict[str, Any]) -> dict[str, Any]:
    keep = ("n_personas", "n_origins", "ordinary_origins", "already_in_shortfall_origins",
            "shortfall_episodes", "caught", "missed", "lead_times", "alert_episodes",
            "false_alerts", "alerts_while_in_shortfall", "recall", "precision",
            "false_per_30_days")  # fmt: skip
    return {k: m[k] for k in keep}


def summarize(records: list[Record]) -> dict[str, Any]:
    """Forecast metrics (ordinary and all origins), by history length, alerts with the
    provisional rule. Counts and summary numbers only."""
    groups = group_origins(records)
    return {
        "ordinary": _plain(forecast_metrics(groups["ordinary"])),
        "all": _plain(forecast_metrics(groups["all"])),
        "already": _plain(forecast_metrics(groups["already in shortfall"])),
        "by_history": {b: _plain(forecast_metrics(rows))
                       for b, rows in by_history_bucket(groups["ordinary"]).items()},
        "alerts": _alerts(alert_metrics(records, PROVISIONAL_THRESHOLD, True)),
    }  # fmt: skip


def synthetic_summary(run_dir: Path) -> dict[str, Any]:
    """Everything the page shows for synthetic development personas."""
    from eval.thresholds import choose, curve, load_lopo
    from lookahead.forecast.calibration import Calibration

    raw = load_records(run_dir)
    cal = Calibration.load(run_dir / "calibration.json")
    variant = cal.recalibration.kind if cal.recalibration.kind != "none" else "widened"
    calibrated = load_lopo(run_dir, variant)
    points = curve(calibrated)
    best = choose(points)
    out = summarize(raw)
    out["by_archetype"] = {
        a: _plain(forecast_metrics(rows))
        for a, rows in by_archetype(group_origins(raw)["ordinary"]).items()
    }
    out["calibrated"] = {
        "variant": variant,
        "ordinary": _plain(forecast_metrics(group_origins(calibrated)["ordinary"])),
        "by_archetype": {a: _plain(forecast_metrics(rows)) for a, rows in
                         by_archetype(group_origins(calibrated)["ordinary"]).items()},
        "multipliers": list(cal.multipliers),
    }  # fmt: skip
    out["threshold_curve"] = [{"threshold": p["threshold"], **_alerts(p)} for p in points]
    out["threshold_chosen"] = None if best is None else best["threshold"]
    out["source"] = {
        "personas": len({r["persona"] for r in raw}), "origins": len(raw),
        "futures": raw[0]["futures"], "set": "synthetic development personas",
    }  # fmt: skip
    return out


def real_dir() -> Path:
    return Path(os.environ.get("LOOKAHEAD_REAL_BACKTEST", str(REAL_DEFAULT)))


def real_summary(path: Path | None = None) -> dict[str, Any] | None:
    """The real backtest, summarized at runtime, or None when there is none. At the account's own
    buffer and at $0. Never written anywhere."""
    folder = real_results_dir(path or real_dir())
    if not folder.is_dir() or not any(folder.glob("*.jsonl")):
        return None
    records = load_records(folder)
    if not records:
        return None
    zero = [restate(r, 0) for r in records]
    return {
        "own_buffer": summarize(records), "zero_buffer": summarize(zero),
        "source": {"accounts": len({r["persona"] for r in records}), "origins": len(records),
                   "futures": records[0]["futures"]},
    }  # fmt: skip


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --- Rows for the page (plain text; the page draws them) ---


def _d(x: float | None) -> str:
    if x is None:
        return "-"
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


FORECAST_LABELS = (
    "Origins (accounts or personas)",
    "Error of the middle future: 7 / 14 / 30 days",
    "Error if the balance just stays: 7 / 14 / 30 days",
    "Skill against that (above 0 is better)",
    "Lowest balance: bias (+ too optimistic) / typical miss",
    "80% band covers the real balance: 7 / 14 / 30 days",
    "Band covers all 30 days",
    "Band width: 7 / 14 / 30 days",
    "Brier score of the shortfall chance (reference)",
)


def forecast_values(m: dict[str, Any] | None) -> list[str]:
    """One column of the forecast table (or dashes)."""
    if not m or not m.get("n_origins"):
        return ["-"] * len(FORECAST_LABELS)
    e, b = m["error"], m["brier"]
    hs = ("7", "14", "30")
    return [
        f"{m['n_origins']} ({m['n_personas']})",
        " / ".join(_d(e[h]["mean_p50"]) for h in hs),
        " / ".join(_d(e[h]["mean_naive"]) for h in hs),
        " / ".join("-" if e[h]["skill_p50"] is None else f"{e[h]['skill_p50']:.2f}" for h in hs),
        f"{_d(m['lowest']['bias'])} / {_d(m['lowest']['mae'])}",
        " / ".join(_pct(m["coverage"][h]) for h in hs),
        _pct(m["path_coverage"]),
        " / ".join(_d(m["width"][h]) for h in hs),
        f"{b['brier']:.3f} ({b['reference']:.3f})",
    ]


ALERT_LABELS = (
    "Ordinary origins (accounts or personas)",
    "Shortfall episodes",
    "Caught ahead of time (recall)",
    "Alerts that were right (precision)",
    "False alerts per 30 days",
    "Lead time, days: mean",
)


def alert_values(a: dict[str, Any] | None) -> list[str]:
    if not a:
        return ["-"] * len(ALERT_LABELS)
    leads = a["lead_times"]
    return [
        f"{a['ordinary_origins']} ({a['n_personas']})",
        str(a["shortfall_episodes"]),
        f"{a['caught']} ({_pct(a['recall'])})",
        f"{a['alert_episodes'] - a['false_alerts']} of {a['alert_episodes']} "
        f"({_pct(a['precision'])})",
        "-" if a["false_per_30_days"] is None else f"{a['false_per_30_days']:.3f}",
        "-" if not leads else f"{sum(leads) / len(leads):.1f}",
    ]


def reliability_values(m: dict[str, Any] | None) -> list[str]:
    """Five cells: 'n origins, said x%, happened y%'."""
    if not m or not m.get("n_origins"):
        return ["-"] * 5
    return [
        "-"
        if not b["n"]
        else f"{b['n']}: said {_pct(b['mean_forecast'])}, happened {_pct(b['observed'])}"
        for b in m["brier"]["reliability"]
    ]


def breakdown_rows(groups: dict[str, dict[str, Any]], order: tuple[str, ...] | None = None
                   ) -> list[tuple[str, str, str, str, str, str]]:  # fmt: skip
    """(group, origins (personas), 30-day error, coverage over all days, lowest-balance bias,
    Brier) for each group."""
    keys = [k for k in (order or tuple(groups)) if k in groups]
    out = []
    for k in keys:
        m = groups[k]
        out.append((k, f"{m['n_origins']} ({m['n_personas']})", _d(m["error"]["30"]["mean_p50"]),
                    _pct(m["coverage"]["all"]), _d(m["lowest"]["bias"]),
                    f"{m['brier']['brier']:.3f}"))  # fmt: skip
    return out


HISTORY_ORDER = BUCKETS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.report_data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--run", type=Path, default=Path("data/backtest/calibration"))
    parser.add_argument("--out", type=Path, default=SYNTHETIC_OUT)
    args = parser.parse_args(argv)
    args.out.write_text(json.dumps(synthetic_summary(args.run), indent=1), encoding="utf-8")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
