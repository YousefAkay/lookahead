"""Pre-declared categorizer experiments (DECISIONS.md), all on DEV labels; holdout untouched.

    python -m eval.categorizer_experiments --db data/private/db/lookahead.db

Same protocols as step 7a (unseen merchants, future rows; overlap-aware synthetic rows),
training fixed at real + synthetic (chosen in 7a), same choice rule (macro-F1 on unseen
merchants). Variants, declared before running:
  0. per-category recall on unseen merchants for the current model (with n);
  1. "balanced": class_weight="balanced" (and the SVM with and without it, for comparison);
  2. "lexicon": the frozen keyword lexicon as extra features;
  3. "calibrated": sigmoid calibration on grouped folds inside each training part;
  3b. "averaged" (a control): the same per-fold model averaging, without calibration, to
     separate the ensemble effect (averaged - current) from the calibration effect
     (calibrated - averaged); not a combination candidate (calibrated already averages);
  then "combined": every change that gained >= 1 point of unseen-merchant macro-F1 alone.
Flag rule, declared before running: if the calibrated curve rises clearly (accuracy at 90%
coverage >= accuracy at 100% + 5 points), keep a confidence threshold for new merchants;
otherwise flag every new merchant. Prints rates, n and category names only.
"""

import argparse
import json
import math
import sqlite3
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupKFold

import lookahead.categorizer as categorizer
from eval.categorizer_dev import (
    ProtocolResult,
    _score,
    _without,
    future_rows,
    per_category_recall,
    unseen_merchants,
)
from lookahead.categorizer import CoveragePoint, HybridCategorizer, make_logreg, make_svm
from lookahead.labels import labelled_examples
from lookahead.merchants import is_private_path

DEFAULT_OUT = Path("data/private/eval/categorizer_experiments.json")
MIN_GAIN = 0.01  # a change joins "combined" if it gains >= 1 point of macro-F1 alone
CLEAR_RISE = 0.05  # flag rule: accuracy at 90% coverage >= accuracy at 100% + 5 points


@dataclass(frozen=True)
class Variant:
    make: Callable[[], object]
    calibrate: bool = False
    average: bool = False


# "current" is the model before these experiments (no lexicon), kept as the baseline so a
# rerun matches the logged results. The winner (lexicon) is now `categorizer.make_current`.
VARIANTS: dict[str, Variant] = {
    "current": Variant(make_logreg),
    "balanced": Variant(partial(make_logreg, balanced=True)),
    "lexicon": Variant(partial(make_logreg, lexicon=True)),
    "calibrated": Variant(make_logreg, calibrate=True),
    "averaged": Variant(make_logreg, average=True),
}
COMBINABLE = ("balanced", "lexicon", "calibrated")  # declared; "averaged" is a control


def combined_variant(changes: list[str]) -> Variant:
    options = {k: True for k in ("balanced", "lexicon") if k in changes}
    return Variant(partial(make_logreg, **options), calibrate="calibrated" in changes)


def combined_changes(gains: dict[str, float]) -> list[str]:
    """Declared in advance: every change that gained at least MIN_GAIN on its own."""
    return [name for name, gain in gains.items() if gain >= MIN_GAIN - 1e-12]


def flag_rule(curve: list[CoveragePoint]) -> str:
    """Declared in advance: keep a threshold only if confidence clearly sorts the answers."""
    at = {round(p.share, 2): p.accuracy for p in curve}
    rises = at[0.9] - at[1.0] >= CLEAR_RISE - 1e-12
    return "confidence threshold" if rises else "always flag new merchants"


def _fitter(variant: Variant) -> Callable[[pd.DataFrame, pd.DataFrame | None], HybridCategorizer]:
    def fit(train: pd.DataFrame, extra: pd.DataFrame | None) -> HybridCategorizer:
        model = HybridCategorizer(
            make=variant.make,
            calibrate=variant.calibrate,
            average=variant.average,  # type: ignore[arg-type]
        )
        if extra is None:
            return model.fit(train, train["label"])
        return model.fit(train, train["label"], extra_x=extra, extra_y=extra["label"])

    return fit


@dataclass
class VariantResult:
    unseen: ProtocolResult
    future: ProtocolResult
    recall: list[tuple[str, float, int, int]] = field(default_factory=list)


def run_variant(dev: pd.DataFrame, extra: pd.DataFrame | None, variant: Variant) -> VariantResult:
    fit = _fitter(variant)
    truth, preds, groups = unseen_merchants(dev, extra, fit=fit)
    recall = per_category_recall(truth, [p.category for p in preds], groups)
    return VariantResult(_score(truth, preds, groups), _score(*future_rows(dev, extra, fit=fit)),
                         recall)  # fmt: skip


def svm_unseen(
    dev: pd.DataFrame, extra: pd.DataFrame | None, balanced: bool
) -> tuple[float, float]:
    """The SVM alone on unseen merchants (no probabilities: accuracy and macro-F1 only)."""
    make = partial(make_svm, balanced=balanced)
    got = np.empty(len(dev), dtype=object)
    k = min(5, dev["group"].nunique())
    for train_idx, test_idx in GroupKFold(n_splits=k).split(dev, groups=dev["group"]):
        train, test = dev.iloc[train_idx], dev.iloc[test_idx]
        fold_extra = _without(extra, test, [])
        x, y, g = train, list(train["label"]), list(train["group"])
        if fold_extra is not None and len(fold_extra):
            cols = categorizer.FEATURE_COLUMNS
            x = pd.concat([train[cols], fold_extra[cols]], ignore_index=True)
            y += list(fold_extra["label"])
            g += [f"extra:{v}" for v in fold_extra["group"]]
        params, _ = categorizer.tune(make, x, y, g)  # type: ignore[arg-type]
        got[test_idx] = make().set_params(**params).fit(x, y).predict(test)
    labels = sorted(set(dev["label"]))
    truth = dev["label"]
    return float(accuracy_score(truth, got)), float(
        f1_score(truth, got, labels=labels, average="macro", zero_division=0)
    )


