"""The counts-only check of the Scenario page's daily spend (lookahead/scenario_check.py): it
prints counts and shares only, never amounts, merchant text, dates or account names, and it
opens the database read-only. Made-up rows in a temporary database."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.demo import build_demo_db
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.recurring_store import refresh
from lookahead.scenario_check import DOMINANT_SHARE, counted_spend, largest_shares, main
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


# --- No single row may dominate the counted daily spend (my real-data check of 2026-11-17) ---


def test_largest_shares() -> None:
    assert largest_shares([100, 300, 600], top=(1, 2)) == {1: 0.6, 2: 0.9}
    assert largest_shares([], top=(1,)) == {1: 0.0}


def test_a_big_occasional_bill_does_not_dominate_the_counted_spend(tmp_path: Path) -> None:
    """The case my diagnostic found: a big bill payment typed recurring outflow, in no series,
    was 72.1% of the counted total. Now it is left out, and no row passes the line."""
    path = tmp_path / "b.db"
    c = connect(path)
    window(c)
    for k in range(2, 80, 3):
        insert(c, days_back(k), -2_500, "Opos Grocer", flow="everyday")
    insert(c, days_back(45), -480_000, "Tax Office", kind="bill payment",
           flow="recurring_outflow")  # fmt: skip
    counted = counted_spend(c, ACCOUNT, refresh(c, ACCOUNT))
    c.close()
    assert 480_000 not in counted
    assert largest_shares(counted)[1] <= DOMINANT_SHARE


def test_the_warning_shows_when_one_row_dominates(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """In the fixture, the one-off $123.45 purchase is about two thirds of the counted total
    but under the $500 floor of a big one-off payment, so it stays counted: the check warns."""
    assert main(["--db", str(db)]) == 0
    assert "WARNING: one counted row is more than 25%" in capsys.readouterr().out


@pytest.mark.slow
def test_no_row_dominates_the_counted_spend_of_the_synthetic_personas(tmp_path: Path) -> None:
    """Personas A, B and C (the demo personas, not held out), as the demo builds them."""
    for pid in "ABC":
        write_persona(PERSONAS[pid], tmp_path / "csv", seed=42, start=date(2026, 6, 1),
                      export_date=date(2026, 9, 30))  # fmt: skip
    build_demo_db(tmp_path / "demo.db", tmp_path / "csv")
    conn = connect(tmp_path / "demo.db")
    accounts = [r[0] for r in conn.execute("SELECT DISTINCT account FROM transactions")]
    assert len(accounts) == 3
    for account in accounts:
        counted = counted_spend(conn, account, refresh(conn, account))
        assert counted, account
        assert largest_shares(counted)[1] <= DOMINANT_SHARE, account
    conn.close()


def test_it_says_where_the_income_comes_from(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """My fourth real-data check: a solid income from 5 deposits needs its sources shown."""
    assert main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "Counted deposits, by flow-typing reason (bank's type)" in out
    assert "Counted deposits, by stored flow type (its source)" in out
    assert "Income per month = the counted deposits over 90 days, times 30.44 / 90" in out
    assert "Acme" not in out and "1500" not in out
