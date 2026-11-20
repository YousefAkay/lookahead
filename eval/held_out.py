"""The held-out test of the forecaster, once: the only route to held-out data.

Protocol: docs/specs/2026-11-19-held-out-test-protocol.md (committed before this file). One
command does everything on one code commit:

    python -m eval.held_out --berka ../lookahead-runs/data/berka --jobs 8

1. Refuses (and writes the refusal to the ledger) unless the protocol is committed, unchanged and
   not a draft, tracked code has no uncommitted changes, the alert threshold is 0.35, the
   forecaster's logic version is 3, the ledger is committed, and its git history holds no
   completed run.
2. The dry run: the same code on the development pools (both synthetic sets, Berka primary and
   sensitivity development). Its rows must equal the committed development report; if any row
   differs, it stops before any held-out data is opened.
3. The held-out run: the four held-out pools. Void checks (counts, futures, no NaN, logic
   version); a void attempt shows no number.
4. Everything is written to the ledger (docs/results/held_out_ledger.jsonl) before any number is
   printed; then the result file docs/results/held_out_<date>.md is written and printed. The
   result file is made from the ledger entry alone: if anything after "completed" fails,
   `python -m eval.held_out --rebuild` writes it again from the ledger and opens no data.

Records are saved under data/backtest/held-out-test/<attempt>/ (not committed).
"""

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import FUTURES, LOGIC, origins
from eval.baselines import MIN_DIPS, scored_record, walk_records
from eval.berka_gate import load as load_berka
from eval.berka_scores import CORE_ROWS, account_records, summary_core
from eval.berka_split import development_accounts, held_out_accounts
from eval.metrics import alert_metrics
from eval.personas_db import persona_db
from lookahead.alerts import PROBABILITY_THRESHOLD
from lookahead.generator.archetypes import HELD_OUT, LONG_HELD_OUT, development_ids

PROTOCOL = Path("docs/specs/2026-11-19-held-out-test-protocol.md")
LEDGER = Path("docs/results/held_out_ledger.jsonl")
DEV_REPORT = Path("docs/results/scores_development_2026-11-19.md")
OUT = Path("data/backtest/held-out-test")
THRESHOLD = 0.35  # the protocol's pin: refused if the app's threshold is anything else
FORECASTER_LOGIC = 3
SEED = 42
ORIGINS = 40
# The dev report's columns the dry run must reproduce, in its order (the fourth is not compared)
DRY_RUN_COLUMNS = ("12-month", "24-month", "berka-primary")
Record = dict[str, Any]


# --- Pools ---


def pool_ids(pool: str, held: bool) -> list[Any]:
    """The personas or accounts of one pool (12-month, 24-month, berka-primary or -sensitivity)."""
    if pool == "12-month":
        return sorted(HELD_OUT) if held else development_ids(False)
    if pool == "24-month":
        return sorted(LONG_HELD_OUT) if held else development_ids(True)
    group = pool.removeprefix("berka-")
    return held_out_accounts(group) if held else development_accounts(group)


POOLS = ("12-month", "24-month", "berka-primary", "berka-sensitivity")


def unit_of(pool: str) -> Callable[[Record], str]:
    """A unit is an archetype for the synthetic sets and an account for Berka."""
    if pool.startswith("berka"):
        return lambda r: str(r["persona"])
    return lambda r: str(r["archetype"])


# --- The run (workers) ---


def _synthetic_job(pid: str) -> list[Record]:
    """Every origin of one persona, made exactly as eval.baselines.run_persona makes them."""
    with tempfile.TemporaryDirectory() as tmp, persona_db(pid, Path(tmp), SEED) as db:
        return [scored_record(db.conn, pid, o, FUTURES, db.persona.archetype) for o in origins()]


def _berka_job(args: tuple[int, Any]) -> list[Record]:
    """Every origin of one Berka account, made exactly as eval.berka_scores.run makes them."""
    account, frame = args
    with tempfile.TemporaryDirectory() as tmp:
        return account_records(account, frame, Path(tmp))


