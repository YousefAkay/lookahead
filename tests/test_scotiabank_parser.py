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
    export_encoding,
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


# --- Encodings: UTF-8 (with or without a BOM) or Windows-1252, never a guess beyond that ---

ACCENTED = (
    f'"{FILTER}","2026-09-28","pos purchase",'
    '"Apos Caf\u00e9 D\u00e9panneur  Montr\u00e9al ","Debit","-6.48","1100.97"'
)
OLDER = (
    '"","2026-09-27","pos purchase","Apos Boulangerie \u00c9toile   Qu\u00e9bec ","Debit",'
    '"-4.25","1107.45"'
)


def encoded(tmp_path: Path, name: str, encoding: str, bom: bool = False) -> Path:
    """The same made-up export, in the given encoding."""
    text = "\r\n".join([HEADER, ACCENTED, OLDER])
    path = tmp_path / name
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode(encoding))
    return path


@pytest.mark.parametrize(("encoding", "bom", "name"), [
    ("utf-8", True, "UTF-8"), ("utf-8", False, "UTF-8"), ("cp1252", False, "Windows-1252"),
])  # fmt: skip
def test_accented_letters_survive_in_either_encoding(
    tmp_path: Path, encoding: str, bom: bool, name: str
) -> None:
    path = encoded(tmp_path, "export.csv", encoding, bom)
    rows = parse_export(path)
    assert [r.sub_description for r in rows] == [
        "Apos Caf\u00e9 D\u00e9panneur  Montr\u00e9al ",
        "Apos Boulangerie \u00c9toile   Qu\u00e9bec ",
    ]
    assert export_encoding(path) == name


def test_the_same_rows_in_either_encoding_are_the_same_rows(tmp_path: Path) -> None:
    from datetime import datetime

    from lookahead.db import connect
    from lookahead.ingest.dedupe import fingerprint_export
    from lookahead.ingest.store import store_exports

    utf8 = parse_export(encoded(tmp_path, "a.csv", "utf-8", bom=True))
    cp1252 = parse_export(encoded(tmp_path, "b.csv", "cp1252"))
    assert fingerprint_export(utf8, "acct") == fingerprint_export(cp1252, "acct")
    conn = connect(tmp_path / "t.db")
    first = store_exports(conn, "acct", [("a", utf8)], datetime(2026, 10, 1))
    second = store_exports(conn, "acct", [("b", cp1252)], datetime(2026, 10, 2))
    assert (first.inserted, second.inserted) == (2, 0)  # nothing stored twice
    conn.close()


def test_a_file_in_neither_encoding_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "other.csv"
    # 0x81 is not a Windows-1252 character, and on its own it is not UTF-8 either.
    path.write_bytes((HEADER + "\r\n" + ROW_NEW).encode("ascii").replace(b"Cafe", b"Caf\x81"))
    with pytest.raises(ExportFormatError, match="neither UTF-8 nor Windows-1252"):
        parse_export(path)


def test_a_question_mark_in_the_file_stays_a_question_mark(tmp_path: Path) -> None:
    """If the bank already wrote "?" for a letter, the letter is gone: nothing invents it back."""
    path = handmade(tmp_path, [ROW_NEW.replace("Montr ", "Montr?al ")])
    assert parse_export(path)[0].sub_description == "Apos Example Cafe #12   Montr?al "


def test_the_import_says_which_encoding_it_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from lookahead.ingest.__main__ import main

    folder = tmp_path / "raw"
    folder.mkdir()
    encoded(folder, "export.csv", "cp1252")
    main([str(folder), "--db", str(tmp_path / "t.db")])
    out = capsys.readouterr().out
    assert "2 rows read (Windows-1252)" in out
    assert "Montr" not in out  # counts only, never the text


# --- Balance chain ---


def test_balance_chain_break_is_reported_by_position(tmp_path: Path) -> None:
    bad_old = ROW_OLD.replace("1107.45", "1000.00")
    assert balance_chain_breaks(parse_export(handmade(tmp_path, [ROW_NEW, ROW_OLD]))) == []
    assert balance_chain_breaks(parse_export(handmade(tmp_path, [ROW_NEW, bad_old]))) == [1]
