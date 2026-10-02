"""Step 7b: the holdout (Test A and Test B), scored ONCE with choices made in advance.

    python -m eval.categorizer_holdout --db data/private/db/lookahead.db --dry-run
    python -m eval.categorizer_holdout --db data/private/db/lookahead.db

Nothing is chosen here. The model is trained once on all dev labels with the settings
saved by step 7a (training variant, C, n-grams, flag threshold), then it predicts:
- Test A: rows after the date cutoff (mostly known merchants);
- Test B: merchant groups never seen in training, reported in two parts: merchants that
  are in the synthetic list ("pre-taught chains", not generalization) and those that are not.
Candidates, declared before running (DECISIONS.md):
  "trained hybrid":         the default and the headline result;
  "hybrid + LLM fallback":  the LLM answers for every new merchant (the 4a candidate);
  extra line, not a candidate: the LLM only for flagged new merchants.
Skipped rows are not scored. Holdout card-merchant names go to the LLM once, under the
same rules and cache as 4a. --dry-run sends nothing, scores nothing and does not count as
the run. After a real run the script refuses to run again unless --again is given, which
is recorded. Prints rates, counts, cost and category names only.
"""

import argparse
import json
import math
import sqlite3
import statistics
import warnings
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import lookahead.llm_categorizer as llm
from eval.categorizer_dev import ProtocolResult, _names, _score, per_category_recall
from eval.categorizer_llm import WithLLM
from lookahead.categorizer import HybridCategorizer
from lookahead.labels import labelled_examples
from lookahead.merchants import is_private_path
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.resolve import NameInfo, resolve

DEFAULT_OUT = Path("data/private/eval/categorizer_holdout.json")
DEFAULT_CHOICE = Path("data/private/eval/categorizer_choice.json")
CANDIDATES = ("trained hybrid", "hybrid + LLM fallback", "extra: LLM for flagged new merchants")


def in_synthetic_list(tested: pd.DataFrame, synthetic: pd.DataFrame) -> np.ndarray:
    """True for each tested row whose merchant is one of the synthetic merchants, by the
    step 2 resolution rules (the same matching as step 7a, no extra fuzzy matching)."""
    tested_names = _names(tested)
    synthetic_names = {n for row in _names(synthetic) for n in row}
    every = synthetic_names | {n for row in tested_names for n in row}
    resolution = resolve([NameInfo(n) for n in sorted(every)])
    known = {resolution.group_of(n) for n in synthetic_names}
    return np.array(
        [any(resolution.group_of(n) in known for n in row) for row in tested_names], dtype=bool
    )


def load_choice(path: Path) -> tuple[dict[str, object], float, str, int]:
    """(model settings, flag threshold, training variant, synthetic months) saved by 7a."""
    saved = json.loads(path.read_text(encoding="utf-8"))
    params = {k: tuple(v) if isinstance(v, list) else v for k, v in saved["params"].items()}
    threshold = saved["flag_threshold"]
    return (
        params,
        math.inf if threshold is None else float(threshold),
        saved["training"],
        int(saved.get("synthetic_months", 0)),
    )


def _part(model: object, rows: pd.DataFrame) -> tuple[ProtocolResult, list] | None:
    if rows.empty:
        return None
    preds = model.predict_rows(rows)  # type: ignore[attr-defined]
    recall = per_category_recall(rows["label"], [p.category for p in preds], rows["group"])
    return _score(rows["label"], preds, rows["group"]), recall


def _pct(x: float) -> str:
    return "  n/a" if math.isnan(x) else f"{x:5.1%}"


def _show(title: str, part: tuple[ProtocolResult, list] | None) -> dict[str, object] | None:
    if part is None:
        print(f"  {title}: no rows")
        return None
    r, recall = part
    curve = "  ".join(f"{c.share:.0%}: {_pct(c.accuracy)} (n={c.rows})" for c in r.curve)
    print(f"  {title}: n = {r.rows} rows, {r.groups} merchant groups; accuracy "
          f"{_pct(r.accuracy)}, macro-F1 {_pct(r.macro_f1)}; flagged {_pct(r.flag_rate)}, "
          f"accuracy unflagged {_pct(r.unflagged_accuracy)}")  # fmt: skip
    print(f"      curve {curve}")
    return {
        **{k: v for k, v in asdict(r).items() if k != "curve"},
        "curve": [asdict(c) for c in r.curve],
        "recall": [list(row) for row in recall],
    }


def _show_recall(title: str, part: tuple[ProtocolResult, list] | None) -> None:
    if part is None:
        return
    print(f"  per-category recall, {title} (recall, rows, groups):")
    for category, recall, n, groups in part[1]:
        print(f"     {category:<28} {_pct(recall)}  ({n} rows, {groups} groups)")


