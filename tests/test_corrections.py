"""Tests for step 8b: corrections made in the app. Synthetic data, temporary files only."""

import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.categorize import Settings, categorize, training_rows
from lookahead.corrections import (
    NO_MERCHANT,
    correct_merchant,
    correct_row,
    corrections,
    merchant_fingerprints,
    to_check,
)
from lookahead.db import connect
from lookahead.labels import current_labels, labelled_examples, load_rows

ACCOUNT = "scotia-chequing"
SETTINGS = Settings(threshold=0.5, synthetic_months=4)
DAY = date(2026, 10, 3)


@pytest.fixture
def conn(db: Path, tmp_path: Path) -> sqlite3.Connection:
    c = connect(Path(shutil.copy(db, tmp_path / "t.db")))
    categorize(c, ACCOUNT, SETTINGS, date(2026, 10, 2))
    return c


def stored(conn: sqlite3.Connection, fingerprint: str) -> tuple:
    return tuple(
        conn.execute(
            "SELECT category, source, flagged FROM category_predictions WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
    )


def a_row(conn: sqlite3.Connection, where: str) -> tuple[str, str]:
    """(fingerprint, merchant) of one stored row matching a SQL condition."""
    return tuple(
        conn.execute(
            f"SELECT fingerprint, merchant FROM category_predictions WHERE {where} LIMIT 1"
        ).fetchone()
    )


def test_a_single_row_correction_shows_at_once(conn: sqlite3.Connection) -> None:
    fingerprint, _ = a_row(conn, "category = 'Groceries' AND source = 'your label'")
    correct_row(conn, ACCOUNT, fingerprint, "Shopping", DAY)
    assert stored(conn, fingerprint) == ("Shopping", "your label", 0)
    assert corrections(conn, ACCOUNT)[fingerprint] == ("Shopping", "row")


def test_a_rule_decided_row_can_be_corrected_one_row_at_a_time(conn: sqlite3.Connection) -> None:
    # an outgoing e-Transfer that was really a gift (allowed for a transfer)
    fingerprint, _ = a_row(conn, "flow_type = 'transfer' AND source = 'rule'")
    correct_row(conn, ACCOUNT, fingerprint, "Gifts & donations", DAY)
    assert stored(conn, fingerprint)[:2] == ("Gifts & donations", "your label")
    categorize(conn, ACCOUNT, SETTINGS, DAY)  # a later run keeps the correction
    assert stored(conn, fingerprint)[:2] == ("Gifts & donations", "your label")


def test_a_category_that_contradicts_the_flow_type_is_refused(conn: sqlite3.Connection) -> None:
    fingerprint, _ = a_row(conn, "flow_type = 'everyday' AND category = 'Groceries'")
    with pytest.raises(ValueError, match="does not fit"):
        correct_row(conn, ACCOUNT, fingerprint, "Income", DAY)
    with pytest.raises(ValueError, match="unknown category"):
        correct_row(conn, ACCOUNT, fingerprint, "Fun", DAY)
    assert fingerprint not in corrections(conn, ACCOUNT)  # nothing stored


def test_a_merchant_correction_covers_its_rows_but_not_single_row_ones(
    conn: sqlite3.Connection,
) -> None:
    fingerprint, merchant = a_row(conn, "source = 'your label' AND merchant IS NOT NULL")
    covered = merchant_fingerprints(conn, ACCOUNT, merchant)
    assert fingerprint in covered
    correct_row(conn, ACCOUNT, fingerprint, "Entertainment", DAY)
    assert correct_merchant(conn, ACCOUNT, merchant, "Shopping", DAY) == len(covered)
    for f in covered:
        expected = "Entertainment" if f == fingerprint else "Shopping"
        assert stored(conn, f) == (expected, "your label", 0)
    # rows of other merchants are untouched
    others = conn.execute(
        "SELECT COUNT(*) FROM category_predictions WHERE detail = 'corrected by you'"
    ).fetchone()[0]
    assert others == len(covered)


def test_corrections_never_change_the_evaluation_labels(conn: sqlite3.Connection) -> None:
    before_labels = current_labels(conn, ACCOUNT)
    before_holdout = labelled_examples(conn, ACCOUNT, ("test_a", "test_b"))[
        ["fingerprint", "label"]
    ]
    rows = load_rows(conn, ACCOUNT)
    group = rows.query("split == 'test_b'")["group"].iloc[0]
    assert correct_merchant(conn, ACCOUNT, group, "Education", DAY) > 0
    assert current_labels(conn, ACCOUNT) == before_labels
    after = labelled_examples(conn, ACCOUNT, ("test_a", "test_b"))[["fingerprint", "label"]]
    assert after.equals(before_holdout)


def test_holdout_corrections_change_the_display_only(conn: sqlite3.Connection) -> None:
    rows = load_rows(conn, ACCOUNT)
    holdout = rows.query("split == 'test_b'")
    group = holdout["group"].iloc[0]
    before = training_rows(conn, ACCOUNT)
    correct_merchant(conn, ACCOUNT, group, "Education", DAY)
    for f in holdout.loc[holdout["group"] == group, "fingerprint"]:
        assert stored(conn, f)[0] == "Education"  # shown
    after = training_rows(conn, ACCOUNT)
    assert after[["fingerprint", "label"]].equals(before[["fingerprint", "label"]])  # not trained
    assert not set(after["fingerprint"]) & set(rows.query("split != 'dev'")["fingerprint"])


def test_development_corrections_reach_training(conn: sqlite3.Connection) -> None:
    rows = load_rows(conn, ACCOUNT)
    dev = rows.query("split == 'dev'")
    group = dev["group"].iloc[0]
    correct_merchant(conn, ACCOUNT, group, "Education", DAY)
    training = training_rows(conn, ACCOUNT).set_index("fingerprint")["label"]
    for f in dev.loc[dev["group"] == group, "fingerprint"]:
        assert training[f] == "Education"


def test_rows_imported_after_the_freeze_join_training_once_corrected(
    conn: sqlite3.Connection,
) -> None:
    rows = load_rows(conn, ACCOUNT)
    late = rows.query("split == 'test_a'")["fingerprint"].iloc[0]
    with conn:  # as if this row had arrived after the splits were frozen: it has no split
        conn.execute("DELETE FROM label_splits WHERE fingerprint = ?", (late,))
        conn.execute("DELETE FROM category_labels WHERE fingerprint = ?", (late,))
    assert late not in set(training_rows(conn, ACCOUNT)["fingerprint"])  # no label yet
    correct_row(conn, ACCOUNT, late, "Shopping", DAY)
    training = training_rows(conn, ACCOUNT).set_index("fingerprint")["label"]
    assert training[late] == "Shopping"


def test_corrections_are_dated_for_a_later_replay(conn: sqlite3.Connection) -> None:
    fingerprint, _ = a_row(conn, "category = 'Groceries' AND source = 'your label'")
    correct_row(conn, ACCOUNT, fingerprint, "Shopping", date(2026, 10, 3))
    correct_row(conn, ACCOUNT, fingerprint, "Entertainment", date(2026, 10, 9))
    assert corrections(conn, ACCOUNT, as_of=date(2026, 10, 2)) == {}
    assert corrections(conn, ACCOUNT, as_of=date(2026, 10, 5))[fingerprint][0] == "Shopping"
    assert corrections(conn, ACCOUNT)[fingerprint][0] == "Entertainment"


def test_the_to_check_list_is_least_confident_first(conn: sqlite3.Connection) -> None:
    with conn:  # two made-up uncertain rows
        conn.execute(
            "UPDATE category_predictions SET flagged = 1, confidence = 0.3, source = 'model'"
            " WHERE fingerprint IN (SELECT fingerprint FROM category_predictions"
            " WHERE merchant IS NOT NULL AND source = 'your label' LIMIT 1)"
        )
        conn.execute(
            "UPDATE category_predictions SET flagged = 1, confidence = NULL, source = 'none'"
            " WHERE fingerprint IN (SELECT fingerprint FROM category_predictions"
            " WHERE merchant IS NULL LIMIT 1)"
        )
    queue = to_check(conn, ACCOUNT)
    assert list(queue["to_check"]) == [1, 1]
    assert queue["merchant"].iloc[0] != NO_MERCHANT and queue["confidence"].iloc[0] == 0.3
    assert queue["merchant"].iloc[1] == NO_MERCHANT
    everything = to_check(conn, ACCOUNT, flagged_only=False)
    assert len(everything) > 2 and everything["rows"].sum() == 634  # persona C (2026-11-18)
