"""Tests for the pre-declared categorizer experiments (dev only). Made-up and synthetic rows."""

import csv
import json
import math
import re
from datetime import date, datetime
from functools import partial
from pathlib import Path

import pandas as pd
import pytest

import lookahead.categorizer as categorizer
from lookahead.categorizer import HybridCategorizer, make_logreg, make_svm

PARAMS = {"model__C": 1.0, "features__name__ngram_range": (2, 5)}


@pytest.fixture(autouse=True)
def small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        categorizer, "PARAM_GRID", {"model__C": [1.0], "features__name__ngram_range": [(2, 5)]}
    )


def rows(names: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"name": n, "group": n, "channel": "in_person", "txn_type": "pos purchase",
          "direction": "out", "amount_bucket": "10-30"} for n in names]
    )  # fmt: skip


def toy() -> tuple[pd.DataFrame, list[str]]:
    names, labels = [], []
    for name, label in [
        ("Maxi", "Groceries"), ("Metro Plus", "Groceries"), ("Iga Extra", "Groceries"),
        ("Super Marche Lob", "Groceries"), ("Cafe Velo", "Restaurants & cafés"),
        ("Resto Pho", "Restaurants & cafés"), ("Bistro Lune", "Restaurants & cafés"),
        ("Cafe Quorvel", "Restaurants & cafés"), ("Barbier Royal", "Personal care"),
        ("Coiffure Lune", "Personal care"), ("Salon Iris", "Personal care"),
    ]:  # fmt: skip
        names += [name] * 3
        labels += [label] * 3
    return rows(names), labels


# --- Model options ---


def test_balanced_class_weight_option() -> None:
    assert make_logreg(balanced=True).named_steps["model"].class_weight == "balanced"
    assert make_logreg().named_steps["model"].class_weight is None
    assert make_svm(balanced=True).named_steps["model"].class_weight == "balanced"


def test_lexicon_option_adds_the_keyword_features() -> None:
    names = [t[0] for t in make_logreg(lexicon=True).named_steps["features"].transformers]
    assert "lexicon" in names
    assert "lexicon" not in [t[0] for t in make_logreg().named_steps["features"].transformers]
    x, y = toy()
    model = make_logreg(lexicon=True).fit(x, y)
    assert model.predict(rows(["Coiffure Soleil"]))[0] == "Personal care"


# --- Calibration inside the hybrid ---


def test_calibrated_hybrid_gives_probabilities_and_a_threshold() -> None:
    x, y = toy()
    model = HybridCategorizer(params=PARAMS, calibrate=True).fit(x, y)
    assert type(model.model_).__name__ == "CalibratedClassifierCV"
    got = model.predict_rows(rows(["Bistro Soleil"]))[0]
    assert got.route == "new merchant" and 0 < got.confidence <= 1  # type: ignore[operator]
    assert model.threshold_ == math.inf or 0 < model.threshold_ <= 1


def test_calibrated_hybrid_with_extra_rows() -> None:
    x, y = toy()
    extra = rows(["Bixi Velo", "Taxi Lune", "Stm Opus"] * 3)
    model = HybridCategorizer(params=PARAMS, calibrate=True).fit(
        x, y, extra_x=extra, extra_y=["Transport"] * len(extra)
    )
    assert "Bixi Velo" not in model.known  # extra rows never become known merchants
    got = model.predict_rows(rows(["Taxi Soleil"]))[0]
    # On a toy this small, calibration can change the answer itself (that cost is what
    # experiment 3 measures); here only check it runs and gives a probability.
    assert got.route == "new merchant" and 0 < got.confidence <= 1  # type: ignore[operator]


# --- Pre-declared decisions ---


def test_combination_takes_changes_that_gained_at_least_one_point() -> None:
    from eval.categorizer_experiments import combined_changes

    gains = {"balanced": 0.015, "lexicon": 0.009, "calibrated": 0.03}
    assert combined_changes(gains) == ["balanced", "calibrated"]
    assert combined_changes({"balanced": 0.0, "lexicon": -0.02, "calibrated": 0.005}) == []


@pytest.mark.parametrize(
    ("at_90", "at_100", "rule"),
    [(0.85, 0.79, "confidence threshold"), (0.83, 0.79, "always flag new merchants"),
     (0.84, 0.79, "confidence threshold")],
)  # fmt: skip
def test_flag_rule_declared_in_advance(at_90: float, at_100: float, rule: str) -> None:
    from eval.categorizer_experiments import flag_rule
    from lookahead.categorizer import CoveragePoint

    curve = [CoveragePoint(0.9, 90, at_90), CoveragePoint(1.0, 100, at_100)]
    assert flag_rule(curve) == rule


def test_per_category_recall() -> None:
    from eval.categorizer_experiments import per_category_recall

    truth = pd.Series(["Groceries", "Groceries", "Transport", "Transport"])
    got = ["Groceries", "Transport", "Transport", "Transport"]
    groups = pd.Series(["Maxi", "Iga", "Stm", "Stm"])
    table = per_category_recall(truth, got, groups)
    assert table == [("Groceries", 0.5, 2, 2), ("Transport", 1.0, 2, 1)]


# --- End to end on a synthetic stand-in ---


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from lookahead.db import connect
    from lookahead.generator.personas import PERSONAS
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports
    from lookahead.labels import freeze_splits, label_row, load_rows

    tmp = tmp_path_factory.mktemp("exp")
    export, key = write_persona(PERSONAS["C"], tmp / "s", 42, date(2025, 10, 1), date(2026, 9, 30))
    conn = connect(tmp / "t.db")
    store_exports(conn, "scotia-chequing", [(export.name, parse_export(export))],
                  datetime(2026, 10, 2))  # fmt: skip
    conn.commit()
    freeze_splits(conn, "scotia-chequing", seed=3)
    parsed = parse_export(export)
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    category = {(r.posted_date, r.amount_cents, r.sub_description): t["category"]
                for r, t in zip(parsed, truth, strict=True)}  # fmt: skip
    for r in load_rows(conn, "scotia-chequing").itertuples():
        label_row(conn, "scotia-chequing", r.fingerprint,
                  category[(r.posted_date, r.amount_cents, r.text)])  # fmt: skip
    conn.close()
    return tmp / "t.db"


def test_command_runs_every_declared_variant_counts_only(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from eval.categorizer_experiments import main

    out_file = tmp_path / "data" / "private" / "eval" / "experiments.json"
    assert main(["--db", str(db), "--out", str(out_file), "--months", "4"]) == 0
    out = capsys.readouterr().out
    for variant in ("current", "balanced", "lexicon", "calibrated", "SVM", "SVM balanced"):
        assert variant in out
    assert "per-category recall" in out and "Flag rule" in out
    assert "$" not in out and not re.search(r"\d+\.\d{2}", out)
    for name in ("Tim Hortons", "Maxi", "Couche-Tard", "Nadia"):
        assert name not in out
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert {"current", "balanced", "lexicon", "calibrated"} <= set(saved["variants"])


def test_variants_use_partial_factories() -> None:
    from eval.categorizer_experiments import VARIANTS

    assert VARIANTS["balanced"].make.keywords == {"balanced": True}  # type: ignore[attr-defined]
    assert isinstance(VARIANTS["lexicon"].make, partial)
