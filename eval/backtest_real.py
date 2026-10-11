"""The backtest on the account holder's real data. Run by the account holder only.

    python -m eval.backtest_real --quick        # 4 origins per account, 100 futures
    python -m eval.backtest_real                # every weekly origin that fits, 500 futures
    python -m eval.backtest_real --paths        # the same, also keeping every future's daily
                                                # balance (for eval.calibrate apply)
    python -m eval.backtest_real --arm 1        # an arm of the 2026-10-11 plan (1: random lump
                                                # arrivals; 2: every lump proposal confirmed),
                                                # saved to data/private/backtest/arms/arm<n>

The quick run saves to data/private/backtest/quick and the full run to data/private/backtest/full,
so the two never meet (their settings differ). A run with --paths saves to
data/private/backtest/paths: records and paths of an account are written together once all its
origins are done, and an account already saved there is skipped.

It refuses a database or an output folder outside data/private, before anything is created.
It opens the database read-only (the file is not changed), makes the same forecasts as the
synthetic runner at every Monday that has eight weeks of history before it and thirty observed
days after it, and saves one record per origin there (private, never committed). Files and
records are named by a short salted hash of the account (`account-<10 hex digits>`; the salt is a
random value kept in data/private), never by the account's name or by its position in the list,
so adding an account never attaches old results to another one; files named by position by an
older version are moved to `older/`. It prints counts only: how many accounts and origins, and
how many origins were followed by a balance below the buffer or below zero. It never prints a
row, an amount, a date of a transaction or an account name. Real results are a separate column
from synthetic ones in every report; one person's history is a case study.
"""

import argparse
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import (
    FUTURES,
    KEEPS_NAMES,
    QUICK_FUTURES,
    _check_settings,
    _saved_origins,
    archive_older,
    keeps_name,
    read_records,
    run_origin,
    run_origin_with_paths,
    split_by_shortfall,
    version_settings,
)
from lookahead.db import connect_readonly
from lookahead.forecast.path import DEFAULT_OPTIONS, ForecastOptions
from lookahead.forecast.spending import DEFAULT_KEEPS, KeepsComing
from lookahead.merchants import is_private_path
from lookahead.pseudonymize import pseudonym_key
from lookahead.ui import private_db_path
from lookahead.views import accounts

DEFAULT_OUT = Path("data/private/backtest")
SALT_FILE = "backtest_account_salt.txt"  # kept in the data/private folder, made on first use
NUMBERED = re.compile(r"account-\d+\.(jsonl|meta\.json|paths\.npz)")
HISTORY = timedelta(weeks=8)
HORIZON_DAYS = 30


def real_origins(first_date: date, last_date: date) -> list[date]:
    """Weekly Mondays with eight weeks of history behind them and thirty observed days after."""
    day = first_date + HISTORY
    day += timedelta(days=(7 - day.weekday()) % 7)
    out = []
    while day <= last_date - timedelta(days=HORIZON_DAYS):
        out.append(day)
        day += timedelta(days=7)
    return out


def real_arm_options(arm: int) -> ForecastOptions:
    """The forecaster options of an arm on real data: 0 the current forecaster, 1 random lump
    arrivals, 2 every lump proposal confirmed with random arrivals for the rest (senders keyed by
    the account holder's own pseudonym key). Arm 3 needs the generator's answer key, and b)
    stopped at its gate, so those are not offered."""
    if arm == 0:
        return DEFAULT_OPTIONS
    if arm == 1:
        return ForecastOptions(lump_arrivals=True)
    if arm == 2:
        return ForecastOptions(lump_arrivals=True, lump_proposals=True, confirm_all_lumps=True,
                               proposal_key=pseudonym_key())  # fmt: skip
    raise ValueError("on real data only arms 0, 1 and 2 can be run")


def check_private(db_path: Path, out_dir: Path) -> None:
    """Real data and the records made from it live only in data/private (DECISIONS.md, privacy
    rules): refuse a database or an output folder anywhere else, before anything is created."""
    if not is_private_path(db_path):
        raise ValueError("refusing a database outside data/private/")
    if not is_private_path(out_dir):
        raise ValueError("refusing to write real-data results outside data/private/")


