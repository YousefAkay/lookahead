"""Tests for the app's helpers: demo database, display table, daily balance, --glob."""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from lookahead.demo import DEMO_ACCOUNTS, build_demo_db
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.__main__ import main as ingest_main
from lookahead.pseudonymize import token
from lookahead.ui import daily_balance, display_table

KEY = bytes(range(32))


@pytest.fixture
def synthetic_dir(tmp_path: Path) -> Path:
    """The three synthetic exports plus their answer keys, like data/synthetic/."""
    out = tmp_path / "synthetic"
    for pid in "ABC":
        write_persona(
            PERSONAS[pid], out, seed=42, start=date(2026, 6, 1), export_date=date(2026, 9, 30)
        )
    return out


def counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT account, COUNT(*) FROM transactions GROUP BY account").fetchall()
    conn.close()
    return dict(rows)


# --- Demo database ---


def test_demo_db_holds_each_persona_as_an_account(synthetic_dir: Path, tmp_path: Path) -> None:
    db = tmp_path / "demo.db"
    build_demo_db(db, synthetic_dir)
    assert (
        sorted(counts(db)) == list(DEMO_ACCOUNTS) == ["synthetic-A", "synthetic-B", "synthetic-C"]
    )


def test_building_the_demo_db_twice_adds_nothing(synthetic_dir: Path, tmp_path: Path) -> None:
    db = tmp_path / "demo.db"
    build_demo_db(db, synthetic_dir)
    first = counts(db)
    build_demo_db(db, synthetic_dir)
    assert counts(db) == first


# --- --glob option of the import command ---


def test_glob_imports_only_matching_files(synthetic_dir: Path, tmp_path: Path) -> None:
    # The folder also holds answer keys, which are not bank exports.
    args = [str(synthetic_dir), "--db", str(tmp_path / "g.db"), "--account", "synthetic-C"]
    assert ingest_main([*args, "--glob", "persona_C.csv"]) == 0
    assert list(counts(tmp_path / "g.db")) == ["synthetic-C"]


def test_default_glob_stops_on_the_answer_keys(synthetic_dir: Path, tmp_path: Path) -> None:
    assert ingest_main([str(synthetic_dir), "--db", str(tmp_path / "all.db")]) == 2


# --- Display table ---


def sample() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-02", "2026-09-02", "2026-09-01"]),
            "type": ["credit memo", "pos purchase", "bill payment"],
            "text": [
                "1/Nadia Haddad ",
                "Apos Maxi #350           Montr ",
                "Mb-Concordia University 12345678",
            ],
            "amount_cents": [300_000, -648, -725_000],
            "balance_cents": [310_000, 10_000, 10_648],
            "source_file": ["f", "f", "f"],
        }
    )


def test_display_table_shows_dollars_and_dates() -> None:
    table = display_table(sample(), key=None)
    assert list(table.columns) == ["Date", "Type", "Text", "Amount ($)", "Balance ($)"]
    assert table["Date"].tolist() == ["2026-09-02", "2026-09-02", "2026-09-01"]
    assert table["Amount ($)"].tolist() == [3000.00, -6.48, -7250.00]
    assert table["Text"].iloc[0] == "1/Nadia Haddad "  # raw when not pseudonymized


def test_display_table_pseudonymizes_with_a_key() -> None:
    table = display_table(sample(), key=KEY)
    assert table["Text"].iloc[0] == f"1/{token('PERSON', 'Nadia Haddad', KEY)} "
    assert table["Text"].iloc[1] == "Apos Maxi #350           Montr "  # businesses unchanged
    assert "12345678" not in table["Text"].iloc[2]


def test_display_table_of_nothing() -> None:
    assert display_table(sample().iloc[0:0], key=KEY).empty


# --- Daily balance for the chart ---


def test_daily_balance_is_the_last_posting_of_each_day() -> None:
    daily = daily_balance(sample())
    assert daily["date"].tolist() == [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02")]
    assert daily["balance ($)"].tolist() == [106.48, 3100.00]  # newest row of Sep 2 comes first


# --- UI fixes: step chart, dash for blank text ---


def test_balance_chart_is_a_step_line() -> None:
    from lookahead.ui import balance_chart

    figure = balance_chart(daily_balance(sample()))
    # Balances jump at postings and stay flat between them: horizontal, then vertical.
    assert figure.data[0].line.shape == "hv"


def test_blank_bank_text_shows_a_dash() -> None:
    blank = sample()
    blank.loc[1, "text"] = " "  # service charges and ABM withdrawals have a single space
    for key in (None, KEY):
        assert display_table(blank, key=key)["Text"].iloc[1] == "—"


# --- Label review table ---


def test_review_table_shows_split_and_label() -> None:
    from lookahead.labels import Label
    from lookahead.ui import review_table

    rows = pd.DataFrame(
        {
            "posted_date": [date(2026, 9, 2), date(2026, 9, 1)],
            "txn_type": ["pos purchase", "credit memo"],
            "text": ["Apos Maxi #350           Montr ", "1/Nadia Haddad "],
            "amount_cents": [-648, 300_000],
            "split": ["dev", "test_b"],
            "fingerprint": ["f1", "f2"],
        }
    )
    labels = {"f1": Label("Groceries", "labelled")}
    table = review_table(rows, labels, key=KEY)
    assert table["Label"].tolist() == ["Groceries", "—"]
    assert table["Split"].tolist() == ["development", "holdout (Test B)"]
    assert table["Amount ($)"].tolist() == [-6.48, 3000.0]
    assert "Nadia" not in table["Text"].iloc[1]  # pseudonymized with a key
    skipped = review_table(rows, {"f1": Label(None, "skipped")}, key=None)
    assert skipped["Label"].iloc[0] == "skipped" and "Nadia" in skipped["Text"].iloc[1]
