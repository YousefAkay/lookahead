"""Step 2 of the 2026-10-12 plan: the arms on the merged code, backtested on development personas.

Method in DECISIONS.md (2026-10-12, "Step 2: the arms on the merged code"). Each arm is the
backtest of `eval/backtest.py` (the same origins, futures, seed and method, the same record
format) with one set of forecaster options, keeps-coming on in every arm (the default):

    0  the forecaster as merged (the baseline): options off
    3  b) by calendar position placed by default (only if Step 1 passed its gate)

Arms 1, 2 and 4 were the a) lump proposals (every proposal confirmed; the right ones confirmed;
a) with b)). a) was retired on 2026-10-12 and its option removed from the forecaster; their
records are in docs/results/arms_2026-10-12.md and DECISIONS.md.

Random lump arrivals (`lump_arrivals`) are not an arm: keeps-coming replaces them. The option
sets of the 2026-10-11 plan are in the history of this file.

    python -m eval.arms --arms 3 --set both     # records in data/backtest/arms-2026-10-12/...
    python -m eval.arms --report                # writes docs/results/arms_2026-10-12.md
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest import (
    FUTURES,
    HORIZON,
    METHOD,
    _check_settings,
    current_default_dir,
    format_of,
    origins,
    run_origin,
    version_settings,
)
from eval.metrics import (
    HORIZONS,
    PROVISIONAL_THRESHOLD,
    alert_metrics,
    by_archetype,
    forecast_metrics,
    load_records,
)
from eval.personas_db import persona_db
from eval.proposals_score import KEY
from lookahead.forecast.path import ForecastOptions, build_forecast, forecaster_settings
from lookahead.generator.archetypes import (
    END,
    assert_development_only,
    build_persona,
    development_ids,
    is_long,
)
from lookahead.generator.writer import export_rows

ARMS_OUT = Path("data/backtest/arms-2026-10-12")
REPORT = Path("docs/results/arms_2026-10-12.md")
ARM_NAMES = {
    0: "merged forecaster (baseline)",
    3: "b) by calendar position",
}
UPPER_BOUNDS: tuple[int, ...] = ()  # arms that use the answer key (a) was retired)
WORSE_BY = 0.01  # the drop rule: more than 1 percent worse ...
DROP_IF_WORSE = 5  # ... for 5 or more of the 8 archetypes, on either measure
FAMILY = ("-tuition-funding", "-tuition-topup", "-family")

Record = dict[str, Any]


def arm_options(arm: int) -> ForecastOptions:
    """The forecaster options of an arm (keeps-coming stays on: it is the default)."""
    if arm == 0:
        return ForecastOptions()
    if arm == 3:
        return ForecastOptions(proposal_key=KEY, outflow_placements=True, outflow_rule="calendar")
    if arm in (1, 2, 4):
        raise ValueError(f"arm {arm} was a) lump proposals, retired on 2026-10-12")
    raise ValueError(f"arm {arm} does not exist")


def arm_dir(arm: int, long: bool) -> Path:
    """Arm 0 is the development backtest itself (same code path); the others have their own."""
    if arm == 0:
        return current_default_dir(long)
    return ARMS_OUT / ("long" if long else "12") / f"arm{arm}"


def run_persona_arm(persona_id: str, arm: int, out_dir: Path, futures: int = FUTURES,
                    seed: int = 42) -> int:  # fmt: skip
    """All 40 origins of one persona for one arm, saved as they are made (resumable). Each record
    also says how many lump proposals were confirmed and how many payments were placed at that
    origin (`confirmed`, `placements`), so a persona that changes with neither can be found."""
    assert_development_only([persona_id])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{persona_id}.jsonl"
    meta = out_dir / f"{persona_id}.meta.json"
    settings = {"futures": futures, "seed": seed, "method": METHOD, "arm": arm,
                **version_settings(persona_id),
                "forecaster": forecaster_settings(arm_options(arm))}  # fmt: skip
    if arm:  # arm 0 is the same code path as before; the others are this plan's
        settings["plan"] = "2026-10-12"
    _check_settings(meta, settings)  # refuses a resume with other settings, naming the difference
    done = set()
    if path.exists():
        done = {json.loads(line)["origin"] for line in path.read_text().splitlines() if line}
    todo = [d for d in origins() if d.isoformat() not in done]
    if not todo:
        return 0
    with persona_db(persona_id, out_dir / "work", seed) as db, path.open("a") as out:
        pid, conn = persona_id, db.conn
        for origin in todo:
            options = arm_options(arm)
            record = run_origin(conn, pid, "synthetic", origin, futures, db.persona.archetype,
                                db.persona.held_out, options)  # fmt: skip
            seen = build_forecast(conn, pid, METHOD, origin, None, HORIZON, 0, options=options)
            assert seen is not None
            record["arm"] = arm
            record["placements"] = len(seen.outflow_placements)
            out.write(json.dumps(record) + "\n")
            out.flush()
    return len(todo)


def _job(args: tuple[str, int, bool]) -> str:
    pid, arm, long = args
    n = run_persona_arm(pid, arm, arm_dir(arm, long))
    return f"arm {arm} {pid}: {n} new origins"


# --- The report ---


def _windows(persona_id: str) -> tuple[set[date], set[date]]:
    """The posting days of tuition payments and of family money (answer key)."""
    p = build_persona(persona_id)
    rows = export_rows(p.persona, p.start, END, 42)
    tuition = {t.posted_date for t in rows if (t.series_id or "").endswith("-tuition")}
    family = {t.posted_date for t in rows if (t.series_id or "").endswith(FAMILY)}
    return tuition, family


def window_kind(record: Record, tuition: set[date], family: set[date]) -> str:
    origin = date.fromisoformat(record["origin"])
    days = {origin + timedelta(days=k) for k in range(1, HORIZON + 1)}
    if days & tuition:
        return "with a tuition payment"
    return "family money, no tuition" if days & family else "neither"


def lowest_by_window(records: list[Record]) -> dict[str, tuple[int, float, float]]:
    """Lowest-balance bias and typical miss by the kind of window (n origins, bias, miss)."""
    cache: dict[str, tuple[set[date], set[date]]] = {}
    groups: dict[str, list[float]] = {}
    for r in records:
        if r["persona"] not in cache:
            cache[r["persona"]] = _windows(r["persona"])
        kind = window_kind(r, *cache[r["persona"]])
        groups.setdefault(kind, []).append((r["median_lowest_cents"] - r["realized_lowest_cents"])
                                           / 100)  # fmt: skip
    return {k: (len(v), statistics.mean(v), statistics.mean(abs(x) for x in v))
            for k, v in sorted(groups.items())}  # fmt: skip


def _d(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _p(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _alerts(m: Record) -> str:
    return (f"{_p(m['recall'])} / {_p(m['recall_on'])} ({m['caught']}, {m['caught_on']} of "
            f"{m['shortfall_episodes']}); precision {_p(m['precision'])} "
            f"({m['alert_episodes'] - m['false_alerts']}/{m['alert_episodes']}); "
            f"{m['false_per_30_days']:.3f} false per 30 days")  # fmt: skip


def arm_rows(records: list[Record]) -> list[tuple[str, str]]:
    ordinary = [r for r in records if not r["in_shortfall_at_origin"]]
    m = forecast_metrics(ordinary)
    e = m["error"]
    prov = alert_metrics(records, PROVISIONAL_THRESHOLD, True)
    rows = [
        ("n (ordinary origins, personas)", f"{m['n_origins']}, {m['n_personas']}"),
        ("Error, middle future, mean: 7 / 14 / 30 days",
         " / ".join(_d(e[h]["mean_p50"]) for h in HORIZONS)),
        ("Skill vs balance stays: 7 / 14 / 30 days",
         " / ".join(f"{e[h]['skill_p50']:.2f}" for h in HORIZONS)),
        ("Lowest balance: bias / typical miss",
         f"{_d(m['lowest']['bias'])} / {_d(m['lowest']['mae'])}"),
        ("80% band coverage: 7 / 14 / 30 / all days",
         " / ".join(_p(m["coverage"][h]) for h in (*HORIZONS, "all"))),
        ("80% band width: 7 / 14 / 30 days", " / ".join(_d(m["width"][h]) for h in HORIZONS)),
        ("Path-level coverage", _p(m["path_coverage"])),
        ("Brier (reference)", f"{m['brier']['brier']:.3f} ({m['brier']['reference']:.3f})"),
        ("Alerts, provisional rule: recall opened ahead / on ahead", _alerts(prov)),
        ("Origins with a confirmed proposal / a placement",
         f"{sum(r.get('confirmed', 0) > 0 for r in records)} / "
         f"{sum(r.get('placements', 0) > 0 for r in records)} of {len(records)}"),
    ]  # fmt: skip
    for kind, (n, bias, miss) in lowest_by_window(ordinary).items():
        rows.append((f"Lowest balance, windows {kind}: bias / miss (n)",
                     f"{_d(bias)} / {_d(miss)} ({n})"))  # fmt: skip
    return rows


def per_archetype(records: list[Record]) -> dict[str, Record]:
    out = {}
    for code, rows in by_archetype([r for r in records if not r["in_shortfall_at_origin"]]).items():
        m = forecast_metrics(rows)
        out[code] = {"n": m["n_origins"], "personas": m["n_personas"],
                     "error30": m["error"][30]["mean_p50"], "bias": m["lowest"]["bias"],
                     "miss": m["lowest"]["mae"], "coverage": m["coverage"]["all"],
                     "brier": m["brier"]["brier"]}  # fmt: skip
    return out


def worse_archetypes(
    arm: dict[str, Record], base: dict[str, Record]
) -> tuple[list[str], list[str]]:
    """The drop rule (written before computing): an archetype is worse on the 30-day error if the
    arm's mean absolute 30-day error is more than 1 percent above arm 0's, and worse on the lowest
    balance if the absolute value of its mean lowest-balance miss is more than 1 percent above."""
    error = [c for c in base if arm[c]["error30"] > base[c]["error30"] * (1 + WORSE_BY)]
    low = [c for c in base if abs(arm[c]["bias"]) > abs(base[c]["bias"]) * (1 + WORSE_BY)]
    return error, low


FORECAST_FIELDS = ("p10", "p50", "p90", "point", "lowest_cents", "shortfall_probability",
                   "median_lowest_cents")  # fmt: skip


def changed_without_cause(arm: list[Record], base: list[Record]) -> list[str]:
    """(persona, origin) where the arm's forecast differs from arm 0's although nothing was
    confirmed or placed there: the plan's "unexpected"."""
    at = {(r["persona"], r["origin"]): r for r in base}
    out = []
    for r in arm:
        b = at[(r["persona"], r["origin"])]
        if (
            r.get("confirmed", 0) == 0
            and r.get("placements", 0) == 0
            and any(r[f] != b[f] for f in FORECAST_FIELDS)
        ):
            out.append(f"{r['persona']} {r['origin']}")
    return out


