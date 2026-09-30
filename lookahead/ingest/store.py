"""Store parsed exports in SQLite: new transactions plus balance anchors, in one transaction.

Anchors: the end-of-day balance for every date in a file EXCEPT its newest date. An
export stops at the export moment (and holds one next-day row), so its newest date may
be incomplete; a later export would show a different end-of-day balance for it.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

from lookahead.db import existing_fingerprints, insert_anchors, insert_transactions
from lookahead.ingest.dedupe import merge_exports
from lookahead.ingest.scotiabank import ParsedRow


@dataclass
class StoreResult:
    inserted: int = 0
    skipped: dict[str, int] = field(default_factory=dict)  # per file: rows already stored
    anchors: int = 0


def anchors_from_export(rows: list[ParsedRow]) -> list[tuple[date, int]]:
    """(date, end-of-day balance) for each date in the file except the newest.

    Rows are newest first, so the first row seen for a date is that date's last posting.
    """
    if not rows:
        return []
    newest = rows[0].posted_date
    end_of_day: dict[date, int] = {}
    for r in rows:
        if r.posted_date != newest and r.posted_date not in end_of_day:
            end_of_day[r.posted_date] = r.balance_cents
    return sorted(end_of_day.items())


def store_exports(
    conn: sqlite3.Connection,
    account: str,
    exports: list[tuple[str, list[ParsedRow]]],
    imported_at: datetime,
) -> StoreResult:
    """Insert new rows and anchors from (file name, rows) exports. All or nothing."""
    stamp = imported_at.isoformat(timespec="seconds")
    with conn:  # one transaction: commits at the end, rolls back on any error
        merged = merge_exports(exports, account, seen=existing_fingerprints(conn, account))
        inserted = insert_transactions(
            conn,
            [
                (
                    account,
                    k.row.posted_date.isoformat(),
                    k.row.amount_cents,
                    k.row.description,
                    k.row.sub_description,
                    k.row.balance_cents,
                    k.fingerprint,
                    k.source,
                    stamp,
                )
                for k in merged.kept
            ],
        )
        anchors = insert_anchors(
            conn,
            [
                (account, d.isoformat(), balance, "csv", name)
                for name, rows in exports
                for d, balance in anchors_from_export(rows)
            ],
        )
    return StoreResult(inserted=inserted, skipped=merged.skipped, anchors=anchors)
