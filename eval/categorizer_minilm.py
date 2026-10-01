"""Step 5c: sentence embeddings (multilingual MiniLM) as a categorizer candidate, DEV only.

    python -m eval.categorizer_minilm --db data/private/db/lookahead.db

Same protocols, folds, overlap-aware synthetic rows and training (real + synthetic) as the
pre-declared experiments; the holdout is untouched. Variants, declared before running:
  "current":           the model in use (character TF-IDF + lexicon + logistic regression);
  "minilm":            name embeddings instead of the character TF-IDF and lexicon;
  "minilm + current":  name embeddings added to the current features.
Rule, declared before running: a MiniLM variant becomes the dev candidate only if it beats
"current" by at least 1 point of macro-F1 on unseen merchants (it costs a large library and
a 470 MB model, so a tie or a tiny gain keeps the current model); between the two MiniLM
variants the higher macro-F1 wins. It enters the app only if it also wins on Test B (7b).
Embeddings are computed once per distinct name and cached in data/private/cache/.
Prints rates, n and category names only.
"""

import argparse
import json
import sqlite3
import time
import warnings
from dataclasses import asdict
from datetime import date
from functools import partial
from pathlib import Path

import lookahead.embeddings as embeddings
from eval.categorizer_experiments import MIN_GAIN, Variant, VariantResult, _line, _pct, run_variant
from lookahead.categorizer import make_current, make_embedding
from lookahead.labels import labelled_examples
from lookahead.merchants import is_private_path

DEFAULT_OUT = Path("data/private/eval/categorizer_minilm.json")
VARIANTS: dict[str, Variant] = {
    "current": Variant(make_current),
    "minilm": Variant(make_embedding),
    "minilm + current": Variant(partial(make_embedding, with_current=True)),
}


def choose(unseen_macro_f1: dict[str, float]) -> str:
    """The rule, fixed in advance: MiniLM needs at least MIN_GAIN over the current model."""
    best = max((n for n in unseen_macro_f1 if n != "current"), key=lambda n: unseen_macro_f1[n])
    gain = unseen_macro_f1[best] - unseen_macro_f1["current"]
    return best if gain >= MIN_GAIN - 1e-12 else "current"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_minilm")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--account", default="scotia-chequing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=embeddings.DEFAULT_CACHE)
    parser.add_argument("--months", type=int, default=12, help="synthetic months; 0 = none")
    args = parser.parse_args(argv)
    if not (is_private_path(args.out) and is_private_path(args.cache)):
        print("--out and --cache must point inside data/private/ (they describe your data)")
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

    print(f"Step 5c, dev only (holdout untouched): {len(dev)} rows, "
          f"{dev['group'].nunique()} merchant groups; training: real + synthetic "
          f"({args.months} months)")  # fmt: skip
    names = list(dev["name"]) + ([] if extra is None else list(extra["name"]))
    start = time.perf_counter()
    loaded, encoded = embeddings.warm(names, args.cache)
    print(f"embeddings: {loaded} names read from the cache, {encoded} newly computed in "
          f"{time.perf_counter() - start:.0f} s")  # fmt: skip

    results: dict[str, VariantResult] = {}
    for name, variant in VARIANTS.items():
        results[name] = run_variant(dev, extra, variant)
        _line(name, results[name], None if name == "current" else results["current"])
    for name in VARIANTS:
        print(f"per-category recall on unseen merchants, {name} (recall, rows, groups):")
        for category, recall, n, groups in results[name].recall:
            print(f"   {category:<28} {_pct(recall)}  ({n} rows, {groups} groups)")
    chosen = choose({n: r.unseen.macro_f1 for n, r in results.items()})
    print(f"Rule (declared in advance: MiniLM needs +{MIN_GAIN:.0%} macro-F1 on unseen "
          f"merchants): dev candidate = {chosen}")  # fmt: skip

    saved = {
        "decided_at": date.today().isoformat(),
        "embedding_model": embeddings.MODEL_NAME,
        "dev_rows": len(dev),
        "variants": {
            n: {
                "unseen": {k: v for k, v in asdict(r.unseen).items() if k != "curve"},
                "unseen_curve": [asdict(c) for c in r.unseen.curve],
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
