"""The backtest on the account holder's real data. Run by the account holder only.

    python -m eval.backtest_real --quick        # 4 origins per account, 100 futures
    python -m eval.backtest_real                # every weekly origin that fits

It opens the database read-only (the file is not changed), makes the same forecasts as the
synthetic runner at every Monday that has eight weeks of history before it and thirty observed
days after it, and saves one record per origin under data/private/backtest/ (private, never
committed, files named by number and not by account). It prints counts only: how many accounts
and origins, and how many origins were followed by a balance below the buffer or below zero. It
never prints a row, an amount, a date of a transaction or an account name. Real results are a
separate column from synthetic ones in every report; one person's history is a case study.
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest import (
    FUTURES,
    QUICK_FUTURES,
    _check_settings,
    _saved_origins,
    read_records,
    run_origin,
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


def run_real(db_path: Path, out_dir: Path, futures: int, quick: bool = False) -> dict[str, int]:
    """Run every account of the database. Returns counts (accounts, new origins, saved)."""
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
                out_dir / f"account-{number}.meta.json", {"futures": futures, "method": "weekday"}
            )
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
    }


def print_counts(counts: dict[str, Any], out_dir: Path) -> None:
    """Counts only."""
    records = read_records(out_dir)
    below = sum(r["realized_first_below_buffer"] is not None for r in records)
    zero = sum(r["realized_below_zero"] for r in records)
    n = counts["accounts"]
    print(
        f"{n} account{'s' if n != 1 else ''}, {counts['origins_saved']} origins saved "
        f"({counts['new_origins']} new this run)"
    )
    print(f"origins followed by a balance below the buffer: {below}; below zero: {zero}")
    print(f"saved in {out_dir} (private: never commit)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.backtest_real", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--futures", type=int, default=None)
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    futures = args.futures or (QUICK_FUTURES if args.quick else FUTURES)
    counts = run_real(db_path, args.out, futures, args.quick)
    print_counts(counts, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
