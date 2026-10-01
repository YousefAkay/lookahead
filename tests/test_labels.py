"""Tests for frozen splits, stored labels and the review queue (step 6).

Temporary databases from synthetic personas only: never data/private/.
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.labels import (
    AlreadyFrozen,
    current_labels,
    freeze_splits,
    label_merchant,
    label_row,
    labelled_examples,
    load_rows,
    main,
    progress,
    review_queue,
    skip_merchant,
    suggestion_model,
)

ACCOUNT = "synthetic-C"
TODAY = date(2026, 10, 2)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    export, _ = write_persona(
        PERSONAS["C"], tmp_path / "s", 42, date(2026, 1, 1), date(2026, 9, 30)
    )
    c = connect(tmp_path / "db" / "t.db")
    store_exports(c, ACCOUNT, [(export.name, parse_export(export))], datetime(2026, 10, 2))
    c.commit()
    return c


@pytest.fixture
def frozen(conn: sqlite3.Connection) -> sqlite3.Connection:
    freeze_splits(conn, ACCOUNT, seed=7, today=TODAY)
    return conn


# --- Freezing the splits ---


def test_every_categorizer_row_gets_exactly_one_split(frozen: sqlite3.Connection) -> None:
    rows = load_rows(frozen, ACCOUNT)
    assert len(rows) > 0
    assert set(rows["split"]) == {"dev", "test_a", "test_b"}
    assert rows["fingerprint"].is_unique


def test_test_b_takes_whole_merchant_groups(frozen: sqlite3.Connection) -> None:
    rows = load_rows(frozen, ACCOUNT)
    for _, group in rows.groupby("group"):
        splits = set(group["split"])
        assert splits == {"test_b"} or "test_b" not in splits


def test_test_b_is_about_a_quarter_of_the_groups(frozen: sqlite3.Connection) -> None:
    rows = load_rows(frozen, ACCOUNT)
    groups = rows.groupby("group")["split"].first()
    assert (groups == "test_b").sum() == round(0.25 * len(groups))


def test_test_a_is_later_than_every_dev_row(frozen: sqlite3.Connection) -> None:
    rows = load_rows(frozen, ACCOUNT)
    assert (
        rows[rows.split == "test_a"]["posted_date"].min()
        > rows[rows.split == "dev"]["posted_date"].max()
    )


def test_freezing_twice_is_refused(frozen: sqlite3.Connection) -> None:
    before = load_rows(frozen, ACCOUNT)["split"].tolist()
    with pytest.raises(AlreadyFrozen):
        freeze_splits(frozen, ACCOUNT, seed=8, today=TODAY)
    assert load_rows(frozen, ACCOUNT)["split"].tolist() == before


def test_seed_and_date_are_saved(frozen: sqlite3.Connection) -> None:
    info = frozen.execute(
        "SELECT seed, frozen_at FROM label_split_info WHERE account = ?", (ACCOUNT,)
    ).fetchone()
    assert tuple(info) == (7, "2026-10-02")


def test_rows_before_freezing_have_no_split(conn: sqlite3.Connection) -> None:
    assert load_rows(conn, ACCOUNT).empty


# --- Labels ---


def first_group(conn: sqlite3.Connection, split: str | None = None) -> str:
    rows = load_rows(conn, ACCOUNT)
    if split:
        rows = rows[rows.split == split]
    return rows.groupby("group").size().sort_values(ascending=False).index[0]


def test_one_decision_labels_every_row_of_the_merchant(frozen: sqlite3.Connection) -> None:
    group = first_group(frozen)
    n = label_merchant(frozen, ACCOUNT, group, "Groceries", TODAY)
    rows = load_rows(frozen, ACCOUNT)
    mine = rows[rows.group == group]
    assert n == len(mine)
    labels = current_labels(frozen, ACCOUNT)
    assert {labels[f].category for f in mine["fingerprint"]} == {"Groceries"}


def test_a_single_row_override_wins_and_survives_a_later_merchant_label(
    frozen: sqlite3.Connection,
) -> None:
    group = first_group(frozen)
    fps = load_rows(frozen, ACCOUNT).query("group == @group")["fingerprint"].tolist()
    label_merchant(frozen, ACCOUNT, group, "Shopping", TODAY)
    label_row(frozen, ACCOUNT, fps[0], "Subscriptions", TODAY)
    label_merchant(frozen, ACCOUNT, group, "Groceries", TODAY)  # later, whole merchant
    labels = current_labels(frozen, ACCOUNT)
    assert labels[fps[0]].category == "Subscriptions"
    assert {labels[f].category for f in fps[1:]} == {"Groceries"}


def test_skip_marks_rows_skipped(frozen: sqlite3.Connection) -> None:
    group = first_group(frozen)
    skip_merchant(frozen, ACCOUNT, group, TODAY)
    labels = current_labels(frozen, ACCOUNT)
    fps = load_rows(frozen, ACCOUNT).query("group == @group")["fingerprint"]
    assert {labels[f].status for f in fps} == {"skipped"}
    assert {labels[f].category for f in fps} == {None}


def test_unknown_category_is_refused(frozen: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown category"):
        label_merchant(frozen, ACCOUNT, first_group(frozen), "Snacks", TODAY)


def test_labels_are_dated(frozen: sqlite3.Connection) -> None:
    label_merchant(frozen, ACCOUNT, first_group(frozen), "Groceries", TODAY)
    dates = {r[0] for r in frozen.execute("SELECT decided_at FROM category_labels")}
    assert dates == {"2026-10-02"}


def test_labelled_examples_exclude_skipped_rows_and_other_splits(
    frozen: sqlite3.Connection,
) -> None:
    dev = first_group(frozen, "dev")
    label_merchant(frozen, ACCOUNT, dev, "Groceries", TODAY)
    holdout = first_group(frozen, "test_b")
    label_merchant(frozen, ACCOUNT, holdout, "Shopping", TODAY)
    # The merchant also has Test A rows (it spans the time cutoff): those stay out.
    fps = load_rows(frozen, ACCOUNT).query("group == @dev and split == 'dev'")["fingerprint"]
    label_row(frozen, ACCOUNT, fps.iloc[0], None, TODAY)  # skip one row
    ex = labelled_examples(frozen, ACCOUNT, splits=("dev",))
    assert set(ex["group"]) == {dev} and len(ex) == len(fps) - 1
    assert set(ex["label"]) == {"Groceries"}
    assert set(labelled_examples(frozen, ACCOUNT, splits=("test_b",))["group"]) == {holdout}


# --- Review queue and progress ---


def test_queue_starts_with_the_biggest_merchants(frozen: sqlite3.Connection) -> None:
    queue = review_queue(frozen, ACCOUNT)
    sizes = [t.rows for t in queue]
    assert sizes == sorted(sizes, reverse=True)
    assert not any(t.done for t in queue)


def test_holdout_merchants_are_shown_blind(frozen: sqlite3.Connection) -> None:
    rows = load_rows(frozen, ACCOUNT)
    holdout_groups = set(rows[rows.split != "dev"]["group"])
    for task in review_queue(frozen, ACCOUNT, suggestion_model(frozen, ACCOUNT)):
        assert task.blind == (task.group in holdout_groups)
        if task.blind:
            assert task.suggestion is None and task.confidence is None


def test_labelled_merchants_are_done_and_go_last(frozen: sqlite3.Connection) -> None:
    group = first_group(frozen)
    label_merchant(frozen, ACCOUNT, group, "Groceries", TODAY)
    queue = review_queue(frozen, ACCOUNT)
    assert queue[-1].group == group and queue[-1].done
    assert not queue[0].done


def test_progress_counts_merchants_and_rows(frozen: sqlite3.Connection) -> None:
    total = progress(frozen, ACCOUNT)
    assert total.merchants_done == 0 and total.rows_covered == 0
    group = first_group(frozen)
    n = label_merchant(frozen, ACCOUNT, group, "Groceries", TODAY)
    p = progress(frozen, ACCOUNT)
    assert p.merchants_done == 1 and p.rows_covered == n and p.labelled == n
    assert p.merchants_total == total.merchants_total and p.rows_total == total.rows_total


def make_dev_only(conn: sqlite3.Connection) -> list[str]:
    """Synthetic merchants all span the time cutoff, so none is dev-only (all would be
    shown blind). Move the Test A rows of the dev merchants to dev for these tests."""
    rows = load_rows(conn, ACCOUNT)
    groups = sorted(set(rows[rows.split == "dev"]["group"]))
    fps = rows[rows.group.isin(groups) & (rows.split == "test_a")]["fingerprint"]
    conn.executemany("UPDATE label_splits SET split = 'dev' WHERE fingerprint = ?",
                     [(f,) for f in fps])  # fmt: skip
    conn.commit()
    return groups


def test_a_merchant_spanning_the_time_cutoff_is_shown_blind(frozen: sqlite3.Connection) -> None:
    rows = load_rows(frozen, ACCOUNT)
    mixed = rows.groupby("group")["split"].agg(set)
    spanning = [g for g, s in mixed.items() if s == {"dev", "test_a"}]
    assert spanning
    tasks = {t.group: t for t in review_queue(frozen, ACCOUNT)}
    assert all(tasks[g].blind for g in spanning)


def test_suggestions_come_only_from_dev_labels(frozen: sqlite3.Connection) -> None:
    assert suggestion_model(frozen, ACCOUNT) is None  # nothing labelled yet
    dev_groups = make_dev_only(frozen)
    for group, category in zip(dev_groups[:4], ["Groceries", "Transport"] * 2, strict=True):
        label_merchant(frozen, ACCOUNT, group, category, TODAY)
    holdout = first_group(frozen, "test_b")
    label_merchant(frozen, ACCOUNT, holdout, "Shopping", TODAY)  # must not train the model
    model = suggestion_model(frozen, ACCOUNT)
    assert model is not None
    assert set(model.known) == set(dev_groups[:4])
    queue = review_queue(frozen, ACCOUNT, model)
    open_dev = [t for t in queue if not t.done and not t.blind]
    assert open_dev and all(t.suggestion is not None for t in open_dev)
    confidences = [t.confidence for t in open_dev]
    assert confidences == sorted(confidences)  # least confident first


# --- The freeze command ---


def test_freeze_command_prints_counts_and_refuses_twice(
    conn: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db" / "t.db"
    conn.close()
    assert main(["freeze-splits", "--db", str(db), "--account", ACCOUNT]) == 0
    out = capsys.readouterr().out
    assert "dev:" in out and "test_a:" in out and "test_b:" in out
    assert "Tim Hortons" not in out and "$" not in out
    assert main(["freeze-splits", "--db", str(db), "--account", ACCOUNT]) == 1
    assert "already frozen" in capsys.readouterr().out


# --- Clearing labels (owner found a mixed merchant) ---


def test_clear_makes_a_merchant_unlabelled_again(frozen: sqlite3.Connection) -> None:
    from lookahead.labels import clear_merchant

    group = first_group(frozen)
    fps = load_rows(frozen, ACCOUNT).query("group == @group")["fingerprint"].tolist()
    label_merchant(frozen, ACCOUNT, group, "Food delivery", TODAY)
    label_row(frozen, ACCOUNT, fps[0], "Shopping", TODAY)  # an earlier override goes too
    n = clear_merchant(frozen, ACCOUNT, group, TODAY)
    assert n == len(fps)
    labels = current_labels(frozen, ACCOUNT)
    assert not any(f in labels for f in fps)
    assert progress(frozen, ACCOUNT).rows_covered == 0


def test_labels_after_a_clear_apply_normally(frozen: sqlite3.Connection) -> None:
    from lookahead.labels import clear_merchant

    group = first_group(frozen)
    fps = load_rows(frozen, ACCOUNT).query("group == @group")["fingerprint"].tolist()
    label_merchant(frozen, ACCOUNT, group, "Food delivery", TODAY)
    clear_merchant(frozen, ACCOUNT, group, TODAY)
    label_merchant(frozen, ACCOUNT, group, "Transport", TODAY)
    assert {current_labels(frozen, ACCOUNT)[f].category for f in fps} == {"Transport"}


def test_old_label_table_is_migrated_keeping_every_label(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE category_labels (
            label_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, account TEXT NOT NULL,
            category TEXT, status TEXT NOT NULL CHECK (status IN ('labelled', 'skipped')),
            scope TEXT NOT NULL CHECK (scope IN ('merchant', 'row')),
            decided_at TEXT NOT NULL, CHECK ((status = 'labelled') = (category IS NOT NULL)));
        INSERT INTO category_labels VALUES (1, 'f1', 'a', 'Food delivery', 'labelled',
            'merchant', '2026-10-01'), (2, 'f2', 'a', NULL, 'skipped', 'row', '2026-10-01');
        """
    )
    old.commit()
    old.close()
    conn = connect(path)
    labels = current_labels(conn, "a")
    assert labels["f1"].category == "Food delivery" and labels["f2"].status == "skipped"
    conn.execute(  # the new status is accepted now
        "INSERT INTO category_labels (fingerprint, account, category, status, scope, decided_at)"
        " VALUES ('f1', 'a', NULL, 'cleared', 'merchant', '2026-10-02')"
    )
    assert "f1" not in current_labels(conn, "a")


