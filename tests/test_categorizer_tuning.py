"""Tests for tuning, the needs-review threshold and the reliability table (step 5b)."""

import math

import numpy as np
import pandas as pd
import pytest

from lookahead.categorizer import (
    PARAM_GRID,
    choose_review_threshold,
    make_logreg,
    reliability_table,
    review_stats,
    tune,
)

# --- Needs-review threshold ---


def test_threshold_is_the_lowest_that_meets_the_target() -> None:
    confidence = np.array([0.9, 0.8, 0.7, 0.6])
    correct = np.array([True, True, False, True])
    # At 0.6 accepted rows are 3/4 right, at 0.7 2/3, at 0.8 2/2.
    assert choose_review_threshold(confidence, correct, target=0.9) == 0.8
    assert choose_review_threshold(confidence, correct, target=0.75) == 0.6


def test_unreachable_target_sends_everything_to_review() -> None:
    confidence = np.array([0.9, 0.8])
    correct = np.array([False, False])
    assert choose_review_threshold(confidence, correct, target=0.95) == math.inf


def test_review_stats() -> None:
    confidence = np.array([0.95, 0.9, 0.5, 0.4])
    correct = np.array([True, False, True, False])
    rate, accuracy, kept = review_stats(confidence, correct, threshold=0.9)
    assert rate == 0.5 and accuracy == 0.5 and kept == 2


def test_review_stats_when_everything_is_reviewed() -> None:
    rate, accuracy, kept = review_stats(np.array([0.5]), np.array([True]), math.inf)
    assert rate == 1.0 and kept == 0 and math.isnan(accuracy)


# --- Reliability table ---


def test_reliability_table_bins_add_up() -> None:
    confidence = np.array([0.15, 0.35, 0.55, 0.65, 0.95, 0.99])
    correct = np.array([False, True, True, False, True, True])
    table = reliability_table(confidence, correct, bins=5)
    assert table["rows"].sum() == 6
    top = table[table["bin"] == "0.8-1.0"].iloc[0]
    assert top["rows"] == 2 and top["accuracy"] == 1.0
    assert abs(top["mean_confidence"] - 0.97) < 1e-9


# --- Tuning with grouped cross-validation ---


def toy() -> tuple[pd.DataFrame, list[str], list[str]]:
    names = {
        "Groceries": ["Maxi", "Metro Plus", "Iga Extra", "Super Marche Lob"],
        "Restaurants & cafés": ["Cafe Velo", "Cafe Quorvel", "Resto Pho", "Bistro Lune"],
        "Transport": ["Bixi Velo", "Stm Opus", "Taxi Diamond", "Communauto"],
    }
    rows, y, groups = [], [], []
    for label, merchants in names.items():
        for m in merchants:
            for i in range(3):
                rows.append(
                    {"name": f"{m} {i}", "group": m, "channel": "in_person",
                     "txn_type": "pos purchase", "direction": "out", "amount_bucket": "10-30"}
                )  # fmt: skip
                y.append(label)
                groups.append(m)
    return pd.DataFrame(rows), y, groups


def test_tune_picks_from_the_grid() -> None:
    x, y, groups = toy()
    params, score = tune(make_logreg, x, y, groups, folds=3)
    assert params["model__C"] in PARAM_GRID["model__C"]
    assert params["features__name__ngram_range"] in PARAM_GRID["features__name__ngram_range"]
    assert 0.0 <= score <= 1.0


def test_tune_never_puts_a_merchant_on_both_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    import lookahead.categorizer as c

    seen: list[tuple[set[str], set[str]]] = []
    real_split = c.GroupKFold.split

    def spy(self, x, y=None, groups=None):  # type: ignore[no-untyped-def]
        for train, test in real_split(self, x, y, groups):
            g = np.asarray(groups)
            seen.append((set(g[train]), set(g[test])))
            yield train, test

    monkeypatch.setattr(c.GroupKFold, "split", spy)
    x, y, groups = toy()
    tune(make_logreg, x, y, groups, folds=3)
    assert seen and all(not (a & b) for a, b in seen)


# --- Nested tuning in the synthetic eval never sees a test merchant ---


def test_nested_tuning_only_sees_training_merchants(monkeypatch: pytest.MonkeyPatch) -> None:
    import eval.categorizer as ev

    df = ev.synthetic_examples(months=4)
    seen: list[set[str]] = []
    real_tune = ev.tune

    def spy(make, x, y, groups, folds=3):  # type: ignore[no-untyped-def]
        seen.append(set(groups))
        return real_tune(make, x, y, groups, folds)

    monkeypatch.setattr(ev, "tune", spy)
    monkeypatch.setattr(
        ev, "out_of_fold_confidence", lambda *a, **k: (np.ones(1), np.ones(1, bool))
    )
    ev.tuned_test_b(df, make_logreg, with_review=False)
    folds = ev.grouped_folds(df)
    assert len(seen) == len(folds)
    for tuned_on, (_, test_idx) in zip(seen, folds, strict=True):
        assert not tuned_on & set(df.iloc[test_idx]["merchant"])
