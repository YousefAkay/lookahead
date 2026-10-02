"""Point 4a: Claude (Haiku 4.5) as a categorizer for NEW card merchants, DEV only.

    python -m eval.categorizer_llm --db data/private/db/lookahead.db --dry-run
    python -m eval.categorizer_llm --db data/private/db/lookahead.db

Same protocols, folds and training as step 5c; the holdout is untouched (no holdout name
is sent). Known merchants always use the lookup. Variants, declared before running:
  "current":                       the model in use;
  "llm for new merchants":         the LLM's answer replaces the model's for every new
                                   merchant;
  "llm for flagged new merchants": only where the model's answer is flagged (the fallback
                                   as it would work in the app).
A name that may not be sent, or an answer that is not a category, keeps the model's answer.
Zero-shot: the prompt has the category list only, no example from my data.
Rule, declared before running: the LLM becomes a fallback candidate only if a variant beats
"current" by at least 1 point of macro-F1 on unseen merchants.
What leaves the machine: card-merchant names only (rules in lookahead/llm_categorizer.py),
one call per name, cached in data/private/cache/. --dry-run sends nothing.
Prints rates, counts, cost and category names only.
"""

import argparse
import json
import sqlite3
import statistics
import warnings
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import pandas as pd

import lookahead.llm_categorizer as llm
from eval.categorizer_dev import _fit, _score, future_rows, per_category_recall, unseen_merchants
from eval.categorizer_experiments import MIN_GAIN, VariantResult, _line, _pct
from eval.categorizer_minilm import choose
from lookahead.categorizer import HybridCategorizer, Prediction
from lookahead.labels import labelled_examples
from lookahead.merchants import is_private_path
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key

DEFAULT_OUT = Path("data/private/eval/categorizer_llm.json")


class WithLLM:
    """A fitted hybrid whose new-merchant answers come from saved LLM answers."""

    def __init__(
        self, base: HybridCategorizer, answers: dict[str, str], key: bytes, flagged_only: bool
    ) -> None:
        self.base, self.answers, self.key, self.flagged_only = base, answers, key, flagged_only

    def predict_rows(self, x: pd.DataFrame) -> list[Prediction]:
        out = []
        rows = zip(self.base.predict_rows(x), x["txn_type"], x["text"], strict=True)
        for p, txn_type, text in rows:
            if p.route == "new merchant" and (p.flagged or not self.flagged_only):
                name = llm.sendable_name(txn_type, text, self.key)
                category = self.answers.get(name) if name else None
                if category:
                    p = replace(p, category=category, route="new merchant, LLM")
            out.append(p)
        return out


def run(
    dev: pd.DataFrame, extra: pd.DataFrame | None, answers: dict[str, str] | None, key: bytes,
    flagged_only: bool = False,
) -> VariantResult:  # fmt: skip
    def fit(train: pd.DataFrame, fold_extra: pd.DataFrame | None) -> object:
        base = _fit(train, fold_extra)
        return base if answers is None else WithLLM(base, answers, key, flagged_only)

    truth, preds, groups = unseen_merchants(dev, extra, fit=fit)  # type: ignore[arg-type]
    recall = per_category_recall(truth, [p.category for p in preds], groups)
    future = _score(*future_rows(dev, extra, fit=fit))  # type: ignore[arg-type]
    return VariantResult(_score(truth, preds, groups), future, recall)


