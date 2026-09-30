"""Tests for fingerprints and duplicate removal across overlapping exports."""

import csv
import io
from datetime import date
from pathlib import Path

import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import FILTER_TEXT, write_persona
from lookahead.ingest.dedupe import fingerprint_export, merge_exports
from lookahead.ingest.scotiabank import parse_export

START, EXPORT = date(2026, 6, 1), date(2026, 9, 30)
ACCOUNT = "scotia-chequing"
BOM = b"\xef\xbb\xbf"
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
    """An export in the real byte format; Filter text on the first data row only."""
    records = [[FILTER_TEXT if i == 0 else "", *r[1:]] for i, r in enumerate(records)]
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows([HEADER, *records])
    path.write_bytes(BOM + buffer.getvalue().removesuffix("\r\n").encode("ascii"))
    return path


def records_of(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))[1:]


def key_rows(path: Path) -> list[dict[str, str]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


@pytest.fixture
def persona_c(tmp_path: Path) -> tuple[Path, Path]:
    return write_persona(PERSONAS["C"], tmp_path, seed=42, start=START, export_date=EXPORT)


# --- Within one file, every row keeps its own fingerprint ---


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_no_row_of_a_single_export_is_ever_merged(pid: str, tmp_path: Path) -> None:
    export, key = write_persona(PERSONAS[pid], tmp_path, seed=42, start=START, export_date=EXPORT)
    rows = parse_export(export)
    assert len(set(fingerprint_export(rows, ACCOUNT))) == len(rows)
    # including both planted duplicate kinds, which must survive the import
    quirks = [k["quirk"] for k in key_rows(key)]
    assert quirks.count("identical_legit") == 2 and quirks.count("duplicate_charge") == 1


def test_zero_amount_twins_get_different_fingerprints(tmp_path: Path) -> None:
    fee = ["", "2026-09-22", "service charge", " ", "Debit", "0.00", "100.00"]
    rows = parse_export(write_records(tmp_path / "f.csv", [fee, fee]))
    a, b = fingerprint_export(rows, ACCOUNT)
    assert a != b  # same date, amount, text and balance: only the occurrence number differs


def test_spacing_differences_do_not_change_the_fingerprint(tmp_path: Path) -> None:
    row = ["", "2026-09-22", "pos purchase", "Apos Maxi #350   Montr ", "Debit", "-1.00", "99.00"]
    spaced = [*row[:3], "Apos Maxi #350 Montr", *row[4:]]
    [a] = fingerprint_export(parse_export(write_records(tmp_path / "a.csv", [row])), ACCOUNT)
    [b] = fingerprint_export(parse_export(write_records(tmp_path / "b.csv", [spaced])), ACCOUNT)
    assert a == b


def test_account_is_part_of_the_fingerprint(persona_c: tuple[Path, Path]) -> None:
    rows = parse_export(persona_c[0])
    assert fingerprint_export(rows, "a") != fingerprint_export(rows, "b")


# --- Overlapping exports ---


def split_overlapping(export: Path, tmp_path: Path) -> tuple[Path, Path, int]:
    """Two exports sharing 40 rows: the newer one (rows 1-120), the older (rows 81-end)."""
    recs = records_of(export)
    newer = write_records(tmp_path / "newer.csv", recs[:120])
    older = write_records(tmp_path / "older.csv", recs[80:])
    return newer, older, 40


def test_overlapping_exports_merge_to_the_original_rows(
    persona_c: tuple[Path, Path], tmp_path: Path
) -> None:
    newer, older, overlap = split_overlapping(persona_c[0], tmp_path)
    result = merge_exports(
        [("older.csv", parse_export(older)), ("newer.csv", parse_export(newer))], ACCOUNT
    )
    original = parse_export(persona_c[0])
    assert len(result.kept) == len(original)
    assert result.skipped == {"older.csv": 0, "newer.csv": overlap}
    assert {k.fingerprint for k in result.kept} == set(fingerprint_export(original, ACCOUNT))


def test_import_order_does_not_matter(persona_c: tuple[Path, Path], tmp_path: Path) -> None:
    newer, older, overlap = split_overlapping(persona_c[0], tmp_path)
    one = merge_exports([("o", parse_export(older)), ("n", parse_export(newer))], ACCOUNT)
    two = merge_exports([("n", parse_export(newer)), ("o", parse_export(older))], ACCOUNT)
    assert {k.fingerprint for k in one.kept} == {k.fingerprint for k in two.kept}
    assert sum(one.skipped.values()) == sum(two.skipped.values()) == overlap


def test_same_file_twice_skips_every_row_the_second_time(persona_c: tuple[Path, Path]) -> None:
    rows = parse_export(persona_c[0])
    result = merge_exports([("first", rows), ("again", rows)], ACCOUNT)
    assert result.skipped == {"first": 0, "again": len(rows)}


def test_already_imported_fingerprints_are_skipped(persona_c: tuple[Path, Path]) -> None:
    rows = parse_export(persona_c[0])
    seen = set(fingerprint_export(rows[:50], ACCOUNT))
    result = merge_exports([("f", rows)], ACCOUNT, seen=seen)
    assert result.skipped == {"f": 50} and len(result.kept) == len(rows) - 50


def test_zero_amount_twins_dedupe_correctly_across_files(tmp_path: Path) -> None:
    fee = ["", "2026-09-22", "service charge", " ", "Debit", "0.00", "100.00"]
    buy = ["", "2026-09-22", "pos purchase", "Apos Maxi #350   Montr ", "Debit", "-5.00", "100.00"]
    first = parse_export(write_records(tmp_path / "1.csv", [fee, fee, buy]))
    second = parse_export(write_records(tmp_path / "2.csv", [fee, fee, buy]))
    result = merge_exports([("1", first), ("2", second)], ACCOUNT)
    assert len(result.kept) == 3 and result.skipped == {"1": 0, "2": 3}


def test_kept_rows_remember_their_source(persona_c: tuple[Path, Path], tmp_path: Path) -> None:
    newer, older, _ = split_overlapping(persona_c[0], tmp_path)
    result = merge_exports(
        [("older.csv", parse_export(older)), ("newer.csv", parse_export(newer))], ACCOUNT
    )
    assert {k.source for k in result.kept} == {"older.csv", "newer.csv"}
    assert sum(k.source == "newer.csv" for k in result.kept) == 80  # rows only in the newer file