def compute(held: bool, berka_dir: Path, jobs: int) -> dict[str, list[Record]]:
    """The records of the four pools, development (`held` False) or held out."""
    ids = {pool: pool_ids(pool, held) for pool in POOLS}
    wanted = set(ids["berka-primary"]) | set(ids["berka-sensitivity"])
    table = load_berka(berka_dir)
    table = table[table["account_id"].isin(wanted)].reset_index(drop=True)
    frames = {a: g.reset_index(drop=True) for a, g in table.groupby("account_id")}
    out: dict[str, list[Record]] = {}
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futures = {pool: [ex.submit(_synthetic_job, pid) for pid in ids[pool]]
                   for pool in ("12-month", "24-month")}  # fmt: skip
        for pool in ("berka-primary", "berka-sensitivity"):
            futures[pool] = [
                ex.submit(_berka_job, (a, frames[a])) for a in ids[pool] if a in frames
            ]
        for pool in POOLS:
            out[pool] = []
            for n, f in enumerate(futures[pool], 1):
                out[pool] += f.result()
                print(f"  {'held-out' if held else 'development'} {pool}: {n}/{len(futures[pool])}",
                      flush=True)  # fmt: skip
    return out


# --- The numbers ---


def dips(records: Sequence[Record]) -> Record:
    """Dips caught at the fixed threshold, forecast and random walk, both meanings of caught."""
    pool = [r for r in records if r["walk"] is not None]
    out: Record = {}
    for tag, recs in (("f", list(pool)), ("r", walk_records(list(pool)))):
        m = alert_metrics(recs, PROBABILITY_THRESHOLD, median_rule=False)
        out[tag] = {k: m[k] for k in ("caught_on", "caught", "shortfall_episodes", "alert_episodes",
                                      "false_alerts", "precision", "false_per_30_days",
                                      "ordinary_origins")}  # fmt: skip
    return out


def _mean(values: list[float | None]) -> Record:
    kept = [float(v) for v in values if v is not None]
    return {"value": float(np.mean(kept)) if kept else None, "units": len(kept)}


CORE_KEYS = ("crps_f", "crps_n", "crps_r", "crps30_f", "crps30_n", "crps30_r", "coverage30",
             "coverage_all", "brier_f", "brier_n", "brier_r", "bias", "abs_bias")  # fmt: skip
SKILL_KEYS = ("skill_n", "skill_r", "skill30_n", "skill30_r")
BRIER_SKILL_KEYS = ("brier_skill_n", "brier_skill_r")


def _has_scored(records: Sequence[Record]) -> bool:
    return any(not r["in_shortfall_at_origin"] and r["walk"] is not None for r in records)


def per_unit(records: Sequence[Record], unit: Callable[[Record], str]) -> Record:
    """Each quantity computed within each unit, then averaged over units with equal weight.

    Scores: units with at least one scored origin; a CRPS skill only where the baseline's mean
    is above 0; a Brier skill only for units with at least MIN_DIPS dips. Dips caught: recall over
    units with at least one episode, precision over units with at least one alert episode, false
    alerts per 30 days over all units that have ordinary origins."""
    groups: dict[str, list[Record]] = {}
    for r in records:
        groups.setdefault(unit(r), []).append(r)
    scored = [summary_core(g) for g in groups.values() if _has_scored(g)]
    out: Record = {"units": len(groups), "units_scored": len(scored)}
    for k in (*CORE_KEYS, *SKILL_KEYS):
        out[k] = _mean([s[k] for s in scored])
    for k in BRIER_SKILL_KEYS:
        out[k] = _mean([s[k] for s in scored if s["dips"] >= MIN_DIPS])
    caught = [dips(g) for g in groups.values()]
    for tag in ("f", "r"):
        ms = [c[tag] for c in caught]
        out[f"recall_on_{tag}"] = _mean([m["caught_on"] / m["shortfall_episodes"]
                                         for m in ms if m["shortfall_episodes"]])  # fmt: skip
        out[f"recall_{tag}"] = _mean([m["caught"] / m["shortfall_episodes"]
                                      for m in ms if m["shortfall_episodes"]])  # fmt: skip
        out[f"precision_{tag}"] = _mean([m["precision"] for m in ms if m["alert_episodes"]])
        out[f"false30_{tag}"] = _mean([m["false_per_30_days"] for m in ms])
    return out