def main(argv: list[str] | None = None, client: object | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_llm")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--account", default="scotia-chequing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=llm.DEFAULT_CACHE)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--months", type=int, default=12, help="synthetic months; 0 = none")
    parser.add_argument("--dry-run", action="store_true", help="count names; send nothing")
    args = parser.parse_args(argv)
    if not (is_private_path(args.out) and is_private_path(args.cache)):
        print("--out and --cache must point inside data/private/ (they describe your data)")
        return 2
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    try:
        key = pseudonym_key(args.env)
    except PseudonymKeyError as e:
        print(f"{e} (the person rules need it to decide what may be sent)")
        return 2
    conn = sqlite3.connect(f"{args.db.resolve().as_uri()}?mode=ro", uri=True)
    dev = labelled_examples(conn, args.account, ("dev",)).reset_index(drop=True)
    conn.close()
    if dev.empty:
        print("no labelled dev rows: freeze the splits and label first (step 6)")
        return 2

    sendable = [
        llm.sendable_name(t, text, key)
        for t, text in zip(dev["txn_type"], dev["text"], strict=True)
    ]
    names = {n for n in sendable if n}
    cached = len(names & set(llm.load_cache(args.cache)))
    print(f"Point 4a, dev only (holdout untouched): {len(dev)} rows, "
          f"{dev['group'].nunique()} groups; model {llm.MODEL}, temperature 0")  # fmt: skip
    print(f"names that may be sent: {len(names)} distinct card-merchant names "
          f"({sum(n is not None for n in sendable)} rows); rows whose name is never sent: "
          f"{sum(n is None for n in sendable)}")  # fmt: skip
    print(f"already answered (cache): {cached}; calls needed: {len(names) - cached}")
    if args.dry_run:
        print("dry run: nothing was sent")
        return 0

    try:
        answers, calls = llm.classify(names, args.cache, client)
    except Exception as e:  # the message may quote a request: print the error type only
        print(f"stopped: {type(e).__name__} (answers so far are saved; run again to continue)")
        return 1
    usage_in = sum(a.input_tokens for a in answers.values())
    usage_out = sum(a.output_tokens for a in answers.values())
    cost = sum(a.cost_usd for a in answers.values())
    invalid = sum(a.category is None for a in answers.values())
    latency = statistics.median(a.latency_s for a in answers.values()) if answers else 0.0
    per_thousand = cost / len(answers) * 1000 if answers else 0.0
    print(f"calls made now: {calls}; answers that are not a category: {invalid}")
    print(f"tokens: {usage_in} in, {usage_out} out; cost of all {len(answers)} names: "
          f"{cost:.3f} USD; cost per 1,000 merchants: {per_thousand:.3f} USD; "
          f"median latency: {latency * 1000:.0f} ms per name")  # fmt: skip

    extra = None
    if args.months > 0:
        from eval.categorizer import synthetic_examples

        extra = synthetic_examples(args.months)
    warnings.filterwarnings("ignore")
    categories = {n: a.category for n, a in answers.items() if a.category}
    results = {
        "current": run(dev, extra, None, key),
        "llm for new merchants": run(dev, extra, categories, key),
        "llm for flagged new merchants": run(dev, extra, categories, key, flagged_only=True),
    }
    for name, r in results.items():
        _line(name, r, None if name == "current" else results["current"])
    for name in ("current", "llm for new merchants"):
        print(f"per-category recall on unseen merchants, {name} (recall, rows, groups):")
        for category, recall, n, groups in results[name].recall:
            print(f"   {category:<28} {_pct(recall)}  ({n} rows, {groups} groups)")
    chosen = choose({n: r.unseen.macro_f1 for n, r in results.items()})
    candidate = "none" if chosen == "current" else chosen
    print(f"Rule (declared in advance: the LLM needs +{MIN_GAIN:.0%} macro-F1 on unseen "
          f"merchants): fallback candidate = {candidate}")  # fmt: skip

    saved = {
        "decided_at": date.today().isoformat(),
        "model": llm.MODEL,
        "temperature": llm.TEMPERATURE,
        "names_sent": len(answers),
        "invalid_answers": invalid,
        "input_tokens": usage_in,
        "output_tokens": usage_out,
        "cost_usd": cost,
        "cost_per_1000_merchants_usd": per_thousand,
        "median_latency_s": latency,
        "variants": {
            n: {
                "unseen": {k: v for k, v in asdict(r.unseen).items() if k != "curve"},
                "future": {k: v for k, v in asdict(r.future).items() if k != "curve"},
            }
            for n, r in results.items()
        },
        "chosen": chosen,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"Saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
