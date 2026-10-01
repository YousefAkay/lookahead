"""Step 7a: every categorizer choice made on DEVELOPMENT labels only (holdout untouched).

    python -m eval.categorizer_dev --db data/private/db/lookahead.db

Two protocols inside dev, both with the hybrid (lookup for known merchants, tuned logistic
regression for new ones; tuning nested inside each training part):
- unseen merchants: grouped k-fold over dev merchant groups (like Test B);
- future rows: train on the earliest 70% of dev rows by date, test on the rest (like Test A).
Two training variants: real dev labels only, and real + synthetic rows (synthetic rows
teach the model only, never the lookup, and are never tested on).

Overlap-aware (owner's choice): the synthetic merchants are the Montreal chains, and some
are also the owner's merchants. Whenever a merchant is held out (an unseen-merchants fold,
or a merchant new in the future-rows test period), synthetic rows of the same merchant
(by the step 2 rules: same text, "?" accents, alias list, cut prefix) are dropped from
that training part, so "unseen" really is unseen. The app may still ship with all
synthetic rows: that is "pre-taught chains", not generalization.

Choice rule, fixed before running: the variant with the higher macro-F1 on unseen merchants
(where the model does the work); a tie goes to real only (simpler). Then the final model
settings and the flag threshold (target FLAG_TARGET, provisional) are chosen on all dev
rows and saved, so step 7b scores the holdout once with choices made in advance.
Prints counts, rates and category names only.
"""

import argparse
import json
import math
import sqlite3
import warnings
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupKFold

import lookahead.categorizer as categorizer
from lookahead.categorizer import CoveragePoint, HybridCategorizer, Prediction, coverage_curve
from lookahead.labels import labelled_examples
from lookahead.merchants import is_private_path
from lookahead.resolve import NameInfo, resolve

DEFAULT_OUT = Path("data/private/eval/categorizer_choice.json")
VARIANTS = ("real only", "real + synthetic")
PROTOCOLS = ("unseen merchants", "future rows")
FUTURE_SHARE = 0.7  # train on the earliest 70% of dev rows


@dataclass
class ProtocolResult:
    rows: int
    groups: int
    accuracy: float
    macro_f1: float
    flag_rate: float
    unflagged_accuracy: float
    curve: list[CoveragePoint]


@dataclass
class DevReport:
    dev_rows: int
    dev_groups: int
    results: dict[tuple[str, str], ProtocolResult] = field(default_factory=dict)
    chosen: str = "real only"
    final_params: dict[str, object] = field(default_factory=dict)
    final_threshold: float = math.inf
    dropped: dict[tuple[str, str], int] = field(default_factory=dict)  # synthetic rows dropped


def _score(truth: pd.Series, preds: list[Prediction], groups: pd.Series) -> ProtocolResult:
    got = np.array([p.category for p in preds], dtype=object)
    correct = got == truth.to_numpy()
    flagged = np.array([p.flagged for p in preds])
    confidence = [p.confidence if p.confidence is not None else 0.0 for p in preds]
    labels = sorted(set(truth))
    return ProtocolResult(
        rows=len(preds),
        groups=groups.nunique(),
        accuracy=float(accuracy_score(truth, got)),
        macro_f1=float(f1_score(truth, got, labels=labels, average="macro", zero_division=0)),
        flag_rate=float(flagged.mean()),
        unflagged_accuracy=float(correct[~flagged].mean()) if (~flagged).any() else math.nan,
        curve=coverage_curve(confidence, correct),
    )


def _names(rows: pd.DataFrame) -> list[list[str]]:
    columns = [c for c in ("name", "group", "merchant") if c in rows.columns]
    return [[str(v) for v in values] for values in zip(*(rows[c] for c in columns), strict=True)]


def matches_synthetic(synthetic: pd.DataFrame, tested: pd.DataFrame) -> np.ndarray:
    """True for each synthetic row whose merchant is one of the tested merchants, by the
    step 2 resolution rules (no extra fuzzy matching)."""
    tested_names = {n for row in _names(tested) for n in row}
    synthetic_names = _names(synthetic)
    every = tested_names | {n for row in synthetic_names for n in row}
    resolution = resolve([NameInfo(n) for n in sorted(every)])
    tested_groups = {resolution.group_of(n) for n in tested_names}
    return np.array(
        [any(resolution.group_of(n) in tested_groups for n in row) for row in synthetic_names],
        dtype=bool,
    )


def _without(
    extra: pd.DataFrame | None, tested: pd.DataFrame, dropped: list[int]
) -> pd.DataFrame | None:
    if extra is None or tested.empty:
        return extra
    mask = matches_synthetic(extra, tested)
    dropped.append(int(mask.sum()))
    return extra[~mask]


def _fit(train: pd.DataFrame, extra: pd.DataFrame | None) -> HybridCategorizer:
    if extra is None:
        return HybridCategorizer().fit(train, train["label"])
    return HybridCategorizer().fit(train, train["label"], extra_x=extra, extra_y=extra["label"])


def unseen_merchants(
    dev: pd.DataFrame, extra: pd.DataFrame | None, folds: int = 5,
    dropped: list[int] | None = None,
) -> tuple[pd.Series, list[Prediction], pd.Series]:  # fmt: skip
    k = min(folds, dev["group"].nunique())
    out: list[Prediction | None] = [None] * len(dev)
    for train_idx, test_idx in GroupKFold(n_splits=k).split(dev, groups=dev["group"]):
        fold_extra = _without(extra, dev.iloc[test_idx], dropped if dropped is not None else [])
        model = _fit(dev.iloc[train_idx], fold_extra)
        for i, p in zip(test_idx, model.predict_rows(dev.iloc[test_idx]), strict=True):
            out[i] = p
    return dev["label"], [p for p in out if p is not None], dev["group"]


