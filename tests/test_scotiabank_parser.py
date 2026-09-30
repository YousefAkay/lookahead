"""Tests for the Scotiabank chequing export parser."""

import csv
import io
from datetime import date
from pathlib import Path

import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import (
    ExportFormatError,
    balance_chain_breaks,
    parse_amount_cents,
    parse_export,
)

START, EXPORT = date(2026, 6, 1), date(2026, 9, 30)
HEADER = '"Filter","Date","Description","Sub-description","Type of Transaction","Amount","Balance"'
FILTER = "All available transactions (up to 2 years)"


def key_rows(path: Path) -> list[dict[str, str]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def handmade(tmp_path: Path, rows: list[str], header: str = HEADER, bom: bool = True) -> Path:
    """A small export file: BOM, CRLF, no final newline, like the real one."""
    text = "\r\n".join([header, *rows])
    path = tmp_path / "export.csv"
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("ascii"))
    return path


ROW_NEW = (
    f'"{FILTER}","2026-09-28","pos purchase",'
    '"Apos Example Cafe #12   Montr ","Debit","-6.48","1100.97"'
)
ROW_OLD = '"","2026-09-27","deposit","Free Interac E-Transfer ","Credit","150.00","1107.45"'


# --- The synthetic exports parse to exactly their answer keys ---


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_synthetic_export_matches_answer_key(pid: str, tmp_path: Path) -> None:
    export, key = write_persona(PERSONAS[pid], tmp_path, seed=42, start=START, export_date=EXPORT)
    rows, truth = parse_export(export), key_rows(key)
    assert len(rows) == len(truth)
    for r, k in zip(rows, truth, strict=True):
        assert r.position == int(k["row"])
        assert r.posted_date.isoformat() == k["date"]
        assert r.amount_cents == int(k["amount_cents"])
        assert r.description == k["txn_type"]
    assert balance_chain_breaks(rows) == []


def test_accepts_the_row_dated_after_the_export_date(tmp_path: Path) -> None:
    export, _ = write_persona(PERSONAS["C"], tmp_path, seed=42, start=START, export_date=EXPORT)
    assert parse_export(export)[0].posted_date == date(2026, 10, 1)


def test_far_future_date_is_accepted(tmp_path: Path) -> None:
    row = f'"{FILTER}","2099-01-01","deposit","Free Interac E-Transfer ","Credit","1.00","1.00"'
    assert parse_export(handmade(tmp_path, [row]))[0].posted_date == date(2099, 1, 1)


# --- Fields ---


def test_fields_are_parsed_and_trailing_spaces_kept(tmp_path: Path) -> None:
    newest, older = parse_export(handmade(tmp_path, [ROW_NEW, ROW_OLD]))
    assert newest.posted_date == date(2026, 9, 28)
    assert (newest.amount_cents, newest.balance_cents) == (-648, 110_097)
    assert newest.description == "pos purchase"
    assert newest.sub_description == "Apos Example Cafe #12   Montr "  # raw, as in the file
    assert newest.sub_description_norm == "Apos Example Cafe #12 Montr"  # for matching
    assert (newest.position, older.position) == (1, 2)
    assert (newest.line_no, older.line_no) == (2, 3)


@pytest.mark.parametrize(
    ("text", "cents"),
    [("-6.48", -648), ("22.54", 2_254), ("0.00", 0), ("-0.05", -5), ("20345.01", 2_034_501)],
)
def test_amount_to_integer_cents(text: str, cents: int) -> None:
    assert parse_amount_cents(text) == cents


@pytest.mark.parametrize("bad", ["6.4", "6", "6,48", "$6.48", "", "1e3", "--6.48"])
def test_malformed_amounts_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_amount_cents(bad)


def test_zero_amount_may_be_debit_or_credit(tmp_path: Path) -> None:
    rows = [
        f'"{FILTER}","2026-09-22","service charge"," ","Debit","0.00","100.00"',
        '"","2026-09-22","service charge"," ","Credit","0.00","100.00"',
    ]
    assert [r.amount_cents for r in parse_export(handmade(tmp_path, rows))] == [0, 0]


# --- File-level tolerance: BOM, line endings ---


def test_file_without_bom_or_with_lf_endings_still_parses(tmp_path: Path) -> None:
    path = handmade(tmp_path, [ROW_NEW, ROW_OLD], bom=False)
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n") + b"\n")
    assert len(parse_export(path)) == 2


# --- Errors name the line ---


def test_wrong_header_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ExportFormatError, match="line 1"):
        parse_export(handmade(tmp_path, [ROW_NEW], header='"Date","Amount"'))


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"\xef\xbb\xbf")
    with pytest.raises(ExportFormatError, match="empty"):
        parse_export(path)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ('"","2026-09-27","deposit"," ","Credit","150.00"', "7 fields"),
        ('"","27/09/2026","deposit"," ","Credit","150.00","1.00"', "date"),
        ('"","2026-09-27","deposit"," ","Credit","150.0","1.00"', "amount"),
        ('"","2026-09-27","deposit"," ","Credit","150.00","1,00"', "balance"),
        ('"","2026-09-27","deposit"," ","Debit","150.00","1.00"', "Debit"),
        ('"","2026-09-27","deposit"," ","Refund","150.00","1.00"', "Type"),
    ],
)
def test_bad_rows_are_rejected_with_line_number(tmp_path: Path, row: str, reason: str) -> None:
    with pytest.raises(ExportFormatError, match=rf"line 3.*{reason}"):
        parse_export(handmade(tmp_path, [ROW_NEW, row]))


def test_non_utf8_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "latin1.csv"
    path.write_bytes((HEADER + "\r\n" + ROW_NEW.replace("Cafe", "Caf\xe9")).encode("latin-1"))
    with pytest.raises(ExportFormatError, match="UTF-8"):
        parse_export(path)


# --- Balance chain ---


def test_balance_chain_break_is_reported_by_position(tmp_path: Path) -> None:
    bad_old = ROW_OLD.replace("1107.45", "1000.00")
    assert balance_chain_breaks(parse_export(handmade(tmp_path, [ROW_NEW, ROW_OLD]))) == []
    assert balance_chain_breaks(parse_export(handmade(tmp_path, [ROW_NEW, bad_old]))) == [1]
