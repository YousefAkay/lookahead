"""Experiment 3: net-flow anchoring (synthetic development personas and Berka development accounts).

The rule is in DECISIONS.md (2026-10-17, "Experiment 3", and the change of the minimum to 8 rows).
The forecast's average unscheduled net flow over the 30 days is pulled towards the trailing
realized one: every future's balance on forecast day h moves by lambda x (U_trail - U_fc) x h.

* U_trail: the unscheduled rows (not in a recurring series, not set aside as big one-time money or
  a pass-through) posted in the 91 days ending on the origin, summed and divided by 91. No other
  trimming.
* U_fc: the mean over the futures of the balance on day 30, less the starting balance, less the
  scheduled items placed in the 30 days, divided by 30 (spending, fees, irregular deposits and
  keeps-coming arrivals, no scheduled items).
* Used only with at least 91 days of history and at least 8 unscheduled rows in the 91 days;
  below that the forecast is left exactly as it is.

Two variants, lambda = 1 and lambda = 0.5, come from one forecast per origin. Nothing here is
tuned: the window, the trimming and the minimum are the committed ones.

    python -m eval.anchor quick                    # 2 personas per set and 3 accounts, 4 origins
    python -m eval.anchor run --set 12 --jobs 2    # the 20 development personas, 12-month set
    python -m eval.anchor run --set long --jobs 2
    python -m eval.anchor run --set berka --jobs 2 # the 200 Berka primary development accounts
    python -m eval.anchor report                   # writes docs/results/anchor_2026-10-17.md

Records go to data/backtest/exp3 and data/berka/runs/exp3 (not committed). Held-out personas and
held-out Berka accounts are refused.
"""

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from eval import anchor_history as ah
from eval.backtest import (
    FUTURES,
    METHOD,
    QUICK_FUTURES,
    _check_settings,
    origins,
    quick_origins,
    run_origin_full,
    version_settings,
)
from eval.baselines import crps_samples
from eval.berka import DATA, build_db, dip_line_cents, origin_dates
from eval.berka_gate import load as load_berka
from eval.berka_split import assert_development_only as berka_development_only
from eval.berka_split import development_accounts
from eval.metrics import alert_metrics, forecast_metrics
from eval.personas_db import persona_db
from eval.thresholds import THRESHOLDS
from eval.thresholds_raw import choose, curve
from lookahead.forecast.path import DEFAULT_OPTIONS, Forecast, forecaster_settings
from lookahead.generator.archetypes import assert_development_only, development_ids

VARIANTS = {"1": 1.0, "0.5": 0.5}
PLAN = "2026-10-17 experiment 3"
OUT = Path("data/backtest/exp3")
BERKA_OUT = DATA / "runs" / "exp3"
REPORT = Path("docs/results/anchor_2026-10-17.md")
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 2026
MIN_DIPS = 5
MIN_EPISODES = 30
CASH_FILE = Path("docs/berka_cash_series_accounts.json")

Record = dict[str, Any]


# --- The anchoring ---


def shifts(delta: float, days: int) -> np.ndarray:
    """What is added to the balance on forecast days 1 .. days."""
    return delta * np.arange(1, days + 1)


def unscheduled_forecast_flow(draws: np.ndarray, start_cents: int, scheduled_cents: int) -> float:
    """U_fc in cents a day: the mean final balance less the start less the scheduled items."""
    return float((draws[:, -1].mean() - start_cents - scheduled_cents) / draws.shape[1])


def nearest_rank(values: np.ndarray, share: float) -> int:
    """The simulator's percentile: the value at rank round(share x (n - 1)) of the sorted values."""
    ordered = sorted(int(np.rint(v)) for v in values)
    return ordered[round(share * (len(ordered) - 1))]


def anchored(
    draws: np.ndarray, base: Record, delta: float, buffer_cents: int, realized: np.ndarray
) -> Record:  # fmt: skip
    """The fields of one origin after moving every future by `delta` x h on day h."""
    add = shifts(delta, draws.shape[1])
    moved = draws + add
    p50 = np.rint(np.asarray(base["p50"]) + add).astype(int)
    return {
        "delta": delta,
        "p10": np.rint(np.asarray(base["p10"]) + add).astype(int).tolist(),
        "p50": p50.tolist(),
        "p90": np.rint(np.asarray(base["p90"]) + add).astype(int).tolist(),
        "point": np.rint(np.asarray(base["point"]) + add).astype(int).tolist(),
        "shortfall_probability": float((moved.min(axis=1) < buffer_cents).mean()),
        "median_lowest_cents": nearest_rank(moved.min(axis=1), 0.5),
        "crps": crps_samples(moved, realized).round(1).tolist(),
        "moved": bool(np.any(p50 != np.asarray(base["p50"]))),
    }


