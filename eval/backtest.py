"""Backtest runner: a forecast at every weekly origin, next to what happened.

Design and persona mix are fixed in DECISIONS.md (2026-10-08). For each persona, one at a time:
the 12 months are imported into a throw-away database, a forecast is built "as of" each Monday
origin (rule zero: only rows posted on or before that day, recurring series re-detected from
them, decisions replayed as of that day), and the balances that really followed are read from the
later rows. One JSON line per origin is written as soon as it is made, so a stopped run resumes,
and the throw-away database is deleted before the next persona starts.

    python -m eval.backtest --quick                 # 3 personas, 4 origins, 100 futures
    python -m eval.backtest                         # the 20 development personas, all origins
    python -m eval.backtest --only v2-SAL-04 --held-out   # a held-out persona: say so
    python -m eval.backtest --summary               # counts of what is saved (no amounts)

Held-out personas run only with --held-out, and only for the final test. This module never reads
data/private; the real-data script (eval/backtest_real.py) is run by the account holder.
"""

import argparse
import functools
import gc
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from lookahead.db import connect
from lookahead.forecast.path import build_forecast
from lookahead.forecast.start import forecast_start
from lookahead.generator.archetypes import (
    END,
    HELD_OUT,
    PERSONA_IDS,
    QUICK_IDS,
    START,
    build_persona,
    development_ids,
)
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

FIRST_ORIGIN = date(2025, 12, 1)  # eight weeks of history behind the first origin
LAST_ORIGIN = date(2026, 8, 31)  # thirty observed days after the last one
HORIZON = 30
FUTURES = 500
QUICK_FUTURES = 100
METHOD = "weekday"
DEFAULT_OUT = Path("data/backtest")


def origins(first: date | None = None, last: date | None = None) -> list[date]:
    """Every Monday from `first` to `last` (default: the recorded 39 origins)."""
    day = first or FIRST_ORIGIN
    day += timedelta(days=(7 - day.weekday()) % 7)  # the first Monday on or after it
    out = []
    while day <= (last or LAST_ORIGIN):
        out.append(day)
        day += timedelta(days=7)
    return out


def quick_origins() -> list[date]:
    """Four origins spread over the year, for --quick."""
    return origins()[::10]


def select_personas(
    only: list[str] | None, include_held_out: bool, quick: bool = False
) -> list[str]:  # fmt: skip
    """Which personas to run. Held-out ones only when asked for, in the final test."""
    if quick:
        return list(QUICK_IDS)
    chosen = list(only) if only else (list(PERSONA_IDS) if include_held_out else development_ids())
    held = sorted(set(chosen) & HELD_OUT)
    if held and not include_held_out:
        raise ValueError(f"held-out personas run only with --held-out: {', '.join(held)}")
    return chosen


def realized_balances(
    conn: sqlite3.Connection, account: str, origin: date, horizon: int = HORIZON
) -> list[int]:
    """The closing balance of each of the `horizon` days after `origin`, from the stored rows.

    A bank export lists a day's postings newest first, so the first row stored for a day is its
    last posting and carries that day's closing balance. A day with no postings keeps the
    balance before it (the origin's balance for the first days)."""
    start = forecast_start(conn, account, origin)
    if start is None:
        raise ValueError("no rows by the origin")
    last = origin + timedelta(days=horizon)
    closing: dict[date, int] = {}
    rows = conn.execute(
        "SELECT posted_date, balance_after_cents FROM transactions WHERE account = ?"
        " AND posted_date > ? AND posted_date <= ? ORDER BY posted_date, id",
        (account, origin.isoformat(), last.isoformat()),
    )
    for day, balance in rows:
        closing.setdefault(date.fromisoformat(day), int(balance))
    out, carried = [], start.balance_cents
    for k in range(1, horizon + 1):
        carried = closing.get(origin + timedelta(days=k), carried)
        out.append(carried)
    return out