def results(pool: str, records: Sequence[Record]) -> Record:
    """Pooled, per unit, dips caught, and (Berka) the zero-estimate origins apart."""
    out: Record = {"pooled": {**summary_core(records), "caught": dips(records)},
                   "per_unit": per_unit(records, unit_of(pool))}  # fmt: skip
    if pool.startswith("berka"):
        zero = [r for r in records if r.get("zero_estimate")]
        out["zero_estimate"] = summary_core(zero) if _has_scored(zero) else {"origins": len(zero)}
    return out


# --- The dry run's comparison ---


def report_cells(text: str) -> dict[str, list[str]]:
    """The committed development report's table: label -> its cells."""
    out = {}
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith("| |"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out[cells[0]] = cells[1:]
    return out


def dry_run_differences(summaries: dict[str, Record], text: str) -> list[str]:
    """Every row (of CORE_ROWS) where the dry run's formatted value differs from the report."""
    cells = report_cells(text)
    diffs = []
    for label, fn in CORE_ROWS:
        reported = cells.get(label)
        for i, pool in enumerate(DRY_RUN_COLUMNS):
            mine = fn(summaries[pool])
            if reported is None or len(reported) <= i or reported[i] != mine:
                was = None if reported is None or len(reported) <= i else reported[i]
                diffs.append(f"{pool} | {label}: report {was!r}, dry run {mine!r}")
    return diffs


# --- The void checks ---

NUMBER_LISTS = ("crps_forecast", "crps_stays", "p10", "p50", "p90", "realized")


def void_problems(pools: dict[str, list[Record]], expected: dict[str, int]) -> list[str]:
    """Counts only: what makes the held-out attempt void (no score is in the message)."""
    problems = []
    for pool, records in pools.items():
        per: dict[str, int] = {}
        for r in records:
            per[str(r["persona"])] = per.get(str(r["persona"]), 0) + 1
        if len(per) != expected[pool]:
            problems.append(f"{pool}: {len(per)} units, expected {expected[pool]}")
        short = sum(n != ORIGINS for n in per.values())
        if short:
            problems.append(f"{pool}: {short} units without exactly {ORIGINS} origins")
        if any(r["futures"] != FUTURES for r in records):
            problems.append(f"{pool}: an origin without {FUTURES} futures")
        if any(r.get("logic") != FORECASTER_LOGIC for r in records):
            problems.append(f"{pool}: a record from another forecaster logic version")
        bad = 0
        for r in records:
            values = [x for k in NUMBER_LISTS for x in r[k]]
            values += [r["shortfall_probability"], r["median_lowest_cents"]]
            if r["walk"] is not None:
                values += [*r["crps_walk"], r["walk"]["shortfall_probability"]]
            bad += not all(math.isfinite(float(v)) for v in values)
        if bad:
            problems.append(f"{pool}: {bad} origins with a NaN or infinite score")
    return problems


# --- Readiness and the ledger ---


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def context(root: Path) -> Record:
    """What every ledger entry holds."""
    protocol = root / PROTOCOL
    return {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_commit": git(root, "rev-parse", "HEAD").stdout.strip(),
        "protocol_commit": git(root, "log", "-1", "--format=%H", "--", str(PROTOCOL))
        .stdout.strip(),
        "protocol_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest()
        if protocol.exists() else None,
        "threshold": PROBABILITY_THRESHOLD,
    }  # fmt: skip


def completed_in_history(root: Path) -> bool:
    """True if any version of the ledger, in any commit, holds a completed run."""
    log = git(root, "log", "--all", "-p", "--", str(LEDGER)).stdout
    return '"status": "completed"' in log


def refusals(root: Path) -> list[str]:
    """Why the command may not run now (empty: it may)."""
    out = []
    protocol = root / PROTOCOL
    if not protocol.exists() or not git(root, "ls-files", str(PROTOCOL)).stdout.strip():
        out.append("the protocol is not committed")
    else:
        if "DRAFT" in protocol.read_text(encoding="utf-8").splitlines()[0]:
            out.append("the protocol still says DRAFT")
        if git(root, "status", "--porcelain", "--", str(PROTOCOL)).stdout.strip():
            out.append("the protocol has changed since its commit")
    if git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        out.append("tracked files have uncommitted changes")
    ledger = root / LEDGER
    if ledger.exists() and (
        not git(root, "ls-files", str(LEDGER)).stdout.strip()
        or git(root, "status", "--porcelain", "--", str(LEDGER)).stdout.strip()
    ):  # fmt: skip
        out.append("the ledger is not committed: commit every attempt before the next")
    if completed_in_history(root):
        out.append("a completed run is in the ledger's history: the held-out data is used")
    if PROBABILITY_THRESHOLD != THRESHOLD:
        out.append(f"the alert threshold is {PROBABILITY_THRESHOLD}, not {THRESHOLD}")
    if LOGIC != FORECASTER_LOGIC:
        out.append(f"the forecaster's logic version is {LOGIC}, not {FORECASTER_LOGIC}")
    return out


def append(root: Path, entry: Record) -> None:
    path = root / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=float) + "\n")