def _private_root(path: Path) -> Path:
    """The data/private folder that `path` lies in."""
    for folder in [path.resolve(), *path.resolve().parents]:
        if folder.name.casefold() == "private" and folder.parent.name.casefold() == "data":
            return folder
    raise ValueError("not inside data/private/")


def account_key(account: str, out_dir: Path) -> str:
    """A short name for an account's result files and records: a salted hash of the account,
    stable when other accounts are added or removed (a list position is not). The salt is a
    random value kept in the data/private folder, made on first use, so the key cannot be
    recomputed from a list of account names elsewhere."""
    salt_path = _private_root(out_dir) / SALT_FILE
    if not salt_path.exists():
        salt_path.write_text(secrets.token_hex(16), encoding="utf-8")
    salt = salt_path.read_text(encoding="utf-8").strip()
    return "account-" + hashlib.sha256(f"{salt}:{account}".encode()).hexdigest()[:10]


def archive_numbered(out_dir: Path) -> bool:
    """Results named by list position (account-1, account-2, before the stable keys) are moved
    into `out_dir/older/<n>` (nothing is deleted), so they are never read as another account's.
    Returns True if anything was moved."""
    old = [p for p in out_dir.glob("account-*") if NUMBERED.fullmatch(p.name)]
    if not old:
        return False
    n = 1
    while (out_dir / "older" / str(n)).exists():
        n += 1
    target = out_dir / "older" / str(n)
    target.mkdir(parents=True)
    for path in old:
        shutil.move(str(path), str(target / path.name))
    return True


