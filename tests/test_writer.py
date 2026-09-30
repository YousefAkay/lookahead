"""Tests for the Scotiabank-format writer and the generate command."""

import csv
import io
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.generator.__main__ import main
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import FILTER_TEXT, HEADER, money, write_persona

START = date(2026, 6, 1)
EXPORT = date(2026, 9, 30)
BOM = b"\xef\xbb\xbf"


@pytest.fixture(params=["A", "B", "C"])
def written(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[str, Path, Path]:
    """Write one persona into a temporary folder; return (persona id, export, answer key)."""
    pid = request.param
    export, key = write_persona(PERSONAS[pid], tmp_path, seed=42, start=START, export_date=EXPORT)
    return pid, export, key


def export_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def key_rows(path: Path) -> list[dict[str, str]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def cents(text: str) -> int:
    return round(float(text) * 100)


# --- Bytes on disk: BOM, CRLF, no trailing newline, ASCII ---


def test_file_starts_with_a_bom(written: tuple[str, Path, Path]) -> None:
    assert written[1].read_bytes().startswith(BOM)


def test_every_line_ends_with_crlf_and_the_last_has_none(written: tuple[str, Path, Path]) -> None:
    raw = written[1].read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0  # no bare LF
    assert not raw.endswith(b"\n") and not raw.endswith(b"\r")


def test_after_the_bom_the_file_is_pure_ascii(written: tuple[str, Path, Path]) -> None:
    written[1].read_bytes()[len(BOM) :].decode("ascii")  # raises if not ASCII


# --- Content ---


def test_exact_header(written: tuple[str, Path, Path]) -> None:
    first_line = written[1].read_bytes()[len(BOM) :].split(b"\r\n")[0].decode("ascii")
    expected = '"Filter","Date","Description","Sub-description",'
    expected += '"Type of Transaction","Amount","Balance"'
    assert first_line == expected
    assert export_rows(written[1])[0] == HEADER


def test_filter_only_on_the_first_data_row(written: tuple[str, Path, Path]) -> None:
    data = export_rows(written[1])[1:]
    assert data[0][0] == FILTER_TEXT
    assert all(r[0] == "" for r in data[1:])


def test_reads_back_cleanly(written: tuple[str, Path, Path]) -> None:
    for r in export_rows(written[1])[1:]:
        assert len(r) == 7
        date.fromisoformat(r[1])
        assert re.fullmatch(r"-?\d+\.\d{2}", r[5]) and re.fullmatch(r"-?\d+\.\d{2}", r[6])
        assert r[3]  # at least a single space


def test_type_matches_sign(written: tuple[str, Path, Path]) -> None:
    for r in export_rows(written[1])[1:]:
        assert r[4] == ("Credit" if cents(r[5]) > 0 else "Debit")


def test_running_balance_is_consistent(written: tuple[str, Path, Path]) -> None:
    pid = written[0]
    data = export_rows(written[1])[1:]  # newest first
    for newer, older in zip(data, data[1:], strict=False):
        assert cents(newer[6]) == cents(older[6]) + cents(newer[5])
    oldest = data[-1]
    assert cents(oldest[6]) == PERSONAS[pid].opening_balance_cents + cents(oldest[5])


def test_rows_are_newest_first_with_exactly_one_after_the_export_date(
    written: tuple[str, Path, Path],
) -> None:
    dates = [date.fromisoformat(r[1]) for r in export_rows(written[1])[1:]]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == EXPORT + timedelta(days=1)
    assert sum(d > EXPORT for d in dates) == 1
    assert dates[-1] >= START


# --- Answer key ---


def test_answer_key_lines_up_with_the_export(written: tuple[str, Path, Path]) -> None:
    pid, export, key = written
    data, truth = export_rows(export)[1:], key_rows(key)
    assert len(truth) == len(data)
    for i, (r, k) in enumerate(zip(data, truth, strict=True), start=1):
        assert int(k["row"]) == i
        assert k["date"] == r[1] and int(k["amount_cents"]) == cents(r[5])
        assert k["txn_type"] == r[2]
    header = key.read_text(encoding="utf-8").splitlines()[:2]
    assert f"# opening_balance_cents={PERSONAS[pid].opening_balance_cents}" in header
    assert f"# export_date={EXPORT.isoformat()}" in header


# --- Money formatting and the command ---


@pytest.mark.parametrize(
    ("value", "text"),
    [(-648, "-6.48"), (2_254, "22.54"), (0, "0.00"), (-5, "-0.05"), (-100_000, "-1000.00")],
)
def test_money(value: int, text: str) -> None:
    assert money(value) == text


def test_command_writes_six_files(tmp_path: Path) -> None:
    assert main(["--out", str(tmp_path)]) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted(
        [f"persona_{p}.csv" for p in "ABC"] + [f"persona_{p}_answer_key.csv" for p in "ABC"]
    )
