"""Tests for step 7a: categorizer choices made on DEV labels only (holdout untouched).

A synthetic persona is frozen and labelled from its answer key, standing in for the
owner's data. Temporary files only: never data/private/.
"""

import csv
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

import lookahead.categorizer as categorizer
from eval.categorizer_dev import choose_training, dev_evaluation, main
from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.labels import freeze_splits, label_row, labelled_examples, load_rows

ACCOUNT = "scotia-chequing"


@pytest.fixture(autouse=True)
def small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    """One setting instead of 21 keeps the nested tuning fast in tests."""
    monkeypatch.setattr(
        categorizer, "PARAM_GRID",
        {"model__C": [1.0], "features__name__ngram_range": [(2, 5)]},
    )  # fmt: skip


def label_from_answer_key(conn: sqlite3.Connection, export: Path, key: Path) -> None:
    rows = parse_export(export)
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    category = {
        (r.posted_date, r.amount_cents, r.sub_description): t["category"]
        for r, t in zip(rows, truth, strict=True)
    }
    for r in load_rows(conn, ACCOUNT).itertuples():
        label_row(conn, ACCOUNT, r.fingerprint, category[(r.posted_date, r.amount_cents, r.text)])


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("dev")
    export, key = write_persona(PERSONAS["B"], tmp / "s", 42, date(2025, 10, 1), date(2026, 9, 30))
    path = tmp / "t.db"
    conn = connect(path)
    store_exports(conn, ACCOUNT, [(export.name, parse_export(export))], datetime(2026, 10, 2))
    conn.commit()
    freeze_splits(conn, ACCOUNT, seed=3)
    label_from_answer_key(conn, export, key)
    conn.close()
    return path


def test_only_dev_rows_are_ever_trained_on(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = connect(db)
    dev = labelled_examples(conn, ACCOUNT, ("dev",))
    holdout = set(load_rows(conn, ACCOUNT).query("split != 'dev'")["fingerprint"])
    conn.close()
    seen: list[set[str]] = []
    real_fit = categorizer.HybridCategorizer.fit

    def spy(self, x, y, groups=None, extra_x=None, extra_y=None):  # type: ignore[no-untyped-def]
        seen.append(set(x["fingerprint"]))
        return real_fit(self, x, y, groups, extra_x, extra_y)

    monkeypatch.setattr(categorizer.HybridCategorizer, "fit", spy)
    dev_evaluation(dev, synthetic=None)
    assert seen and all(not (s & holdout) for s in seen)


def test_report_covers_both_protocols_and_the_curve(db: Path) -> None:
    conn = connect(db)
    dev = labelled_examples(conn, ACCOUNT, ("dev",))
    conn.close()
    report = dev_evaluation(dev, synthetic=None)
    for protocol in ("unseen merchants", "future rows"):
        r = report.results[("real only", protocol)]
        assert r.rows > 0 and 0 <= r.accuracy <= 1 and 0 <= r.macro_f1 <= 1
        assert [p.share for p in r.curve] == [0.90, 0.95, 0.99, 1.0]
    assert report.chosen == "real only"  # no synthetic variant to compare
    unseen = report.results[("real only", "unseen merchants")]
    assert sum(n for _, _, n, _ in report.recall) == unseen.rows  # recall table, chosen variant
    assert {c for c, *_ in report.recall} == set(dev["label"])
    assert report.final_params and (report.final_threshold > 0)


@pytest.mark.parametrize(
    ("real", "both", "chosen"),
    [(0.40, 0.45, "real + synthetic"), (0.45, 0.40, "real only"), (0.40, 0.40, "real only")],
)
def test_the_choice_rule_is_fixed_in_advance(real: float, both: float, chosen: str) -> None:
    assert choose_training({"real only": real, "real + synthetic": both}) == chosen


def test_command_prints_counts_only_and_saves_the_choice(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "data" / "private" / "eval" / "choice.json"
    code = main(["--db", str(db), "--account", ACCOUNT, "--out", str(out_file), "--months", "0"])
    out = capsys.readouterr().out
    assert code == 0
    assert "holdout untouched" in out and "Chosen" in out and "Per-category recall" in out
    assert "$" not in out and not re.search(r"\d+\.\d{2}", out)  # rates print as percents
    for name in ("Iga", "Metro", "Netflix", "Hydro"):
        assert name not in out
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["training"] == "real only" and "flag_threshold" in saved and saved["params"]


def test_command_refuses_to_save_outside_data_private(db: Path, tmp_path: Path) -> None:
    assert main(["--db", str(db), "--account", ACCOUNT, "--out", str(tmp_path / "c.json")]) == 2


# --- Overlap-aware: synthetic rows never contain a merchant being tested (owner's choice) ---

import pandas as pd  # noqa: E402

from eval.categorizer_dev import future_rows, matches_synthetic, unseen_merchants  # noqa: E402


def frame(names: list[str], label: str = "Groceries") -> pd.DataFrame:
    return pd.DataFrame(
        [{"name": n, "group": n, "merchant": n, "channel": "in_person", "txn_type": "pos purchase",
          "direction": "out", "amount_bucket": "10-30", "label": label,
          "posted_date": date(2026, 1, 1 + i)} for i, n in enumerate(names)]
    )  # fmt: skip


def test_synthetic_rows_matching_a_tested_merchant_are_found() -> None:
    synthetic = frame(["Maxi", "Caf? Velisaro", "Stm-Opus", "Metro", "Tim Hortons"])
    tested = frame(["MAXI", "Cafe Velisaro", "STM"])
    assert list(matches_synthetic(synthetic, tested)) == [True, True, True, False, False]


def test_unseen_merchant_folds_drop_matching_synthetic_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import eval.categorizer_dev as ev

    dev = frame([f"Shop {c}" for c in "ABCDEFGHIJ" for _ in range(2)])
    synthetic = frame(["Shop A", "Shop B", "Other Store"] * 2, label="Shopping")
    seen: list[tuple[set[str], set[str]]] = []

    def fake_fit(train, extra):  # type: ignore[no-untyped-def]
        seen.append((set(train["group"]), set(extra["group"]) if extra is not None else set()))

        class Always:
            def predict_rows(self, x):  # type: ignore[no-untyped-def]
                return [categorizer.Prediction("Groceries", "new merchant", False, 0.5)] * len(x)

        return Always()

    monkeypatch.setattr(ev, "_fit", fake_fit)
    unseen_merchants(dev, synthetic, folds=5)
    all_groups = set(dev["group"])
    for train_groups, extra_groups in seen:
        held_out = all_groups - train_groups
        assert not extra_groups & held_out  # e.g. "Shop A" dropped in the fold testing it
        assert "Other Store" in extra_groups


def test_future_rows_drop_synthetic_rows_of_merchants_new_in_the_test_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import eval.categorizer_dev as ev

    dev = frame(["Shop A"] * 7 + ["Shop A", "Shop New", "Shop New"])  # last 30% by date
    synthetic = frame(["Shop A", "Shop New", "Other Store"], label="Shopping")
    seen: list[set[str]] = []

    def fake_fit(train, extra):  # type: ignore[no-untyped-def]
        seen.append(set(extra["group"]))

        class Always:
            def predict_rows(self, x):  # type: ignore[no-untyped-def]
                return [categorizer.Prediction("Groceries", "new merchant", False, 0.5)] * len(x)

        return Always()

    monkeypatch.setattr(ev, "_fit", fake_fit)
    future_rows(dev, synthetic)
    # "Shop New" is held out (only in the test period); "Shop A" is known from training.
    assert seen == [{"Shop A", "Other Store"}]
