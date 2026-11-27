"""Fixtures shared by several test files."""

import csv
from collections.abc import Iterator
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


@pytest.fixture(scope="session", autouse=True)
def private_folders_of_the_tests(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """`is_private_path` counts only the repository's own data/private folder. The tests build a
    stand-in `data/private` inside their temporary folders, so those folders count too."""
    from lookahead import merchants

    merchants.SANDBOXES.append(tmp_path_factory.getbasetemp())
    yield
    merchants.SANDBOXES.clear()


# Every module that reads the local settings file. tests/test_env_guard.py fails if another
# module starts importing load_env without being listed here.
ENV_READERS = ("lookahead.config", "lookahead.llm_fallback", "lookahead.llm_categorizer",
               "lookahead.pseudonymize", "lookahead.ask.llm")  # fmt: skip
ENV_KEYS = ("ANTHROPIC_API_KEY", "PSEUDONYM_KEY")


@pytest.fixture(autouse=True)
def never_the_real_env_file(  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
):
    """No test reads the developer's `.env` or uses a key from the shell. load_env reads only
    files inside the tests' temporary folders; any other settings file counts as absent and is
    never opened (as in a worktree, which has no .env). A test that needs a key sets it."""
    import importlib

    from lookahead import config

    real = config.load_env
    sandbox = tmp_path_factory.getbasetemp().resolve()

    def guarded(path: Path = Path(".env")) -> dict[str, str]:
        return real(path) if path.resolve().is_relative_to(sandbox) else {}

    for name in ENV_READERS:
        monkeypatch.setattr(importlib.import_module(name), "load_env", guarded)
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


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


@pytest.fixture(autouse=True)
def _served_on_localhost() -> Iterator[None]:
    """Pages run as if served on localhost (as the app must be with real data; the config file no
    longer says so, see DECISIONS.md 2026-10-11). A test may change it; it is put back after."""
    from streamlit import config as st_config

    st_config.set_option("server.address", "localhost")
    yield
    st_config.set_option("server.address", "localhost")