# --- The result file ---


def _pct(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x:+.{digits}%}"


def _dips_cells(m: Record) -> dict[str, str]:
    eps = m["shortfall_episodes"]
    alerts = m["alert_episodes"]
    precision = "-" if m["precision"] is None else f"{m['precision']:.0%}"
    false30 = "-" if m["false_per_30_days"] is None else f"{m['false_per_30_days']:.3f}"
    return {
        "on": f"{m['caught_on']}/{eps}", "opened": f"{m['caught']}/{eps}",
        "precision": f"{precision} ({alerts - m['false_alerts']}/{alerts})",
        "false30": f"{false30} ({m['ordinary_origins']:,} ordinary origins)",
    }  # fmt: skip


DIPS_ROWS = (("on", "alert on in the 30 days before the dip (caught_on)"),
             ("opened", "alert opened at an ordinary origin before the dip (caught)"),
             ("precision", "precision (true/alert episodes)"),
             ("false30", "false alerts per 30 days"))  # fmt: skip


def pooled_rows(cols: list[Record]) -> list[str]:
    lines = [f"| {label} | " + " | ".join(fn(c["pooled"]) for c in cols) + " |"
             for label, fn in CORE_ROWS]  # fmt: skip
    for tag, who in (("f", "forecast"), ("r", "random walk")):
        cells = [_dips_cells(c["pooled"]["caught"][tag]) for c in cols]
        for key, label in DIPS_ROWS:
            lines.append(f"| dips, {who} at 35%: {label} | "
                         + " | ".join(x[key] for x in cells) + " |")  # fmt: skip
    return lines


