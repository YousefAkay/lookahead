"""Tests for `python -m lookahead.ingest`: the import command and counts-only private check."""

import csv
import io
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import FILTER_TEXT, write_persona
from lookahead.ingest.__main__ import main

START, EXPORT = date(2026, 6, 1), date(2026, 9, 30)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + buffer.getvalue().removesuffix("\r\n").encode("ascii"))
    return path


@pytest.fixture
def records(tmp_path: Path) -> list[list[str]]:
    export, _ = write_persona(
        PERSONAS["C"], tmp_path / "gen", seed=42, start=START, export_date=EXPORT
    )
    with export.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))[1:]


def run(folder: Path, db: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main([str(folder), "--db", str(db)])
    return code, capsys.readouterr().out


def assert_private(out: str, records: list[list[str]]) -> None:
    """Nothing that could reveal an amount, balance or description is printed."""
    assert not re.search(r"\d+\.\d{2}", out), out  # no money
    assert "$" not in out
    texts = {r[3].strip() for r in records if r[3].strip()} | {r[2] for r in records}
    assert not any(t in out for t in texts), out  # no merchant text, no transaction types


# --- Happy paths ---


def test_single_export_imports_and_passes(
    records: list[list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_records(tmp_path / "raw" / "export.csv", records)
    code, out = run(tmp_path / "raw", tmp_path / "db" / "l.db", capsys)
    assert code == 0
    assert f"{len(records)} rows read" in out
    assert "balance chain PASS" in out
    assert f"{len(records)} new rows, 0 duplicates skipped" in out
    assert "reconciliation OK" in out and out.rstrip().endswith("RESULT: PASS")
    assert_private(out, records)
    n = (
        sqlite3.connect(tmp_path / "db" / "l.db")
        .execute("SELECT COUNT(*) FROM transactions")
        .fetchone()[0]
    )
    assert n == len(records)


def test_overlapping_exports_report_the_duplicates(
    records: list[list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_records(tmp_path / "raw" / "a.csv", records[:120])  # newer
    write_records(tmp_path / "raw" / "b.csv", records[80:])  # older
    code, out = run(tmp_path / "raw", tmp_path / "l.db", capsys)
    assert code == 0
    assert f"{len(records)} new rows, 40 duplicates skipped" in out
    assert "RESULT: PASS" in out
    assert_private(out, records)


def test_running_twice_adds_nothing_the_second_time(
    records: list[list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_records(tmp_path / "raw" / "export.csv", records)
    run(tmp_path / "raw", tmp_path / "l.db", capsys)
    code, out = run(tmp_path / "raw", tmp_path / "l.db", capsys)
    assert code == 0
    assert f"0 new rows, {len(records)} duplicates skipped" in out and "RESULT: PASS" in out


# --- Failures are reported by position and date only ---


def test_a_hole_between_exports_fails_with_dates_only(
    records: list[list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_records(tmp_path / "raw" / "a.csv", records[:60])
    write_records(tmp_path / "raw" / "b.csv", records[100:])
    code, out = run(tmp_path / "raw", tmp_path / "l.db", capsys)
    assert code == 1
    assert re.search(
        r"reconciliation FAIL.*\n.*break between \d{4}-\d{2}-\d{2} and \d{4}-\d{2}-\d{2}", out
    )
    assert out.rstrip().endswith("RESULT: FAIL")
    assert_private(out, records)


def test_a_broken_balance_chain_is_reported_by_row_position(
    records: list[list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = [list(r) for r in records]
    bad[10][6] = "0.00"  # corrupt one balance
    write_records(tmp_path / "raw" / "export.csv", bad)
    code, out = run(tmp_path / "raw", tmp_path / "l.db", capsys)
    assert code == 1
    assert "balance chain FAIL at rows 10, 11" in out
    assert_private(out, records)


def test_a_format_error_stops_before_storing_and_hides_the_value(
    records: list[list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = [list(r) for r in records]
    bad[4][5] = "12.3"  # amount with one decimal
    write_records(tmp_path / "raw" / "export.csv", bad)
    code, out = run(tmp_path / "raw", tmp_path / "l.db", capsys)
    assert code == 2
    assert "file 1: format error (amount) at line 6" in out
    assert "12.3" not in out
    assert not (tmp_path / "l.db").exists()  # nothing was stored


def test_empty_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "raw").mkdir()
    code, out = run(tmp_path / "raw", tmp_path / "l.db", capsys)
    assert code == 2 and "no .csv files" in out
