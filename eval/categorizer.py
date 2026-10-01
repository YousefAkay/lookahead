"""Categorizer on synthetic data: Test A (future rows) and Test B (unseen merchants).

    python -m eval.categorizer               # 12 months from the frozen generator

A PIPELINE CHECK, NOT EVIDENCE: after the step 4 rules, the synthetic personas leave few
merchants, each always with one category, so Test A is near 100% by memorization and
Test B rests on a few dozen merchants. Real numbers come from the owner's labels (step 6).

- Test A: per persona, train on rows up to the date at 70% of its rows, test on the rest.
- Test B: grouped 4-fold by the answer key's merchant; each merchant held out once; all
  predictions pooled. A category whose only merchant is held out cannot be learned.
"""

import argparse
from collections.abc import Callable
from datetime import date

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, recall_score
from sklearn.model_selection import GroupKFold

from lookahead.categorizer import MerchantLookup, examples, make_logreg, make_svm
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
    print("Pipeline check only: too few merchants to judge the model. See DECISIONS.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