def anchor_origin(
    conn: sqlite3.Connection, account: str, origin: date, futures: int, archetype: str
) -> Record:  # fmt: skip
    """One origin: the default forecast, the anchoring's inputs, and both variants."""
    record, paths, f = run_origin_full(
        conn, account, "synthetic", origin, futures, archetype, False, keep_paths=True,
        options=DEFAULT_OPTIONS,
    )  # fmt: skip
    return finish_origin(conn, account, origin, record, np.asarray(paths, dtype=float), f)


def finish_origin(
    conn: sqlite3.Connection, account: str, origin: date, record: Record, draws: np.ndarray,
    f: Forecast,
) -> Record:  # fmt: skip
    counts = ah.count_from_forecast(conn, account, origin, f)
    qualifies = ah.qualifies(counts)
    first, last = f.start.first_day, f.start.last_day
    scheduled = sum(i.cents for i in f.items if first <= i.day <= last)
    u_fc = unscheduled_forecast_flow(draws, f.start.balance_cents, scheduled)
    u_trail = counts["unscheduled_cents"] / ah.WINDOW_DAYS
    realized = np.asarray(record["realized"], dtype=float)
    record["lowest_cents"] = []  # not needed below; keeps the records small
    record["crps_default"] = crps_samples(draws, realized).round(1).tolist()
    record["crps_stays"] = np.abs(float(record["start_balance_cents"]) - realized).round(1).tolist()
    record["everyday"] = sum(d.spending_cents for d in f.days)
    record["counts"] = counts
    record["qualifies"] = qualifies
    record["u_trail"] = u_trail
    record["u_fc"] = u_fc
    record["variants"] = {}
    for key, lam in VARIANTS.items():
        delta = lam * (u_trail - u_fc) if qualifies else 0.0
        record["variants"][key] = anchored(draws, record, delta, record["buffer_cents"], realized)
    return record


def settings_for(futures: int, seed: int, persona_id: str) -> dict[str, Any]:
    return {"futures": futures, "seed": seed, "method": METHOD, "plan": PLAN,
            "variants": VARIANTS, "window": ah.WINDOW_DAYS, "min_history": ah.MIN_HISTORY_DAYS,
            "min_rows": ah.MIN_ROWS, **version_settings(persona_id),
            "forecaster": forecaster_settings(DEFAULT_OPTIONS)}  # fmt: skip


def saved_origins(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(x)["origin"] for x in path.read_text("utf-8").splitlines() if x}


# --- Runs ---


def run_persona(pid: str, long: bool, folder: Path, days: list[date], futures: int) -> int:
    assert_development_only([pid])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{pid}.jsonl"
    _check_settings(folder / f"{pid}.meta.json", settings_for(futures, 42, pid))
    todo = [d for d in days if d.isoformat() not in saved_origins(path)]
    if not todo:
        return 0
    with persona_db(pid, folder / "work", 42) as db, path.open("a") as out:
        for origin in todo:
            rec = anchor_origin(db.conn, pid, origin, futures, db.persona.archetype)
            out.write(json.dumps(rec) + "\n")
            out.flush()
    return len(todo)


def run_account(args: tuple[int, pd.DataFrame, Path, int, bool]) -> int:
    account, frame, folder, futures, quick = args
    berka_development_only([account])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{account}.jsonl"
    _check_settings(folder / f"{account}.meta.json", settings_for(futures, 0, str(account)))
    days = origin_dates(frame)
    if quick:
        days = days[::10]
    todo = [d for d in days if d.isoformat() not in saved_origins(path)]
    if not todo:
        return 0
    db = folder / "work" / f"{account}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = build_db(frame, account, db, dip_line_cents(frame, origin_dates(frame)[0]))
    try:
        with path.open("a") as out:
            for origin in todo:
                rec = anchor_origin(conn, str(account), origin, futures, "berka")
                rec["account"] = account
                out.write(json.dumps(rec) + "\n")
                out.flush()
    finally:
        conn.close()
        db.unlink(missing_ok=True)
    return len(todo)


def _persona_job(args: tuple[str, bool, Path, int, bool]) -> str:
    pid, long, folder, futures, quick = args
    n = run_persona(pid, long, folder, quick_origins() if quick else origins(), futures)
    return f"{pid}: {n} new origins"


def _berka_frames() -> tuple[list[int], dict[int, pd.DataFrame]]:
    ids = development_accounts("primary")
    berka_development_only(ids)
    t = load_berka(DATA)
    t = t[t["account_id"].isin(ids)].reset_index(drop=True)  # only development rows are kept
    return ids, {a: g.reset_index(drop=True) for a, g in t.groupby("account_id")}


