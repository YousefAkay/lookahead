"""Building scrub patterns from bank files (tools/scrub_patterns.py), on made-up files only.

Every name, number and amount below is invented for this test.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from lookahead.db import connect
from tools.scrub_patterns import build_patterns

HEADER = '"Filter","Date","Description","Sub-description","Type of Transaction","Amount","Balance"'
ROWS = [
    ("2026-10-05", "credit memo", "1/Zorblax Quintworth  ", "Credit", "750.00", "1000.00"),
    ("2026-10-04", "withdrawal", "Free Interac E-Transfer ", "Debit", "-150.00", "250.00"),
    ("2026-10-03", "bill payment", "Mb-Glim Power Co 12345678", "Debit", "-120.00", "400.00"),
    ("2026-10-02", "bill payment", "Mb-Orrin Tadmuller 87654321", "Debit", "-40.00", "520.00"),
    ("2026-10-01", "pos purchase", "Apos Maxi #6438          Montr ", "Debit", "-35.05", "560.00"),
]
NAMES = ("zorblax quintworth", "orrin tadmuller", "velma kroostad")


def export(folder: Path, name: str = "Statement_0098765432.csv") -> Path:
    lines = [HEADER]
    for k, (day, desc, sub, kind, amount, balance) in enumerate(ROWS):
        first = "All available transactions (up to 2 years)" if k == 0 else ""
        lines.append(f'"{first}","{day}","{desc}","{sub}","{kind}","{amount}","{balance}"')
    path = folder / name
    path.write_text("﻿" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def database(folder: Path) -> Path:
    path = folder / "made_up.db"
    conn = connect(path)
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES ('acct', '2026-09-30', 25000, 'credit memo', '1/Velma Kroostad ', 0,"
            " 'f1', 'x', ?)",
            (datetime(2026, 10, 1).isoformat(),),
        )
    conn.close()
    return path


def allowlist(folder: Path) -> Path:
    path = folder / "allow.txt"
    path.write_text("# known businesses\nGlim Power Co\n", encoding="utf-8")
    return path


def test_patterns_come_from_the_bank_files_and_the_database(tmp_path: Path) -> None:
    exports = tmp_path / "raw"
    exports.mkdir()
    export(exports)
    pats = build_patterns(exports, database(tmp_path), allowlist(tmp_path))
    assert set(NAMES) <= set(pats.text["etransfer"])
    assert "glim power co" not in pats.text["etransfer"]  # a known business
    assert "free interac e-transfer" not in pats.text["etransfer"]
    assert {75_000, 15_000, 12_000, 25_000} <= pats.amounts  # every amount over $100
    assert 4_000 not in pats.amounts and 3_505 not in pats.amounts  # $100 or less
    assert {"0098765432", "12345678", "87654321"} <= set(pats.text["account"])
    assert "maxi" in pats.text["merchant"]  # my card merchants (to split brand names)
    assert "orrin tadmuller" not in pats.text["merchant"]  # a payee is not a card merchant


def test_the_command_prints_counts_only_and_never_a_value(tmp_path: Path) -> None:
    exports = tmp_path / "raw"
    exports.mkdir()
    export(exports)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "me@example.net"],
                 ["config", "user.name", "Me"]):  # fmt: skip
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "notes.md").write_text("Paid Orrin Tadmuller 750.00 to 0098765432.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"], check=True)
    extra = tmp_path / "extra.txt"
    extra.write_text("address: 99 Quimby Lane\n", encoding="utf-8")
    out = tmp_path / "report.md"
    done = subprocess.run(
        [sys.executable, "-m", "tools.scrub_patterns", "--exports", str(exports),
         "--db", str(database(tmp_path)), "--allowlist", str(allowlist(tmp_path)),
         "--extra", str(extra), "--repo", str(repo), "--tip", "main",
         "--public-email", "me@example.net", "--out", str(out)],
        capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parents[1],
    )  # fmt: skip
    printed = done.stdout + done.stderr
    for value in (*NAMES, "Zorblax", "Orrin", "Tadmuller", "0098765432", "12345678", "750",
                  "Quimby", "Glim"):  # fmt: skip
        assert value not in printed, value
        assert value not in out.read_text(encoding="utf-8"), value
    assert "etransfer" in printed and "| etransfer | 1 |" in printed  # notes.md has one
    assert "| account | 1 |" in printed and "| amount | 1 |" in printed
    assert "## 7." in printed and f"written: {out}" in printed


def test_a_file_that_is_not_an_export_is_skipped_by_count_only(tmp_path: Path) -> None:
    exports = tmp_path / "raw"
    exports.mkdir()
    export(exports)
    (exports / "notes.csv").write_text("not,an,export\nOrrin Tadmuller,1,2\n", encoding="utf-8")
    pats = build_patterns(exports, None, None)
    assert "zorblax quintworth" in pats.text["etransfer"]
