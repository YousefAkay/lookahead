"""Categorizer on synthetic data: Test A (future rows) and Test B (unseen merchants).

    python -m eval.categorizer               # 12 months from the frozen generator

A PIPELINE CHECK, NOT EVIDENCE: after the step 4 rules, the synthetic personas leave few
merchants, each always with one category, so Test A is near 100% by memorization and
Test B rests on a few dozen merchants. Real numbers come from the owner's labels (step 6).

- Test A: per persona, train on rows up to the date at 70% of its rows, test on the rest.
- Test B: grouped 4-fold by the answer key's merchant; each merchant held out once; all
  predictions pooled. A category whose only merchant is held out cannot be learned.
- Step 5b ("tuned"): C and the n-gram range chosen by grouped 3-fold cross-validation on
  the training part only (for Test B: inside each outer fold, so nested). The review
  threshold comes from out-of-fold probabilities on that same training part.
"""

import argparse
import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, recall_score
from sklearn.model_selection import GroupKFold

from lookahead.categorizer import (
    REVIEW_TARGET,
    MerchantLookup,
    choose_review_threshold,
    examples,
    make_logreg,
    make_svm,
    out_of_fold_confidence,
    reliability_table,
    tune,
)
from lookahead.flows import Txn
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import export_rows

EXPORT_DATE = date(2026, 9, 30)
MODELS: dict[str, Callable[[], object]] = {
    "merchant lookup": MerchantLookup,
    "TF-IDF + linear SVM": make_svm,
    "TF-IDF + logistic regression": make_logreg,
}


def _start(months: int) -> date:
    """First day of the month `months - 1` months before EXPORT_DATE's month."""
    index = EXPORT_DATE.year * 12 + EXPORT_DATE.month - 1 - (months - 1)
    return date(index // 12, index % 12 + 1, 1)


def synthetic_examples(months: int = 12, seed: int = 42) -> pd.DataFrame:
    """Rows the rules leave for the categorizer, from each persona (a separate account),
    with the answer key's category (`label`) and merchant (`merchant`)."""
    frames = []
    offset = 0
    for pid in "ABC":
        rows = export_rows(PERSONAS[pid], _start(months), EXPORT_DATE, seed)
        txns = [
            Txn(offset + i, t.posted_date, t.amount_cents, t.description, t.sub_description)
            for i, t in enumerate(rows)
        ]
        df = examples(txns)
        truth = {offset + i: t for i, t in enumerate(rows)}
        df["label"] = [truth[r].category for r in df["row_id"]]
        df["merchant"] = [truth[r].merchant for r in df["row_id"]]
        df["persona"] = pid
        frames.append(df)
        offset += len(rows)
    return pd.concat(frames, ignore_index=True)


def time_split(df: pd.DataFrame, share: float = 0.7) -> pd.Series:
    """True for training rows: per persona, rows up to the date at `share` of its rows."""
    train = pd.Series(False, index=df.index)
    for _, rows in df.groupby("persona"):
        dates = rows["posted_date"].sort_values()
        cutoff = dates.iloc[max(0, int(np.ceil(share * len(dates))) - 1)]
        train[rows.index] = rows["posted_date"] <= cutoff
    return train


def grouped_folds(df: pd.DataFrame, k: int = 4) -> list[tuple[np.ndarray, np.ndarray]]:
    """Folds with no merchant in both training and test (by the answer key's merchant)."""
    return list(GroupKFold(n_splits=k).split(df, groups=df["merchant"]))


def _scores(y_true: pd.Series, y_pred: np.ndarray) -> tuple[float, float]:
    labels = sorted(set(y_true))
    return (
        accuracy_score(y_true, y_pred),
        f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0),
    )


@dataclass
class TunedRun:
    """Predictions of one tuned model on a test set, with review decisions (logreg)."""

    predictions: np.ndarray
    confidence: np.ndarray | None = None  # logistic regression only
    accepted: np.ndarray | None = None  # not sent to review
    params: list[dict[str, object]] = field(default_factory=list)  # chosen per fit
    thresholds: list[float] = field(default_factory=list)


def fit_tuned(
    make: Callable[[], object], train: pd.DataFrame, test: pd.DataFrame, run: TunedRun,
    test_idx: np.ndarray,
) -> None:  # fmt: skip
    """Tune on `train` only, fit, predict `test` into `run` at positions `test_idx`."""
    y, groups = list(train["label"]), list(train["merchant"])
    params, _ = tune(make, train, y, groups)  # type: ignore[arg-type]
    model = make().set_params(**params).fit(train, y)  # type: ignore[attr-defined]
    run.params.append(params)
    run.predictions[test_idx] = model.predict(test)
    if run.confidence is not None and run.accepted is not None:
        conf, correct = out_of_fold_confidence(make().set_params(**params), train, y, groups)  # type: ignore[attr-defined]
        threshold = choose_review_threshold(conf, correct)
        run.thresholds.append(threshold)
        test_conf = model.predict_proba(test).max(axis=1)
        run.confidence[test_idx] = test_conf
        run.accepted[test_idx] = test_conf >= threshold


