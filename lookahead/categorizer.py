"""Transaction categorizer (step 5): features, baselines and the main model.

Only rows the category rules (step 4) leave for the categorizer are used, for training and
for predicting. Features (HANDOFF section 07):
- name: the extracted merchant name (plus the restaurant for delivery orders), as character
  n-grams of 2-5 characters within words, so new store numbers and spellings still match;
- channel (online / in person / none), transaction type, direction, amount bucket.
Models:
- MerchantLookup (baseline 1): reuse the most common label of a known merchant group;
- TF-IDF + linear SVM (baseline 2);
- TF-IDF + logistic regression (main model: it gives probabilities, for "needs review").
Hyperparameters are defaults here; step 5b tunes them with grouped cross-validation.

Counts-only check on a database (no labels exist before step 6, so no accuracy):
    python -m lookahead.categorizer report --db data/private/db/lookahead.db
"""

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.svm import LinearSVC

from lookahead.categories import category_by_rules, load_category_preferences
from lookahead.flows import Txn
from lookahead.merchants import extract_merchant
from lookahead.resolve import UserDecisions, load_user_decisions, name_infos, resolve

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


def _features() -> ColumnTransformer:
    return ColumnTransformer(
        [
            (
                "name",
                TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True),
                "name",
            ),
            ("meta", OneHotEncoder(handle_unknown="ignore"), META_COLUMNS),
        ]
    )


def make_svm(c: float = 1.0) -> Pipeline:
    """Baseline 2: character TF-IDF + linear SVM (no probabilities)."""
    return Pipeline([("features", _features()), ("model", LinearSVC(C=c))])


def make_logreg(c: float = 1.0) -> Pipeline:
    """Main model: character TF-IDF + logistic regression (probabilities for review)."""
    return Pipeline([("features", _features()), ("model", LogisticRegression(C=c, max_iter=5000))])


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
    print("labels stored: 0 (labelling starts in step 6; no accuracy before that)")
    return 0


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
