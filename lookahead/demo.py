"""The synthetic demo database: each v0 persona imported as its own account."""

from datetime import datetime
from pathlib import Path

from lookahead.db import connect
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

DEMO_ACCOUNTS = ("synthetic-A", "synthetic-B", "synthetic-C")


def build_demo_db(db_path: Path, synthetic_dir: Path) -> None:
    """Import persona_A/B/C.csv from `synthetic_dir`. Safe to run again (dedupe)."""
    conn = connect(db_path)
    for account in DEMO_ACCOUNTS:
        export = synthetic_dir / f"persona_{account.removeprefix('synthetic-')}.csv"
        store_exports(conn, account, [(export.name, parse_export(export))], datetime.now())
    conn.close()
