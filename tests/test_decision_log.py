"""Tests for dated decisions: the merchant decision log, replay by date, and migration.

Temporary databases only: never data/private/.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.db import main as db_main
from lookahead.resolve import apply_decisions, load_user_decisions

D1, D2, D3 = date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 20)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def test_every_decision_is_logged_with_its_date(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Maxi", "Maxi Verdun"), ("split", "Maxi", "Maxi Dollar")], D1)
    rows = conn.execute(
        "SELECT decided_at, decision, key_a, key_b FROM merchant_decisions ORDER BY decision_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("2026-09-01", "merge", "maxi", "maxi verdun"),
        ("2026-09-01", "split", "maxi", "maxi dollar"),
    ]


def test_blank_decisions_are_not_logged(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("", "A Shop", "B Shop")], D1)
    assert conn.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0] == 0


def test_replay_only_uses_decisions_made_by_the_date(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Maxi", "Maxi Verdun")], D2)
    assert load_user_decisions(conn, as_of=D1).merges == {}
    assert load_user_decisions(conn, as_of=D2).merges == {"Maxi": {"maxi", "maxi verdun"}}


def test_joining_two_merchants_later_does_not_leak_back(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Maxi", "Maxi Verdun")], D1)
    apply_decisions(conn, [("merge", "Iga", "Iga Extra")], D1)
    apply_decisions(conn, [("merge", "Maxi Verdun", "Iga Extra")], D3)
    before = load_user_decisions(conn, as_of=D2).merges
    assert before == {"Maxi": {"maxi", "maxi verdun"}, "Iga": {"iga", "iga extra"}}
    after = load_user_decisions(conn, as_of=D3).merges
    assert after == {"Maxi": {"maxi", "maxi verdun", "iga", "iga extra"}}


def test_a_later_split_is_not_seen_before_it_was_made(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Jean Coutu", "Jean Coutou")], D1)
    apply_decisions(conn, [("split", "Jean Coutu", "Jean Coutou")], D3)
    assert load_user_decisions(conn, as_of=D2).merges == {
        "Jean Coutu": {"jean coutu", "jean coutou"}
    }
    late = load_user_decisions(conn, as_of=D3)
    assert late.merges == {} and late.splits == {frozenset(("jean coutu", "jean coutou"))}


def test_replay_of_everything_matches_the_current_tables(conn: sqlite3.Connection) -> None:
    rows = [
        ("merge", "Maxi", "Maxi Verdun"),
        ("merge", "Iga", "Iga Extra"),
        ("merge", "Maxi Verdun", "Iga Extra"),
        ("split", "Maxi", "Iga"),
        ("merge", "Jean Coutu", "Jean Coutou"),
        ("split", "Jean Coutu", "Jean Coutou"),
        ("split", "Super C", "Super Cafe"),
        ("merge", "Super C", "Super Cafe"),
    ]
    for i, row in enumerate(rows):
        apply_decisions(conn, [row], date(2026, 9, 1 + i))
    from_log = load_user_decisions(conn)
    from_tables = load_user_decisions(conn, use_log=False)
    assert from_log == from_tables


# --- Migration of a database written before decisions had dates ---


def old_database(path: Path) -> None:
    """Tables as they were before the log existed, with one merge and one split."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE merchants (merchant_id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL,
            source TEXT NOT NULL);
        CREATE TABLE merchant_aliases (alias_key TEXT PRIMARY KEY, merchant_id INTEGER NOT NULL,
            source TEXT NOT NULL);
        CREATE TABLE merchant_splits (key_a TEXT NOT NULL, key_b TEXT NOT NULL,
            PRIMARY KEY (key_a, key_b));
        INSERT INTO merchants VALUES (1, 'Maxi', 'user');
        INSERT INTO merchant_aliases VALUES ('maxi', 1, 'user'), ('maxi verdun', 1, 'user'),
            ('maxi lasalle', 1, 'user');
        INSERT INTO merchant_splits VALUES ('jean coutou', 'jean coutu');
        """
    )
    conn.commit()
    conn.close()


def test_old_decisions_are_migrated_with_the_migration_date(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old_database(path)
    before = load_user_decisions(sqlite3.connect(path), use_log=False)
    conn = connect(path, today=D2)
    after = load_user_decisions(conn)
    assert after == before
    dates = {r[0] for r in conn.execute("SELECT decided_at FROM merchant_decisions")}
    assert dates == {"2026-09-10"}
    assert load_user_decisions(conn, as_of=D1) == load_user_decisions(sqlite3.connect(":memory:"))


def test_migration_runs_once(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old_database(path)
    connect(path, today=D2).close()
    conn = connect(path, today=D3)
    assert conn.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0] == 3
    assert {r[0] for r in conn.execute("SELECT decided_at FROM merchant_decisions")} == {
        "2026-09-10"
    }


def test_replay_by_date_refuses_an_unmigrated_database(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old_database(path)
    readonly = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    with pytest.raises(ValueError, match="migrate"):
        load_user_decisions(readonly, as_of=D1)


def test_migrate_command_prints_counts_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "old.db"
    old_database(path)
    assert db_main(["migrate", "--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert "merchant decisions in the log: 3" in out
    assert "maxi" not in out.casefold() and "coutu" not in out.casefold()


def test_migrate_command_on_a_missing_database(tmp_path: Path) -> None:
    assert db_main(["migrate", "--db", str(tmp_path / "none.db")]) == 2
    assert not (tmp_path / "none.db").exists()
