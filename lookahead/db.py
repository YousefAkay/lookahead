"""SQLite storage. Only the tables the importer needs so far (HANDOFF section 06).

Deviation from HANDOFF 06 (see DECISIONS.md): `description` + `sub_description`
instead of one `raw_description`, and `balance_after_cents` added. Money is integer
cents; dates are ISO text ("2026-09-30"), which sort correctly as text.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id                  INTEGER PRIMARY KEY,
    account             TEXT    NOT NULL,
    posted_date         TEXT    NOT NULL,
    amount_cents        INTEGER NOT NULL,
    description         TEXT    NOT NULL,
    sub_description     TEXT    NOT NULL,
    balance_after_cents INTEGER NOT NULL,
    fingerprint         TEXT    NOT NULL UNIQUE,
    source_file         TEXT    NOT NULL,
    imported_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS transactions_by_date ON transactions (account, posted_date);

CREATE TABLE IF NOT EXISTS balance_anchors (
    account       TEXT    NOT NULL,
    date          TEXT    NOT NULL,
    balance_cents INTEGER NOT NULL,
    source        TEXT    NOT NULL CHECK (source IN ('csv', 'user')),
    source_file   TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (account, date, source, source_file)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (and if needed create) the database; rows come back addressable by column name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def existing_fingerprints(conn: sqlite3.Connection, account: str) -> set[str]:
    return {
        r[0]
        for r in conn.execute("SELECT fingerprint FROM transactions WHERE account = ?", (account,))
    }


TRANSACTION_COLUMNS = (
    "account, posted_date, amount_cents, description, sub_description,"
    " balance_after_cents, fingerprint, source_file, imported_at"
)


def insert_transactions(conn: sqlite3.Connection, rows: list[tuple[object, ...]]) -> int:
    """Insert (account, posted_date, amount_cents, description, sub_description,
    balance_after_cents, fingerprint, source_file, imported_at) tuples. A repeated
    fingerprint raises sqlite3.IntegrityError: dedupe should have removed it."""
    conn.executemany(
        f"INSERT INTO transactions ({TRANSACTION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def insert_anchors(conn: sqlite3.Connection, rows: list[tuple[object, ...]]) -> int:
    """Insert or replace (account, date, balance_cents, source, source_file) anchors."""
    conn.executemany(
        "INSERT OR REPLACE INTO balance_anchors (account, date, balance_cents, source, source_file)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)
