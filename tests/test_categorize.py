"""Tests for step 8a: categories for every stored row, kept in the database for the app.
Synthetic data and temporary files only."""

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from lookahead.categorize import SOURCES, Settings, categorize, load_settings, main, predict, train
from lookahead.db import connect
from lookahead.labels import labelled_examples, load_rows
from lookahead.taxonomy import CATEGORIES, FLOW_TYPES
from lookahead.ui import BLANK, FLAG, HIDDEN, display_table
from lookahead.views import has_categories, transactions

ACCOUNT = "scotia-chequing"
SETTINGS = Settings(threshold=0.5, synthetic_months=4)
TODAY = date(2026, 10, 2)


@pytest.fixture
def conn(db: Path, tmp_path: Path) -> sqlite3.Connection:
    """A private copy of the shared labelled synthetic database."""
    return connect(Path(shutil.copy(db, tmp_path / "t.db")))


def test_settings_come_from_the_saved_choice(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "missing.json") == Settings()
    path = tmp_path / "choice.json"
    path.write_text(json.dumps({
        "params": {"model__C": 100.0, "features__name__ngram_range": [2, 4]},
        "flag_threshold": 0.97, "synthetic_months": 12,
    }), encoding="utf-8")  # fmt: skip
    assert load_settings(path) == Settings(
        {"model__C": 100.0, "features__name__ngram_range": (2, 4)}, 0.97, 12
    )


def test_the_model_trains_on_development_labels_only(conn: sqlite3.Connection) -> None:
    model, n = train(conn, ACCOUNT, SETTINGS)
    dev = labelled_examples(conn, ACCOUNT, ("dev",))
    holdout_only = set(load_rows(conn, ACCOUNT).query("split == 'test_b'")["group"])
    assert n == len(dev) > 0
    assert set(model.known) == set(dev["group"])  # the lookup holds dev merchants only
    assert not set(model.known) & holdout_only


def test_every_row_gets_a_flow_type_a_source_and_a_valid_category(
    conn: sqlite3.Connection,
) -> None:
    model, _ = train(conn, ACCOUNT, SETTINGS)
    df = predict(conn, ACCOUNT, model)
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert len(df) == total and df["fingerprint"].is_unique
    assert set(df["flow_type"]) <= set(FLOW_TYPES) and set(df["source"]) <= set(SOURCES)
    assert set(df["category"].dropna()) <= set(CATEGORIES)
    # only netted pairs and rows with nothing to go on have no category
    assert set(df.loc[df["category"].isna(), "source"]) <= {"netted", "none"}
    assert not df.loc[df["source"].isin(["rule", "your label", "netted"]), "flagged"].any()
    assert {"rule", "your label"} <= set(df["source"])


def test_a_confirmed_label_is_shown_as_is(conn: sqlite3.Connection) -> None:
    model, _ = train(conn, ACCOUNT, SETTINGS)
    df = predict(conn, ACCOUNT, model).set_index("fingerprint")
    labelled = labelled_examples(conn, ACCOUNT, ("dev", "test_a", "test_b"))
    for fingerprint, label in zip(labelled["fingerprint"], labelled["label"], strict=True):
        assert df.loc[fingerprint, "category"] == label
        assert df.loc[fingerprint, "source"] == "your label"


def test_unlabelled_rows_use_the_lookup_or_the_model(tmp_path: Path, db: Path) -> None:
    conn = connect(Path(shutil.copy(db, tmp_path / "t.db")))
    model, _ = train(conn, ACCOUNT, SETTINGS)
    rows = load_rows(conn, ACCOUNT)
    with conn:  # forget the holdout labels, as for rows nobody reviewed
        conn.executemany(
            "DELETE FROM category_labels WHERE fingerprint = ?",
            [(f,) for f in rows.query("split != 'dev'")["fingerprint"]],
        )
    df = predict(conn, ACCOUNT, model).set_index("fingerprint")
    test_b = df.loc[rows.query("split == 'test_b'")["fingerprint"]]
    test_a = df.loc[rows.query("split == 'test_a'")["fingerprint"]]
    assert set(test_b["source"]) == {"model"}  # merchants never seen in training
    assert "known merchant" in set(test_a["source"])
    assert test_b["confidence"].between(0, 1).all()
    assert (test_b["flagged"] == (test_b["confidence"] < 0.5)).all()


