"""Transaction categorizer (step 5): features, baselines and the main model.

Only rows the category rules (step 4) leave for the categorizer are used, for training and
for predicting. Features (HANDOFF section 07):
- name: the extracted merchant name (plus the restaurant for delivery orders), as character
  n-grams of 2-5 characters within words, so new store numbers and spellings still match;
- channel (online / in person / none), transaction type, direction, amount bucket.
Models:
- MerchantLookup (baseline 1): reuse the most common label of a known merchant group;
- TF-IDF + linear SVM (baseline 2);
- TF-IDF + logistic regression (main model: it gives probabilities, for "flagged"), with
  the frozen keyword lexicon as extra features (`make_current`, chosen on dev labels).
Tuning (step 5b): `tune` picks C and the n-gram range by macro-F1 with grouped
cross-validation (no merchant group in both parts), on training data only. "Needs review":
`choose_flag_threshold` picks, from out-of-fold probabilities on training data, the lowest
confidence at which the accepted rows are at least FLAG_TARGET correct.

The system used (step 5b-ii): `HybridCategorizer`. A merchant group with labels in training
gets the lookup (its labels disagree -> flagged, unless you stored a preference); an
unseen group gets the tuned model, and only those rows use the review threshold.

Counts-only check on a database (no labels exist before step 6, so no accuracy):
    python -m lookahead.categorizer report --db data/private/db/lookahead.db
"""

import argparse
import math
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder
from sklearn.svm import LinearSVC

from lookahead.categories import category_by_rules, load_category_preferences
from lookahead.flows import Txn
from lookahead.lexicon import lexicon_matrix
from lookahead.merchants import extract_merchant
from lookahead.resolve import UserDecisions, load_user_decisions, name_infos, name_key, resolve

META_COLUMNS = ["channel", "txn_type", "direction", "amount_bucket"]
FEATURE_COLUMNS = ["name", "group", *META_COLUMNS]
_COLUMNS = ["row_id", "posted_date", *FEATURE_COLUMNS]

# Upper bounds in cents of |amount| -> bucket label; the last bucket has no upper bound.
_BUCKETS = [(1_000, "under 10"), (3_000, "10-30"), (10_000, "30-100"), (50_000, "100-500")]


def amount_bucket(cents: int) -> str:
    """Size of the amount in dollars, ignoring the sign: "under 10", "10-30", ..., "500+"."""
    for upper, label in _BUCKETS:
        if abs(cents) < upper:
            return label
    return "500+"


def examples(
    txns: Sequence[Txn],
    user: UserDecisions | None = None,
    preferences: dict[str, str] | None = None,
) -> pd.DataFrame:
    """One row of features per transaction the rules leave for the categorizer, in input
    order. Rows that wait to inherit their purchase's category are not included."""
    decisions = category_by_rules(txns, user, preferences)
    resolution = resolve(name_infos(t.text for t in txns), user)
    records = []
    for t in txns:
        if decisions[t.row_id].source != "categorizer":
            continue
        got = extract_merchant(t.text)
        if got.merchant is None:  # nothing to learn from (rare: unpaired money back etc.)
            continue
        name = (
            got.merchant if got.via_restaurant is None else f"{got.merchant} {got.via_restaurant}"
        )
        records.append(
            {
                "row_id": t.row_id,
                "posted_date": t.posted_date,
                "name": name,
                "group": resolution.group_of(got.merchant),
                "channel": got.channel or "none",
                "txn_type": " ".join(t.txn_type.split()).casefold(),
                "direction": "in" if t.amount_cents > 0 else "out",
                "amount_bucket": amount_bucket(t.amount_cents),
            }
        )
    return pd.DataFrame(records, columns=_COLUMNS)


def _features(lexicon: bool = False) -> ColumnTransformer:
    parts: list[tuple[str, object, object]] = [
        (
            "name",
            TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True),
            "name",
        ),
        ("meta", OneHotEncoder(handle_unknown="ignore"), META_COLUMNS),
    ]
    if lexicon:  # experiment 2: one "contains a keyword of category X" column per category
        parts.append(("lexicon", FunctionTransformer(lexicon_matrix), "name"))
    return ColumnTransformer(parts)