def quick(futures: int = QUICK_FUTURES) -> int:
    """A few origins of each kind, timed, to measure the cost of the full run."""
    timings: dict[str, tuple[float, int]] = {}
    for long in (False, True):
        ids = development_ids(long=long)[:2]
        folder = OUT / ("quick-long" if long else "quick-12")
        start = time.time()
        n = sum(run_persona(pid, long, folder, quick_origins(), futures) for pid in ids)
        timings["long" if long else "12"] = (time.time() - start, n)
    ids, frames = _berka_frames()
    start = time.time()
    n = sum(run_account((a, frames[a], BERKA_OUT / "quick", futures, True)) for a in ids[:3])
    timings["berka"] = (time.time() - start, n)
    for k, (sec, n) in timings.items():
        each = sec / max(n, 1)
        print(f"quick {k}: {n} origins in {sec:.0f} s ({each:.2f} s each, {futures} futures)")
    return 0


# --- The report ---


def view(r: Record, key: str | None) -> Record:
    """The record as the default forecast made it (key None) or as a variant moved it."""
    if key is None:
        return r
    v = r["variants"][key]
    return {**r, "p10": v["p10"], "p50": v["p50"], "p90": v["p90"], "point": v["point"],
            "shortfall_probability": v["shortfall_probability"],
            "median_lowest_cents": v["median_lowest_cents"]}  # fmt: skip


def load_set(folder: Path) -> list[Record]:
    out: list[Record] = []
    for path in sorted(folder.glob("*.jsonl")):
        out += [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]
    return out


def ordinary(records: list[Record]) -> list[Record]:
    return [r for r in records if not r["in_shortfall_at_origin"]]


def stats(records: list[Record]) -> Record | None:
    """The measures of the rule for one group of (ordinary) records."""
    rows = ordinary(records)
    if not rows:
        return None
    m = forecast_metrics(rows)
    return {"n": len(rows), "accounts": len({r["persona"] for r in rows}),
            "err30": m["error"][30]["mean_p50"], "bias": m["lowest"]["bias"],
            "cov30": m["coverage"][30], "covall": m["coverage"]["all"],
            "width30": m["width"][30], "brier": m["brier"]["brier"]}  # fmt: skip


def rel(new: float, old: float) -> float:
    return (new - old) / old if old else 0.0


def cov_gap(c: float) -> float:
    return abs(c - 0.8)


def crps_mean(records: list[Record], key: str | None) -> float:
    """CRPS in dollars, mean over origins and the 30 days."""
    field = "crps_default" if key is None else None
    return float(np.mean([
        np.mean(r[field] if field else r["variants"][key]["crps"]) for r in records
    ])) / 100  # fmt: skip


def crps_stays(records: list[Record]) -> float:
    return float(np.mean([np.mean(r["crps_stays"]) for r in records])) / 100


def events(records: list[Record]) -> int:
    return sum(r["realized_first_below_buffer"] is not None for r in records)


def brier_stays(records: list[Record]) -> float:
    return float(np.mean([r["realized_first_below_buffer"] is not None for r in records]))


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def moved_share(records: list[Record], key: str) -> tuple[int, int, int]:
    """(moved origins, qualifying origins, origins) for a variant."""
    return (sum(r["variants"][key]["moved"] for r in records),
            sum(r["qualifies"] for r in records), len(records))  # fmt: skip


