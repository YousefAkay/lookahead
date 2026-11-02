"""The counts-only text check (lookahead/ingest/textcheck.py): where a "?" in merchant text comes
from. Made-up exports only."""

from datetime import datetime
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.ingest.textcheck import count_marks, main, marks

HEADER = '"Filter","Date","Description","Sub-description","Type of Transaction","Amount","Balance"'


def export(path: Path, subs: list[str], encoding: str = "utf-8") -> Path:
    balance, lines = 100_000, [HEADER]
    for i, sub in enumerate(subs):
        lines.append(f'"","2026-09-{28 - i:02d}","pos purchase","{sub}","Debit","-1.00",'
                     f'"{balance / 100:.2f}"')  # fmt: skip
        balance += 100
    path.write_bytes("\r\n".join(lines).encode(encoding))
    return path


@pytest.mark.parametrize(("text", "found"), [
    ("Apos Café Montréal", {"accented letter"}),
    ("Apos Caf? Montr?al", {"? touching a letter"}),
    ("Apos Caf�", {"replacement character"}),
    ("Apos Example Cafe #12 ?", set()),  # a "?" on its own is not a lost letter
    ("Apos Example Cafe 2?3", set()),
])  # fmt: skip
def test_the_marks_of_a_piece_of_text(text: str, found: set[str]) -> None:
    assert marks(text) == found


def test_counts_are_per_row() -> None:
    c = count_marks(["Café Québec", "Montr?al", "Plain"])
    assert (c["rows"], c["accented letter"], c["? touching a letter"]) == (3, 1, 1)


def test_the_report_counts_files_and_stored_rows_and_never_prints_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    older = export(raw / "a.csv", ["Apos Café Montréal", "Apos Plain Shop"], "cp1252")
    export(raw / "b.csv", ["Apos Caf? Montr?al", "Apos Other Shop"])
    conn = connect(tmp_path / "t.db")
    store_exports(conn, "acct", [("a", parse_export(older))], datetime(2026, 10, 1, 9))
    conn.close()
    assert main([str(raw), "--db", str(tmp_path / "t.db")]) == 0
    out = capsys.readouterr().out
    assert "file 1 (Windows-1252): 2 rows; accented letter 1, ? touching a letter 0" in out
    assert "file 2 (UTF-8): 2 rows; accented letter 0, ? touching a letter 1" in out
    assert "stored, imported 2026-10-01: 2 rows; accented letter 1" in out
    assert "Montr" not in out and "Caf" not in out