def main(argv: list[str] | None = None, client: object | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_holdout")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--account", default="scotia-chequing")
    parser.add_argument("--choice", type=Path, default=DEFAULT_CHOICE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=llm.DEFAULT_CACHE)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true", help="counts only; send nothing")
    parser.add_argument("--again", action="store_true", help="score the holdout a further time")
    args = parser.parse_args(argv)
    if not all(is_private_path(p) for p in (args.out, args.cache, args.choice)):
        print("--out, --cache and --choice must point inside data/private/")
        return 2
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    if not args.choice.exists():
        print("no saved choice: run step 7a first (python -m eval.categorizer_dev)")
        return 2
    try:
        key = pseudonym_key(args.env)
    except PseudonymKeyError as e:
        print(f"{e} (the person rules need it to decide what may be sent)")
        return 2
    earlier = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else None
    runs = earlier["runs"] if earlier else 0
    if earlier and not (args.again or args.dry_run):
        print(f"the holdout was already scored ({runs} run(s), last on {earlier['scored_at']}): "
              f"refusing to score it again. The results are in the saved file. --again scores "
              f"it anyway and records that.")  # fmt: skip
        return 1

    conn = sqlite3.connect(f"{args.db.resolve().as_uri()}?mode=ro", uri=True)
    dev = labelled_examples(conn, args.account, ("dev",)).reset_index(drop=True)
    test_a = labelled_examples(conn, args.account, ("test_a",)).reset_index(drop=True)
    test_b = labelled_examples(conn, args.account, ("test_b",)).reset_index(drop=True)
    conn.close()
    if dev.empty or (test_a.empty and test_b.empty):
        print("no labelled dev or holdout rows: freeze the splits and label first (step 6)")
        return 2
    params, threshold, training, months = load_choice(args.choice)
    holdout = pd.concat([test_a, test_b], ignore_index=True)
    sendable = [
        llm.sendable_name(t, text, key)
        for t, text in zip(holdout["txn_type"], holdout["text"], strict=True)
    ]
    names = {n for n in sendable if n}
    cached = len(names & set(llm.load_cache(args.cache)))

    flag = "every new merchant flagged" if math.isinf(threshold) else f"{threshold:.0%}"
    print(f"Step 7b, the holdout, scored once. Choices made in advance on dev: training "
          f"{training}, C {params.get('model__C')}, n-grams "
          f"{params.get('features__name__ngram_range')}, flag threshold {flag}")  # fmt: skip
    for title, part in (("dev (training)", dev), ("Test A", test_a), ("Test B", test_b)):
        print(f"  {title}: {len(part)} labelled rows, {part['group'].nunique()} merchant groups")
    print(f"holdout names that may be sent: {len(names)} distinct card-merchant names; "
          f"holdout rows whose name is never sent: {sum(n is None for n in sendable)}")  # fmt: skip
    print(f"already answered (cache): {cached}; calls needed: {len(names) - cached}")
    if args.dry_run:
        state = f"already scored {runs} time(s)" if earlier else "not scored yet"
        print(f"dry run: nothing was sent, nothing was scored (holdout {state})")
        return 0

    try:
        answers, calls = llm.classify(names, args.cache, client)
    except Exception as e:  # the message may quote a request: print the error type only
        print(f"stopped before scoring: {type(e).__name__} (answers so far are saved; the "
              f"holdout was not scored; run again to continue)")  # fmt: skip
        return 1

    from eval.categorizer import synthetic_examples

    synthetic = synthetic_examples(months or 12)  # also the "pre-taught chains" list
    extra = synthetic if training == "real + synthetic" else None
    warnings.filterwarnings("ignore")
    model = HybridCategorizer(params=params, threshold=threshold)
    if extra is None:
        model.fit(dev, dev["label"])
    else:
        model.fit(dev, dev["label"], extra_x=extra, extra_y=extra["label"])
    categories = {n: a.category for n, a in answers.items() if a.category}
    models = {
        CANDIDATES[0]: model,
        CANDIDATES[1]: WithLLM(model, categories, key, flagged_only=False),
        CANDIDATES[2]: WithLLM(model, categories, key, flagged_only=True),
    }
    taught = in_synthetic_list(test_b, synthetic) if len(test_b) else np.zeros(0, dtype=bool)
    parts = {
        "Test A (rows after the date cutoff)": test_a,
        "Test B (merchants never seen), all": test_b,
        "Test B, merchants in the synthetic list (pre-taught)": test_b[taught],
        "Test B, merchants not in the synthetic list": test_b[~taught],
    }
    results: dict[str, dict[str, object]] = {}
    for name, candidate in models.items():
        print(f"{name}:")
        scored = {title: _part(candidate, rows) for title, rows in parts.items()}
        results[name] = {title: _show(title, part) for title, part in scored.items()}
        if name != CANDIDATES[2]:
            for title in list(parts)[:2]:
                _show_recall(title, scored[title])

    cost = sum(a.cost_usd for a in answers.values())
    invalid = sum(a.category is None for a in answers.values())
    latency = statistics.median(a.latency_s for a in answers.values()) if answers else 0.0
    per_thousand = cost / len(answers) * 1000 if answers else 0.0
    print(f"LLM ({llm.MODEL}, temperature 0): {len(answers)} holdout names, {calls} calls made "
          f"now, {invalid} answers that are not a category; cost {cost:.3f} USD, "
          f"{per_thousand:.3f} USD per 1,000 merchants; median latency "
          f"{latency * 1000:.0f} ms per name")  # fmt: skip

    saved = {
        "scored_at": date.today().isoformat(),
        "runs": runs + 1,
        "choice": {"training": training, "threshold": None if math.isinf(threshold) else threshold,
                   "params": {k: list(v) if isinstance(v, tuple) else v
                              for k, v in params.items()}},
        "rows": {"dev": len(dev), "test_a": len(test_a), "test_b": len(test_b)},
        "llm": {"model": llm.MODEL, "names": len(answers), "invalid_answers": invalid,
                "cost_usd": cost, "cost_per_1000_merchants_usd": per_thousand,
                "median_latency_s": latency},
        "results": results,
    }  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"Saved to {args.out} (run {runs + 1}). The holdout is now used: no choice may be "
          f"made from these numbers.")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