def synthetic_section(title: str, records: list[Record], arm5: dict[str, Record]) -> tuple[
    list[str], dict[str, bool]
]:  # fmt: skip
    """Conditions 1 to 5 on one synthetic set, and the share of origins moved."""
    keys = [None, "1", "0.5"]
    verdict: dict[str, bool] = {}
    out = [f"## {title}", ""]
    # condition 1
    changed = 0
    checked = 0
    for r in records:
        b = arm5.get(f"{r['persona']}|{r['origin']}")
        if b is None:
            continue
        for k in ("1", "0.5"):
            v = r["variants"][k]
            if not v["moved"]:
                checked += 1
                changed += any(b[f] != (v[f] if f in v else r[f]) for f in
                               ("p10", "p50", "p90", "shortfall_probability",
                                "median_lowest_cents"))  # fmt: skip
    sane = all(r["p50"] == arm5[f"{r['persona']}|{r['origin']}"]["p50"] for r in records
               if f"{r['persona']}|{r['origin']}" in arm5)  # fmt: skip
    verdict["1"] = changed == 0 and sane
    out += [f"Origins: {len(records)} ({len(ordinary(records))} not already below the buffer); "
            f"personas: {len({r['persona'] for r in records})}.", "",
            f"**Condition 1 (unchanged where it should be): {'PASS' if verdict['1'] else 'FAIL'}.**"
            f" Origins the anchoring did not move, both variants: {checked:,}; of those, differing "
            f"from arm 5 in p10, p50, p90, dip chance or median lowest balance: {changed}. The "
            f"default forecast equals arm 5 at {sum(1 for r in records if f'{r['persona']}|{r['origin']}' in arm5):,}"  # noqa: E501
            f" of {len(records):,} records.", ""]  # fmt: skip
    # share moved
    out += ["### Share of origins moved", "",
            "| variant | origins moved | origins meeting the minimum | origins | personas with "
            "an origin moved | zero everyday estimate: moved / origins |",
            "|---|---|---|---|---|---|"]  # fmt: skip
    for k in ("1", "0.5"):
        mv, q, n = moved_share(records, k)
        pers = len({r["persona"] for r in records if r["variants"][k]["moved"]})
        z = [r for r in records if r["everyday"] == 0]
        zm = sum(r["variants"][k]["moved"] for r in z)
        out.append(
            f"| λ = {k} | {mv:,} ({mv / len(records):.1%}) | {q:,} ({q / len(records):.1%})"
            f" | {len(records):,} | {pers} | {zm} / {len(z)} |"
        )
    out.append("")
    # archetypes
    by_arch: dict[str, list[Record]] = {}
    for r in records:
        by_arch.setdefault(r["archetype"], []).append(r)
    by_arch = dict(sorted(by_arch.items()))
    table: dict[str, dict[str | None, Record]] = {}
    for a, rows in by_arch.items():
        table[a] = {k: stats([view(r, k) for r in rows]) or {} for k in keys}
    out += ["### By archetype (ordinary origins): 30-day error / lowest-balance bias / 30-day band "
            "coverage / all-days coverage / 30-day width", "",
            "| archetype (n origins, personas) | default | λ = 1 | λ = 0.5 | share of origins "
            "moved, λ = 1 |", "|---|---|---|---|---|"]  # fmt: skip

    def cell(s: Record) -> str:
        return (
            f"${s['err30']:,.0f} / ${s['bias']:,.0f} / {s['cov30']:.0%} / {s['covall']:.0%} / "
            f"${s['width30']:,.0f}"
        )

    for a, rows in by_arch.items():
        t = table[a]
        mv, _, n = moved_share(ordinary(rows), "1")
        out.append(
            f"| {a} ({t[None]['n']}, {t[None]['accounts']}) | {cell(t[None])} | "
            f"{cell(t['1'])} | {cell(t['0.5'])} | {mv / n:.0%} ({mv}/{n}) |"
        )
    out.append("")
    # condition 2
    bad_arch, bad_persona = [], []
    for a in by_arch:
        d, v = table[a][None], table[a]["1"]
        if v["err30"] > d["err30"] * 1.01 or abs(v["bias"]) > abs(d["bias"]) * 1.01:
            bad_arch.append(a)
    by_persona: dict[str, list[Record]] = {}
    for r in records:
        by_persona.setdefault(r["persona"], []).append(r)
    for pid, rows in by_persona.items():
        d, v = stats(rows), stats([view(r, "1") for r in rows])
        if d and v and (v["err30"] > d["err30"] * 1.05 or abs(v["bias"]) > abs(d["bias"]) * 1.05):
            bad_persona.append(pid)
    verdict["2"] = not bad_arch and not bad_persona
    out += [f"**Condition 2 (no harm): {'PASS' if verdict['2'] else 'FAIL'}.** Archetypes worse by "
            f"more than 1% on the 30-day error or the absolute bias: "
            f"{', '.join(bad_arch) if bad_arch else 'none'} (of {len(by_arch)}). Personas worse "
            f"by more than 5%: {', '.join(bad_persona) if bad_persona else 'none'} (of "
            f"{len(by_persona)}).", ""]  # fmt: skip
    # condition 3
    gains, rel_e, rel_b = [], [], []
    for a, rows in by_arch.items():
        d, v = table[a][None], table[a]["1"]
        de, db = rel(v["err30"], d["err30"]), rel(abs(v["bias"]), abs(d["bias"]))
        rel_e.append(de)
        rel_b.append(db)
        mv, _, n = moved_share(ordinary(rows), "1")
        if (de < -0.01 or db < -0.01) and mv / n >= 0.5:
            gains.append(a)
    verdict["3"] = len(gains) >= 2 and float(np.mean(rel_e)) < 0 and float(np.mean(rel_b)) < 0
    out += [f"**Condition 3 (a real gain): {'PASS' if verdict['3'] else 'FAIL'}.** Archetypes "
            f"better"
            f" by more than 1% on the 30-day error or the absolute bias, with at least half of "
            f"their origins moved: {', '.join(gains) if gains else 'none'} ({len(gains)} of "
            f"{len(by_arch)}; 2 needed). Average over the {len(by_arch)} archetypes of the "
            f"relative change: 30-day error {_pct(float(np.mean(rel_e)))}, absolute bias "
            f"{_pct(float(np.mean(rel_b)))} (both must be below 0).", ""]  # fmt: skip
    # condition 4
    cov_bad, width_bad = [], []
    for a in by_arch:
        d, v = table[a][None], table[a]["1"]
        if (cov_gap(v["cov30"]) > cov_gap(d["cov30"]) + 0.02
                or cov_gap(v["covall"]) > cov_gap(d["covall"]) + 0.02):  # fmt: skip
            cov_bad.append(a)
        if v["width30"] > d["width30"] * 1.10:
            width_bad.append(a)
    verdict["4"] = not cov_bad and not width_bad
    out += [f"**Condition 4 (honest band): {'PASS' if verdict['4'] else 'FAIL'}.** Judged on the "
            f"30-day coverage and on the all-days coverage; either breaking the 2 points fails. "
            f"Archetypes with coverage further from 80% by more than 2 points: "
            f"{', '.join(cov_bad) if cov_bad else 'none'}. Archetypes with the 30-day band wider "
            f"by more than 10%: {', '.join(width_bad) if width_bad else 'none'}.", ""]  # fmt: skip
    # condition 5
    sk = {k: [] for k in keys}
    bsk = {k: [] for k in keys}
    for a, rows in by_arch.items():
        ords = ordinary(rows)
        st = crps_stays(ords)
        for k in keys:
            sk[k].append(1 - crps_mean(ords, k) / st)
            if events(ords) >= MIN_DIPS:
                bsk[k].append(1 - table[a][k]["brier"] / brier_stays(ords))
    mean_sk = {k: float(np.mean(v)) for k, v in sk.items()}
    mean_bsk = {k: float(np.mean(v)) if v else float("nan") for k, v in bsk.items()}
    verdict["5"] = mean_sk["1"] >= mean_sk[None] and (
        not bsk[None] or mean_bsk["1"] >= mean_bsk[None])  # fmt: skip
    out += [f"**Condition 5 (not just flatter): {'PASS' if verdict['5'] else 'FAIL'}.** Mean CRPS "
            f"skill against balance stays over the {len(by_arch)} archetypes: default "
            f"{mean_sk[None]:+.3f}, λ = 1 {mean_sk['1']:+.3f}, λ = 0.5 {mean_sk['0.5']:+.3f}. "
            f"Mean Brier skill against balance stays over the {len(bsk[None])} archetypes with "
            f"{MIN_DIPS}+ dips: default {mean_bsk[None]:+.3f}, λ = 1 {mean_bsk['1']:+.3f}, "
            f"λ = 0.5 {mean_bsk['0.5']:+.3f}.", ""]  # fmt: skip
    # direction of the CRPS change, for condition 9
    change = {k: float(np.mean([rel(crps_mean(ordinary(rows), k), crps_mean(ordinary(rows), None))
                                for rows in by_arch.values()])) for k in ("1", "0.5")}  # fmt: skip
    verdict["direction"] = (change["1"] <= 0) == (change["0.5"] <= 0)
    out += [f"Mean over archetypes of the relative change in CRPS (all 30 days): λ = 1 "
            f"{_pct(change['1'])}, λ = 0.5 {_pct(change['0.5'])} (for condition 9).",
            ""]  # fmt: skip
    return out, verdict