@functools.cache
def code_version() -> str:
    """The commit the results came from (informational), or "unknown"."""
    try:
        done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10, check=True)  # fmt: skip
        return done.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run_origin(
    conn: sqlite3.Connection, persona_id: str, dataset: str, origin: date, futures: int,
    archetype: str | None = None, held_out: bool | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The forecast made as of `origin` and what really happened afterwards, as one record.

    Everything under "forecast" keys is built from rows posted on or before `origin`; the
    realized balances are read afterwards, from later rows, and never go back into the forecast.
    `dataset` is "synthetic" or "real": the two are never mixed in a report."""
    if dataset == "synthetic" and archetype is None:
        persona = build_persona(persona_id)
        archetype, held_out = persona.archetype, persona.held_out
    forecast = build_forecast(conn, persona_id, METHOD, origin, None, HORIZON, futures)
    if forecast is None or forecast.simulation is None:
        raise ValueError(f"no forecast for {persona_id} as of {origin}")
    sim = forecast.simulation
    buffer = forecast.buffer_cents
    realized = realized_balances(conn, persona_id, origin)
    below = [d for d, b in zip(sim.days, realized, strict=True) if b < buffer]
    return {
        "dataset": dataset, "persona": persona_id, "archetype": archetype, "held_out": held_out,
        "origin": origin.isoformat(), "data_date": forecast.start.data_last_updated.isoformat(),
        "futures": futures, "buffer_cents": buffer, "version": code_version(),
        "start_balance_cents": forecast.start.balance_cents,
        "days": [d.isoformat() for d in sim.days],
        "p10": list(sim.p10), "p50": list(sim.p50), "p90": list(sim.p90),
        "point": [d.balance_cents for d in forecast.days],
        "lowest_cents": list(sim.lowest_cents),
        "shortfall_probability": sim.shortfall_probability,
        "median_lowest_cents": sim.median_lowest_cents,
        "n_items": len(forecast.items), "has_income": forecast.has_income,
        "weeks_drawn_from": sim.weeks_drawn_from,
        "realized": realized, "realized_lowest_cents": min(realized),
        "realized_first_below_buffer": below[0].isoformat() if below else None,
        "realized_below_zero": min(realized) < 0,
    }  # fmt: skip


def _saved_origins(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["origin"] for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()}  # fmt: skip


def _check_settings(meta_path: Path, settings: dict[str, Any]) -> None:
    """Results made with other settings must not be mixed with new ones."""
    if meta_path.exists():
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        if saved != settings:
            raise ValueError(f"{meta_path.name} was made with different settings: {saved}")
    else:
        meta_path.write_text(json.dumps(settings), encoding="utf-8")


def run_persona(
    persona_id: str, out_dir: Path, days: list[date], futures: int, seed: int, work_dir: Path,
    allow_held_out: bool = False,
) -> int:  # fmt: skip
    """Run the origins `days` for one persona, saving each as it is made. Returns how many were
    newly computed (those already saved are skipped, so a stopped run resumes)."""
    if persona_id in HELD_OUT and not allow_held_out:
        raise ValueError(f"held-out personas run only with --held-out: {persona_id}")
    persona = build_persona(persona_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"{persona_id}.jsonl"
    _check_settings(out_dir / f"{persona_id}.meta.json",
                    {"futures": futures, "seed": seed, "method": METHOD})  # fmt: skip
    todo = [d for d in days if d.isoformat() not in _saved_origins(result_path)]
    if not todo:
        return 0
    folder = work_dir / persona_id
    folder.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        export, _ = write_persona(persona.persona, folder, seed, START, END)
        conn = connect(folder / "run.db")
        store_exports(
            conn, persona_id, [(export.name, parse_export(export))], datetime(2026, 10, 1)
        )
        set_safety_buffer_cents(conn, persona.buffer_cents)  # fixed for the whole run
        with result_path.open("a", encoding="utf-8") as out:
            for origin in todo:
                record = run_origin(conn, persona_id, "synthetic", origin, futures,
                                    persona.archetype, persona.held_out)  # fmt: skip
                out.write(json.dumps(record) + "\n")
                out.flush()
    finally:
        if conn is not None:
            conn.close()
        gc.collect()
        shutil.rmtree(folder, ignore_errors=True)  # one persona at a time, nothing left behind
    return len(todo)


def read_records(out_dir: Path) -> list[dict[str, Any]]:
    """Every saved record under `out_dir`."""
    records = []
    for path in sorted(out_dir.glob("*.jsonl")):
        records += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]  # fmt: skip
    return records


def print_summary(out_dir: Path) -> None:
    """Counts of what is saved. No amounts, no rows."""
    records = read_records(out_dir)
    personas = {r["persona"] for r in records}
    by_archetype = Counter(r["archetype"] for r in records)
    held = {r["persona"] for r in records if r["held_out"]}
    people = f"{len(personas)} persona{'s' if len(personas) != 1 else ''}"
    saved = f"{len(records)} origin{'s' if len(records) != 1 else ''}"
    print(f"{people}, {saved} saved in {out_dir}")
    if held:
        print(f"  {len(held)} held-out persona(s) among them")
    for code, n in sorted(by_archetype.items()):
        print(f"  {code}: {n} origins")
    bad = sum(r["realized_below_zero"] for r in records)
    below = sum(r["realized_first_below_buffer"] is not None for r in records)
    print(f"  origins followed by a balance below the buffer: {below}; below zero: {bad}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.backtest", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--quick", action="store_true", help="3 personas, 4 origins, 100 futures")
    parser.add_argument("--only", nargs="*", default=None, help="persona ids")
    parser.add_argument("--held-out", action="store_true",
                        help="include held-out personas (final test only)")  # fmt: skip
    parser.add_argument("--futures", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42, help="seed of the generated histories")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--work", type=Path, default=None, help="where the throw-away databases go")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    out = args.out or DEFAULT_OUT / ("quick" if args.quick else "full")
    if args.summary:
        print_summary(out)
        return 0
    futures = args.futures or (QUICK_FUTURES if args.quick else FUTURES)
    days = quick_origins() if args.quick else origins()
    work = args.work or out / "work"
    ids = select_personas(args.only, args.held_out, args.quick)
    for n, pid in enumerate(ids, start=1):
        done = run_persona(pid, out, days, futures, args.seed, work, allow_held_out=args.held_out)
        print(f"[{n}/{len(ids)}] {pid}: {done} new origins", flush=True)
    print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
