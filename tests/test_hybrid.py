"""Tests for the hybrid categorizer (step 5b-ii): lookup for known merchant groups, the
tuned model for unseen ones, review rules for each route. Made-up rows only."""

import math
from datetime import date

import pandas as pd
import pytest

from lookahead.categorizer import HybridCategorizer, Prediction, examples
from lookahead.flows import Txn

PARAMS = {"model__C": 1.0, "features__name__ngram_range": (2, 5)}


def row(name: str, group: str | None = None) -> dict[str, str]:
    return {
        "name": name, "group": group or name, "channel": "in_person",
        "txn_type": "pos purchase", "direction": "out", "amount_bucket": "10-30",
    }  # fmt: skip


def train() -> tuple[pd.DataFrame, list[str]]:
    rows, y = [], []
    for name, label in [
        ("Maxi", "Groceries"), ("Metro Plus", "Groceries"), ("Iga Extra", "Groceries"),
        ("Cafe Velo", "Restaurants & cafés"), ("Cafe Lune", "Restaurants & cafés"),
        ("Resto Pho", "Restaurants & cafés"),
    ]:  # fmt: skip
        for _ in range(3):
            rows.append(row(name))
            y.append(label)
    rows += [row("Bazar Mix"), row("Bazar Mix"), row("Bazar Mix")]  # labels disagree
    y += ["Shopping", "Shopping", "Groceries"]
    return pd.DataFrame(rows), y


def fitted(**kwargs: object) -> HybridCategorizer:
    x, y = train()
    options = {"params": PARAMS, "threshold": 0.0, **kwargs}
    return HybridCategorizer(**options).fit(x, y)  # type: ignore[arg-type]


def predict_one(model: HybridCategorizer, name: str, group: str | None = None) -> Prediction:
    return model.predict_rows(pd.DataFrame([row(name, group)]))[0]


# --- Known merchant groups: lookup ---


def test_known_group_uses_the_lookup_without_review() -> None:
    got = predict_one(fitted(), "Maxi")
    assert got == Prediction("Groceries", "known merchant", flagged=False, confidence=1.0)


def test_known_group_whose_labels_disagree_goes_to_review() -> None:
    got = predict_one(fitted(), "Bazar Mix")
    assert got.category == "Shopping" and got.flagged
    assert got.confidence == pytest.approx(2 / 3)  # share of its labels that agree
    assert got.route == "known merchant, labels disagree"


def test_your_preference_wins_over_disagreeing_labels() -> None:
    got = predict_one(fitted(preferences={"bazar mix": "Shopping"}), "Bazar Mix")
    assert got == Prediction("Shopping", "your preference", flagged=False, confidence=1.0)


def test_your_preference_also_covers_an_unseen_group() -> None:
    got = predict_one(fitted(preferences={"cafe soleil": "Restaurants & cafés"}), "Cafe Soleil")
    assert got.route == "your preference" and not got.flagged


# --- Unseen merchant groups: the model, with the review threshold ---


def test_unseen_group_uses_the_model() -> None:
    got = predict_one(fitted(), "Cafe Soleil")
    assert got.route == "new merchant" and got.category == "Restaurants & cafés"
    assert got.confidence is not None and 0 < got.confidence <= 1


@pytest.mark.parametrize(("threshold", "review"), [(0.0, False), (math.inf, True)])
def test_review_threshold_applies_to_new_merchants(threshold: float, review: bool) -> None:
    got = predict_one(fitted(threshold=threshold), "Cafe Soleil")
    assert got.flagged is review


def test_review_threshold_never_touches_known_groups() -> None:
    got = predict_one(fitted(threshold=math.inf), "Maxi")
    assert not got.flagged


# --- Fitting: tuning and threshold come from the training data only ---


def test_threshold_is_chosen_from_training_when_not_given() -> None:
    x, y = train()
    model = HybridCategorizer(params=PARAMS).fit(x, y)
    assert model.threshold_ == math.inf or 0 < model.threshold_ <= 1


def test_params_are_tuned_on_the_training_data_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lookahead.categorizer as c

    calls: list[int] = []

    def fake_tune(make, x, y, groups, folds=3):  # type: ignore[no-untyped-def]
        calls.append(len(x))
        return PARAMS, 0.5

    monkeypatch.setattr(c, "tune", fake_tune)
    x, y = train()
    HybridCategorizer(threshold=0.0).fit(x, y)
    assert calls == [len(x)]


def test_predict_returns_one_category_per_row() -> None:
    x, _ = train()
    assert len(fitted().predict(x)) == len(x)


# --- Upstream: a row with a stored preference never reaches the categorizer ---


def test_rows_with_a_preference_are_decided_before_the_categorizer() -> None:
    text = "Apos Bazar Mix           Montr "
    rows = [Txn(1, date(2026, 9, 1), -1_000, "pos purchase", text)]
    assert len(examples(rows)) == 1
    assert examples(rows, preferences={"bazar mix": "Shopping"}).empty


# --- Combined system on synthetic splits (eval/categorizer.py) ---


def test_hybrid_routes_on_synthetic_splits() -> None:
    import eval.categorizer as ev

    df = ev.synthetic_examples(months=4)
    _, preds_b = ev.hybrid_test_b(df)
    assert {p.route for p in preds_b} == {"new merchant"}  # merchant-disjoint by design
    _, preds_a = ev.hybrid_test_a(df)
    routes = [p.route for p in preds_a]
    assert routes.count("known merchant") > len(routes) / 2
    assert all(p.flagged for p in preds_a if p.route == "known merchant, labels disagree")


# --- Extra training rows (e.g. synthetic) teach the model only, never the lookup ---


def test_extra_rows_teach_the_model_but_not_the_lookup() -> None:
    x, y = train()
    extra = pd.DataFrame([row(n) for n in ["Bixi Velo", "Stm Opus", "Taxi Lune"] for _ in range(3)])
    extra_y = ["Transport"] * len(extra)
    model = HybridCategorizer(params=PARAMS, threshold=0.0).fit(
        x, y, extra_x=extra, extra_y=extra_y
    )
    assert "Bixi Velo" not in model.known  # synthetic merchants never become "known"
    got = predict_one(model, "Bixi Lune")
    assert got.route == "new merchant" and got.category == "Transport"


def test_without_extra_rows_the_model_cannot_know_transport() -> None:
    got = predict_one(fitted(), "Bixi Lune")
    assert got.category != "Transport"


def test_threshold_with_extra_rows_is_measured_on_the_main_rows() -> None:
    x, y = train()
    extra = pd.DataFrame([row("Bixi Velo")] * 3)
    model = HybridCategorizer(params=PARAMS).fit(x, y, extra_x=extra, extra_y=["Transport"] * 3)
    assert model.threshold_ == math.inf or 0 < model.threshold_ <= 1


# --- Coverage vs accuracy ---


def test_coverage_curve() -> None:
    from lookahead.categorizer import coverage_curve

    confidence = [0.99, 0.95, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
    correct = [True] * 8 + [False, False]
    curve = coverage_curve(confidence, correct, shares=(0.5, 0.8, 0.9, 1.0))
    assert [(c.share, c.rows, c.accuracy) for c in curve] == [
        (0.5, 5, 1.0), (0.8, 8, 1.0), (0.9, 9, 8 / 9), (1.0, 10, 0.8),
    ]  # fmt: skip