def bootstrap_gain(default: np.ndarray, variant: np.ndarray) -> tuple[float, float, float]:
    """The relative gain 1 - mean(variant) / mean(default) over accounts, and the 2.5% and 97.5%
    points of the bootstrap that resamples accounts."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n = len(default)
    gains = []
    for _ in range(BOOTSTRAP_DRAWS):
        idx = rng.integers(0, n, n)
        gains.append(1 - variant[idx].mean() / default[idx].mean())
    lo, hi = np.quantile(gains, [0.025, 0.975])
    return float(1 - variant.mean() / default.mean()), float(lo), float(hi)


def account_crps(records: list[Record], key: str | None, day: int | None) -> dict[int, float]:
    by: dict[int, list[float]] = {}
    for r in records:
        c = r["crps_default"] if key is None else r["variants"][key]["crps"]
        by.setdefault(r["account"], []).append(c[day] if day is not None else float(np.mean(c)))
    return {a: float(np.mean(v)) / 100 for a, v in by.items()}


UNMOVED_FIELDS = ("p10", "p50", "p90", "point", "shortfall_probability", "median_lowest_cents")


def unmoved_differences(records: list[Record]) -> tuple[int, int]:
    """Condition 1 without an arm to compare with: of the (origin, variant) pairs the anchoring
    did not move, how many (checked, differing) from the default forecast of the same record in
    p10, p50, p90, the point forecast, the dip chance or the median lowest balance."""
    checked = differing = 0
    for r in records:
        for v in r["variants"].values():
            if not v["moved"]:
                checked += 1
                differing += any(v[f] != r[f] for f in UNMOVED_FIELDS)
    return checked, differing


def berka_section(records: list[Record]) -> tuple[list[str], dict[str, bool]]:
    """Conditions 1 and 6 to 9 on the Berka primary development accounts."""
    verdict: dict[str, bool] = {}
    accounts = sorted({r["account"] for r in records})
    out = [f"## Berka, primary development accounts ({len(accounts)} accounts, "
           f"{len(records):,} origins)", "",
           "The 30-day CRPS is the CRPS of the balance on forecast day 30; the mean over all 30 "
           "days is given as information. CRPS uses every origin; the error, bias, coverage and "
           "Brier use the origins not already below one month's outflow.", ""]  # fmt: skip
    # condition 1
    checked, differing = unmoved_differences(records)
    below = sum(not r["qualifies"] for r in records)
    few_days = sum(r["counts"]["history_days"] < ah.MIN_HISTORY_DAYS for r in records)
    few_rows = sum(
        r["counts"]["history_days"] >= ah.MIN_HISTORY_DAYS
        and r["counts"]["unscheduled"] < ah.MIN_ROWS
        for r in records
    )
    verdict["1"] = differing == 0
    out += [f"**Condition 1 (unchanged where it should be): {'PASS' if verdict['1'] else 'FAIL'}.**"
            f" Origins below the minimum ({ah.MIN_HISTORY_DAYS} days of history or {ah.MIN_ROWS} "
            f"unscheduled rows): {below:,} of {len(records):,} ({few_days:,} short of the days, "
            f"{few_rows:,} with enough days but too few rows). (Origin, variant) "
            f"pairs the anchoring did not move: {checked:,}; of those, differing from the default "
            f"forecast of the same record in p10, p50, p90, the point forecast, the dip chance or "
            f"the median lowest balance: {differing}.", ""]  # fmt: skip
    # share moved
    out += ["### Share of origins moved", "",
            "| variant | origins moved | meeting the minimum | origins | accounts with an origin "
            "moved |", "|---|---|---|---|---|"]  # fmt: skip
    for k in ("1", "0.5"):
        mv, q, n = moved_share(records, k)
        acc = len({r["account"] for r in records if r["variants"][k]["moved"]})
        out.append(
            f"| λ = {k} | {mv:,} ({mv / n:.1%}) | {q:,} ({q / n:.1%}) | {n:,} | {acc} of "
            f"{len(accounts)} |"
        )
    zero = [r for r in records if r["everyday"] == 0]
    za = len({r["account"] for r in zero})
    out += ["", f"Within the zero-estimate stratum ({len(zero):,} origins in {za} accounts): "
            + "; ".join(f"λ = {k}: moved {sum(r['variants'][k]['moved'] for r in zero):,} of "
                        f"{len(zero):,} ({sum(r['variants'][k]['moved'] for r in zero) / max(len(zero), 1):.1%}), "  # noqa: E501
                        f"in {len({r['account'] for r in zero if r['variants'][k]['moved']})} "
                        f"accounts" for k in ("1", "0.5")) + ".", ""]  # fmt: skip
    # condition 6
    d = account_crps(records, None, 29)
    res: dict[str, tuple[float, float, float]] = {}
    for k in ("1", "0.5"):
        v = account_crps(records, k, 29)
        res[k] = bootstrap_gain(
            np.array([d[a] for a in accounts]), np.array([v[a] for a in accounts])
        )
    dm = account_crps(records, None, None)
    info = {k: bootstrap_gain(np.array([dm[a] for a in accounts]),
                              np.array([account_crps(records, k, None)[a] for a in accounts]))
            for k in ("1",)}  # fmt: skip
    g, lo, hi = res["1"]
    verdict["6"] = g >= 0.01 and lo > 0
    out += [f"### Condition 6 (the margin): {'PASS' if verdict['6'] else 'FAIL'}", "",
            f"Mean 30-day CRPS (day 30), accounts weighted equally, n = {len(accounts)} accounts "
            f"({len(records):,} origins): default ${np.mean([d[a] for a in accounts]):,.2f}. "
            f"λ = 1: relative gain {_pct(g)}, 95% bootstrap interval [{_pct(lo)}, {_pct(hi)}] "
            f"(seed {BOOTSTRAP_SEED}, {BOOTSTRAP_DRAWS} draws); needs a gain of at least +1.0% and"
            f" a lower end above 0. λ = 0.5: {_pct(res['0.5'][0])} "
            f"[{_pct(res['0.5'][1])}, {_pct(res['0.5'][2])}]. For information, the CRPS averaged "
            f"over the 30 days, λ = 1: {_pct(info['1'][0])} "
            f"[{_pct(info['1'][1])}, {_pct(info['1'][2])}].", ""]  # fmt: skip
    # condition 7
    keys = [None, "1"]
    s = {k: stats([view(r, k) for r in records]) for k in keys}
    d7, v7 = s[None], s["1"]
    checks = {
        "30-day error": rel(v7["err30"], d7["err30"]) <= 0.01,
        "30-day coverage": cov_gap(v7["cov30"]) <= cov_gap(d7["cov30"]) + 0.02,
        "all-days coverage": cov_gap(v7["covall"]) <= cov_gap(d7["covall"]) + 0.02,
        "Brier": rel(v7["brier"], d7["brier"]) <= 0.01,
    }
    picks, caught = {}, {}
    for k in keys:
        recs = [view(r, k) for r in records]
        pick = choose(curve(recs, THRESHOLDS))
        picks[k] = None if pick is None else pick["threshold"]
        m = alert_metrics(recs, picks[k], median_rule=False) if pick else None
        caught[k] = m
    episodes = (caught[None] or {}).get("shortfall_episodes", 0)
    dips_ok = True
    dip_note = f"{episodes} shortfall episodes (needs {MIN_EPISODES}): informational only"
    if episodes >= MIN_EPISODES and caught[None] and caught["1"]:
        dips_ok = caught["1"]["caught_on"] >= caught[None]["caught_on"]
        dip_note = (
            f"dips caught on ahead at the 70% precision bar: default "
            f"{caught[None]['caught_on']}/{episodes} (threshold {picks[None]}), λ = 1 "
            f"{caught['1']['caught_on']}/{episodes} (threshold {picks['1']})"
        )
    checks["dips caught"] = dips_ok
    verdict["7"] = all(checks.values())
    out += [f"### Condition 7 (other Berka measures): {'PASS' if verdict['7'] else 'FAIL'}", "",
            f"n = {d7['n']:,} origins in {d7['accounts']} accounts. 30-day error: default "
            f"${d7['err30']:,.0f}, λ = 1 ${v7['err30']:,.0f} ({_pct(rel(v7['err30'], d7['err30']))}"
            f"; limit +1%). 80% coverage at 30 days: {d7['cov30']:.1%} → {v7['cov30']:.1%}; over "
            f"all days {d7['covall']:.1%} → {v7['covall']:.1%} (limit 2 points further from 80). "
            f"Brier of the dip chance: {d7['brier']:.4f} → {v7['brier']:.4f} "
            f"({_pct(rel(v7['brier'], d7['brier']))}; limit +1%). {dip_note}. "
            f"Each check: " + ", ".join(f"{k} {'ok' if v else 'FAILS'}" for k, v in checks.items())
            + ".", ""]  # fmt: skip
    # condition 8
    cash = (
        set(json.loads(CASH_FILE.read_text("utf-8"))["accounts"]) if CASH_FILE.exists() else set()
    )
    rest = [r for r in records if r["account"] not in cash]
    dr = account_crps(rest, None, 29)
    vr = account_crps(rest, "1", 29)
    ra = sorted(dr)
    gr = bootstrap_gain(np.array([dr[a] for a in ra]), np.array([vr[a] for a in ra]))
    direction_ok = (gr[0] > 0) == (g > 0)
    zs = {k: stats([view(r, k) for r in zero]) for k in keys}
    if zs[None] and zs["1"]:
        zd, zv = zs[None], zs["1"]
        zchecks = {
            "30-day error": rel(zv["err30"], zd["err30"]) <= 0.01,
            "absolute bias": rel(abs(zv["bias"]), abs(zd["bias"])) <= 0.01,
            "30-day coverage": cov_gap(zv["cov30"]) <= cov_gap(zd["cov30"]) + 0.02,
            "all-days coverage": cov_gap(zv["covall"]) <= cov_gap(zd["covall"]) + 0.02,
        }
        ztext = (
            f"Zero-estimate stratum, n = {zd['n']:,} ordinary origins in {zd['accounts']} "
            f"accounts: 30-day error ${zd['err30']:,.0f} → ${zv['err30']:,.0f}; bias "
            f"${zd['bias']:,.0f} → ${zv['bias']:,.0f}; coverage at 30 days {zd['cov30']:.1%} "
            f"→ {zv['cov30']:.1%}, all days {zd['covall']:.1%} → {zv['covall']:.1%}. "
            + ", ".join(f"{k} {'ok' if v else 'FAILS'}" for k, v in zchecks.items())
            + "."
        )
    else:
        zchecks = {}
        ztext = "Zero-estimate stratum: too few ordinary origins to measure."
    verdict["8"] = direction_ok and all(zchecks.values())
    out += [f"### Condition 8 (the vetoes): {'PASS' if verdict['8'] else 'FAIL'}", "", ztext, "",
            f"Without the {len(cash)} mostly-cash accounts: n = {len(ra)} accounts "
            f"({len(rest):,} origins), λ = 1 relative gain in the 30-day CRPS {_pct(gr[0])} "
            f"[{_pct(gr[1])}, {_pct(gr[2])}]; the direction {'holds' if direction_ok else 'does NOT hold'}"  # noqa: E501
            f" (main result {_pct(g)}). These can only veto.", ""]  # fmt: skip
    # condition 9
    flip = (res["1"][0] > 0) != (res["0.5"][0] > 0)
    verdict["9"] = not flip
    out += [f"### Condition 9 on Berka (λ = 0.5 does not flip the direction): "
            f"{'PASS' if verdict['9'] else 'FAIL'}", "",
            f"λ = 1 {_pct(res['1'][0])}, λ = 0.5 {_pct(res['0.5'][0])} (relative change in the "
            f"30-day CRPS, n = {len(accounts)} accounts).", ""]  # fmt: skip
    return out, verdict


def report() -> str:
    arm5 = {}
    for name in ("12", "long"):
        folder = Path("data/backtest/arms-2026-10-12") / name / "arm5"
        arm5[name] = {f"{r['persona']}|{r['origin']}": r for r in load_set(folder)}
    lines = ["# Experiment 3: net-flow anchoring (synthetic and Berka development)", "",
             "Made with `python -m eval.anchor report`. Rule in DECISIONS.md (2026-10-17). "
             "Nothing here changes a default. Variants tried: λ = 1 and λ = 0.5, and the change of "
             "the minimum from 20 to 8 rows (3 in all). Condition 10 (my account can only veto) "
             "is not judged here: it needs the real-account run. The synthetic CRPS in condition "
             "5 is the mean over the 30 days (as in experiment 1); the Berka CRPS of condition 6 "
             "is on day 30.", ""]  # fmt: skip
    verdicts: dict[str, dict[str, bool]] = {}
    for name, title in (("12", "12-month set"), ("long", "24-month set")):
        records = load_set(OUT / name)
        sec, verdicts[name] = synthetic_section(title, records, arm5[name])
        lines += sec
    berka = load_set(BERKA_OUT)
    sec, verdicts["berka"] = berka_section(berka)
    lines += sec
    syn = [verdicts[n] for n in ("12", "long")]
    lines += ["## Verdict by condition (judged, not decided: the decision is mine)", "",
              "| condition | 12-month | 24-month | Berka |", "|---|---|---|---|"]  # fmt: skip

    def mark(v: dict[str, bool], key: str) -> str:
        return "-" if key not in v else ("pass" if v[key] else "FAIL")

    for c, label in (("1", "1 unchanged where it should be"), ("2", "2 no harm"),
                     ("3", "3 a real gain"), ("4", "4 honest band"), ("5", "5 not just flatter"),
                     ("6", "6 Berka margin"), ("7", "7 Berka other measures"),
                     ("8", "8 vetoes")):  # fmt: skip
        lines.append(
            f"| {label} | {mark(syn[0], c)} | {mark(syn[1], c)} | {mark(verdicts['berka'], c)} |"
        )
    flips = [not v["direction"] for v in syn] + [not verdicts["berka"]["9"]]
    lines.append(
        f"| 9 not fragile (λ = 0.5 does not flip the direction) | "
        f"{'FAIL' if flips[0] else 'pass'} | {'FAIL' if flips[1] else 'pass'} | "
        f"{'FAIL' if flips[2] else 'pass'} |"
    )
    lines.append("| 10 my account can only veto | not judged | not judged | not judged |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.anchor", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("command", choices=["quick", "run", "report"])
    parser.add_argument("--set", choices=["12", "long", "berka"], default="12")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--futures", type=int, default=QUICK_FUTURES, help="for quick only")
    args = parser.parse_args(argv)
    if args.command == "quick":
        return quick(args.futures)
    if args.command == "report":
        text = report()
        REPORT.write_text(text, encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # the report has "λ"
        print(text)
        return 0
    if args.set == "berka":
        ids, frames = _berka_frames()
        jobs_b = [(a, frames[a], BERKA_OUT, FUTURES, False) for a in ids]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for n, _ in enumerate(pool.map(run_account, jobs_b), 1):
                if n % 10 == 0:
                    print(f"[{n}/{len(ids)}] accounts", flush=True)
        return 0
    long = args.set == "long"
    folder = OUT / ("long" if long else "12")
    jobs = [(pid, long, folder, FUTURES, False) for pid in development_ids(long=long)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for n, line in enumerate(pool.map(_persona_job, jobs), 1):
            print(f"[{n}/{len(jobs)}] {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