def future_rows(
    dev: pd.DataFrame, extra: pd.DataFrame | None, dropped: list[int] | None = None
) -> tuple[pd.Series, list[Prediction], pd.Series]:
    dates = dev["posted_date"].sort_values()
    cutoff = dates.iloc[max(0, math.ceil(FUTURE_SHARE * len(dates)) - 1)]
    train, test = dev[dev["posted_date"] <= cutoff], dev[dev["posted_date"] > cutoff]
    held_out = test[~test["group"].isin(set(train["group"]))]  # new in the test period
    model = _fit(train, _without(extra, held_out, dropped if dropped is not None else []))
    return test["label"], model.predict_rows(test), test["group"]


def choose_training(unseen_macro_f1: dict[str, float]) -> str:
    """The rule, fixed in advance: higher macro-F1 on unseen merchants; a tie -> real only."""
    best = max(unseen_macro_f1.values())
    return "real only" if unseen_macro_f1["real only"] >= best else "real + synthetic"


def dev_evaluation(dev: pd.DataFrame, synthetic: pd.DataFrame | None) -> DevReport:
    """All of step 7a on dev rows (labelled, with group, label and features)."""
    dev = dev.reset_index(drop=True)
    report = DevReport(len(dev), dev["group"].nunique())
    variants = {"real only": None, **({"real + synthetic": synthetic} if synthetic is not None
                                      else {})}  # fmt: skip
    for variant, extra in variants.items():
        dropped_unseen: list[int] = []
        dropped_future: list[int] = []
        report.results[(variant, "unseen merchants")] = _score(
            *unseen_merchants(dev, extra, dropped=dropped_unseen)
        )
        report.results[(variant, "future rows")] = _score(
            *future_rows(dev, extra, dropped=dropped_future)
        )
        report.dropped[(variant, "unseen merchants")] = sum(dropped_unseen)
        report.dropped[(variant, "future rows")] = sum(dropped_future)
    report.chosen = choose_training(
        {v: report.results[(v, "unseen merchants")].macro_f1 for v in variants}
    )
    final = _fit(dev, variants[report.chosen])
    report.final_params = final.params_
    report.final_threshold = final.threshold_
    return report


def _pct(x: float) -> str:
    return "  n/a" if math.isnan(x) else f"{x:5.1%}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_dev")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--account", default="scotia-chequing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--months", type=int, default=12, help="synthetic months; 0 = none")
    args = parser.parse_args(argv)
    if not is_private_path(args.out):
        print("--out must point inside data/private/ (it describes your data)")
        return 2
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    conn = sqlite3.connect(f"{args.db.resolve().as_uri()}?mode=ro", uri=True)
    dev = labelled_examples(conn, args.account, ("dev",))
    conn.close()
    if dev.empty:
        print("no labelled dev rows: freeze the splits and label first (step 6)")
        return 2
    synthetic = None
    if args.months > 0:
        from eval.categorizer import synthetic_examples

        synthetic = synthetic_examples(args.months)

    warnings.filterwarnings("ignore")  # small folds: missing classes, slow convergence
    r = dev_evaluation(dev, synthetic)
    print(f"Step 7a, dev only (holdout untouched): {r.dev_rows} labelled rows, "
          f"{r.dev_groups} merchant groups")  # fmt: skip
    print("  curve = accuracy when only the most confident 90 / 95 / 99 / 100% of rows are")
    print("  categorized without a flag (every row still gets a category)")
    for (variant, protocol), p in r.results.items():
        curve = "  ".join(f"{c.share:.0%}: {_pct(c.accuracy)} (n={c.rows})" for c in p.curve)
        print(f"{variant:<17} {protocol:<17} rows {p.rows:>4}, groups {p.groups:>3}: "
              f"accuracy {_pct(p.accuracy)}, macro-F1 {_pct(p.macro_f1)}, flagged "
              f"{_pct(p.flag_rate)}, accuracy unflagged {_pct(p.unflagged_accuracy)}")  # fmt: skip
        print(f"    curve {curve}")
        if variant == "real + synthetic":
            print(f"    synthetic rows dropped because their merchant was held out: "
                  f"{r.dropped[(variant, protocol)]} (summed over folds)")  # fmt: skip
    print("Choice rule (fixed in advance): higher macro-F1 on unseen merchants; tie -> real only")
    print(f"Chosen: {r.chosen}")
    c, grams = r.final_params.get("model__C"), r.final_params.get("features__name__ngram_range")
    threshold = "none reaches the target: every new merchant flagged" if math.isinf(
        r.final_threshold) else f"{r.final_threshold:.0%} model confidence"  # fmt: skip
    print(f"Final settings, tuned on all dev rows: C {c}, n-grams {grams}; flag threshold "
          f"{threshold} (target {categorizer.FLAG_TARGET:.0%}, provisional)")  # fmt: skip

    saved = {
        "decided_at": date.today().isoformat(),
        "account": args.account,
        "training": r.chosen,
        "synthetic_months": args.months if r.chosen == "real + synthetic" else 0,
        "params": {k: list(v) if isinstance(v, tuple) else v for k, v in r.final_params.items()},
        "flag_threshold": None if math.isinf(r.final_threshold) else r.final_threshold,
        "flag_target": categorizer.FLAG_TARGET,
        "dev_rows": r.dev_rows,
        "dev_groups": r.dev_groups,
        "dev_results": {
            f"{v} / {p}": {k: val for k, val in asdict(res).items() if k != "curve"}
            for (v, p), res in r.results.items()
        },  # fmt: skip
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"Saved the choice to {args.out} (step 7b reads it; scores the holdout once)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
