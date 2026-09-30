"""Tests for reconciliation: do stored anchors and transactions add up?"""

import csv
import io
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import FILTER_TEXT, write_persona
from lookahead.ingest.reconcile import reconcile
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

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


def write_records(path: Path, records: list[list[str]]) -> Path:
    records = [[FILTER_TEXT if i == 0 else "", *r[1:]] for i, r in enumerate(records)]
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows([HEADER, *records])
    path.write_bytes(b"\xef\xbb\xbf" + buffer.getvalue().removesuffix("\r\n").encode("ascii"))
    return path


def records_of(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))[1:]


@pytest.fixture
def export_c(tmp_path: Path) -> Path:
    export, _ = write_persona(
        PERSONAS["C"], tmp_path / "out", seed=42, start=START, export_date=EXPORT
    )
    return export


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "db" / "test.db")


def store(db: sqlite3.Connection, *paths: Path) -> None:
    for p in paths:
        store_exports(db, ACCOUNT, [(p.name, parse_export(p))], NOW)


def test_empty_database_reconciles(db: sqlite3.Connection) -> None:
    report = reconcile(db, ACCOUNT)
    assert report.ok and report.anchors_checked == 0


def test_a_clean_import_reconciles(db: sqlite3.Connection, export_c: Path) -> None:
    store(db, export_c)
    report = reconcile(db, ACCOUNT)
    assert report.ok, report
    assert report.anchors_checked > 50 and report.breaks == [] and report.conflicts == []


def test_a_missing_row_breaks_exactly_at_its_date(db: sqlite3.Connection, export_c: Path) -> None:
    store(db, export_c)
    anchor_dates = [
        r[0] for r in db.execute("SELECT DISTINCT date FROM balance_anchors ORDER BY date")
    ]
    victim_date = anchor_dates[len(anchor_dates) // 2]  # a date in the middle with an anchor
    victim = db.execute(
        "SELECT id, amount_cents FROM transactions"
        " WHERE posted_date = ? AND amount_cents != 0 LIMIT 1",
        (victim_date,),
    ).fetchone()
    db.execute("DELETE FROM transactions WHERE id = ?", (victim["id"],))

    [brk] = reconcile(db, ACCOUNT).breaks
    previous = anchor_dates[anchor_dates.index(victim_date) - 1]
    assert (brk.from_date.isoformat(), brk.to_date.isoformat()) == (previous, victim_date)
    assert brk.expected_cents - brk.anchor_cents == -victim["amount_cents"]  # gap = the missing row


def test_overlapping_exports_reconcile_without_conflicts(
    db: sqlite3.Connection, export_c: Path, tmp_path: Path
) -> None:
    recs = records_of(export_c)
    store(
        db,
        write_records(tmp_path / "older.csv", recs[80:]),
        write_records(tmp_path / "newer.csv", recs[:120]),
    )
    report = reconcile(db, ACCOUNT)
    assert report.ok, report
    shared = db.execute(
        "SELECT COUNT(*) FROM (SELECT date FROM balance_anchors GROUP BY date HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert shared > 0  # the overlap really produced dates anchored by both files


def test_a_hole_between_two_exports_is_a_break(
    db: sqlite3.Connection, export_c: Path, tmp_path: Path
) -> None:
    recs = records_of(export_c)
    newer, older = recs[:60], recs[100:]  # rows 61-100 are in neither file
    store(
        db,
        write_records(tmp_path / "older.csv", older),
        write_records(tmp_path / "newer.csv", newer),
    )
    report = reconcile(db, ACCOUNT)
    assert not report.ok and len(report.breaks) == 1
    [brk] = report.breaks
    assert brk.from_date <= date.fromisoformat(recs[99][1]) <= brk.to_date  # spans the hole


def test_two_files_disagreeing_about_a_day_is_a_conflict(
    db: sqlite3.Connection, export_c: Path
) -> None:
    store(db, export_c)
    d, balance = db.execute(
        "SELECT date, balance_cents FROM balance_anchors LIMIT 1 OFFSET 10"
    ).fetchone()
    db.execute(
        "INSERT INTO balance_anchors VALUES (?, ?, ?, 'csv', 'other.csv')",
        (ACCOUNT, d, balance + 100),
    )
    report = reconcile(db, ACCOUNT)
    [conflict] = report.conflicts
    assert conflict.date.isoformat() == d
    assert sorted(conflict.balances.values()) == [balance, balance + 100]
    assert report.breaks == []  # the disputed date is left out of the chain check
    assert not report.ok


def test_a_wrong_single_anchor_breaks_on_both_sides(db: sqlite3.Connection, export_c: Path) -> None:
    store(db, export_c)
    dates = [r[0] for r in db.execute("SELECT date FROM balance_anchors ORDER BY date")]
    db.execute(
        "UPDATE balance_anchors SET balance_cents = balance_cents + 500 WHERE date = ?",
        (dates[20],),
    )
    breaks = reconcile(db, ACCOUNT).breaks
    assert [(b.from_date.isoformat(), b.to_date.isoformat()) for b in breaks] == [
        (dates[19], dates[20]),
        (dates[20], dates[21]),
    ]


def test_a_correct_user_anchor_reconciles(db: sqlite3.Connection, export_c: Path) -> None:
    store(db, export_c)
    d, balance = db.execute(
        "SELECT date, balance_cents FROM balance_anchors LIMIT 1 OFFSET 5"
    ).fetchone()
    db.execute("INSERT INTO balance_anchors VALUES (?, ?, ?, 'user', '')", (ACCOUNT, d, balance))
    assert reconcile(db, ACCOUNT).ok


def test_other_accounts_are_ignored(db: sqlite3.Connection, export_c: Path) -> None:
    store(db, export_c)
    assert reconcile(db, "some-other-account").anchors_checked == 0
