"""Tests for SQLite storage: transactions, balance anchors, idempotent imports."""

import csv
import io
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from lookahead.db import TRANSACTION_COLUMNS, connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import FILTER_TEXT, write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import anchors_from_export, store_exports

START, EXPORT = date(2026, 6, 1), date(2026, 9, 30)
ACCOUNT = "scotia-chequing"
NOW = datetime(2026, 10, 2, 9, 30)
HEADER = [
    "Filter",
    "Date",
    "Description",
    "Sub-description",
    "Type of Transaction",
    "Amount",
    "Balance",
]


def key_rows(path: Path) -> list[dict[str, str]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def write_records(path: Path, records: list[list[str]]) -> Path:
    records = [[FILTER_TEXT if i == 0 else "", *r[1:]] for i, r in enumerate(records)]
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows([HEADER, *records])
    path.write_bytes(b"\xef\xbb\xbf" + buffer.getvalue().removesuffix("\r\n").encode("ascii"))
    return path


@pytest.fixture
def persona_c(tmp_path: Path) -> tuple[Path, Path]:
    return write_persona(PERSONAS["C"], tmp_path / "out", seed=42, start=START, export_date=EXPORT)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "db" / "test.db")


def count(db: sqlite3.Connection, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- Schema ---


def test_schema_is_created_and_reopening_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "db" / "x.db"
    connect(path).close()
    conn = connect(path)  # second time: CREATE ... IF NOT EXISTS, no error
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"transactions", "balance_anchors"} <= tables


# --- Transactions ---


def test_stored_rows_match_the_answer_key(
    persona_c: tuple[Path, Path], db: sqlite3.Connection
) -> None:
    export, key = persona_c
    result = store_exports(db, ACCOUNT, [(export.name, parse_export(export))], NOW)
    truth = key_rows(key)
    assert result.inserted == len(truth) == count(db, "transactions")
    stored = db.execute(
        "SELECT posted_date, amount_cents, description FROM transactions ORDER BY posted_date, id"
    ).fetchall()
    expected = sorted((k["date"], int(k["amount_cents"]), k["txn_type"]) for k in truth)
    assert sorted(tuple(r) for r in stored) == expected


def test_row_details_are_kept(persona_c: tuple[Path, Path], db: sqlite3.Connection) -> None:
    export, _ = persona_c
    newest = parse_export(export)[0]
    store_exports(db, ACCOUNT, [(export.name, parse_export(export))], NOW)
    row = db.execute(
        "SELECT * FROM transactions WHERE posted_date = ?", (newest.posted_date.isoformat(),)
    ).fetchone()
    assert row["account"] == ACCOUNT
    assert row["sub_description"] == newest.sub_description  # raw, trailing space kept
    assert row["balance_after_cents"] == newest.balance_cents
    assert row["source_file"] == export.name
    assert row["imported_at"] == NOW.isoformat(timespec="seconds")


def test_importing_the_same_file_again_adds_nothing(
    persona_c: tuple[Path, Path], db: sqlite3.Connection
) -> None:
    export, _ = persona_c
    rows = parse_export(export)
    store_exports(db, ACCOUNT, [(export.name, rows)], NOW)
    again = store_exports(db, ACCOUNT, [(export.name, rows)], NOW)
    assert again.inserted == 0 and again.skipped == {export.name: len(rows)}
    assert count(db, "transactions") == len(rows)


def test_overlapping_exports_in_separate_runs(
    persona_c: tuple[Path, Path], db: sqlite3.Connection, tmp_path: Path
) -> None:
    export, _ = persona_c
    with export.open(encoding="utf-8-sig", newline="") as f:
        recs = list(csv.reader(f))[1:]
    older = write_records(tmp_path / "older.csv", recs[80:])
    newer = write_records(tmp_path / "newer.csv", recs[:120])
    first = store_exports(db, ACCOUNT, [("older.csv", parse_export(older))], NOW)
    second = store_exports(db, ACCOUNT, [("newer.csv", parse_export(newer))], NOW)
    assert (first.inserted, second.inserted, second.skipped["newer.csv"]) == (
        len(recs) - 80,
        80,
        40,
    )
    assert count(db, "transactions") == len(recs)


def test_database_refuses_a_duplicate_fingerprint(
    persona_c: tuple[Path, Path], db: sqlite3.Connection
) -> None:
    export, _ = persona_c
    store_exports(db, ACCOUNT, [(export.name, parse_export(export))], NOW)
    fp = db.execute("SELECT fingerprint FROM transactions LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            f"INSERT INTO transactions ({TRANSACTION_COLUMNS})"
            " VALUES ('a', '2026-01-01', 0, 'x', ' ', 0, ?, 'f', 'now')",
            (fp,),
        )


# --- Balance anchors ---


def end_of_day_balances(key: Path, opening: int) -> dict[str, int]:
    """From the answer key: balance at the end of each date (rows are newest first)."""
    balance, out = opening, {}
    for k in reversed(key_rows(key)):
        balance += int(k["amount_cents"])
        out[k["date"]] = balance
    return out


def test_anchors_are_end_of_day_balances_except_the_newest_date(
    persona_c: tuple[Path, Path],
) -> None:
    export, key = persona_c
    anchors = dict(anchors_from_export(parse_export(export)))
    truth = end_of_day_balances(key, PERSONAS["C"].opening_balance_cents)
    newest = max(truth)
    assert newest == "2026-10-01" and date(2026, 10, 1) not in anchors  # possibly incomplete day
    assert {d.isoformat(): b for d, b in anchors.items()} == {
        d: b for d, b in truth.items() if d != newest
    }


def test_anchors_are_stored_per_file(persona_c: tuple[Path, Path], db: sqlite3.Connection) -> None:
    export, _ = persona_c
    rows = parse_export(export)
    result = store_exports(db, ACCOUNT, [(export.name, rows)], NOW)
    assert result.anchors == count(db, "balance_anchors") == len(anchors_from_export(rows))
    sources = {
        tuple(r) for r in db.execute("SELECT DISTINCT source, source_file FROM balance_anchors")
    }
    assert sources == {("csv", export.name)}


def test_storing_the_same_file_twice_does_not_duplicate_anchors(
    persona_c: tuple[Path, Path], db: sqlite3.Connection
) -> None:
    export, _ = persona_c
    rows = parse_export(export)
    store_exports(db, ACCOUNT, [(export.name, rows)], NOW)
    store_exports(db, ACCOUNT, [(export.name, rows)], NOW)
    assert count(db, "balance_anchors") == len(anchors_from_export(rows))


def test_a_failed_import_stores_nothing(
    persona_c: tuple[Path, Path], db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Make saving anchors fail AFTER the transactions were inserted: the whole import
    # must roll back, leaving neither transactions nor anchors.
    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr("lookahead.ingest.store.insert_anchors", broken)
    export, _ = persona_c
    with pytest.raises(RuntimeError):
        store_exports(db, ACCOUNT, [(export.name, parse_export(export))], NOW)
    assert count(db, "transactions") == 0 and count(db, "balance_anchors") == 0