# --- Checking the frozen splits after a code change ---


def test_check_splits_on_an_unchanged_database(frozen: sqlite3.Connection) -> None:
    from lookahead.labels import check_splits

    c = check_splits(frozen, ACCOUNT)
    assert c.frozen == c.still_left and c.now_rules == 0
    assert c.unsplit == 0 and c.mixed_test_b_groups == 0 and c.ok


def test_check_splits_counts_rows_now_decided_by_rules(frozen: sqlite3.Connection) -> None:
    from lookahead.labels import check_splits

    # Pretend a frozen row is now decided by a rule: its fingerprint no longer matches.
    frozen.execute(
        "UPDATE label_splits SET fingerprint = 'gone'"
        " WHERE rowid = (SELECT MIN(rowid) FROM label_splits)"
    )
    c = check_splits(frozen, ACCOUNT)
    assert c.now_rules == 1 and c.unsplit == 1 and not c.ok


def test_check_splits_finds_a_group_mixing_test_b_with_other_rows(
    frozen: sqlite3.Connection,
) -> None:
    from lookahead.labels import check_splits

    group = first_group(frozen, "test_b")
    rows = load_rows(frozen, ACCOUNT)
    fp = rows[rows["group"] == group]["fingerprint"].iloc[0]
    frozen.execute("UPDATE label_splits SET split = 'dev' WHERE fingerprint = ?", (fp,))
    assert check_splits(frozen, ACCOUNT).mixed_test_b_groups == 1


# --- Label counts for reports ---


def test_label_status_counts(frozen: sqlite3.Connection) -> None:
    from lookahead.labels import clear_merchant, label_status_counts

    rows = load_rows(frozen, ACCOUNT)
    sizes = rows.groupby("group").size().sort_values(ascending=False)
    a, b, c = sizes.index[:3]
    label_merchant(frozen, ACCOUNT, a, "Groceries", TODAY)
    skip_merchant(frozen, ACCOUNT, b, TODAY)
    label_merchant(frozen, ACCOUNT, c, "Transport", TODAY)
    clear_merchant(frozen, ACCOUNT, c, TODAY)
    counts = label_status_counts(frozen, ACCOUNT)
    assert counts == {
        "labelled": sizes[a],
        "skipped": sizes[b],
        "cleared": sizes[c],  # cleared and not labelled again
        "not reviewed": len(rows) - sizes[a] - sizes[b],  # cleared rows count here too
        "merchants done": 2,
        "merchants": len(sizes),
    }
