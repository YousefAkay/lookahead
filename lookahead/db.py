"""SQLite storage. Only the tables the importer needs so far (HANDOFF section 06).

Deviation from HANDOFF 06 (see DECISIONS.md): `description` + `sub_description`
instead of one `raw_description`, and `balance_after_cents` added. Money is integer
cents; dates are ISO text ("2026-09-30"), which sort correctly as text.
"""

import argparse
import sqlite3
import sys
from datetime import date
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

-- Every owner decision, dated, in the order made. Replaying it up to a date gives the
-- decisions made by then (Time Machine); the tables above are the state as of today.
CREATE TABLE IF NOT EXISTS merchant_decisions (
    decision_id INTEGER PRIMARY KEY,
    decided_at  TEXT    NOT NULL,  -- ISO date
    decision    TEXT    NOT NULL CHECK (decision IN ('merge', 'split')),
    key_a       TEXT    NOT NULL,
    key_b       TEXT    NOT NULL,
    name_a      TEXT    NOT NULL   -- names the merchant if this merge creates one
);
-- Owner category choices per merchant name key; the latest one made by a date wins.
CREATE TABLE IF NOT EXISTS category_preferences (
    merchant_key TEXT NOT NULL,
    category     TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    PRIMARY KEY (merchant_key, decided_at)
);

-- Label review (step 6). Splits are frozen once per account, before any labelling.
CREATE TABLE IF NOT EXISTS label_split_info (
    account     TEXT    PRIMARY KEY,
    frozen_at   TEXT    NOT NULL,
    seed        INTEGER NOT NULL,
    cutoff_date TEXT    NOT NULL,  -- Test A = later rows (outside Test B merchants)
    test_b_groups TEXT  NOT NULL   -- JSON list of merchant groups held out for Test B
);
CREATE TABLE IF NOT EXISTS label_splits (
    fingerprint TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    split       TEXT NOT NULL CHECK (split IN ('dev', 'test_a', 'test_b'))
);
-- Every label decision, dated; a row's current label is its latest single-row decision,
-- else its latest merchant decision. category is NULL when skipped ("don't know") or
-- cleared (a reset: the row's earlier decisions no longer count).
CREATE TABLE IF NOT EXISTS category_labels (
    label_id    INTEGER PRIMARY KEY,
    fingerprint TEXT    NOT NULL,
    account     TEXT    NOT NULL,
    category    TEXT,
    status      TEXT    NOT NULL CHECK (status IN ('labelled', 'skipped', 'cleared')),
    scope       TEXT    NOT NULL CHECK (scope IN ('merchant', 'row')),
    decided_at  TEXT    NOT NULL,
    CHECK ((status = 'labelled') = (category IS NOT NULL))
);
"""


def connect(path: Path, today: date | None = None) -> sqlite3.Connection:
    """Open (and if needed create) the database; rows come back addressable by column name.

    Also migrates merchant decisions stored before they had dates (see `migrate_decisions`).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _migrate_label_status(conn)
    conn.executescript(SCHEMA)
    migrate_decisions(conn, today or date.today())
    return conn


def _migrate_label_status(conn: sqlite3.Connection) -> None:
    """category_labels made before "cleared" existed: rebuild it with the new CHECK rule
    (SQLite cannot change a CHECK in place), keeping every row and its id."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'category_labels'"
    ).fetchone()
    if row is None or "'cleared'" in row[0]:
        return
    # One script with its own BEGIN/COMMIT: all of it happens, or none of it.
    conn.executescript(
        "BEGIN;\n"
        "ALTER TABLE category_labels RENAME TO category_labels_old;\n"
        + SCHEMA
        + "\nINSERT INTO category_labels (label_id, fingerprint, account, category, status,"
        " scope, decided_at) SELECT label_id, fingerprint, account, category, status,"
        " scope, decided_at FROM category_labels_old;\n"
        "DROP TABLE category_labels_old;\n"
        "COMMIT;"
    )


def migrate_decisions(conn: sqlite3.Connection, today: date) -> int:
    """Write merges and splits stored before the decision log existed into the log, dated
    `today` (their real dates were not recorded). Runs only while the log is empty, so
    only once. Returns the number of log rows written."""
    if conn.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0]:
        return 0
    events: list[tuple[str, str, str, str, str]] = []
    day = today.isoformat()
    merchants: dict[int, tuple[str, list[str]]] = {}
    for merchant_id, name, key in conn.execute(
        "SELECT m.merchant_id, m.canonical_name, a.alias_key FROM merchant_aliases a"
        " JOIN merchants m USING (merchant_id) WHERE a.source = 'user' ORDER BY a.alias_key"
    ):
        merchants.setdefault(merchant_id, (name, []))[1].append(key)
    for name, keys in merchants.values():
        events += [(day, "merge", keys[0], other, name) for other in keys[1:]]
    for key_a, key_b in conn.execute("SELECT key_a, key_b FROM merchant_splits"):
        events.append((day, "split", key_a, key_b, key_a))
    with conn:
        conn.executemany(
            "INSERT INTO merchant_decisions (decided_at, decision, key_a, key_b, name_a)"
            " VALUES (?, ?, ?, ?, ?)",
            events,
        )
    return len(events)


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


def main(argv: list[str] | None = None) -> int:
    """`python -m lookahead.db migrate --db PATH`: bring an existing database up to date.
    Prints counts only."""
    parser = argparse.ArgumentParser(prog="python -m lookahead.db")
    commands = parser.add_subparsers(dest="command", required=True)
    mig = commands.add_parser("migrate", help="add new tables and date old decisions")
    mig.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    conn = sqlite3.connect(args.db)
    had_log = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'merchant_decisions'"
    ).fetchone()[0]
    before = conn.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0] if had_log else 0
    conn.close()
    conn = connect(args.db)
    after = conn.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0]
    conn.close()
    print(f"merchant decisions in the log: {after} ({after - before} migrated now, dated today)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
