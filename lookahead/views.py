"""Read-only queries behind the app pages. They return plain data; pages only display it.

pandas 3 notes: text columns come back with the `str` dtype (pandas 2 gave `object`);
filtering returns independent copies (copy-on-write), so no chained-assignment warnings.
Money stays in integer cents; conversion to dollars happens only when displaying.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from lookahead.ingest.reconcile import reconcile

COLUMNS = ["date", "type", "text", "amount_cents", "balance_cents", "source_file"]


def accounts(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0] for r in conn.execute("SELECT DISTINCT account FROM transactions ORDER BY account")
    ]


def transaction_types(conn: sqlite3.Connection, account: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT description FROM transactions WHERE account = ? ORDER BY description",
        (account,),
    )
    return [r[0] for r in rows]


def transactions(
    conn: sqlite3.Connection,
    account: str,
    start: date | None = None,
    end: date | None = None,
    text: str | None = None,
    types: list[str] | None = None,
) -> pd.DataFrame:
    """One row per transaction, newest first. Filters: inclusive date range, a
    case-insensitive search in the merchant text, and transaction types.

    Within a day, rows keep the export's order (newest first) for a single export.
    """
    where, params = ["account = ?"], [account]
    if start:
        where.append("posted_date >= ?")
        params.append(start.isoformat())
    if end:
        where.append("posted_date <= ?")
        params.append(end.isoformat())
    if text:
        where.append("sub_description LIKE ? ESCAPE '\\'")  # LIKE ignores ASCII case
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    if types:
        where.append(f"description IN ({', '.join('?' for _ in types)})")
        params.extend(types)
    sql = (
        "SELECT posted_date AS date, description AS type, sub_description AS text,"
        " amount_cents, balance_after_cents AS balance_cents, source_file"
        f" FROM transactions WHERE {' AND '.join(where)}"
        " ORDER BY posted_date DESC, id ASC"
    )
    df = pd.read_sql_query(sql, conn, params=params)
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    return df.astype({"amount_cents": "int64", "balance_cents": "int64"})[COLUMNS]


@dataclass
class ImportStatus:
    rows: int
    first_date: date | None
    last_date: date | None
    last_import: str | None
    reconciled: bool
    anchors_checked: int
    breaks: list[tuple[date, date]] = field(default_factory=list)


def import_status(conn: sqlite3.Connection, account: str) -> ImportStatus:
    """How much is stored, when it was imported, and whether it reconciles."""
    rows, first, last, imported = conn.execute(
        "SELECT COUNT(*), MIN(posted_date), MAX(posted_date), MAX(imported_at)"
        " FROM transactions WHERE account = ?",
        (account,),
    ).fetchone()
    report = reconcile(conn, account)
    return ImportStatus(
        rows=rows,
        first_date=date.fromisoformat(first) if first else None,
        last_date=date.fromisoformat(last) if last else None,
        last_import=imported,
        reconciled=report.ok,
        anchors_checked=report.anchors_checked,
        breaks=[(b.from_date, b.to_date) for b in report.breaks],
    )
