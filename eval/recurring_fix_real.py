"""My real backtest with the recurring-placement fix off or on (DECISIONS.md, 2026-10-12).

The same weekly origins and the same forecaster as `eval.backtest_real` (keeps-coming on), with
`--fix b`: series whose last payment is more than 1.5 periods back are not placed. Each variant
has its own folder under `data/private/backtest/recurring-fix-<variant>/`; `eval.metrics` reads
them like any other real run. The database is opened read-only; the output folder must be inside
`data/private`. Prints counts only.

    python -m eval.recurring_fix_real --fix off
    python -m eval.recurring_fix_real --fix b
    python -m eval.recurring_fix_real --fix a
    python -m eval.recurring_fix_real --fix both
    python -m eval.metrics --no-synthetic --real data/private/backtest/recurring-fix-b
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from eval.backtest import (
    FUTURES,
    QUICK_FUTURES,
    _check_settings,
    _saved_origins,
    archive_older,
    keeps_name,
    read_records,
    run_origin,
    version_settings,
)
from eval.backtest_real import (
    DEFAULT_OUT,
    account_key,
    archive_numbered,
    check_private,
    print_counts,
    real_origins,
)
from eval.fix_variants import FIXES, fix_options
from lookahead.db import connect_readonly
from lookahead.forecast.spending import DEFAULT_KEEPS
from lookahead.merchants import is_private_path
from lookahead.ui import private_db_path
from lookahead.views import accounts


def run_real_fix(
    db_path: Path, out_dir: Path, futures: int, variant: str, quick: bool = False
) -> dict[str, int]:  # fmt: skip
    """Run every account with the fix `variant`. Returns counts (accounts, new origins, saved)."""
    check_private(db_path, out_dir)
    archived = (
        1 if archive_older(out_dir) or (out_dir.exists() and archive_numbered(out_dir)) else 0
    )
    conn = connect_readonly(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    new = 0
    names = accounts(conn)
    try:
        for account in names:
            first, last = conn.execute(
                "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
                (account,),
            ).fetchone()
            days = real_origins(date.fromisoformat(first), date.fromisoformat(last))
            if quick:
                days = days[:: max(1, len(days) // 4)][:4]
            key = account_key(account, out_dir)  # stable when accounts are added or removed
            path = out_dir / f"{key}.jsonl"
            settings: dict[str, Any] = {
                "futures": futures, "method": "weekday", **version_settings(),
                "keeps_coming": keeps_name(DEFAULT_KEEPS), "recurring_fix": variant,
            }  # fmt: skip
            _check_settings(out_dir / f"{key}.meta.json", settings)
            with path.open("a", encoding="utf-8") as out:
                for origin in [d for d in days if d.isoformat() not in _saved_origins(path)]:
                    record = run_origin(conn, account, "real", origin, futures, None, None,
                                        options=fix_options(variant))  # fmt: skip
                    record["persona"] = key  # the name stays out of the files
                    record["recurring_fix"] = variant
                    out.write(json.dumps(record) + "\n")
                    out.flush()
                    new += 1
    finally:
        conn.close()
    return {"accounts": len(names), "new_origins": new,
            "origins_saved": len(read_records(out_dir)), "archived_older": archived}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.recurring_fix_real", description=__doc__)
    parser.add_argument("--fix", choices=sorted(FIXES), required=True)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--futures", type=int, default=None)
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    if not is_private_path(db_path):
        print("The database must be inside a data/private folder.")
        return 1
    futures = args.futures or (QUICK_FUTURES if args.quick else FUTURES)
    out = args.out or DEFAULT_OUT / f"recurring-fix-{args.fix}" / (
        "quick" if args.quick else "full"
    )
    if not is_private_path(out):
        print("The output folder must be inside a data/private folder.")
        return 1
    counts = run_real_fix(db_path, out, futures, args.fix, args.quick)
    if counts["archived_older"]:
        print(f"Saved results were from an older version: moved them to {out / 'older'}.")
    print_counts(counts, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
