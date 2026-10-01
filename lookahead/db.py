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

-- Merchant resolution (HANDOFF 06 planned `aliases` as a column; a table keeps one row
-- per name, so a name can be looked up and belongs to exactly one merchant).
-- For now only the owner's decisions are stored; rule merges are recomputed each time.
CREATE TABLE IF NOT EXISTS merchants (
    merchant_id    INTEGER PRIMARY KEY,
    canonical_name TEXT    NOT NULL,
    source         TEXT    NOT NULL CHECK (source IN ('rule', 'similarity', 'user'))
);
CREATE TABLE IF NOT EXISTS merchant_aliases (
    alias_key   TEXT    PRIMARY KEY,  -- normalized name (resolve.name_key)
    merchant_id INTEGER NOT NULL REFERENCES merchants (merchant_id),
    source      TEXT    NOT NULL CHECK (source IN ('rule', 'similarity', 'user'))
);
-- Pairs the owner said are different merchants: no rule or proposal joins them.
CREATE TABLE IF NOT EXISTS merchant_splits (
    key_a TEXT NOT NULL,
    key_b TEXT NOT NULL,
    PRIMARY KEY (key_a, key_b),
    CHECK (key_a < key_b)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (and if needed create) the database; rows come back addressable by column name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing database read-only: SQLite refuses writes and never creates the file."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
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
