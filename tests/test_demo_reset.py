"""Resetting the synthetic demo database to a clean state for a demo take."""

from datetime import date
from pathlib import Path

import pytest

from lookahead.alerts import episodes
from lookahead.db import connect
from lookahead.demo import DEMO_ACCOUNTS, build_demo_db, reset_demo_db
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.planned import add_planned
from lookahead.recurring_store import confirm, refresh
from lookahead.settings import set_safety_buffer_cents


@pytest.fixture
def synthetic(tmp_path: Path) -> Path:
    folder = tmp_path / "synthetic"
    for pid in "ABC":
        write_persona(PERSONAS[pid], folder, seed=42, start=date(2026, 6, 1),
                      export_date=date(2026, 9, 30))  # fmt: skip
    return folder


def dirty(db: Path, synthetic: Path) -> None:
    """Leave the marks a round of testing leaves behind."""
    build_demo_db(db, synthetic)
    conn = connect(db)
    series = refresh(conn, "synthetic-C")
    confirm(conn, "synthetic-C", series[0])
    add_planned(conn, "synthetic-C", date(2026, 10, 12), 50000, "Test deposit", date(2026, 10, 7))
    set_safety_buffer_cents(conn, 99900)
    with conn:
        conn.execute("INSERT INTO alert_episodes (account, opened_on, last_seen_on)"
                     " VALUES ('synthetic-C', '2026-10-01', '2026-10-01')")  # fmt: skip
    conn.close()


def test_a_reset_removes_everything_testing_left_behind(tmp_path: Path, synthetic: Path) -> None:
    db = tmp_path / "demo.db"
    dirty(db, synthetic)
    reset_demo_db(db, synthetic)
    conn = connect(db)
    for table in ("planned_items", "recurring_decisions", "alert_episodes", "alert_events",
                  "app_settings", "category_corrections"):  # fmt: skip
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert episodes(conn, "synthetic-C") == []
    conn.close()


def test_a_reset_database_has_the_three_accounts_and_the_label_review_ready(
    tmp_path: Path, synthetic: Path
) -> None:
    db = tmp_path / "demo.db"
    reset_demo_db(db, synthetic)  # no database yet: fine
    conn = connect(db)
    accounts = [r[0] for r in conn.execute("SELECT DISTINCT account FROM transactions")]
    assert sorted(accounts) == sorted(DEMO_ACCOUNTS)
    frozen = [r[0] for r in conn.execute("SELECT account FROM label_split_info")]
    assert sorted(frozen) == sorted(DEMO_ACCOUNTS)  # the Label review page opens on a merchant
    conn.close()


def test_after_a_reset_every_series_is_waiting_to_be_reviewed(
    tmp_path: Path, synthetic: Path
) -> None:
    db = tmp_path / "demo.db"
    dirty(db, synthetic)
    reset_demo_db(db, synthetic)
    conn = connect(db)
    for account in DEMO_ACCOUNTS:
        statuses = {s.status for s in refresh(conn, account)}
        assert statuses == {"detected"}, account
    conn.close()


def test_a_reset_database_already_knows_its_series_so_home_can_count_them(
    tmp_path: Path, synthetic: Path
) -> None:
    from lookahead.recurring_store import cache_current, load_series

    db = tmp_path / "demo.db"
    reset_demo_db(db, synthetic)
    conn = connect(db)
    for account in DEMO_ACCOUNTS:
        assert cache_current(conn, account)
        found = load_series(conn, account)
        assert found and {s.status for s in found} == {"detected"}
    conn.close()


def test_a_reset_refuses_a_path_in_a_private_folder(tmp_path: Path, synthetic: Path) -> None:
    private = tmp_path / "data" / "private" / "db"
    private.mkdir(parents=True)
    db = private / "lookahead.db"
    db.write_bytes(b"real bank data would be here")
    with pytest.raises(ValueError, match="private"):
        reset_demo_db(db, synthetic)
    assert db.read_bytes() == b"real bank data would be here"  # untouched


def test_a_reset_only_deletes_a_database_it_made(tmp_path: Path, synthetic: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("not a database")
    with pytest.raises(ValueError, match="database"):
        reset_demo_db(other, synthetic)
    assert other.read_text() == "not a database"


def test_a_reset_works_while_the_app_has_the_database_open(tmp_path: Path, synthetic: Path) -> None:
    """Windows will not delete a file another program holds open (the running app does)."""
    import sqlite3

    db = tmp_path / "demo.db"
    dirty(db, synthetic)
    holder = sqlite3.connect(db)  # as a running Streamlit page would
    holder.execute("SELECT COUNT(*) FROM transactions").fetchone()
    reset_demo_db(db, synthetic)
    holder.close()
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM planned_items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM alert_episodes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM recurring_decisions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(DISTINCT account) FROM transactions").fetchone()[0] == 3
    conn.close()
