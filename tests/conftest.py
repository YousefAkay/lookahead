"""Fixtures shared by several test files."""

import csv
from datetime import date, datetime
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A synthetic persona, frozen and labelled from its answer key (built once; tests only
    read it, or copy it first)."""
    from lookahead.db import connect
    from lookahead.generator.personas import PERSONAS
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports
    from lookahead.labels import freeze_splits, label_row, load_rows

    tmp = tmp_path_factory.mktemp("exp")
    export, key = write_persona(PERSONAS["C"], tmp / "s", 42, date(2025, 10, 1), date(2026, 9, 30))
    conn = connect(tmp / "t.db")
    store_exports(conn, "scotia-chequing", [(export.name, parse_export(export))],
                  datetime(2026, 10, 2))  # fmt: skip
    conn.commit()
    freeze_splits(conn, "scotia-chequing", seed=3)
    parsed = parse_export(export)
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    category = {(r.posted_date, r.amount_cents, r.sub_description): t["category"]
                for r, t in zip(parsed, truth, strict=True)}  # fmt: skip
    for r in load_rows(conn, "scotia-chequing").itertuples():
        label_row(conn, "scotia-chequing", r.fingerprint,
                  category[(r.posted_date, r.amount_cents, r.text)])  # fmt: skip
    conn.close()
    return tmp / "t.db"


@pytest.fixture(autouse=True)
def close_database_connections(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Close every SQLite connection a test (or an app page run by AppTest) opened and left open.
    Python 3.13 warns about an unclosed connection when it is garbage collected, and that
    happens at a random later moment, in some other test (it showed up in test_embeddings)."""
    import sqlite3

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("check_same_thread", False)  # an app page runs in another thread
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    yield
    for connection in opened:
        try:
            connection.close()
        except sqlite3.Error:
            pass
    opened.clear()