def alerts_by_archetype(records: list[Record]) -> dict[str, Record]:
    out = {}
    for code in sorted({r["archetype"] for r in records}):
        out[code] = alert_metrics([r for r in records if r["archetype"] == code],
                                  PROVISIONAL_THRESHOLD, True)  # fmt: skip
    return out


def report(arms: list[int], loader: Callable[[int, bool], list[Record]]) -> str:
    out = ["# Step 2: the arms on the merged code (synthetic, development personas)", "",
           "Made with `python -m eval.arms --report`. Method in DECISIONS.md (2026-10-12, step 2). "
           "20 development personas per set, 40 weekly origins, 500 futures, seed 42, weekday "
           "method, raw chances, keeps-coming on in every arm. Forecast metrics on origins not "
           "already in shortfall; alerts on the same, with the already-in-shortfall origins left "
           "out as before. Recall is given for both definitions: opened ahead (original) / on "
           "ahead (the reading of 2026-10-12). **Arms 2 and 4 are upper bounds**: the answer key "
           "picks the proposals to confirm, so no user could get them.", ""]  # fmt: skip
    for long in (False, True):
        name = "24-month set" if long else "12-month set"
        loaded = {a: loader(a, long) for a in arms}
        loaded = {a: r for a, r in loaded.items() if r}
        if not loaded:
            continue
        out += [f"## {name}", "", "| | " + " | ".join(f"arm {a}: {ARM_NAMES[a]}" for a in loaded)
                + " |", "|---|" + "---|" * len(loaded)]  # fmt: skip
        columns = {a: dict(arm_rows(r)) for a, r in loaded.items()}
        labels = list(next(iter(columns.values())))
        for label in labels:
            out.append(f"| {label} | " + " | ".join(columns[a].get(label, "-") for a in loaded)
                       + " |")  # fmt: skip
        out += ["", f"### {name}, by archetype: 30-day error / lowest-balance bias / band coverage "
                "/ Brier (ordinary origins)", "",
                "| Archetype (n origins, personas) | " + " | ".join(f"arm {a}" for a in loaded)
                + " |", "|---|" + "---|" * len(loaded)]  # fmt: skip
        by = {a: per_archetype(r) for a, r in loaded.items()}
        first_arm = next(iter(by))
        for code in by[first_arm]:
            first = by[first_arm][code]
            cells = [f"{_d(by[a][code]['error30'])} / {_d(by[a][code]['bias'])} / "
                     f"{_p(by[a][code]['coverage'])} / {by[a][code]['brier']:.3f}"
                     for a in loaded]  # fmt: skip
            out.append(f"| {code} ({first['n']}, {first['personas']}) | " + " | ".join(cells)
                       + " |")  # fmt: skip
        out += ["", f"### {name}, alerts by archetype, provisional rule: recall opened ahead / on "
                "ahead (caught of episodes); precision; false alerts per 30 days", "",
                "| Archetype | " + " | ".join(f"arm {a}" for a in loaded) + " |",
                "|---|" + "---|" * len(loaded)]  # fmt: skip
        alerts = {a: alerts_by_archetype(r) for a, r in loaded.items()}
        for code in alerts[first_arm]:
            out.append(f"| {code} | " + " | ".join(_alerts(alerts[a][code]) for a in loaded)
                       + " |")  # fmt: skip
        if 0 in loaded:
            out += ["", f"### {name}: the drop rule and the unexpected checks (fixed before "
                    "computing)", ""]  # fmt: skip
            for a in [a for a in loaded if a != 0]:
                error, low = worse_archetypes(by[a], by[0])
                dropped = len(error) >= DROP_IF_WORSE or len(low) >= DROP_IF_WORSE
                odd = changed_without_cause(loaded[a], loaded[0])
                bound = " (upper bound)" if a in UPPER_BOUNDS else ""
                out.append(
                    f"- arm {a}{bound}: worse on the 30-day error {len(error)} of {len(by[0])} "
                    f"({', '.join(error) or '-'}); worse on the lowest balance {len(low)} of "
                    f"{len(by[0])} ({', '.join(low) or '-'}); "
                    f"**{'dropped' if dropped else 'not dropped'}**; "
                    f"origins that changed with nothing confirmed or placed: {len(odd)}"
                    + (f" ({', '.join(odd[:10])}{', ...' if len(odd) > 10 else ''})" if odd else "")
                )
        out.append("")
    out.append("ITIGHT is its three development personas (-01, -02, -03); -01 and -03 run past "
               "-$3,000 in both sets (known limitation). -04 and -05 are held out.")  # fmt: skip
    return "\n".join(out) + "\n"


def load_arm(arm: int, long: bool) -> list[Record]:
    folder = arm_dir(arm, long)
    if not folder.exists():
        return []
    records = load_records(folder)
    assert all(is_long(r["persona"]) == long for r in records)
    assert all(r.get("arm", 0) == arm for r in records)
    assert arm == 0 or all("placements" in r for r in records)  # this plan's arms only
    assert all(r["format"] == format_of(r["persona"]) for r in records)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.arms", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--arms", nargs="*", type=int, default=[3])
    parser.add_argument("--set", choices=("12", "long", "both"), default="both")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    if args.report:
        REPORT.write_text(report(args.arms if args.arms != [3] else [0, 3], load_arm),
                          encoding="utf-8")  # fmt: skip
        print(f"written: {REPORT}")
        return 0
    sets = {"12": [False], "long": [True], "both": [False, True]}[args.set]
    jobs = [(pid, arm, long) for long in sets for arm in args.arms
            for pid in (args.only or development_ids(long=long))]  # fmt: skip
    for arm in args.arms:
        arm_options(arm)  # refuses arms that are not run
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for line in pool.map(_job, jobs):
            print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