UNIT_ROWS: tuple[tuple[str, str, Callable[[float], str]], ...] = (
    ("CRPS skill vs balance stays, mean of 30 days", "skill_n", _pct),
    ("CRPS skill vs random walk, mean of 30 days", "skill_r", _pct),
    ("CRPS skill vs balance stays, day 30", "skill30_n", _pct),
    ("CRPS skill vs random walk, day 30", "skill30_r", _pct),
    ("CRPS forecast, mean of 30 days", "crps_f", lambda x: f"{x / 100:,.0f}"),
    ("CRPS balance stays, mean of 30 days", "crps_n", lambda x: f"{x / 100:,.0f}"),
    ("CRPS random walk, mean of 30 days", "crps_r", lambda x: f"{x / 100:,.0f}"),
    ("80% band coverage, day 30", "coverage30", lambda x: f"{x:.1%}"),
    ("80% band coverage, all 30 days", "coverage_all", lambda x: f"{x:.1%}"),
    ("Brier, forecast", "brier_f", lambda x: f"{x:.4f}"),
    ("Brier, balance stays", "brier_n", lambda x: f"{x:.4f}"),
    ("Brier, random walk", "brier_r", lambda x: f"{x:.4f}"),
    (f"Brier skill vs stays (units with {MIN_DIPS}+ dips)", "brier_skill_n", _pct),
    (f"Brier skill vs walk (units with {MIN_DIPS}+ dips)", "brier_skill_r", _pct),
    ("lowest-balance bias, mean", "bias", lambda x: f"{x:+,.0f}"),
    ("lowest-balance bias, mean absolute", "abs_bias", lambda x: f"{x:,.0f}"),
    ("dips, forecast at 35%: recall, alert on (caught_on)", "recall_on_f", lambda x: f"{x:.0%}"),
    ("dips, forecast at 35%: recall, alert opened (caught)", "recall_f", lambda x: f"{x:.0%}"),
    ("dips, forecast at 35%: precision", "precision_f", lambda x: f"{x:.0%}"),
    ("dips, forecast at 35%: false alerts per 30 days", "false30_f", lambda x: f"{x:.3f}"),
    ("dips, random walk at 35%: recall, alert on (caught_on)", "recall_on_r", lambda x: f"{x:.0%}"),
    ("dips, random walk at 35%: recall, alert opened (caught)", "recall_r", lambda x: f"{x:.0%}"),
    ("dips, random walk at 35%: precision", "precision_r", lambda x: f"{x:.0%}"),
    ("dips, random walk at 35%: false alerts per 30 days", "false30_r", lambda x: f"{x:.3f}"),
)


def unit_rows(cols: list[Record]) -> list[str]:
    lines = [
        "| units (with a scored origin) | "
        + " | ".join(f"{c['per_unit']['units']} ({c['per_unit']['units_scored']})" for c in cols)
        + " |"
    ]
    for label, key, fmt in UNIT_ROWS:
        cells = []
        for c in cols:
            v = c["per_unit"][key]
            cells.append("-" if v["value"] is None else f"{fmt(v['value'])} ({v['units']} units)")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return lines


def table(names: list[str], rows: list[str]) -> list[str]:
    return ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names), *rows, ""]


def result_text(entry: Record) -> str:
    dev, held = entry["development"], entry["held_out"]
    main = [p for p in POOLS if p != "berka-sensitivity"]
    names = [f"{p} {kind}" for p in main for kind in ("development", "held-out")]
    cols = [x for p in main for x in (dev[p], held[p])]
    sens = [dev["berka-sensitivity"], held["berka-sensitivity"]]
    sens_names = ["berka-sensitivity development", "berka-sensitivity held-out"]
    zero_pools = ["berka-primary", "berka-sensitivity"]
    zero_names = [f"{p} {kind}, zero-estimate" for p in zero_pools for kind in ("dev.", "held-out")]
    zero_cols = [x for p in zero_pools for x in (dev[p], held[p])]
    zero_rows = [f"| {label} | " + " | ".join(
        fn(c["zero_estimate"]) if "n" in c["zero_estimate"] else "-" for c in zero_cols) + " |"
        for label, fn in CORE_ROWS]  # fmt: skip
    return "\n".join(
        [
            f"# Held-out test of the forecaster ({entry['time'][:10]})",
            "",
            f"Made with `python -m eval.held_out`, once, under the protocol "
            f"`{PROTOCOL.as_posix()}` (commit {entry['protocol_commit'][:10]}, sha256 "
            f"{entry['protocol_sha256'][:12]}), on code commit {entry['code_commit'][:10]}. Alert "
            f"threshold {entry['threshold']}. The dry run reproduced every compared row of "
            f"`{DEV_REPORT.as_posix()}`; its full output is the development column beside each "
            "held-out one. Scores use scored origins (not already below the line, random walk "
            "defined); dips caught use every origin with a defined walk. Synthetic amounts are "
            "dollars; Berka amounts are scaled units (1 unit = 20 koruna). Units in the pooled "
            "tables: personas or accounts; per unit: archetypes (synthetic) or accounts (Berka). "
            "Neighbouring weekly origins share outcomes: the "
            "effective n is smaller than the n shown.",
            "",
            "## Pooled over scored origins",
            "",
            *table(names, pooled_rows(cols)),
            "## Averaged per unit (equal weight; units included in brackets)",
            "",
            *table(names, unit_rows(cols)),
            "## Berka sensitivity pool (secondary; decides nothing, never pooled with primary)",
            "",
            *table(sens_names, pooled_rows(sens)),
            *table(sens_names, unit_rows(sens)),
            "## Berka zero-estimate origins (reported apart, pooled)",
            "",
            *table(zero_names, zero_rows),
        ]
    )


