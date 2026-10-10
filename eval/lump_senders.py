"""How many of the big deposits carry a sender name, counts only.

A lump deposit is what the forecaster sets aside as a large deposit (big, in no series, not paid
straight back out), posted in the last 365 days. A proposal "expecting about $X from <sender>"
needs a sender, so this counts, for each account of a database, how many lumps have a sender name
in their text and how many do not (an incoming e-Transfer usually has none), and how many senders
sent two or more. It opens the database read-only and prints numbers only: no row, no date, no
amount, no text, no name.

    python -m eval.lump_senders                  # your database (data/private)
    python -m eval.lump_senders --db some.db
"""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from pathlib import Path

from lookahead.db import connect_readonly
from lookahead.flows import Flow, Txn, flow_types
from lookahead.forecast.spending import lump_rows
from lookahead.recurring_store import series_as_of
from lookahead.ui import private_db_path
from lookahead.views import accounts

_BANK_WORDS = frozenset({"free", "interac", "e-transfer", "etransfer", "e-transfert", "from"})
_SLOT = re.compile(r"^\s*\d+/")  # a credit memo's text starts with a slot such as "1/"


def sender_of(text: str) -> str:
    """The sender name in a deposit's text, case-folded; empty if the bank prints none. Only
    letters decide: card-style codes, slots like "1/" and the fixed e-Transfer words are no name."""
    words = [w for w in _SLOT.sub("", text.casefold()).split() if w not in _BANK_WORDS]
    name = " ".join(words)
    return name if re.search(r"[^\W\d_]", name) else ""


def count_lumps(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date
) -> dict[str, int]:
    """Counts of the lumps of the last year by sender: named or not, how many senders, how many
    of them sent at least 2 and at least 3 lumps, and how many lumps those senders account for."""
    rows = lump_rows(txns, flows, series_rows, as_of)
    per_sender = Counter(s for t, _, _ in rows if (s := sender_of(t.text)))
    named = sum(per_sender.values())
    return {
        "lumps": len(rows), "named": named, "unnamed": len(rows) - named,
        "senders": len(per_sender),
        "senders_2_plus": sum(1 for n in per_sender.values() if n >= 2),
        "senders_3_plus": sum(1 for n in per_sender.values() if n >= 3),
        "lumps_from_senders_2_plus": sum(n for n in per_sender.values() if n >= 2),
    }  # fmt: skip


def count_account(conn: sqlite3.Connection, account: str) -> dict[str, int] | None:
    """`count_lumps` for one account as of its last posting date; None if it has no rows."""
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ?",
        (account,),
    ).fetchall()
    if not rows:
        return None
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]
    as_of = max(t.posted_date for t in txns)
    series, _ = series_as_of(conn, account, as_of, None)
    row_of = {r[5]: r[0] for r in rows}
    in_series = {
        row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence if fp in row_of
    }
    return count_lumps(txns, flow_types(txns), in_series, as_of)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.lump_senders", description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    conn = connect_readonly(db_path)
    try:
        for number, account in enumerate(accounts(conn), start=1):
            counts = count_account(conn, account)
            print(f"account-{number}")  # the name stays out of the output
            if counts is None:
                print("  no rows")
                continue
            print(f"  lump deposits in the last 365 days: {counts['lumps']}")
            print(f"  with a sender name: {counts['named']}   without: {counts['unnamed']}")
            print(f"  different senders: {counts['senders']}")
            print(f"  senders with 2 or more lumps: {counts['senders_2_plus']} "
                  f"(together {counts['lumps_from_senders_2_plus']} lumps)")  # fmt: skip
            print(f"  senders with 3 or more lumps: {counts['senders_3_plus']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
