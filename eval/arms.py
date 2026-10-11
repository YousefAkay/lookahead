"""Step 2 of the 2026-10-12 plan: the arms on the merged code, backtested on development personas.

Method in DECISIONS.md (2026-10-12, "Step 2: the arms on the merged code"). Each arm is the
backtest of `eval/backtest.py` (the same origins, futures, seed and method, the same record
format) with one set of forecaster options, keeps-coming on in every arm (the default):

    0  the forecaster as merged (the baseline): options off
    3  b) by calendar position placed by default (only if Step 1 passed its gate)

The renewal draw's plan (DECISIONS.md, 2026-10-12, "The renewal draw: built behind a switch"):

    5  the default forecaster as merged, run again for this comparison (the saved arm 0 is older)
    6  the renewal draw on (6 past gaps and 2 longer than the wait, as recorded)
    7  the renewal draw with a minimum of 4 past gaps (sensitivity, synthetic only)
    8  the renewal draw with a minimum of 8 past gaps (sensitivity, synthetic only)

Arms 1, 2 and 4 were the a) lump proposals (every proposal confirmed; the right ones confirmed;
a) with b)). a) was retired on 2026-10-12 and its option removed from the forecaster; their
records are in docs/results/arms_2026-10-12.md and DECISIONS.md.

Random lump arrivals (`lump_arrivals`) are not an arm: keeps-coming replaces them. The option
sets of the 2026-10-11 plan are in the history of this file.

    python -m eval.arms --arms 1 2 --set both   # records in data/backtest/arms-2026-10-12/...
    python -m eval.arms --report                # writes docs/results/arms_2026-10-12.md
    python -m eval.arms --arms 5 6 --set 12     # the renewal draw against the default
    python -m eval.arms --arms 5 6 7 8 --report # writes docs/results/renewal_2026-10-12.md
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
    DEFAULT_OUT,
    FUTURES,
    HORIZON,
    LONG_OUT,
    METHOD,
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
from lookahead.forecast import spending
from lookahead.forecast.path import ForecastOptions, build_forecast
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
RENEWAL_REPORT = Path("docs/results/renewal_2026-10-12.md")
ARM_NAMES = {
    0: "merged forecaster (baseline)",
    3: "b) by calendar position",
    5: "default, run again (constant rate)",
    6: "renewal draw (6 gaps)",
    7: "renewal draw, 4 gaps",
    8: "renewal draw, 8 gaps",
}
RENEWAL_ARMS = {5: None, 6: 6, 7: 4, 8: 8}  # arm -> the minimum number of past gaps (None: off)
BASE_ARM = 5  # the renewal comparison's baseline
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
    if arm in RENEWAL_ARMS:
        return ForecastOptions(renewal=RENEWAL_ARMS[arm] is not None)
    if arm in (1, 2, 4):
        raise ValueError(f"arm {arm} was a) lump proposals, retired on 2026-10-12")
    raise ValueError(f"arm {arm} does not exist")


def arm_dir(arm: int, long: bool) -> Path:
    """Arm 0 is the development backtest itself (same code path); the others have their own."""
    if arm == 0:
        return (LONG_OUT if long else DEFAULT_OUT) / "full"
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
                **version_settings(persona_id)}  # fmt: skip
    if arm:  # arm 0 is the same code path as before; the others are this plan's
        settings["plan"] = "2026-10-12"
    minimum = RENEWAL_ARMS.get(arm)
    if minimum is not None:
        settings["renewal_min_gaps"] = minimum
    if meta.exists() and json.loads(meta.read_text(encoding="utf-8")) != settings:
        raise ValueError(f"{meta} was made with different settings")
    meta.write_text(json.dumps(settings), encoding="utf-8")
    done = set()
    if path.exists():
        done = {json.loads(line)["origin"] for line in path.read_text().splitlines() if line}
    todo = [d for d in origins() if d.isoformat() not in done]
    if not todo:
        return 0
    kept = spending.RENEWAL_MIN_GAPS
    if minimum is not None:
        spending.RENEWAL_MIN_GAPS = minimum  # the sensitivity arms: this process only
    try:
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
                record["renewal"] = seen.arrivals.renewal  # "", "draw" or "fallback"
                out.write(json.dumps(record) + "\n")
                out.flush()
    finally:
        spending.RENEWAL_MIN_GAPS = kept
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
                     "width30": m["width"][30], "brier": m["brier"]["brier"]}  # fmt: skip
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
            and r.get("renewal") != "draw"
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


def renewal_lines(arm: list[Record], base: list[Record], by_arm: dict[str, Record],
                  by_base: dict[str, Record]) -> list[str]:  # fmt: skip
    """The renewal draw's own checks (DECISIONS.md, 2026-10-12): how many origins used the draw,
    the origins that changed with no recurring-size deposits, the personas with such deposits
    where fewer than half of the origins used the draw, and the archetypes whose 30-day band is
    wider than the default's."""
    with_deposits = [r for r in arm if r["arrivals_in"] > 0]
    draw = [r for r in with_deposits if r.get("renewal") == "draw"]
    share = _p(len(draw) / len(with_deposits) if with_deposits else None)
    out = [f"  - origins: {len(arm)}; with recurring-size deposits {len(with_deposits)}; on the "
           f"renewal draw {len(draw)} ({share}), on the constant-rate fallback "
           f"{len(with_deposits) - len(draw)}; with none "
           f"{len(arm) - len(with_deposits)}"]  # fmt: skip
    at = {(r["persona"], r["origin"]): r for r in base}
    moved = [f"{r['persona']} {r['origin']}" for r in arm if r["arrivals_in"] == 0
             and any(r[f] != at[(r["persona"], r["origin"])][f]
                     for f in FORECAST_FIELDS)]  # fmt: skip
    out.append(f"  - origins that changed with no recurring-size deposits (unexpected): "
               f"{len(moved)}" + (f" ({', '.join(moved[:6])})" if moved else ""))  # fmt: skip
    low_use = []
    for pid in sorted({r["persona"] for r in with_deposits}):
        mine = [r for r in with_deposits if r["persona"] == pid]
        used = sum(r.get("renewal") == "draw" for r in mine)
        if used * 2 < len(mine):
            low_use.append(f"{pid} ({used} of {len(mine)})")
    out.append("  - personas with recurring-size deposits where fewer than half of those origins "
               f"used the draw (unexpected): {len(low_use)}"
               + (f" ({', '.join(low_use)})" if low_use else ""))  # fmt: skip
    wider = [c for c in by_base if by_arm[c]["width30"] > by_base[c]["width30"]]
    out.append(f"  - archetypes whose 30-day band is wider than the default's (unexpected): "
               f"{len(wider)} ({', '.join(wider) or '-'})")  # fmt: skip
    by_code: dict[str, list[Record]] = {}
    for r in with_deposits:
        by_code.setdefault(r["archetype"], []).append(r)
    cells = [f"{c} {sum(r.get('renewal') == 'draw' for r in rs)}/{len(rs)}"
             for c, rs in sorted(by_code.items())]  # fmt: skip
    out.append("  - origins on the draw of those with recurring-size deposits, by archetype: "
               + (", ".join(cells) or "-"))  # fmt: skip
    return out


