"""The futures store: the default forecaster's simulated futures, saved once, reused later.

Experiments 4 and 5 read these instead of simulating again. For every development persona (the
12-month and the 24-month set) and every Berka primary development account, one file holds, for
each of the 40 origins: every future's daily balance (500 futures x 30 days, **cents, int32**:
exact, no rounding), the balance at the origin, the buffer, the 30 realized balances, the origin
date and two flags. Nothing derived is stored; everything else (the band, the lowest balances,
the dip chance, the CRPS of any combination) is computed from the paths.

    python -m eval.futures_store trial            # one Berka account, to measure size and time
    python -m eval.futures_store run --set 12 --jobs 2
    python -m eval.futures_store run --set long --jobs 2
    python -m eval.futures_store run --set berka --jobs 2
    python -m eval.futures_store check            # the default recomputed from the saved paths
                                                  # against the experiment 3 records

A file is written only when all 40 origins of its account are done (to a temporary name, then
renamed), so a stopped run never leaves a half-written file and resumes at the next account.
Held-out personas and held-out Berka accounts are refused.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from eval.anchor import BERKA_OUT, _berka_frames
from eval.anchor import OUT as ANCHOR_OUT
from eval.backtest import FUTURES, origins, run_origin_full
from eval.baselines import crps_samples
from eval.berka import DATA, build_db, dip_line_cents, origin_dates
from eval.berka_split import assert_development_only as berka_development_only
from eval.personas_db import persona_db
from lookahead.generator.archetypes import assert_development_only, development_ids

SYNTHETIC_OUT = Path("data/backtest/futures")
BERKA_STORE = DATA / "runs" / "futures"
DAYS = 30


# --- Saving ---


def pack(
    records: list[dict[str, Any]], paths: list[np.ndarray], extra: dict[str, Any]
) -> dict[str, Any]:
    """The arrays of one account from its per-origin records and paths."""
    return {
        "paths": np.stack(paths).astype(np.int32),  # (origins, futures, days), cents
        "start_cents": np.array([r["start_balance_cents"] for r in records], dtype=np.int64),
        "buffer_cents": np.array([r["buffer_cents"] for r in records], dtype=np.int64),
        "realized": np.array([r["realized"] for r in records], dtype=np.int32),
        "origin": np.array([r["origin"] for r in records]),
        "in_shortfall": np.array([r["in_shortfall_at_origin"] for r in records], dtype=bool),
        **extra,
    }


def write_atomic(path: Path, arrays: dict[str, Any]) -> None:
    """Write a compressed file under a temporary name, then rename it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    tmp.replace(path)


def save_persona(pid: str, folder: Path, futures: int = FUTURES) -> int:
    assert_development_only([pid])
    path = folder / f"{pid}.npz"
    if path.exists():
        return 0
    records, paths = [], []
    with persona_db(pid, folder / "work", 42) as db:
        for origin in origins():
            rec, p, _ = run_origin_full(
                db.conn, pid, "synthetic", origin, futures, db.persona.archetype, False,
                keep_paths=True,
            )  # fmt: skip
            records.append(rec)
            paths.append(np.asarray(p, dtype=np.int64))
        archetype = db.persona.archetype
    write_atomic(path, pack(records, paths, {"archetype": np.array(archetype),
                                             "account": np.array(pid)}))  # fmt: skip
    return len(records)


def save_account(args: tuple[int, pd.DataFrame, Path, int]) -> int:
    account, frame, folder, futures = args
    berka_development_only([account])
    path = folder / f"{account}.npz"
    if path.exists():
        return 0
    days = origin_dates(frame)
    db = folder / "work" / f"{account}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = build_db(frame, account, db, dip_line_cents(frame, days[0]))
    records, paths, zero = [], [], []
    try:
        for origin in days:
            rec, p, f = run_origin_full(
                conn, str(account), "synthetic", origin, futures, "berka", False, keep_paths=True,
            )  # fmt: skip
            records.append(rec)
            paths.append(np.asarray(p, dtype=np.int64))
            zero.append(sum(d.spending_cents for d in f.days) == 0)
    finally:
        conn.close()
        db.unlink(missing_ok=True)
    extra = {
        "archetype": np.array("berka"),
        "account": np.array(str(account)),
        "zero_estimate": np.array(zero, dtype=bool),
    }
    write_atomic(path, pack(records, paths, extra))
    return len(records)


