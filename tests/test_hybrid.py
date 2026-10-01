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
    assert got == Prediction("Groceries", "known merchant", needs_review=False)


def test_known_group_whose_labels_disagree_goes_to_review() -> None:
    got = predict_one(fitted(), "Bazar Mix")
    assert got.category == "Shopping" and got.needs_review
    assert got.route == "known merchant, labels disagree"


def test_your_preference_wins_over_disagreeing_labels() -> None:
    got = predict_one(fitted(preferences={"bazar mix": "Shopping"}), "Bazar Mix")
    assert got == Prediction("Shopping", "your preference", needs_review=False)


def test_your_preference_also_covers_an_unseen_group() -> None:
    got = predict_one(fitted(preferences={"cafe soleil": "Restaurants & cafés"}), "Cafe Soleil")
    assert got.route == "your preference" and not got.needs_review


# --- Unseen merchant groups: the model, with the review threshold ---


def test_unseen_group_uses_the_model() -> None:
    got = predict_one(fitted(), "Cafe Soleil")
    assert got.route == "new merchant" and got.category == "Restaurants & cafés"
    assert got.confidence is not None and 0 < got.confidence <= 1


@pytest.mark.parametrize(("threshold", "review"), [(0.0, False), (math.inf, True)])
def test_review_threshold_applies_to_new_merchants(threshold: float, review: bool) -> None:
    got = predict_one(fitted(threshold=threshold), "Cafe Soleil")
    assert got.needs_review is review


def test_review_threshold_never_touches_known_groups() -> None:
    got = predict_one(fitted(threshold=math.inf), "Maxi")
    assert not got.needs_review


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
    assert all(p.needs_review for p in preds_a if p.route == "known merchant, labels disagree")