# --- The command ---


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.held_out",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--berka", type=Path, help="the Berka raw files folder")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--rebuild", action="store_true",
                        help="write the result file again from the ledger's completed entry; "
                             "opens no data")  # fmt: skip
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.rebuild:
        return rebuild(root)
    if args.berka is None:
        parser.error("--berka is required")
    ctx = context(root)
    reasons = refusals(root)
    if reasons:
        append(root, {**ctx, "status": "refused", "reasons": reasons})
        print("refused:\n  " + "\n  ".join(reasons))
        return 2
    folder = root / OUT / ctx["time"].replace(":", "")
    recorded = False
    try:
        print("dry run on the development pools", flush=True)
        dev_records = compute(False, args.berka, args.jobs)
        save(folder / "development", dev_records)
        dev_core = {p: summary_core(r) for p, r in dev_records.items()}
        diffs = dry_run_differences(dev_core, (root / DEV_REPORT).read_text(encoding="utf-8"))
        if diffs:
            append(root, {**ctx, "status": "stopped: the dry run differs", "differences": diffs})
            recorded = True
            print("STOPPED: the dry run does not reproduce the development report. Nothing held "
                  "out was opened.\n  " + "\n  ".join(diffs))  # fmt: skip
            return 3
        print("dry run: every compared row reproduced; now the held-out pools", flush=True)
        dev = {p: results(p, r) for p, r in dev_records.items()}
        held_records = compute(True, args.berka, args.jobs)
        save(folder / "held-out", held_records)
        expected = {p: len(pool_ids(p, True)) for p in POOLS}
        problems = void_problems(held_records, expected)
        if problems:
            append(root, {**ctx, "status": "void", "problems": problems})
            recorded = True
            print("VOID (no number shown):\n  " + "\n  ".join(problems))
            return 4
        held = {p: results(p, r) for p, r in held_records.items()}
        entry = {**ctx, "status": "completed", "development": dev, "held_out": held}
        append(root, entry)
        recorded = True
    except BaseException as e:
        if not recorded:
            append(root, {**ctx, "status": "crashed", "error": f"{type(e).__name__}: {e}"[:300]})
        raise
    out = write_result(root, json.loads(json.dumps(entry, default=float)))
    print(out.read_text(encoding="utf-8"))
    print(f"written: {LEDGER} and {out.relative_to(root)}; commit both before anything else")
    return 0


def write_result(root: Path, entry: Record) -> Path:
    """The result file, made from the ledger entry alone (named by the entry's date)."""
    out = root / f"docs/results/held_out_{entry['time'][:10]}.md"
    out.write_text(result_text(entry) + "\n", encoding="utf-8")
    return out


def completed_entry(root: Path) -> Record:
    """The completed entry of the ledger (there is at most one: a second run is refused)."""
    lines = (root / LEDGER).read_text(encoding="utf-8").splitlines()
    done = [e for e in map(json.loads, lines) if e.get("status") == "completed"]
    if len(done) != 1:
        raise SystemExit(f"the ledger holds {len(done)} completed entries, not 1")
    return done[0]


def rebuild(root: Path) -> int:
    """Writes the result file again from the ledger alone: opens no data, runs nothing."""
    out = write_result(root, completed_entry(root))
    print(f"rebuilt from {LEDGER}: {out.relative_to(root)}")
    return 0


def save(folder: Path, pools: dict[str, list[Record]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for pool, records in pools.items():
        (folder / f"{pool}.jsonl").write_text(
            "".join(json.dumps(r, default=float) + "\n" for r in records), encoding="utf-8"
        )


if __name__ == "__main__":
    sys.exit(main())
