"""The backtest on the account holder's real data. Run by the account holder only.

    python -m eval.backtest_real --quick        # 4 origins per account, 100 futures
    python -m eval.backtest_real                # every weekly origin that fits, 500 futures
    python -m eval.backtest_real --paths        # the same, also keeping every future's daily
                                                # balance (for eval.calibrate apply)

The quick run saves to data/private/backtest/quick and the full run to data/private/backtest/full,
so the two never meet (their settings differ). A run with --paths saves to
data/private/backtest/paths: records and paths of an account are written together once all its
origins are done, and an account already saved there is skipped.

It opens the database read-only (the file is not changed), makes the same forecasts as the
synthetic runner at every Monday that has eight weeks of history before it and thirty observed
days after it, and saves one record per origin there (private, never committed, files named by
number and not by account). It prints counts only: how many accounts
and origins, and how many origins were followed by a balance below the buffer or below zero. It
never prints a row, an amount, a date of a transaction or an account name. Real results are a
separate column from synthetic ones in every report; one person's history is a case study.
"""

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from eval.backtest import (
    FUTURES,
    QUICK_FUTURES,
    _check_settings,
    _saved_origins,
    archive_older,
    read_records,
    run_origin,
    run_origin_with_paths,
    split_by_shortfall,
    version_settings,
)
from lookahead.db import connect_readonly
from lookahead.ui import private_db_path
from lookahead.views import accounts

DEFAULT_OUT = Path("data/private/backtest")
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


def run_real(
    db_path: Path, out_dir: Path, futures: int, quick: bool = False, paths: bool = False
) -> dict[str, int]:  # fmt: skip
    """Run every account of the database. Returns counts (accounts, new origins, saved)."""
    archived = 1 if archive_older(out_dir) else 0  # older results are set aside, never mixed in
    conn = connect_readonly(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    new = 0
    names = accounts(conn)
    try:
        for number, account in enumerate(names, start=1):
            first, last = conn.execute(
                "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
                (account,),
            ).fetchone()
            days = real_origins(date.fromisoformat(first), date.fromisoformat(last))
            if quick:
                days = days[:: max(1, len(days) // 4)][:4]
            path = out_dir / f"account-{number}.jsonl"
            _check_settings(
                out_dir / f"account-{number}.meta.json",
                {"futures": futures, "method": "weekday", **version_settings()},
            )
            if paths:
                new += _run_with_paths(conn, account, number, days, futures, out_dir)
                continue
            with path.open("a", encoding="utf-8") as out:
                for origin in [d for d in days if d.isoformat() not in _saved_origins(path)]:
                    record = run_origin(conn, account, "real", origin, futures, None, None)
                    record["persona"] = f"account-{number}"  # the name stays out of the files
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
    conn: sqlite3.Connection, account: str, number: int, days: list[date], futures: int,
    out_dir: Path,
) -> int:  # fmt: skip
    """All origins of one account, then its records and paths written together. Skipped if both
    are there already."""
    path = out_dir / f"account-{number}.jsonl"
    npz = out_dir / f"account-{number}.paths.npz"
    if path.exists() and npz.exists():
        return 0
    records, kept = [], []
    for origin in days:
        record, p = run_origin_with_paths(conn, account, "real", origin, futures, None, None)
        record["persona"] = f"account-{number}"  # the name stays out of the files
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
    parser.add_argument("--metrics", action="store_true",
                        help="then print the metrics summary of the real results")  # fmt: skip
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    futures = args.futures or (QUICK_FUTURES if args.quick else FUTURES)
    folder = "paths" if args.paths else "quick" if args.quick else "full"
    out = args.out or DEFAULT_OUT / folder  # never the same folder
    counts = run_real(db_path, out, futures, args.quick, args.paths)
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