def _new_run(n: int, with_review: bool) -> TunedRun:
    return TunedRun(
        np.empty(n, dtype=object),
        np.zeros(n) if with_review else None,
        np.zeros(n, dtype=bool) if with_review else None,
    )


def tuned_test_a(df: pd.DataFrame, make: Callable[[], object], with_review: bool) -> TunedRun:
    train = time_split(df)
    test = df[~train]
    run = _new_run(len(test), with_review)
    fit_tuned(make, df[train], test, run, np.arange(len(test)))
    return run


def tuned_test_b(df: pd.DataFrame, make: Callable[[], object], with_review: bool) -> TunedRun:
    run = _new_run(len(df), with_review)
    for train_idx, test_idx in grouped_folds(df):
        fit_tuned(make, df.iloc[train_idx], df.iloc[test_idx], run, test_idx)
    return run


def _print_tuned(label: str, y: pd.Series, run: TunedRun) -> None:
    acc, f1 = _scores(y, run.predictions)
    cs = sorted({p["model__C"] for p in run.params})  # type: ignore[type-var]
    grams = sorted({p["features__name__ngram_range"] for p in run.params})  # type: ignore[type-var]
    print(f"  {label:<30} accuracy {acc:6.1%}   macro-F1 {f1:6.1%}   (C {cs}, n-grams {grams})")
    if run.confidence is not None and run.accepted is not None:
        correct = run.predictions == y.to_numpy()
        kept = run.accepted
        rate = 1 - kept.mean()
        rest = correct[kept].mean() if kept.any() else math.nan
        shown = ", ".join("all" if math.isinf(t) else f"{t:.2f}" for t in run.thresholds)
        print(
            f"    needs review: {rate:.1%} of rows; accuracy on the rest {rest:.1%} "
            f"({kept.sum()} rows); threshold(s) {shown}; target {REVIEW_TARGET:.0%}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer")
    parser.add_argument("--months", type=int, default=12)
    args = parser.parse_args(argv)
    df = synthetic_examples(args.months)
    x, y = df, df["label"]
    print(
        f"Categorizer on SYNTHETIC data ({args.months} months, 3 personas): {len(df)} rows "
        f"left for the categorizer, {df['merchant'].nunique()} merchants, "
        f"{y.nunique()} categories"
    )

    train = time_split(df)
    print(f"Test A (future rows): train {train.sum()} rows, test {(~train).sum()} rows")
    for name, make in MODELS.items():
        model = make().fit(x[train], y[train])  # type: ignore[attr-defined]
        acc, f1 = _scores(y[~train], model.predict(x[~train]))
        print(f"  {name:<30} accuracy {acc:6.1%}   macro-F1 {f1:6.1%}")

    folds = grouped_folds(df)
    print(
        f"Test B (unseen merchants): grouped 4-fold over {df['merchant'].nunique()} merchants, "
        f"all {len(df)} rows tested once"
    )
    pooled: dict[str, np.ndarray] = {}
    for name, make in MODELS.items():
        pred = np.empty(len(df), dtype=object)
        for train_idx, test_idx in folds:
            model = make().fit(x.iloc[train_idx], y.iloc[train_idx])  # type: ignore[attr-defined]
            pred[test_idx] = model.predict(x.iloc[test_idx])
        pooled[name] = pred
        acc, f1 = _scores(y, pred)
        print(f"  {name:<30} accuracy {acc:6.1%}   macro-F1 {f1:6.1%}")

    print("Test B recall per category (logistic regression), with n rows / n merchants:")
    labels = sorted(set(y))
    recalls = recall_score(
        y, pooled["TF-IDF + logistic regression"], labels=labels, average=None, zero_division=0
    )
    for label, r in zip(labels, recalls, strict=True):
        rows = df[df["label"] == label]
        print(
            f"  {label:<28} {r:6.1%}  ({len(rows)} rows / {rows['merchant'].nunique()} merchants)"
        )

    warnings.filterwarnings("ignore")  # tiny folds: missing classes and slow convergence
    print("Tuned (step 5b), Test A:")
    test_a = df[~train]
    for name, make, review in (("TF-IDF + linear SVM", make_svm, False),
                               ("TF-IDF + logistic regression", make_logreg, True)):  # fmt: skip
        _print_tuned(name, test_a["label"], tuned_test_a(df, make, review))
    print("Tuned (step 5b), Test B (nested: tuning inside each outer fold):")
    b_runs = {}
    for name, make, review in (("TF-IDF + linear SVM", make_svm, False),
                               ("TF-IDF + logistic regression", make_logreg, True)):  # fmt: skip
        b_runs[name] = tuned_test_b(df, make, review)
        _print_tuned(name, y, b_runs[name])
    lr = b_runs["TF-IDF + logistic regression"]
    assert lr.confidence is not None
    table = reliability_table(lr.confidence, lr.predictions == y.to_numpy())
    print("Reliability, Test B, tuned logistic regression (confidence vs actual accuracy):")
    for r in table.itertuples():
        if r.rows:
            print(f"  {r.bin}: {r.rows:>4} rows, mean confidence {r.mean_confidence:6.1%}, "
                  f"accuracy {r.accuracy:6.1%}")  # fmt: skip
    print("Pipeline check only: too few merchants to judge the model. See DECISIONS.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
