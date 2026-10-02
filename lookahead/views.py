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
from lookahead.recurring_store import RowOverride, load_series, row_overrides

COLUMNS = ["date", "type", "text", "amount_cents", "balance_cents", "source_file"]
# From lookahead.categorize (empty until it has run): merchant, flow type, category, where
# the category came from, and the flag.
CATEGORY_COLUMNS = ["merchant", "flow_type", "category", "source", "flagged", "fingerprint"]


def has_categories(conn: sqlite3.Connection, account: str) -> bool:
    """True once lookahead.categorize has stored results for this account."""
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_predictions'"
    ).fetchone()
    return bool(
        table
        and conn.execute(
            "SELECT 1 FROM category_predictions WHERE account = ? LIMIT 1", (account,)
        ).fetchone()
    )


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
    categories: list[str] | None = None,
    flagged_only: bool = False,
) -> pd.DataFrame:
    """One row per transaction, newest first. Filters: inclusive date range, a
    case-insensitive search in the merchant text, transaction types, categories and
    flagged rows (the last two need lookahead.categorize to have run).

    Within a day, rows keep the export's order (newest first) for a single export.
    """
    where, params = ["t.account = ?"], [account]
    joined = has_categories(conn, account)
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
    if joined and flagged_only:
        where.append("p.flagged = 1")
    extra = (
        ", p.merchant, p.flow_type, p.category, p.source, p.flagged"
        if joined
        else ", NULL AS merchant, NULL AS flow_type, NULL AS category, NULL AS source,"
        " NULL AS flagged"
    )
    join = " LEFT JOIN category_predictions p ON p.fingerprint = t.fingerprint" if joined else ""
    sql = (
        "SELECT posted_date AS date, description AS type, sub_description AS text,"
        f" amount_cents, balance_after_cents AS balance_cents, source_file{extra},"
        f" t.fingerprint FROM transactions t{join} WHERE {' AND '.join(where)}"
        " ORDER BY posted_date DESC, t.id ASC"
    )
    df = pd.read_sql_query(sql, conn, params=params)
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    df["flagged"] = df["flagged"].fillna(0).astype(bool)
    df = df.astype({"amount_cents": "int64", "balance_cents": "int64"})
    if joined:
        df = _with_series(df, row_overrides(load_series(conn, account)))
        if categories:  # after the series categories, so the filter sees what is shown
            df = df[df["category"].isin(categories)].reset_index(drop=True)
    return df[COLUMNS + CATEGORY_COLUMNS]


def _with_series(df: pd.DataFrame, overrides: dict[str, RowOverride]) -> pd.DataFrame:
    """Apply what recurring series change: a payment in a series is a recurring outflow, and
    takes the series' category if you gave it one. Other rows of the merchant are untouched."""
    if not overrides or df.empty:
        return df
    flow, category, source = (df[c].astype("object").tolist() for c in
                              ("flow_type", "category", "source"))  # fmt: skip
    flagged = df["flagged"].tolist()
    for i, fingerprint in enumerate(df["fingerprint"]):
        change = overrides.get(fingerprint)
        if change is None:
            continue
        if change.flow_type is not None and flow[i] == change.only_if_flow:
            flow[i] = change.flow_type
        if change.category is not None:
            category[i], source[i], flagged[i] = change.category, "series", False
    return df.assign(flow_type=flow, category=category, source=source, flagged=flagged)


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