def _pct(x: float) -> str:
    return "  n/a" if math.isnan(x) else f"{x:5.1%}"


def _line(name: str, r: VariantResult, base: VariantResult | None) -> None:
    u = r.unseen
    gain = "" if base is None else f" ({u.macro_f1 - base.unseen.macro_f1:+.1%} vs current)"
    curve = "  ".join(f"{c.share:.0%}: {_pct(c.accuracy)} (n={c.rows})" for c in u.curve)
    print(f"{name:<11} unseen merchants: accuracy {_pct(u.accuracy)}, macro-F1 "
          f"{_pct(u.macro_f1)}{gain}; flagged {_pct(u.flag_rate)}, unflagged "
          f"{_pct(u.unflagged_accuracy)}")  # fmt: skip
    print(f"{'':<11} curve {curve}")
    f = r.future
    print(f"{'':<11} future rows: accuracy {_pct(f.accuracy)}, macro-F1 {_pct(f.macro_f1)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_experiments")
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
    dev = labelled_examples(conn, args.account, ("dev",)).reset_index(drop=True)
    conn.close()
    if dev.empty:
        print("no labelled dev rows: freeze the splits and label first (step 6)")
        return 2
    extra = None
    if args.months > 0:
        from eval.categorizer import synthetic_examples

        extra = synthetic_examples(args.months)
    warnings.filterwarnings("ignore")

    print(f"Pre-declared experiments, dev only (holdout untouched): {len(dev)} rows, "
          f"{dev['group'].nunique()} merchant groups; training: real + synthetic "
          f"({args.months} months)")  # fmt: skip
    results: dict[str, VariantResult] = {}
    for name, variant in VARIANTS.items():
        results[name] = run_variant(dev, extra, variant)
        _line(name, results[name], None if name == "current" else results["current"])

    print("0. per-category recall on unseen merchants, current model (recall, rows, groups):")
    for category, recall, n, groups in results["current"].recall:
        print(f"   {category:<28} {_pct(recall)}  ({n} rows, {groups} groups)")

    base = results["current"].unseen.macro_f1
    gains = {n: results[n].unseen.macro_f1 - base for n in COMBINABLE}
    changes = combined_changes(gains)
    if len(changes) >= 2:
        results["combined"] = run_variant(dev, extra, combined_variant(changes))
        _line(f"combined ({' + '.join(changes)})", results["combined"], results["current"])
    else:
        print(f"combined: not run ({'only ' + changes[0] if changes else 'no change'} gained "
              f">= {MIN_GAIN:.0%} on its own)")  # fmt: skip

    for balanced in (False, True):
        acc, f1 = svm_unseen(dev, extra, balanced)
        label = "SVM balanced" if balanced else "SVM"
        print(f"{label:<13} unseen merchants: accuracy {_pct(acc)}, macro-F1 {_pct(f1)} "
              f"(comparison only: no probabilities)")  # fmt: skip

    def rise(name: str) -> float:
        at = {round(p.share, 2): p.accuracy for p in results[name].unseen.curve}
        return at[0.9] - at[1.0]

    cur, avg, cal = (results[n].unseen for n in ("current", "averaged", "calibrated"))
    ensemble_f1, ensemble_acc = avg.macro_f1 - cur.macro_f1, avg.accuracy - cur.accuracy
    calib_f1, calib_acc = cal.macro_f1 - avg.macro_f1, cal.accuracy - avg.accuracy
    print(
        f"3b split (unseen merchants): ensemble effect = averaged - current: macro-F1 "
        f"{ensemble_f1:+.1%}, accuracy {ensemble_acc:+.1%}; calibration effect = calibrated"
        f" - averaged: macro-F1 {calib_f1:+.1%}, accuracy {calib_acc:+.1%}"
    )
    print(f"   curve rise (90% minus 100% coverage): current {rise('current'):+.1%}, averaged "
          f"{rise('averaged'):+.1%}, calibrated {rise('calibrated'):+.1%}")  # fmt: skip
    rule = flag_rule(results["calibrated"].unseen.curve)
    at = {round(p.share, 2): p.accuracy for p in results["calibrated"].unseen.curve}
    print(f"Flag rule (declared in advance; calibrated curve 90% {_pct(at[0.9])} vs 100% "
          f"{_pct(at[1.0])}, needs +{CLEAR_RISE:.0%}): {rule}")  # fmt: skip
    best = max(results, key=lambda n: results[n].unseen.macro_f1)
    print(f"Best on the choice rule (macro-F1 on unseen merchants): {best}")

    saved = {
        "decided_at": date.today().isoformat(),
        "dev_rows": len(dev),
        "variants": {
            n: {
                "unseen": {k: v for k, v in asdict(r.unseen).items() if k != "curve"},
                "unseen_curve": [asdict(c) for c in r.unseen.curve],
                "future": {k: v for k, v in asdict(r.future).items() if k != "curve"},
            }
            for n, r in results.items()
        },  # fmt: skip
        "combined_changes": changes,
        "flag_rule": rule,
        "best": best,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"Saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
