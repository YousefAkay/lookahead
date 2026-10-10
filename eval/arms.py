"""Step 3 of the 2026-10-11 plan: the arms, backtested on development personas.

Method in DECISIONS.md (2026-10-11, "Step 3: the arms"). Each arm is the backtest of
`eval/backtest.py` (the same origins, futures, seed and method, the same record format) with one
set of forecaster options:

    0  the current forecaster
    1  random lump arrivals (step 0)
    2  a) every lump proposal confirmed (a naive user), random arrivals for the rest
    3  a) only the proposals right on the answer key confirmed (a best-case user: an upper
       bound), random arrivals for the rest

Arms 4 and 5 (b) alone, a) and b) together) are not run: b) failed its gate in step 2.

    python -m eval.arms --arms 1 2 3 --set both     # records in data/backtest/arms/<set>/arm<n>
    python -m eval.arms --report                    # writes docs/results/arms_2026-10-11.md
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
from eval.proposals_score import KEY, right_lump, series_rows
from lookahead.forecast.path import ForecastOptions, build_forecast
from lookahead.generator.archetypes import (
    END,
    assert_development_only,
    build_persona,
    development_ids,
    is_long,
)
from lookahead.generator.writer import export_rows

ARMS_OUT = Path("data/backtest/arms")
REPORT = Path("docs/results/arms_2026-10-11.md")
CHOSEN_THRESHOLD = 0.90  # step 1, on raw chances, development personas
ARM_NAMES = {
    0: "current forecaster",
    1: "random lump arrivals",
    2: "a) every proposal confirmed (naive)",
    3: "a) right proposals confirmed (upper bound)",
}
FAMILY = ("-tuition-funding", "-tuition-topup", "-family")

Record = dict[str, Any]


def arm_options(arm: int, confirmed: frozenset[str] = frozenset()) -> ForecastOptions:
    if arm == 0:
        return ForecastOptions()
    if arm == 1:
        return ForecastOptions(lump_arrivals=True)
    if arm == 2:
        return ForecastOptions(lump_arrivals=True, lump_proposals=True, confirm_all_lumps=True,
                               proposal_key=KEY)  # fmt: skip
    if arm == 3:
        return ForecastOptions(lump_arrivals=True, lump_proposals=True, confirmed_keys=confirmed,
                               proposal_key=KEY)  # fmt: skip
    raise ValueError(f"arm {arm} is not run (b) stopped at its gate) or does not exist")


def arm_dir(arm: int, long: bool) -> Path:
    """Arm 0 is the development backtest itself (same code path); the others have their own."""
    if arm == 0:
        return (LONG_OUT if long else DEFAULT_OUT) / "full"
    return ARMS_OUT / ("long" if long else "12") / f"arm{arm}"


def run_persona_arm(persona_id: str, arm: int, out_dir: Path, futures: int = FUTURES,
                    seed: int = 42) -> int:  # fmt: skip
    """All 40 origins of one persona for one arm, saved as they are made (resumable)."""
    assert_development_only([persona_id])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{persona_id}.jsonl"
    meta = out_dir / f"{persona_id}.meta.json"
    settings = {"futures": futures, "seed": seed, "method": METHOD, "arm": arm,
                **version_settings(persona_id)}  # fmt: skip
    if meta.exists() and json.loads(meta.read_text(encoding="utf-8")) != settings:
        raise ValueError(f"{meta} was made with different settings")
    meta.write_text(json.dumps(settings), encoding="utf-8")
    done = set()
    if path.exists():
        done = {json.loads(line)["origin"] for line in path.read_text().splitlines() if line}
    todo = [d for d in origins() if d.isoformat() not in done]
    if not todo:
        return 0
    with persona_db(persona_id, out_dir / "work", seed) as db, path.open("a") as out:
        pid, conn = persona_id, db.conn
        fp_row = {fp: rid for rid, fp in conn.execute(
            "SELECT id, fingerprint FROM transactions WHERE account = ?", (pid,))}  # fmt: skip
        by_series = series_rows(db.labels)
        for origin in todo:
            confirmed: frozenset[str] = frozenset()
            if arm == 3:  # the answer key picks the right proposals (an upper bound)
                f0 = build_forecast(conn, pid, METHOD, origin, None, HORIZON, 0,
                                    options=arm_options(2))  # fmt: skip
                assert f0 is not None
                confirmed = frozenset(
                    p.key for p in f0.lump_proposals
                    if right_lump(p, origin, db.labels, fp_row, by_series)[1] is not None
                )  # fmt: skip
            record = run_origin(conn, pid, "synthetic", origin, futures, db.persona.archetype,
                                db.persona.held_out, arm_options(arm, confirmed))  # fmt: skip
            record["arm"] = arm
            record["confirmed"] = len(confirmed)
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
    chosen = alert_metrics(records, CHOSEN_THRESHOLD, False)
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
        ("Alerts, provisional rule: recall original / new", _alerts(prov)),
        ("Alerts, chance above 90%: recall original / new", _alerts(chosen)),
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


def report(arms: list[int], loader: Callable[[int, bool], list[Record]]) -> str:
    out = ["# Step 3: the arms (synthetic, development personas)", "",
           "Made with `python -m eval.arms --report`. Method in DECISIONS.md (2026-10-11, step 3). "
           "20 development personas per set, 40 weekly origins, 500 futures, seed 42, weekday "
           "method, raw chances. Forecast metrics on origins not already in shortfall; alerts on "
           "the same, with the already-in-shortfall origins left out as before. Recall is given "
           "for both definitions: opened ahead (original) / on ahead (new). Arm 3 is an upper "
           "bound (the answer key picks the proposals to confirm). Arms 4 and 5 were not run: "
           "b) failed its gate.", ""]  # fmt: skip
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
        for code in by[next(iter(by))]:
            first = by[next(iter(by))][code]
            cells = [f"{_d(by[a][code]['error30'])} / {_d(by[a][code]['bias'])} / "
                     f"{_p(by[a][code]['coverage'])} / {by[a][code]['brier']:.3f}"
                     for a in loaded]  # fmt: skip
            out.append(f"| {code} ({first['n']}, {first['personas']}) | " + " | ".join(cells)
                       + " |")  # fmt: skip
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
    assert all(r["format"] == format_of(r["persona"]) for r in records)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.arms", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--arms", nargs="*", type=int, default=[1, 2, 3])
    parser.add_argument("--set", choices=("12", "long", "both"), default="both")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    if args.report:
        REPORT.write_text(report([0, 1, 2, 3], load_arm), encoding="utf-8")
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
