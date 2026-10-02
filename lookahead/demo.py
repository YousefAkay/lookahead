"""The synthetic demo database: each v0 persona imported as its own account."""

import sqlite3
from datetime import datetime
from pathlib import Path

from lookahead.categorize import Settings, categorize, train
from lookahead.db import connect, connect_readonly
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

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