def _weights(balanced: bool) -> str | None:
    return "balanced" if balanced else None  # experiment 1: rare categories weigh more


def make_svm(c: float = 1.0, balanced: bool = False, lexicon: bool = False) -> Pipeline:
    """Baseline 2: character TF-IDF + linear SVM (no probabilities)."""
    model = LinearSVC(C=c, class_weight=_weights(balanced))
    return Pipeline([("features", _features(lexicon)), ("model", model)])


def make_logreg(c: float = 1.0, balanced: bool = False, lexicon: bool = False) -> Pipeline:
    """Main model: character TF-IDF + logistic regression (probabilities for flagging)."""
    model = LogisticRegression(C=c, max_iter=5000, class_weight=_weights(balanced))
    return Pipeline([("features", _features(lexicon)), ("model", model)])


def make_embedding(c: float = 1.0, with_current: bool = False) -> Pipeline:
    """Step 5c candidate: sentence embeddings of the merchant name (a multilingual MiniLM)
    + the one-hot columns + logistic regression. `with_current` keeps the current model's
    features (character TF-IDF and lexicon) next to the embeddings."""
    from lookahead.embeddings import embed_names  # needs sentence-transformers only if used

    parts = _features(lexicon=True).transformers if with_current else _features().transformers[1:]
    features = ColumnTransformer([*parts, ("embedding", FunctionTransformer(embed_names), "name")])
    return Pipeline([("features", features), ("model", LogisticRegression(C=c, max_iter=5000))])


def make_current() -> Pipeline:
    """The model in use: logistic regression plus the keyword lexicon. Chosen on dev labels
    by the pre-declared rule (DECISIONS.md); `make_logreg()` alone is the earlier model."""
    return make_logreg(lexicon=True)


class MerchantLookup:
    """Baseline 1: the most common label of the row's merchant group in the training data;
    for a merchant never seen, the most common label overall. Ties go alphabetically."""

    def fit(self, x: pd.DataFrame, y: Sequence[str]) -> "MerchantLookup":
        by_group: dict[str, Counter[str]] = {}
        for group, label in zip(x["group"], y, strict=True):
            by_group.setdefault(group, Counter())[label] += 1
        self.table = {g: _most_common(c) for g, c in by_group.items()}
        self.fallback = _most_common(Counter(y))
        return self

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        return np.array([self.table.get(g, self.fallback) for g in x["group"]], dtype=object)


def _most_common(counts: Counter[str]) -> str:
    return min(counts, key=lambda label: (-counts[label], label))


# --- Tuning and "flagged" (step 5b) ---

PARAM_GRID: dict[str, list[object]] = {
    "model__C": [0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0],
    "features__name__ngram_range": [(2, 4), (2, 5), (3, 5)],
}
FLAG_TARGET = 0.95  # accuracy wanted on rows NOT sent to review (our choice; see DECISIONS)


def tune(
    make: Callable[[], Pipeline],
    x: pd.DataFrame,
    y: Sequence[str],
    groups: Sequence[str],
    folds: int = 3,
) -> tuple[dict[str, object], float]:
    """Best PARAM_GRID setting by macro-F1 over grouped folds (a merchant group is never in
    both the fitting and the scoring part). Call it on training data only."""
    model = make()
    # A model without a name TF-IDF (embeddings only) has no n-gram range to tune.
    grid = {k: v for k, v in PARAM_GRID.items() if k in model.get_params()}
    search = GridSearchCV(model, grid, scoring="f1_macro", cv=GroupKFold(n_splits=folds))
    search.fit(x, list(y), groups=list(groups))
    return dict(search.best_params_), float(search.best_score_)