def report(arms: list[int], loader: Callable[[int, bool], list[Record]]) -> str:
    renewal = BASE_ARM in arms
    out = ["# Step 2: the arms on the merged code (synthetic, development personas)", "",
           "Made with `python -m eval.arms --report`. Method in DECISIONS.md (2026-10-12, step 2). "
           "20 development personas per set, 40 weekly origins, 500 futures, seed 42, weekday "
           "method, raw chances, keeps-coming on in every arm. Forecast metrics on origins not "
           "already in shortfall; alerts on the same, with the already-in-shortfall origins left "
           "out as before. Recall is given for both definitions: opened ahead (original) / on "
           "ahead (the reading of 2026-10-12). **Arms 2 and 4 are upper bounds**: the answer key "
           "picks the proposals to confirm, so no user could get them.", ""]  # fmt: skip
    if renewal:
        out = ["# The renewal draw against the default (synthetic, development personas)", "",
               "Made with `python -m eval.arms --arms 5 6 7 8 --report`. Method and prediction in "
               "DECISIONS.md (2026-10-12, the renewal draw built behind a switch). 20 "
               "development personas per set (the 12-month and the 24-month set are never "
               "pooled), 40 weekly origins, 500 futures, seed 42, weekday method, raw chances. "
               "Arm 5 is the default as merged, arm 6 the renewal draw (6 past gaps, 2 longer "
               "than the wait), arms 7 and 8 the same with a minimum of 4 and 8 gaps. Forecast "
               "metrics on origins not already in shortfall. Recall is given for both "
               "definitions: opened ahead / on ahead.", ""]  # fmt: skip
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
        base = BASE_ARM if renewal else 0
        if base in loaded:
            out += ["", f"### {name}: the drop rule and the unexpected checks (fixed before "
                    "computing)", ""]  # fmt: skip
            for a in [a for a in loaded if a != base]:
                error, low = worse_archetypes(by[a], by[base])
                dropped = len(error) >= DROP_IF_WORSE or len(low) >= DROP_IF_WORSE
                odd = changed_without_cause(loaded[a], loaded[base])
                bound = " (upper bound)" if a in UPPER_BOUNDS else ""
                out.append(
                    f"- arm {a}{bound}: worse on the 30-day error {len(error)} of {len(by[base])} "
                    f"({', '.join(error) or '-'}); worse on the lowest balance {len(low)} of "
                    f"{len(by[base])} ({', '.join(low) or '-'}); "
                    f"**{'dropped' if dropped else 'not dropped'}**; "
                    f"origins that changed with nothing confirmed or placed: {len(odd)}"
                    + (f" ({', '.join(odd[:10])}{', ...' if len(odd) > 10 else ''})" if odd else "")
                )
                if renewal:
                    out += renewal_lines(loaded[a], loaded[base], by[a], by[base])
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
    parser.add_argument("--arms", nargs="*", type=int, default=[3])  # 5 6 7 8: the renewal draw's
    parser.add_argument("--set", choices=("12", "long", "both"), default="both")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    if args.report:
        target = RENEWAL_REPORT if BASE_ARM in args.arms else REPORT
        target.write_text(report(args.arms if args.arms != [3] else [0, 3], load_arm),
                          encoding="utf-8")  # fmt: skip
        print(f"written: {target}")
        return 0
    sets = {"12": [False], "long": [True], "both": [False, True]}[args.set]
    # persona by persona (the arms of one persona side by side), so a stopped run still compares
    jobs = [(pid, arm, long) for long in sets for pid in (args.only or development_ids(long=long))
            for arm in args.arms]  # fmt: skip
    for arm in args.arms:
        arm_options(arm)  # refuses arms that are not run
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for n, line in enumerate(pool.map(_job, jobs), 1):
            print(f"[{n}/{len(jobs)}] {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
