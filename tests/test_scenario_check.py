"""The counts-only check of the Scenario page's daily spend (lookahead/scenario_check.py): it
prints counts and shares only, never amounts, merchant text, dates or account names, and it
opens the database read-only. Made-up rows in a temporary database."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.scenario_check import main
from tests.test_scenario_prefill import ACCOUNT, LAST, days_back, insert, window


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    c = connect(path)
    window(c)
    insert(c, days_back(50), -12_345, "Opos Secret Bakery", flow="everyday")
    insert(c, days_back(40), -9_000, "Free Interac E-Transfer Jane", kind="withdrawal",
           flow="transfer")  # fmt: skip
    insert(c, days_back(20), -300_000, "Customer Transfer Dr. MB-Transfer To 1234",
           kind="customer transfer dr.", flow="transfer")  # fmt: skip
    insert(c, days_back(10), 150_000, "Payroll Dep Acme", kind="deposit", flow="income")
    insert(c, LAST, -100, "Opos Last", flow="everyday", balance=777_777)
    c.close()
    return path


def test_it_prints_counts_and_shares_only(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "What the spend rule did with them" in out
    assert "transfers between your own accounts" in out
    assert "card purchase" in out and "outgoing e-Transfer" in out
    for secret in ("Bakery", "Jane", "Acme", "1234", "123.45", "12345", "7777", "$",
                   ACCOUNT, LAST.isoformat(), "2026-06", "2026-05", "2026-04"):  # fmt: skip
        assert secret not in out, secret


def test_it_does_not_change_the_database(db: Path) -> None:
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert main(["--db", str(db)]) == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_more_than_one_account_asks_which(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    c = connect(db)
    with c:
        c.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES ('other', ?, -100, 'pos purchase', 'Opos X', 0, 'fpx', 'f', '2026-10-01')",
            (date(2026, 6, 1).isoformat(),),
        )
    c.close()
    assert main(["--db", str(db)]) == 2
    assert "--account 1: " in capsys.readouterr().out
    assert main(["--db", str(db), "--account", "2"]) == 0


def test_a_missing_database_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "nothing.db"
    assert main(["--db", str(missing)]) == 2
    assert not missing.exists()
