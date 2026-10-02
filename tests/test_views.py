"""Tests for the read-only query layer behind the app pages."""

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.views import accounts, import_status, transaction_types, transactions

START, EXPORT = date(2026, 6, 1), date(2026, 9, 30)
IMPORTED = datetime(2026, 10, 2, 9, 30)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Personas A and C, each under its own account."""
    conn = connect(tmp_path / "db" / "t.db")
    for pid in ("A", "C"):
        export, _ = write_persona(
            PERSONAS[pid], tmp_path / pid, seed=42, start=START, export_date=EXPORT
        )
        store_exports(conn, f"synthetic-{pid}", [(export.name, parse_export(export))], IMPORTED)
    return conn


def n_rows(db: sqlite3.Connection, account: str) -> int:
    return db.execute("SELECT COUNT(*) FROM transactions WHERE account = ?", (account,)).fetchone()[
        0
    ]


# --- Accounts and types ---


def test_accounts_are_listed_sorted(db: sqlite3.Connection) -> None:
    assert accounts(db) == ["synthetic-A", "synthetic-C"]


def test_transaction_types_for_one_account(db: sqlite3.Connection) -> None:
    types = transaction_types(db, "synthetic-C")
    assert "credit memo" in types and "pos purchase" in types
    assert types == sorted(types)
    assert "customer transfer dr." not in types  # that is persona B's


# --- Transactions ---


def test_all_rows_newest_first(db: sqlite3.Connection) -> None:
    df = transactions(db, "synthetic-C")
    assert len(df) == n_rows(db, "synthetic-C")
    assert list(df.columns) == [
        "date",
        "type",
        "text",
        "amount_cents",
        "balance_cents",
        "source_file",
        "merchant",
        "flow_type",
        "category",
        "source",
        "flagged",
        "fingerprint",
    ]
    assert df["category"].isna().all() and not df["flagged"].any()  # not categorized yet
    assert df["date"].is_monotonic_decreasing


def test_column_types_are_pandas_3_native(db: sqlite3.Connection) -> None:
    df = transactions(db, "synthetic-C")
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert df["amount_cents"].dtype == "int64" and df["balance_cents"].dtype == "int64"
    # pandas 3: text columns use the dedicated string dtype, not the old `object`.
    assert pd.api.types.is_string_dtype(df["text"]) and df["text"].dtype != object


def test_raw_text_is_returned_unchanged(db: sqlite3.Connection) -> None:
    df = transactions(db, "synthetic-C")
    assert (df["text"].str.endswith(" ") | df["text"].str.match(r"Mb-.* \d{8}$")).all()


def test_accounts_are_kept_apart(db: sqlite3.Connection) -> None:
    assert len(transactions(db, "synthetic-A")) == n_rows(db, "synthetic-A")
    assert "credit memo" not in set(transactions(db, "synthetic-A")["type"])


def test_date_range_is_inclusive(db: sqlite3.Connection) -> None:
    df = transactions(db, "synthetic-C", start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert len(df) > 0
    assert df["date"].min() >= pd.Timestamp(2026, 7, 1) and df["date"].max() <= pd.Timestamp(
        2026, 7, 31
    )
    everything = transactions(db, "synthetic-C")
    in_july = everything[
        (everything["date"] >= "2026-07-01") & (everything["date"] <= "2026-07-31")
    ]
    assert len(df) == len(in_july)


def test_text_search_ignores_case(db: sqlite3.Connection) -> None:
    df = transactions(db, "synthetic-C", text="uber canada")
    assert len(df) > 0 and df["text"].str.contains("Uber Canada").all()


def test_type_filter(db: sqlite3.Connection) -> None:
    df = transactions(db, "synthetic-C", types=["credit memo", "bill payment"])
    assert set(df["type"]) == {"credit memo", "bill payment"}


def test_filters_combine_and_empty_results_keep_columns(db: sqlite3.Connection) -> None:
    df = transactions(db, "synthetic-C", text="uber", types=["credit memo"])
    assert df.empty and list(df.columns)[:3] == ["date", "type", "text"]


def test_search_text_is_not_sql(db: sqlite3.Connection) -> None:
    # Search input is passed as a parameter, never pasted into the SQL.
    assert transactions(db, "synthetic-C", text="'; DROP TABLE transactions; --").empty
    assert n_rows(db, "synthetic-C") > 0


# --- Import status ---


def test_import_status_of_a_clean_import(db: sqlite3.Connection) -> None:
    status = import_status(db, "synthetic-C")
    assert status.rows == n_rows(db, "synthetic-C")
    assert status.first_date is not None and status.first_date >= date(2026, 6, 1)
    assert status.last_date == date(2026, 10, 1)  # the row dated after the export date
    assert status.last_import == "2026-10-02T09:30:00"
    assert status.reconciled and status.breaks == [] and status.anchors_checked > 0


def test_import_status_shows_a_break(db: sqlite3.Connection) -> None:
    victim = db.execute(
        "SELECT id FROM transactions WHERE account = 'synthetic-C' AND amount_cents != 0"
        " AND posted_date BETWEEN '2026-08-01' AND '2026-08-31' LIMIT 1"
    ).fetchone()[0]
    db.execute("DELETE FROM transactions WHERE id = ?", (victim,))
    status = import_status(db, "synthetic-C")
    assert not status.reconciled and len(status.breaks) == 1


def test_import_status_of_an_empty_account(db: sqlite3.Connection) -> None:
    status = import_status(db, "nobody")
    assert status.rows == 0 and status.first_date is None and status.reconciled


@pytest.mark.parametrize("wildcard", ["%", "_", "\\"])
def test_like_wildcards_are_searched_literally(db: sqlite3.Connection, wildcard: str) -> None:
    # Unescaped, "%" would match every row and "_" any single character.
    assert transactions(db, "synthetic-C", text=wildcard).empty


# --- What recurring series change on the Transactions page ---


def test_series_rows_show_recurring_outflow_and_the_series_category(db: sqlite3.Connection) -> None:
    from lookahead.categorize import Settings, categorize
    from lookahead.recurring_store import confirm, refresh, set_category

    account = "synthetic-A"
    categorize(db, account, Settings(threshold=0.5, synthetic_months=4))
    before = transactions(db, account).set_index("fingerprint")
    series = refresh(db, account)
    card = next(s for s in series if s.kind == "merchant" and s.direction == "out"
                and before.loc[s.evidence[0], "flow_type"] == "everyday")  # fmt: skip
    after = transactions(db, account).set_index("fingerprint")
    assert set(after.loc[list(card.evidence), "flow_type"]) == {"recurring_outflow"}
    others = [f for f in before.index if f not in {e for s in series for e in s.evidence}]
    assert after.loc[others, "flow_type"].equals(before.loc[others, "flow_type"])
    assert after.loc[others, "category"].equals(before.loc[others, "category"])
    rent = next(s for s in series if s.kind == "e-transfer")  # a candidate: nothing changes
    assert set(after.loc[list(rent.evidence), "flow_type"]) == {"transfer"}

    confirm(db, account, card)
    set_category(db, account, card.key, "Entertainment")
    confirm(db, account, rent)
    refresh(db, account)
    shown = transactions(db, account).set_index("fingerprint")
    assert set(shown.loc[list(card.evidence), "category"]) == {"Entertainment"}
    assert set(shown.loc[list(card.evidence), "source"]) == {"series"}
    assert set(shown.loc[list(rent.evidence), "flow_type"]) == {"recurring_outflow"}  # confirmed
    only = transactions(db, account, categories=["Entertainment"])
    assert set(card.evidence) <= set(only["fingerprint"])  # the filter sees what is shown
    assert set(only["category"]) == {"Entertainment"}