def test_results_are_stored_and_replaced_with_a_dated_run(conn: sqlite3.Connection) -> None:
    first = categorize(conn, ACCOUNT, SETTINGS, TODAY)
    assert first.changed is None and first.rows == sum(first.by_source.values())
    assert has_categories(conn, ACCOUNT)
    second = categorize(conn, ACCOUNT, SETTINGS, TODAY)
    assert second.changed == 0  # same labels, same model: nothing moves
    stored = conn.execute("SELECT COUNT(*) FROM category_predictions").fetchone()[0]
    assert stored == first.rows  # replaced, not appended
    runs = conn.execute(
        "SELECT run_at, model_version, training_rows, settings FROM categorizer_runs"
    ).fetchall()
    assert len(runs) == 2 and runs[0][0] == "2026-10-02"
    assert runs[0][1] == first.model_version and runs[0][2] == first.training_rows
    assert json.loads(runs[0][3])["threshold"] == 0.5


def test_command_prints_counts_only(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = Path(shutil.copy(db, tmp_path / "t.db"))
    assert main(["--db", str(path), "--choice", str(tmp_path / "none.json")]) == 0
    out = capsys.readouterr().out
    assert "Categorized" in out and "flagged" in out and "defaults (no saved choice)" in out
    assert "$" not in out
    for text in ("Tim Hortons", "Maxi", "Nadia", *CATEGORIES):
        assert text not in out  # no names, and no spending pattern by category
    assert main(["--db", str(tmp_path / "missing.db")]) == 2


# --- What the Transactions page shows ---


def test_views_join_the_stored_categories_and_filter_on_them(conn: sqlite3.Connection) -> None:
    categorize(conn, ACCOUNT, SETTINGS, TODAY)
    everything = transactions(conn, ACCOUNT)
    assert everything["source"].notna().all() and everything["flagged"].dtype == bool
    groceries = transactions(conn, ACCOUNT, categories=["Groceries"])
    assert 0 < len(groceries) < len(everything)
    assert set(groceries["category"]) == {"Groceries"}
    flagged = transactions(conn, ACCOUNT, flagged_only=True)
    assert flagged["flagged"].all() and len(flagged) == int(everything["flagged"].sum())


def test_table_shows_category_columns_and_hides_a_persons_name(conn: sqlite3.Connection) -> None:
    categorize(conn, ACCOUNT, SETTINGS, TODAY)
    rows = transactions(conn, ACCOUNT)
    table = display_table(rows, bytes(32))
    assert list(table.columns) == [
        "Date", "Type", "Text", "Merchant", "Category", "Flow", "Flag", "From",
        "Amount ($)", "Balance ($)",
    ]  # fmt: skip
    memo = (rows["type"] == "credit memo").to_numpy()
    assert memo.any() and set(table.loc[memo, "Merchant"]) == {HIDDEN}  # the sender's name
    assert not table["Merchant"].astype(str).str.contains("Nadia").any()
    assert set(table["Flag"]) <= {FLAG, ""}
    raw = display_table(rows, None)  # pseudonymize off: the owner sees everything
    assert raw.loc[memo, "Merchant"].str.contains("Nadia").any()
    assert BLANK in set(raw["Merchant"])  # rows without a merchant (transfers, fees)


def test_table_is_unchanged_before_categorize_runs(conn: sqlite3.Connection) -> None:
    table = display_table(transactions(conn, ACCOUNT), None)
    assert list(table.columns) == ["Date", "Type", "Text", "Amount ($)", "Balance ($)"]
    assert isinstance(table, pd.DataFrame)


# --- Step 8c: retraining ---


def test_retraining_learns_a_development_correction_for_the_whole_merchant(
    conn: sqlite3.Connection,
) -> None:
    from lookahead.corrections import correct_row

    categorize(conn, ACCOUNT, SETTINGS, TODAY)
    rows = load_rows(conn, ACCOUNT)
    dev = rows.query("split == 'dev'")
    group = dev["group"].value_counts().index[0]  # a merchant with several dev rows
    mine = dev.loc[dev["group"] == group, "fingerprint"].tolist()
    with conn:  # forget its review labels, so only corrections can teach the model
        conn.executemany("DELETE FROM category_labels WHERE fingerprint = ?", [(f,) for f in mine])
    correct_row(conn, ACCOUNT, mine[0], "Education", TODAY)
    done = categorize(conn, ACCOUNT, SETTINGS, TODAY)
    stored = dict(conn.execute("SELECT fingerprint, category FROM category_predictions").fetchall())
    source = dict(conn.execute("SELECT fingerprint, source FROM category_predictions").fetchall())
    assert stored[mine[0]] == "Education" and source[mine[0]] == "your label"
    for f in mine[1:]:  # the merchant's other rows follow, through the lookup
        assert stored[f] == "Education" and source[f] == "known merchant"
    assert done.changed and done.changed >= len(mine) - 1
    assert done.training_before is not None and done.flagged_before is not None


def test_retraining_never_trains_on_holdout_rows_even_when_corrected(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lookahead.categorizer as categorizer
    from lookahead.corrections import correct_merchant

    categorize(conn, ACCOUNT, SETTINGS, TODAY)
    rows = load_rows(conn, ACCOUNT)
    holdout = set(rows.query("split != 'dev'")["fingerprint"])
    correct_merchant(conn, ACCOUNT, rows.query("split == 'test_b'")["group"].iloc[0], "Education")
    seen: list[set[str]] = []
    real_fit = categorizer.HybridCategorizer.fit

    def spy(self, x, y, groups=None, extra_x=None, extra_y=None):  # type: ignore[no-untyped-def]
        seen.append(set(x["fingerprint"]))
        assert self.params == SETTINGS.params and self.threshold == 0.5  # nothing tuned
        return real_fit(self, x, y, groups, extra_x, extra_y)

    monkeypatch.setattr(categorizer.HybridCategorizer, "fit", spy)
    categorize(conn, ACCOUNT, SETTINGS, TODAY)
    assert len(seen) == 1 and not seen[0] & holdout


def test_the_model_file_is_saved_only_inside_data_private(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    import joblib

    from lookahead.categorize import model_dir_for

    with pytest.raises(ValueError, match="data/private"):
        categorize(conn, ACCOUNT, SETTINGS, TODAY, model_dir=tmp_path / "models")
    private = tmp_path / "data" / "private"
    assert model_dir_for(tmp_path / "demo.db") is None
    assert model_dir_for(private / "db" / "lookahead.db") == (private / "models").resolve()
    done = categorize(conn, ACCOUNT, SETTINGS, TODAY, model_dir=private / "models")
    assert done.model_file == private / "models" / f"categorizer_{ACCOUNT}_2026-10-02.joblib"
    assert set(joblib.load(done.model_file).known)  # a usable model


def test_retrain_reports_corrections_used_and_shown_only(conn: sqlite3.Connection) -> None:
    from lookahead.corrections import correct_row

    first = categorize(conn, ACCOUNT, SETTINGS, TODAY)
    assert (first.corrections_trained, first.corrections_shown_only) == (0, 0)
    rows = load_rows(conn, ACCOUNT)
    correct_row(conn, ACCOUNT, rows.query("split == 'dev'")["fingerprint"].iloc[0], "Shopping")
    correct_row(conn, ACCOUNT, rows.query("split == 'test_b'")["fingerprint"].iloc[0], "Shopping")
    rule_row = conn.execute(
        "SELECT fingerprint FROM category_predictions WHERE flow_type = 'transfer'"
        " AND source = 'rule' LIMIT 1"
    ).fetchone()[0]
    correct_row(conn, ACCOUNT, rule_row, "Gifts & donations")
    done = categorize(conn, ACCOUNT, SETTINGS, TODAY)
    assert (done.corrections_trained, done.corrections_shown_only) == (1, 2)
