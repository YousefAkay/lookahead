"""Tests for `python -m lookahead.pseudonymize report`: counts-only coverage report."""

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from lookahead.db import TRANSACTION_COLUMNS, connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.pseudonymize import main

ACCOUNT = "scotia-chequing"
HEX_KEY = bytes(range(32)).hex()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[str]]:
    """A database holding persona C, a .env with a key; returns (db, env, raw texts)."""
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)  # use the test .env only
    export, _ = write_persona(
        PERSONAS["C"],
        tmp_path / "gen",
        seed=42,
        start=date(2026, 6, 1),
        export_date=date(2026, 9, 30),
    )
    rows = parse_export(export)
    db_path = tmp_path / "db" / "l.db"
    conn = connect(db_path)
    store_exports(conn, ACCOUNT, [("file", rows)], datetime(2026, 10, 2))
    conn.close()
    env = tmp_path / ".env"
    env.write_text(f"PSEUDONYM_KEY={HEX_KEY}\n", encoding="utf-8")
    return db_path, env, [r.sub_description.strip() for r in rows if r.sub_description.strip()]


def report(db: Path, env: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    # Always pass an allowlist path in a temp folder: tests must never read the default
    # data/private/ allowlist.
    no_allowlist = env.parent / "no-allowlist.txt"
    code = main(["report", "--db", str(db), "--env", str(env), "--allowlist", str(no_allowlist)])
    return code, capsys.readouterr().out


def add_row(db: Path, description: str, sub: str) -> None:
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            f"INSERT INTO transactions ({TRANSACTION_COLUMNS})"
            " VALUES (?, '2026-09-01', -100, ?, ?, 0, ?, 'x', 'now')",
            (ACCOUNT, description, sub, f"fp-{sub}"),
        )
    conn.close()


def assert_private(out: str, raw_texts: list[str]) -> None:
    assert not re.search(r"\d+\.\d{2}", out) and "$" not in out  # no money
    assert not re.search(r"[A-Z]+_[0-9a-f]{12}", out)  # no tokens either
    assert not any(t in out for t in raw_texts), out  # no bank text
    assert "Nadia" not in out and "Haddad" not in out


# --- Synthetic data: every identifier is covered ---


def test_counts_rules_and_passes_on_synthetic_data(
    setup: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, raw = setup
    conn = sqlite3.connect(db)
    memos = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE description = 'credit memo'"
    ).fetchone()[0]
    mobile = conn.execute(
        "SELECT COUNT(*) FROM transactions"
        " WHERE description = 'bill payment' AND sub_description LIKE 'Mb-%'"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()

    code, out = report(db, env, capsys)
    assert code == 0
    assert f"{total} rows scanned" in out
    assert f"person: {memos} rows" in out and f"reference: {mobile} rows" in out
    assert "PERSON: 1 distinct" in out  # one sender
    assert "long digit runs: 0 rows" in out and "possible names: 0 rows" in out
    assert out.rstrip().endswith("RESULT: PASS")
    assert_private(out, raw)


# --- Gaps are counted, not shown ---


def test_a_name_left_in_an_unusual_row_is_counted(
    setup: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, raw = setup
    add_row(db, "accounts payable", "Jean Tremblay ")
    code, out = report(db, env, capsys)
    assert code == 1
    assert "possible names: 1 rows (accounts payable: 1)" in out
    assert "Jean" not in out and "Tremblay" not in out
    assert out.rstrip().endswith("RESULT: FAIL")
    assert_private(out, raw)


def test_a_split_card_number_is_counted(
    setup: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, raw = setup
    add_row(db, "withdrawal", "Card 4520 1234 5678 9012 ")
    code, out = report(db, env, capsys)
    assert code == 1 and "long digit runs: 1 rows (withdrawal: 1)" in out
    assert "4520" not in out


def test_fixed_bank_texts_are_not_names(
    setup: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, _ = setup
    for sub in ("Free Interac E-Transfer ", "Interac E-Transfer Rtn ", "Mb-Transfer "):
        add_row(db, "deposit", sub)
    code, out = report(db, env, capsys)
    assert code == 0 and "possible names: 0 rows" in out


# --- Safety ---


def test_missing_key_is_explained(
    tmp_path: Path, setup: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, _, _ = setup
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    code, out = report(db, empty_env, capsys)
    assert code == 2 and "init-key" in out


def test_missing_database_is_not_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope" / "l.db"
    args = ["report", "--db", str(missing), "--env", str(tmp_path / ".env")]
    code = main([*args, "--allowlist", str(tmp_path / "none.txt")])
    assert code == 2 and "not found" in capsys.readouterr().out
    assert not missing.exists()


def test_report_writes_nothing(
    setup: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, _ = setup
    before = db.read_bytes()
    report(db, env, capsys)
    assert db.read_bytes() == before


# --- Private allowlist of known business texts ---


def run_with_allowlist(
    db: Path, env: Path, allowlist: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    code = main(["report", "--db", str(db), "--env", str(env), "--allowlist", str(allowlist)])
    return code, capsys.readouterr().out


def test_allowlisted_business_texts_are_not_names(
    setup: tuple[Path, Path, list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, raw = setup
    add_row(db, "accounts payable", "Example Tickets Refund ")
    add_row(db, "customer transfer cr.", "Rewards Cash Credit ")
    allowlist = tmp_path / "allow.txt"
    # Case and spacing are ignored; comments and blank lines are skipped.
    allowlist.write_text(
        "# known businesses\n\nexample   TICKETS refund\nRewards Cash Credit\n", encoding="utf-8"
    )
    code, out = run_with_allowlist(db, env, allowlist, capsys)
    assert code == 0, out
    assert "possible names: 0 rows" in out
    assert "allowlisted as businesses: 2 rows" in out
    assert "Example" not in out and "Rewards" not in out  # allowlist contents never printed
    assert_private(out, raw)


def test_allowlist_only_covers_exact_texts(
    setup: tuple[Path, Path, list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, _ = setup
    add_row(db, "accounts payable", "Example Tickets Refund ")
    add_row(db, "accounts payable", "Jean Tremblay ")
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("Example Tickets Refund\n", encoding="utf-8")
    code, out = run_with_allowlist(db, env, allowlist, capsys)
    assert code == 1
    assert "possible names: 1 rows (accounts payable: 1)" in out
    assert "allowlisted as businesses: 1 rows" in out


def test_allowlist_does_not_hide_long_digit_runs(
    setup: tuple[Path, Path, list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, _ = setup
    add_row(db, "withdrawal", "Card 4520 1234 5678 9012 ")
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("Card 4520 1234 5678 9012\n", encoding="utf-8")
    code, out = run_with_allowlist(db, env, allowlist, capsys)
    assert code == 1 and "long digit runs: 1 rows" in out


def test_missing_allowlist_file_means_an_empty_allowlist(
    setup: tuple[Path, Path, list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, env, _ = setup
    code, out = run_with_allowlist(db, env, tmp_path / "none.txt", capsys)
    assert code == 0 and "allowlisted as businesses: 0 rows" in out
