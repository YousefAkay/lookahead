"""The synthetic demo database: each v0 persona imported as its own account."""

import sqlite3
from datetime import datetime
from pathlib import Path

from lookahead.categorize import Settings, categorize, train
from lookahead.db import connect, connect_readonly
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.labels import freeze_splits
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.recurring_store import refresh

DEMO_ACCOUNTS = ("synthetic-A", "synthetic-B", "synthetic-C")
DEMO_SETTINGS = Settings(threshold=0.5, synthetic_months=4)


def has_categories(db_path: Path) -> bool:
    """False for a demo database built before step 8 (the page rebuilds it then)."""
    conn = connect_readonly(db_path)
    try:
        return bool(conn.execute("SELECT 1 FROM category_predictions LIMIT 1").fetchone())
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def build_demo_db(db_path: Path, synthetic_dir: Path) -> None:
    """Import persona_A/B/C.csv from `synthetic_dir`. Safe to run again (dedupe)."""
    conn = connect(db_path)
    for account in DEMO_ACCOUNTS:
        export = synthetic_dir / f"persona_{account.removeprefix('synthetic-')}.csv"
        store_exports(conn, account, [(export.name, parse_export(export))], datetime.now())
    # Categories for the demo: one model trained on the generator's answer keys (the demo
    # has no labels of its own), with a fixed flag threshold. Demo only: it knows every
    # synthetic merchant, so it says nothing about real accuracy.
    model, _ = train(conn, DEMO_ACCOUNTS[0], DEMO_SETTINGS)
    for account in DEMO_ACCOUNTS:
        categorize(conn, account, DEMO_SETTINGS, model=model)
    conn.close()


def reset_demo_db(db_path: Path, synthetic_dir: Path) -> None:
    """Throw the synthetic demo database away and build it again from the persona files: no
    alerts, planned items, decisions, corrections or changed buffer, every recurring series
    waiting to be reviewed, and the label-review splits frozen so that page opens on a merchant.

    It refuses a path inside a folder called "private" and anything that is not a .db file, so it
    can never be pointed at real data by accident."""
    resolved = db_path.resolve()
    if "private" in resolved.parts:
        raise ValueError(f"refusing to reset a database in a private folder: {resolved.name}")
    if resolved.suffix != ".db":
        raise ValueError("a demo database is a .db file")
    try:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{resolved}{suffix}").unlink(missing_ok=True)
    except PermissionError:  # Windows: the running app holds the file open, so empty it instead
        _empty(resolved)
    build_demo_db(db_path, synthetic_dir)
    conn = connect(db_path)
    try:
        key: bytes | None = pseudonym_key()  # the pages detect with it when it exists
    except PseudonymKeyError:
        key = None
    for account in DEMO_ACCOUNTS:
        freeze_splits(conn, account, seed=7)
        refresh(conn, account, key)  # so Home counts the series before Recurring is opened
    conn.close()


def _empty(path: Path) -> None:
    """Drop every table and view in place (for a database file that cannot be deleted)."""
    conn = sqlite3.connect(path)
    objects = conn.execute(
        "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view')"
        " AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for kind, name in objects:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    conn.commit()
    conn.close()


def main() -> None:
    """`python -m lookahead.demo reset`: start the synthetic demo again from a clean state."""
    import argparse

    from lookahead.ui import demo_db_path, synthetic_dir

    parser = argparse.ArgumentParser(prog="python -m lookahead.demo")
    parser.add_argument("command", choices=["reset"])
    parser.parse_args()
    db_path = demo_db_path()
    reset_demo_db(db_path, synthetic_dir())
    print(f"Synthetic demo reset: {db_path}")
    print("Recurring starts with every series to review; Forecast on synthetic-C shows a fresh "
          "alert the first time it opens.")  # fmt: skip


if __name__ == "__main__":
    main()