def _persona_job(args: tuple[str, Path, int]) -> str:
    pid, folder, futures = args
    return f"{pid}: {save_persona(pid, folder, futures)} origins"


def load(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as data:
        return {k: data[k] for k in data.files}


# --- Recomputing the default from the saved paths (condition 1 of experiment 4) ---


def nearest_rank(values: np.ndarray, share: float, axis: int = 0) -> np.ndarray:
    """The simulator's percentile: the value at rank round(share x (n - 1)) of the sorted values."""
    ordered = np.sort(values, axis=axis)
    return np.take(ordered, round(share * (values.shape[axis] - 1)), axis=axis)


def recompute(paths: np.ndarray, buffer_cents: int, realized: np.ndarray) -> dict[str, Any]:
    """The default forecast's saved numbers, from one origin's paths (futures x days)."""
    lows = paths.min(axis=1)
    return {
        "p10": nearest_rank(paths, 0.1).tolist(),
        "p50": nearest_rank(paths, 0.5).tolist(),
        "p90": nearest_rank(paths, 0.9).tolist(),
        "shortfall_probability": float((lows < buffer_cents).mean()),
        "median_lowest_cents": int(nearest_rank(lows, 0.5)),
        "crps_default": crps_samples(paths.astype(float), realized.astype(float))
        .round(1).tolist(),
    }  # fmt: skip


def check_file(path: Path, records: list[dict[str, Any]]) -> tuple[int, int]:
    """(origins checked, origins equal) of one store file against the experiment 3 records."""
    data = load(path)
    by_origin = {r["origin"]: r for r in records}
    checked = equal = 0
    for i, origin in enumerate(data["origin"].tolist()):
        rec = by_origin[origin]
        mine = recompute(data["paths"][i], int(data["buffer_cents"][i]), data["realized"][i])
        checked += 1
        equal += all(mine[k] == rec[k] for k in mine)
    return checked, equal


def read_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]


def check() -> int:
    """Condition 1 of the rule: 800 of 800 per synthetic set and 8,000 of 8,000 on Berka."""
    ok = True
    for name in ("12", "long"):
        checked = equal = 0
        for path in sorted((SYNTHETIC_OUT / name).glob("*.npz")):
            c, e = check_file(path, read_records(ANCHOR_OUT / name / f"{path.stem}.jsonl"))
            checked += c
            equal += e
        print(f"{name}-month set: {equal} of {checked} origins equal the experiment 3 records")
        ok &= checked > 0 and equal == checked
    checked = equal = 0
    for path in sorted(BERKA_STORE.glob("*.npz")):
        c, e = check_file(path, read_records(BERKA_OUT / f"{path.stem}.jsonl"))
        checked += c
        equal += e
    print(f"Berka primary: {equal} of {checked} origins equal the experiment 3 records")
    ok &= checked > 0 and equal == checked
    return 0 if ok else 1


# --- Commands ---


def trial() -> int:
    """One Berka account saved for real, to measure the size and the time."""
    ids, frames = _berka_frames()
    account = ids[0]
    folder = BERKA_STORE.parent / "futures-trial"
    path = folder / f"{account}.npz"
    path.unlink(missing_ok=True)
    start = time.time()
    n = save_account((account, frames[account], folder, FUTURES))
    seconds = time.time() - start
    raw = np.load(path)["paths"].nbytes
    size = path.stat().st_size
    print(
        f"account saved: {n} origins in {seconds:.0f} s ({seconds / n:.2f} s per origin, one "
        f"process); file {size / 1024:.0f} KB compressed ({size / n / 1024:.0f} KB per origin), "
        f"{raw / 1024 / 1024:.1f} MB raw"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.futures_store", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("command", choices=["trial", "run", "check"])
    parser.add_argument("--set", choices=["12", "long", "berka"], default="12")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    if args.command == "trial":
        return trial()
    if args.command == "check":
        return check()
    if args.set == "berka":
        ids, frames = _berka_frames()
        jobs_b = [(a, frames[a], BERKA_STORE, FUTURES) for a in ids]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for n, _ in enumerate(pool.map(save_account, jobs_b), 1):
                if n % 10 == 0:
                    print(f"[{n}/{len(ids)}] accounts", flush=True)
        return 0
    long = args.set == "long"
    folder = SYNTHETIC_OUT / ("long" if long else "12")
    jobs = [(pid, folder, FUTURES) for pid in development_ids(long=long)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for n, line in enumerate(pool.map(_persona_job, jobs), 1):
            print(f"[{n}/{len(jobs)}] {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
