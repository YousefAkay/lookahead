"""Import every Scotiabank export in a folder, then reconcile. Prints counts only.

    python -m lookahead.ingest <folder> --db <database file> [--glob "*.csv"]

Output never contains amounts, balances, descriptions or file names: only counts,
row positions, line numbers, dates of reconciliation breaks, and PASS/FAIL. That makes
it safe to paste back when run on real data (which only the account owner does).

Exit codes: 0 = everything passed, 1 = imported but a check failed, 2 = nothing
imported (no files, or a file is not in the expected format).
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from lookahead.db import connect
from lookahead.ingest.reconcile import reconcile
from lookahead.ingest.scotiabank import (
    ExportFormatError,
    ParsedRow,
    balance_chain_breaks,
    parse_export,
)
from lookahead.ingest.store import store_exports

DEFAULT_ACCOUNT = "scotia-chequing"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.ingest", description=__doc__)
    parser.add_argument("folder", type=Path, help="folder with .csv exports")
    parser.add_argument("--db", type=Path, required=True, help="SQLite database file")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument("--glob", default="*.csv", help='which files to import (default "*.csv")')
    args = parser.parse_args(argv)

    paths = sorted(args.folder.glob(args.glob))
    print(f"Lookahead import: {len(paths)} file(s), account {args.account}")
    if not paths:
        print(f"no files matching {args.glob} found (no .csv files)")
        return 2

    # Parse everything first: a format error stops the run before anything is stored.
    parsed: list[tuple[str, list[ParsedRow]]] = []
    for i, path in enumerate(paths, start=1):
        try:
            parsed.append((f"file {i}", parse_export(path)))
        except ExportFormatError as e:
            where = f" at line {e.line}" if e.line else ""
            print(f"file {i}: format error ({e.kind}){where}")
            print("RESULT: FAIL (nothing imported)")
            return 2

    all_ok = True
    for label, rows in parsed:
        breaks = balance_chain_breaks(rows)
        chain = "PASS" if not breaks else "FAIL at rows " + ", ".join(map(str, breaks))
        print(f"{label}: {len(rows)} rows read, balance chain {chain}")
        all_ok &= not breaks

    # Store oldest exports first, so "duplicates skipped" is counted on the newer files.
    parsed.sort(key=lambda item: item[1][0].posted_date if item[1] else datetime.min.date())
    conn = connect(args.db)
    result = store_exports(conn, args.account, parsed, datetime.now())
    skipped = sum(result.skipped.values())
    per_file = ", ".join(f"{label}: {n}" for label, n in sorted(result.skipped.items()))
    print(f"stored: {result.inserted} new rows, {skipped} duplicates skipped ({per_file})")
    print(f"anchors: {result.anchors} stored")

    report = reconcile(conn, args.account)
    conn.close()
    if report.ok:
        print(f"reconciliation OK ({report.anchors_checked} anchors checked)")
    else:
        all_ok = False
        print(f"reconciliation FAIL ({report.anchors_checked} anchors checked)")
        for b in report.breaks:
            print(f"  break between {b.from_date} and {b.to_date}")
        for c in report.conflicts:
            print(f"  conflict on {c.date}: {len(c.balances)} sources disagree")

    print("RESULT: PASS" if all_ok else "RESULT: FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