def out_of_fold_confidence(
    model: Pipeline, x: pd.DataFrame, y: Sequence[str], groups: Sequence[str], folds: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """(confidence, correct) for every training row, each predicted by a model that never
    saw its merchant group. Confidence = the highest class probability."""
    labels = list(y)
    proba = cross_val_predict(
        model, x, labels, groups=list(groups), cv=GroupKFold(n_splits=folds),
        method="predict_proba",
    )  # fmt: skip
    classes = np.array(sorted(set(labels)))
    return proba.max(axis=1), classes[proba.argmax(axis=1)] == np.array(labels)


def choose_flag_threshold(
    confidence: np.ndarray, correct: np.ndarray, target: float = FLAG_TARGET
) -> float:
    """Lowest confidence at which rows at or above it are at least `target` correct (so the
    fewest rows go to review). math.inf if no threshold reaches it: review everything."""
    for t in np.unique(confidence):  # ascending
        if correct[confidence >= t].mean() >= target:
            return float(t)
    return math.inf


def flag_stats(
    confidence: np.ndarray, correct: np.ndarray, threshold: float
) -> tuple[float, float, int]:
    """(share of rows sent to review, accuracy on the rows kept, number kept)."""
    kept = confidence >= threshold
    accuracy = float(correct[kept].mean()) if kept.any() else math.nan
    return 1.0 - float(kept.mean()), accuracy, int(kept.sum())


@dataclass(frozen=True)
class CoveragePoint:
    share: float  # share of rows categorized automatically (the most confident ones)
    rows: int
    accuracy: float


def coverage_curve(
    confidence: Sequence[float], correct: Sequence[bool],
    shares: Sequence[float] = (0.90, 0.95, 0.99, 1.0),
) -> list[CoveragePoint]:  # fmt: skip
    """Accuracy when only the most confident share of rows is auto-categorized (the rest
    flagged). Every row gets a category either way; this shows the price of coverage."""
    order = np.argsort(-np.asarray(confidence, dtype=float), kind="stable")
    hits = np.asarray(correct, dtype=bool)[order]
    points = []
    for share in shares:
        n = min(len(hits), max(1, math.ceil(share * len(hits) - 1e-9)))
        points.append(CoveragePoint(share, n, float(hits[:n].mean()) if n else math.nan))
    return points


def reliability_table(confidence: np.ndarray, correct: np.ndarray, bins: int = 5) -> pd.DataFrame:
    """Rows per confidence bin with mean confidence and actual accuracy. If the two columns
    match, the confidence can be read as a probability; few rows per bin prove nothing."""
    index = np.minimum((confidence * bins).astype(int), bins - 1)
    records = []
    for b in range(bins):
        mask = index == b
        records.append(
            {
                "bin": f"{b / bins:.1f}-{(b + 1) / bins:.1f}",
                "rows": int(mask.sum()),
                "mean_confidence": float(confidence[mask].mean()) if mask.any() else math.nan,
                "accuracy": float(correct[mask].mean()) if mask.any() else math.nan,
            }
        )
    return pd.DataFrame(records)


class FoldAverage:
    """Experiment 3b (a control): one model per training part of `splits`, probabilities
    averaged, exactly like CalibratedClassifierCV's default ensemble but without the
    calibration. A category a fold model never saw counts as 0 for that model."""

    def __init__(self, estimator: Pipeline, splits: Sequence[tuple[Sequence[int], object]]) -> None:
        self.estimator, self.splits = estimator, splits

    def fit(self, x: pd.DataFrame, y: Sequence[str]) -> "FoldAverage":
        labels = list(y)
        self.classes_ = np.array(sorted(set(labels)))
        self.models_ = [
            clone(self.estimator).fit(x.iloc[list(train)], [labels[i] for i in train])
            for train, _ in self.splits
        ]
        return self

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        out = np.zeros((len(x), len(self.classes_)))
        for model in self.models_:
            columns = np.searchsorted(self.classes_, model.classes_)
            out[:, columns] += model.predict_proba(x)
        return out / len(self.models_)


# --- The hybrid used by the app (step 5b-ii) ---


@dataclass(frozen=True)
class Prediction:
    category: str
    route: str  # "known merchant", "known merchant, labels disagree", "your preference",
    #             or "new merchant"
    flagged: bool  # low confidence: marked for an optional check; never blocks
    # New merchant: the model's probability. Known merchant: the share of its training
    # labels that agree. Your preference: 1.0.
    confidence: float | None = None


class HybridCategorizer:
    """Known merchant group -> lookup; unseen group -> tuned model + review threshold.

    `params` (model settings) and `threshold` are chosen on the training data in `fit`
    unless given. `preferences` (merchant name key -> category) win over everything.
    """

    def __init__(
        self,
        make: Callable[[], Pipeline] = make_current,
        params: dict[str, object] | None = None,
        threshold: float | None = None,
        preferences: dict[str, str] | None = None,
        folds: int = 3,
        calibrate: bool = False,
        average: bool = False,
    ) -> None:
        if calibrate and average:
            raise ValueError("use either calibrate (which already averages) or average")
        self.average = average  # experiment 3b: the same per-fold averaging, no calibration
        self.make, self.params, self.threshold = make, params, threshold
        self.preferences = preferences or {}
        self.folds = folds
        self.calibrate = calibrate  # experiment 3: sigmoid calibration on grouped folds

    def _train(self, x: pd.DataFrame, y: list[str], groups: list[str]) -> object:
        """The new-merchant model on (x, y). Calibrated: fitted on grouped folds of x, so
        the probabilities reflect merchants the model has not seen."""
        model = self.make().set_params(**self.params_)
        if not (self.calibrate or self.average):
            return model.fit(x, y)
        k = min(self.folds, len(set(groups)))
        splits = list(GroupKFold(n_splits=k).split(x, y, groups))
        if self.average:
            return FoldAverage(model, splits).fit(x, y)
        return CalibratedClassifierCV(model, method="sigmoid", cv=splits).fit(x, y)

    def fit(
        self,
        x: pd.DataFrame,
        y: Sequence[str],
        groups: Sequence[str] | None = None,
        extra_x: pd.DataFrame | None = None,
        extra_y: Sequence[str] | None = None,
    ) -> "HybridCategorizer":
        """`extra_x` / `extra_y` (e.g. synthetic rows) teach the model only: they never
        enter the lookup, and the flag threshold is measured on the main rows only."""
        labels = list(y)
        cv_groups = list(x["group"] if groups is None else groups)
        self.known: dict[str, Counter[str]] = {}
        for group, label in zip(x["group"], labels, strict=True):
            self.known.setdefault(group, Counter())[label] += 1
        if extra_x is not None and extra_y is not None and len(extra_x):
            model_x = pd.concat([x[FEATURE_COLUMNS], extra_x[FEATURE_COLUMNS]], ignore_index=True)
            model_y = labels + list(extra_y)
            model_groups = cv_groups + [f"extra:{g}" for g in extra_x["group"]]
        else:
            model_x, model_y, model_groups = x, labels, cv_groups
        self.params_ = self.params or tune(self.make, model_x, model_y, model_groups, self.folds)[0]
        self.model_ = self._train(model_x, model_y, model_groups)
        if self.threshold is not None:
            self.threshold_ = self.threshold
            return self
        # Out of fold on the main rows only; extra rows are always in training.
        has_extra = model_x is not x
        confidence = np.zeros(len(x))
        correct = np.zeros(len(x), dtype=bool)
        for train_idx, test_idx in GroupKFold(n_splits=self.folds).split(x, labels, cv_groups):
            fold_x = x.iloc[train_idx][FEATURE_COLUMNS]
            fold_y = [labels[i] for i in train_idx]
            fold_groups = [cv_groups[i] for i in train_idx]
            if has_extra:
                fold_x = pd.concat([fold_x, extra_x[FEATURE_COLUMNS]], ignore_index=True)  # type: ignore[index]
                fold_y += list(extra_y)  # type: ignore[arg-type]
                fold_groups += [f"extra:{g}" for g in extra_x["group"]]  # type: ignore[index]
            model = self._train(fold_x, fold_y, fold_groups)
            proba = model.predict_proba(x.iloc[test_idx])  # type: ignore[attr-defined]
            confidence[test_idx] = proba.max(axis=1)
            classes = model.classes_  # type: ignore[attr-defined]
            correct[test_idx] = classes[proba.argmax(axis=1)] == np.array(
                [labels[i] for i in test_idx]
            )
        self.threshold_ = choose_flag_threshold(confidence, correct)
        return self

    def _preference(self, name: str, group: str) -> str | None:
        for key in (name_key(group), name_key(name)):
            if key in self.preferences:
                return self.preferences[key]
        return None

    def predict_rows(self, x: pd.DataFrame) -> list[Prediction]:
        out: list[Prediction | None] = [None] * len(x)
        new: list[int] = []
        for i, (name, group) in enumerate(zip(x["name"], x["group"], strict=True)):
            preferred = self._preference(name, group)
            if preferred is not None:
                out[i] = Prediction(preferred, "your preference", flagged=False, confidence=1.0)
            elif group in self.known:
                counts = self.known[group]
                disagree = len(counts) > 1
                route = "known merchant, labels disagree" if disagree else "known merchant"
                label = _most_common(counts)
                share = counts[label] / sum(counts.values())
                out[i] = Prediction(label, route, flagged=disagree, confidence=share)
            else:
                new.append(i)
        if new:
            proba = self.model_.predict_proba(x.iloc[new])
            classes = self.model_.classes_
            for i, p in zip(new, proba, strict=True):
                confidence = float(p.max())
                out[i] = Prediction(
                    str(classes[p.argmax()]), "new merchant",
                    flagged=confidence < self.threshold_, confidence=confidence,
                )  # fmt: skip
        return [p for p in out if p is not None]

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        return np.array([p.category for p in self.predict_rows(x)], dtype=object)


# --- Counts-only report on a database ---


def _report(db_path: Path, account: str) -> int:
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    txns = [
        Txn(row_id, date.fromisoformat(day), cents, kind, text)
        for row_id, day, cents, kind, text in conn.execute(
            "SELECT id, posted_date, amount_cents, description, sub_description"
            " FROM transactions WHERE account = ?",
            (account,),
        )
    ]
    user = load_user_decisions(conn)
    prefs = load_category_preferences(conn)
    conn.close()

    df = examples(txns, user, prefs)
    groups = df["group"].value_counts()
    print(f"Categorizer input: {len(txns)} rows, account {account}")
    print(f"rows left for the categorizer (with a merchant name): {len(df)}")
    print(f"merchant groups among them: {len(groups)}")
    print(f"  groups with 1 row: {(groups == 1).sum()}, 2-5 rows: {groups.between(2, 5).sum()}, "
          f"6+ rows: {(groups >= 6).sum()}")  # fmt: skip
    _print_label_counts(db_path, account)
    return 0


def _print_label_counts(db_path: Path, account: str) -> None:
    """Counts of your stored labels (step 6), read from the database."""
    from lookahead.labels import label_status_counts  # here: labels imports this module

    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    frozen = (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'label_split_info'"
        ).fetchone()[0]
        and conn.execute("SELECT 1 FROM label_split_info WHERE account = ?", (account,)).fetchone()
    )
    if not frozen:
        conn.close()
        print("labels: none (splits not frozen yet; see python -m lookahead.labels)")
        return
    c = label_status_counts(conn, account)
    conn.close()
    print(
        f"labels: labelled {c['labelled']} rows, skipped {c['skipped']}, cleared {c['cleared']}"
        f" (not reviewed: {c['not reviewed']}, cleared ones included)"
    )
    print(f"merchants done: {c['merchants done']} of {c['merchants']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.categorizer")
    commands = parser.add_subparsers(dest="command", required=True)
    rep = commands.add_parser("report", help="counts-only: what the categorizer would see")
    rep.add_argument("--db", type=Path, required=True)
    rep.add_argument("--account", default="scotia-chequing")
    args = parser.parse_args(argv)
    return _report(args.db, args.account)


if __name__ == "__main__":
    sys.exit(main())