def run_real(
    db_path: Path, out_dir: Path, futures: int, quick: bool = False, paths: bool = False,
    arm: int = 0, keeps: KeepsComing | None = DEFAULT_KEEPS,
) -> dict[str, int]:  # fmt: skip
    """Run every account of the database. Returns counts (accounts, new origins, saved). The
    database and `out_dir` must both be inside data/private."""
    check_private(db_path, out_dir)
    # Older results, and files named by list position, are set aside, never mixed in.
    older = archive_older(out_dir)
    numbered = out_dir.exists() and archive_numbered(out_dir)
    archived = 1 if older or numbered else 0
    conn = connect_readonly(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    new = 0
    names = accounts(conn)
    options = real_arm_options(arm)
    try:
        for account in names:
            name = account_key(account, out_dir)  # the account's name stays out of the files
            first, last = conn.execute(
                "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
                (account,),
            ).fetchone()
            days = real_origins(date.fromisoformat(first), date.fromisoformat(last))
            if quick:
                days = days[:: max(1, len(days) // 4)][:4]
            if not days:
                continue  # too short a history: no origins, so no files at all
            path = out_dir / f"{name}.jsonl"
            settings: dict[str, Any] = {"futures": futures, "method": "weekday",
                                        **version_settings()}  # fmt: skip
            if arm:
                settings["arm"] = arm  # an arm never shares a folder with another
            if keeps:
                settings["keeps_coming"] = keeps_name(keeps)
            _check_settings(out_dir / f"{name}.meta.json", settings)
            if paths:
                new += _run_with_paths(conn, account, name, days, futures, out_dir, keeps, options)
                continue
            todo = [d for d in days if d.isoformat() not in _saved_origins(path)]
            if not todo:
                continue  # nothing new: no empty file is made
            with path.open("a", encoding="utf-8") as out:
                for origin in todo:
                    record = run_origin(conn, account, "real", origin, futures, None, None,
                                        options, keeps)  # fmt: skip
                    record["persona"] = name  # the name stays out of the files
                    out.write(json.dumps(record) + "\n")
                    out.flush()
                    new += 1
    finally:
        conn.close()
    return {
        "accounts": len(names),
        "new_origins": new,
        "origins_saved": len(read_records(out_dir)),
        "archived_older": archived,
    }


def _run_with_paths(
    conn: sqlite3.Connection, account: str, name: str, days: list[date], futures: int,
    out_dir: Path, keeps: KeepsComing | None = DEFAULT_KEEPS,
    options: ForecastOptions = DEFAULT_OPTIONS,
) -> int:  # fmt: skip
    """All origins of one account, then its records and paths written together. Skipped if both
    are there already."""
    path = out_dir / f"{name}.jsonl"
    npz = out_dir / f"{name}.paths.npz"
    if path.exists() and npz.exists():
        return 0
    records, kept = [], []
    for origin in days:
        record, p = run_origin_with_paths(conn, account, "real", origin, futures, None, None,
                                          keeps=keeps, options=options)  # fmt: skip
        record["persona"] = name  # the name stays out of the files
        records.append(record)
        kept.append(p)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    np.savez_compressed(npz, origins=np.array([r["origin"] for r in records]),
                        paths=np.array(kept, dtype=np.int64))  # fmt: skip
    return len(records)


def print_counts(counts: dict[str, Any], out_dir: Path) -> None:
    """Counts only."""
    records = read_records(out_dir)
    ordinary, already = split_by_shortfall(records)
    below = sum(r["realized_first_below_buffer"] is not None for r in ordinary)
    zero = sum(r["realized_below_zero"] for r in ordinary)
    n = counts["accounts"]
    print(
        f"{n} account{'s' if n != 1 else ''}, {counts['origins_saved']} origins saved "
        f"({counts['new_origins']} new this run)"
    )
    print(f"{len(already)} origins were already below the buffer (reported separately)")
    print(f"of the other {len(ordinary)}: followed by a balance below the buffer {below}, "
          f"below zero {zero}")  # fmt: skip
    if any(r.get("keeps_coming") for r in records):  # counts of the arrivals the forecast drew
        with_in = sum(r["arrivals_in"] > 0 for r in records)
        with_out = sum(r["arrivals_out"] > 0 for r in records)
        print(
            f"keeps-coming switch on ({records[0]['keeps_coming']}): origins with deposit "
            f"arrivals {with_in}, with payment arrivals {with_out}, of {len(records)}"
        )
    print(f"saved in {out_dir} (private: never commit)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.backtest_real", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None,
                        help=f"default {DEFAULT_OUT}/quick or {DEFAULT_OUT}/full")  # fmt: skip
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--futures", type=int, default=None)
    parser.add_argument("--paths", action="store_true",
                        help="keep every future's daily balance (saved to .../paths)")  # fmt: skip
    parser.add_argument("--arm", type=int, default=0, choices=(0, 1, 2),
                        help="an arm of the 2026-10-11 plan (.../arms/arm<n>)")  # fmt: skip
    parser.add_argument("--keeps-coming", choices=sorted(KEEPS_NAMES), default="with-own",
                        help="big money that keeps coming forecast as random arrivals (the "
                             "default, with transfers from my own accounts counted), without "
                             "them, or 'off' for the old forecaster. Each has its own "
                             "folder.")  # fmt: skip
    parser.add_argument("--metrics", action="store_true",
                        help="then print the metrics summary of the real results")  # fmt: skip
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    futures = args.futures or (QUICK_FUTURES if args.quick else FUTURES)
    if args.arm and args.paths:
        print("--arm and --paths are not combined.")
        return 1
    folder = "paths" if args.paths else "quick" if args.quick else "full"
    keeps = KEEPS_NAMES[args.keeps_coming]
    root = DEFAULT_OUT / "arms" / f"arm{args.arm}" if args.arm else DEFAULT_OUT
    base = root / f"keeps-coming-{args.keeps_coming}" if keeps else root
    out = args.out or base / folder  # never the same folder
    try:
        check_private(db_path, out)
    except ValueError as error:
        print(f"{str(error).capitalize()}. Nothing was written.")
        return 1
    counts = run_real(db_path, out, futures, args.quick, args.paths, args.arm, keeps)
    if counts["archived_older"]:
        print(f"Saved results were from an older version of the backtest: moved them to "
              f"{out / 'older'} and starting fresh.")  # fmt: skip
    print_counts(counts, out)
    if args.metrics:
        from eval.metrics import load_records, report_text

        print()
        print(report_text([], load_records(out)))  # summary numbers only, never rows
    return 0


if __name__ == "__main__":
    sys.exit(main())
