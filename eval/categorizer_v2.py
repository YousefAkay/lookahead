"""Categorizer v2 evaluation harness. BUILT, NOT RUN on anything real.

Later I run it once on a fresh blind set: about 150 new-merchant rows from October 2026 on,
labelled by me after v2 is built. It compares three variants:
    (a) "current":            today's model;
    (b) "cleaner+directory":  (a) with the merchant-text cleaner and the chain directory;
    (c) "+RAG":               (b) with the RAG categorizer on its flagged new merchants (opt-in:
                              only with --rag; it sends names only with --send).

    python -m eval.categorizer_v2 candidates --db PATH --account A --since 2026-10-01 \
        --out data/private/eval/categorizer_v2_blind_candidates.csv
    python -m eval.categorizer_v2 score --db PATH --labels PATH --choice PATH [--rag [--send]]
    python -m eval.categorizer_v2 score --synthetic        # plumbing check, not a result

Rules, in code:
- --db, --labels and --choice have no default: nothing points at private data by itself.
- It refuses to score unless the adopt-rule file exists, is committed, is unchanged since
  that commit, and is not a draft (`adopt_rule_status`).
- It refuses any row from the old, used holdout (Test A, Test B) and any row the model could
  train on.
- It prints counts and accuracy with n only, never row text.
- Every variant run is appended to a ledger (data/private/ only). A second scoring of the same
  labels file is refused unless --again, which the ledger records.
- --synthetic runs everything on personas A, B and C (trained on A, B and C's rows as the
  "blind" set), prints counts only and "dry run, not a result". It writes no ledger, checks
  no adopt rule and never sends anything.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from lookahead import categorizer_v2 as v2
from lookahead.categories import load_category_preferences
from lookahead.categorize import Settings, load_settings, training_rows
from lookahead.categorizer import HybridCategorizer, examples
from lookahead.flows import Txn
from lookahead.merchants import is_private_path
from lookahead.resolve import load_user_decisions
from lookahead.taxonomy import CATEGORIES

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADOPT_RULE = Path("docs/specs/2026-11-18-categorizer-v2-adopt-rule.md")  # once approved
DEFAULT_LEDGER = Path("data/private/eval/categorizer_v2_ledger.jsonl")
DEFAULT_RAG_CACHE = Path("data/private/cache/rag_categories.json")
VARIANTS = ("current", "cleaner+directory", "+RAG")
LEAKAGE_AT = 0.95  # any variant above this: check for leakage before reading anything
BOOTSTRAP_RESAMPLES = 2000
DRY_RUN = "dry run, not a result"

# --- The adopt rule must be committed and unchanged ---


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


@dataclass(frozen=True)
class RuleStatus:
    ok: bool
    reason: str
    commit: str | None = None  # the last commit that changed the file
    sha256: str | None = None


def adopt_rule_status(path: Path) -> RuleStatus:
    """OK only if the file exists, is tracked by git, has no change since its last commit
    (staged or not), and does not say it is a draft or not yet in force."""
    path = path if path.is_absolute() else ROOT / path
    if not path.exists():
        return RuleStatus(False, "the adopt-rule file does not exist")
    folder = path.parent
    if _git(["ls-files", "--error-unmatch", path.name], folder).returncode != 0:
        return RuleStatus(False, "the adopt-rule file is not committed")
    commit = _git(["log", "-1", "--format=%H", "--", path.name], folder).stdout.strip()
    if not commit:
        return RuleStatus(False, "the adopt-rule file has no commit")
    if _git(["status", "--porcelain", "--", path.name], folder).stdout.strip():
        return RuleStatus(False, "the adopt-rule file changed since its commit", commit)
    if _git(["diff", "--quiet", commit, "--", path.name], folder).returncode != 0:
        return RuleStatus(False, "the adopt-rule file differs from its commit", commit)
    text = path.read_text(encoding="utf-8")
    if "DRAFT" in text or "not yet in force" in text.casefold():
        return RuleStatus(False, "the adopt rule is still a draft", commit)
    return RuleStatus(True, "committed and unchanged", commit,
                      hashlib.sha256(text.encode("utf-8")).hexdigest())  # fmt: skip


# --- Rows ---


def load_blind_labels(path: Path) -> dict[str, str]:
    """fingerprint -> category from my labels file (columns fingerprint, category). A blank
    category is a skipped row; any other value must be one of the 17."""
    out: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or not {"fingerprint", "category"} <= set(reader.fieldnames):
            raise ValueError("the labels file needs the columns fingerprint and category")
        for row in reader:
            category = (row["category"] or "").strip()
            if not category:
                continue
            if category not in CATEGORIES:
                raise ValueError(f"unknown category in the labels file: {category!r}")
            if row["fingerprint"] in out:
                raise ValueError("a fingerprint appears twice in the labels file")
            out[row["fingerprint"]] = category
    return out


def _txns(conn, account: str) -> tuple[list[Txn], dict[int, str]]:  # type: ignore[no-untyped-def]
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ?",
        (account,),
    ).fetchall()
    return (
        [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw],
        {r[0]: r[5] for r in raw},
    )


def categorizer_features(conn, account: str) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """Every row the rules leave for the categorizer, with its features, fingerprint, the
    bank's transaction type and text (kept in memory, never printed)."""
    txns, fingerprint = _txns(conn, account)
    df = examples(txns, load_user_decisions(conn), load_category_preferences(conn))
    by_id = {t.row_id: t for t in txns}
    df["fingerprint"] = [fingerprint[i] for i in df["row_id"]]
    df["amount_cents"] = [by_id[i].amount_cents for i in df["row_id"]]
    df["bank_type"] = [by_id[i].txn_type for i in df["row_id"]]
    df["text"] = [by_id[i].text for i in df["row_id"]]
    return df


def holdout_fingerprints(conn) -> set[str]:  # type: ignore[no-untyped-def]
    """Rows of the old, used holdout (Test A and Test B), in any account."""
    try:
        rows = conn.execute(
            "SELECT fingerprint FROM label_splits WHERE split IN ('test_a', 'test_b')"
        ).fetchall()
    except Exception:  # no splits table: nothing was held out
        return set()
    return {r[0] for r in rows}


def candidate_blind_rows(
    features: pd.DataFrame, training_groups: set[str], since: date
) -> pd.DataFrame:
    """Candidate rows for the fresh blind set: rows of merchants first seen on or after
    `since` (over all of the account's categorizer rows) whose merchant has no training
    label. Sorted by date."""
    if features.empty:
        return features
    first = features.groupby("group")["posted_date"].min()
    new_groups = {g for g, d in first.items() if d >= since and g not in training_groups}
    picked = features[features["group"].isin(new_groups)]
    return picked.sort_values(["posted_date", "fingerprint"]).reset_index(drop=True)


# --- Variants ---


@dataclass
class VariantResult:
    variant: str
    categories: list[str | None]  # per blind row, in order
    sources: list[str]


def fit_model(
    train_x: pd.DataFrame, train_y: Sequence[str], settings: Settings, options: v2.Options,
    extra: pd.DataFrame | None,
) -> HybridCategorizer:  # fmt: skip
    """The model `lookahead.categorize.train` would build with these options."""
    model = v2.make_hybrid(options, params=settings.params, threshold=settings.threshold)
    if train_x.empty:
        if extra is None:
            raise ValueError("nothing to train on")
        return model.fit(extra, extra["label"])
    if extra is None:
        return model.fit(train_x, list(train_y))
    return model.fit(train_x, list(train_y), extra_x=extra, extra_y=extra["label"])


def run_variant(
    variant: str, blind: pd.DataFrame, train_x: pd.DataFrame, train_y: Sequence[str],
    settings: Settings, extra: pd.DataFrame | None,
    rag_answer: Callable[[pd.Series], str | None] | None = None,
) -> VariantResult:  # fmt: skip
    """Predictions of one variant for the blind rows (in memory: nothing is stored)."""
    on = variant != "current"
    options = v2.Options(cleaner=on, directory=on)
    model = fit_model(train_x, train_y, settings, options, extra)
    preds = model.predict_rows(blind)
    table = pd.DataFrame(
        {
            "merchant": list(blind["group"]),
            "category": [p.category for p in preds],
            "source": ["model" if p.route == "new merchant" else "known merchant" for p in preds],
            "detail": [p.route for p in preds],
            "flagged": [p.flagged for p in preds],
        }
    )
    table = v2.apply_directory(table, options)
    categories: list[str | None] = list(table["category"])
    sources = list(table["source"])
    if variant == "+RAG":
        if rag_answer is None:
            raise ValueError("the RAG variant needs an answer function")
        for i, (flagged, source) in enumerate(zip(table["flagged"], sources, strict=True)):
            if flagged and source == "model":
                got = rag_answer(blind.iloc[i])
                if got is not None:
                    categories[i], sources[i] = got, "RAG"
    return VariantResult(variant, categories, sources)


def paired_bootstrap(
    base: np.ndarray, other: np.ndarray, clusters: Sequence[str],
    resamples: int = BOOTSTRAP_RESAMPLES, seed: int = 0,
) -> tuple[float, float]:  # fmt: skip
    """(point gain, 2.5th percentile of the gain) of `other` over `base` (both arrays of
    correct / not), resampling merchants (rows of one merchant move together)."""
    base, other = np.asarray(base, dtype=float), np.asarray(other, dtype=float)
    gain = float(other.mean() - base.mean())
    names = sorted(set(clusters))
    index = {n: i for i, n in enumerate(names)}
    member = np.array([index[c] for c in clusters])
    diff_sum = np.bincount(member, other - base, minlength=len(names))
    size = np.bincount(member, minlength=len(names)).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(names), size=(resamples, len(names)))
    gains = diff_sum[draws].sum(axis=1) / size[draws].sum(axis=1)
    return gain, float(np.percentile(gains, 2.5))


# --- Ledger ---


def ledger_entries(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def add_to_ledger(path: Path, entry: dict[str, object]) -> None:
    if not is_private_path(path):
        raise ValueError("the ledger holds results on my data: keep it in data/private/")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


# --- Scoring ---


@dataclass(frozen=True)
class Split:
    blind: pd.DataFrame  # the blind rows that can be scored, with "truth" and "known"
    train_x: pd.DataFrame
    train_y: list[str]
    refused_holdout: int
    refused_training: int
    not_categorizer: int


def prepare(
    features: pd.DataFrame, labels: dict[str, str], train_x: pd.DataFrame,
    train_y: Sequence[str], holdout: set[str],
) -> Split:  # fmt: skip
    in_holdout = {f for f in labels if f in holdout}
    in_training = {f for f in labels if f in set(train_x.get("fingerprint", []))}
    usable = features[features["fingerprint"].isin(set(labels) - in_holdout - in_training)].copy()
    usable["truth"] = [labels[f] for f in usable["fingerprint"]]
    known_groups = set(train_x["group"]) if not train_x.empty else set()
    usable["known"] = usable["group"].isin(known_groups)
    not_categorizer = len(set(labels) - set(features["fingerprint"]) - in_holdout)
    return Split(usable.reset_index(drop=True), train_x, list(train_y), len(in_holdout),
                 len(in_training), not_categorizer)  # fmt: skip


def _accuracy_line(name: str, correct: np.ndarray) -> str:
    if len(correct) == 0:
        return f"  {name}: n = 0"
    return f"  {name}: accuracy {correct.mean():6.1%} (n = {len(correct)})"


def score(
    split: Split, settings: Settings, extra: pd.DataFrame | None, variants: Sequence[str],
    rag_answer: Callable[[pd.Series], str | None] | None, dry_run: bool,
    record: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, VariantResult]:  # fmt: skip
    """Run each variant on the blind rows. A dry run prints counts only."""
    blind = split.blind
    print(f"blind rows: {len(blind)} (new merchants: {int((~blind['known']).sum())}, "
          f"known merchants: {int(blind['known'].sum())}); merchants: "
          f"{blind['group'].nunique()}")  # fmt: skip
    print(f"refused: old holdout rows {split.refused_holdout}, rows the model trains on "
          f"{split.refused_training}; labelled rows not left for the categorizer "
          f"{split.not_categorizer}")  # fmt: skip
    results: dict[str, VariantResult] = {}
    for variant in variants:
        result = run_variant(variant, blind, split.train_x, split.train_y, settings, extra,
                             rag_answer)  # fmt: skip
        results[variant] = result
        by_source = pd.Series(result.sources).value_counts().to_dict()
        print(f"variant {variant}: rows {len(blind)}, by source {dict(sorted(by_source.items()))}")
        correct = np.array([c == t for c, t in zip(result.categories, blind["truth"],
                                                   strict=True)])  # fmt: skip
        entry = {"variant": variant, "rows": len(blind), "dry_run": dry_run}
        if not dry_run:
            new, known = correct[~blind["known"].to_numpy()], correct[blind["known"].to_numpy()]
            print(_accuracy_line("new-merchant rows", new))
            print(_accuracy_line("known-merchant rows", known))
            entry |= {
                "new_n": len(new), "new_correct": int(new.sum()),
                "known_n": len(known), "known_correct": int(known.sum()),
            }  # fmt: skip
            if (len(new) and new.mean() > LEAKAGE_AT) or (len(known) and known.mean() > LEAKAGE_AT):
                print(f"  above {LEAKAGE_AT:.0%}: check for leakage before reading anything")
        if record is not None:
            record(entry)
    if not dry_run and "current" in results:
        new_mask = ~blind["known"].to_numpy()
        truth = blind["truth"].to_numpy()
        base = np.array(results["current"].categories, dtype=object) == truth
        for variant in results:
            if variant == "current" or not new_mask.any():
                continue
            other = np.array(results[variant].categories, dtype=object) == truth
            gain, low = paired_bootstrap(base[new_mask], other[new_mask],
                                         list(blind["group"][new_mask]))  # fmt: skip
            print(f"  {variant} vs current, new-merchant rows: {gain * 100:+.1f} points, "
                  f"bootstrap 2.5th percentile {low * 100:+.1f} (n = {int(new_mask.sum())}, "
                  f"merchants resampled)")  # fmt: skip
    if dry_run:
        print(DRY_RUN)
    return results


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rag_answer_function(conn, account: str, key: bytes, cache: Path, send: bool, client):  # type: ignore[no-untyped-def]
    import lookahead.llm_categorizer as llm
    from lookahead import rag_categorizer as rag

    pairs = rag.labelled_pairs(conn, account, key)
    index = rag.LabelledIndex(pairs)

    def answer(row: pd.Series) -> str | None:
        name = llm.sendable_name(row["bank_type"], row["text"], key)
        if not name:
            return None
        got, _ = rag.categorize_one(rag.retrieve(name, index), cache, client, send)
        return None if got is None else got.category

    return answer


def score_my_data(args: argparse.Namespace) -> int:
    from lookahead.db import connect_readonly
    from lookahead.synthetic_training import synthetic_examples

    for flag in ("db", "labels", "choice"):
        if getattr(args, flag) is None:
            print(f"--{flag} is required (no default points at private data)")
            return 2
    status = adopt_rule_status(args.adopt_rule)
    if not status.ok:
        print(f"refusing to score: {status.reason} ({args.adopt_rule})")
        return 2
    if not is_private_path(args.ledger):
        print("--ledger must be inside data/private/")
        return 2
    labels = load_blind_labels(args.labels)
    labels_hash = _sha256(args.labels)
    earlier = [e for e in ledger_entries(args.ledger) if e.get("labels_sha256") == labels_hash
               and not e.get("dry_run")]  # fmt: skip
    if earlier and not args.again:
        print(
            f"this labels file was already scored ({len(earlier)} variant run(s) in the "
            "ledger): refusing to score it again. --again scores it again and is recorded."
        )
        return 2
    settings = load_settings(args.choice)
    conn = connect_readonly(args.db)
    features = categorizer_features(conn, args.account)
    mine = training_rows(conn, args.account)
    keep = ~mine["fingerprint"].isin(set(labels)) if not mine.empty else []
    train = mine[keep].reset_index(drop=True) if not mine.empty else mine
    split = prepare(features, labels, train, list(train.get("label", [])),
                    holdout_fingerprints(conn))  # fmt: skip
    if split.refused_holdout:
        print(
            f"refusing to score: {split.refused_holdout} labelled rows are from the old "
            "holdout (Test A or Test B). Remove them from the labels file."
        )
        conn.close()
        return 2
    extra = synthetic_examples(settings.synthetic_months) if settings.synthetic_months else None
    variants = list(VARIANTS[:2])
    rag_answer = None
    if args.rag:
        from lookahead.pseudonymize import pseudonym_key

        variants.append("+RAG")
        rag_answer = _rag_answer_function(conn, args.account, pseudonym_key(args.env),
                                          args.rag_cache, args.send, None)  # fmt: skip
    now = datetime.now().isoformat(timespec="seconds")

    def record(entry: dict[str, object]) -> None:
        add_to_ledger(args.ledger, {**entry, "at": now, "labels_sha256": labels_hash,
                                    "adopt_rule_commit": status.commit,
                                    "adopt_rule_sha256": status.sha256, "again": args.again,
                                    "send": bool(args.rag and args.send)})  # fmt: skip

    print(f"Categorizer v2 on the blind set: account {args.account}; adopt rule at commit "
          f"{(status.commit or '')[:10]}")  # fmt: skip
    score(split, settings, extra, variants, rag_answer, dry_run=False, record=record)
    conn.close()
    return 0


def synthetic_split(tmp: Path) -> tuple[Split, Settings]:
    """Personas A, B and C in a temporary database: the model trains on A's dev labels, and
    every categorizer row of B and C is the "blind" set, labelled from its answer key."""
    from lookahead.categorize import training_rows as mine
    from lookahead.db import connect
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports
    from lookahead.labels import freeze_splits, label_row, load_rows

    synthetic = ROOT / "data" / "synthetic"
    conn = connect(tmp / "dry.db")
    truth: dict[str, str] = {}
    for pid in "ABC":
        account = f"synthetic-{pid}"
        export = synthetic / f"persona_{pid}.csv"
        parsed = parse_export(export)
        store_exports(conn, account, [(export.name, parsed)], datetime(2026, 10, 2))
        conn.commit()
        with (synthetic / f"persona_{pid}_answer_key.csv").open(encoding="utf-8") as f:
            key = list(csv.DictReader(line for line in f if not line.startswith("#")))
        category = {(r.posted_date, r.amount_cents, r.sub_description): k["category"]
                    for r, k in zip(parsed, key, strict=True)}  # fmt: skip
        if pid == "A":
            freeze_splits(conn, account, seed=11)
            for r in load_rows(conn, account).itertuples():
                label_row(conn, account, r.fingerprint,
                          category[(r.posted_date, r.amount_cents, r.text)])  # fmt: skip
        else:
            for r in categorizer_features(conn, account).itertuples():
                truth[r.fingerprint] = category[(r.posted_date, r.amount_cents, r.text)]
    train = mine(conn, "synthetic-A")
    features = pd.concat([categorizer_features(conn, f"synthetic-{p}") for p in "BC"],
                         ignore_index=True)  # fmt: skip
    split = prepare(features, truth, train, list(train["label"]), holdout_fingerprints(conn))
    conn.close()
    return split, Settings(threshold=0.5, synthetic_months=0)


def dry_run() -> int:
    """The whole harness on synthetic personas: counts only, no accuracy, nothing sent."""
    print("Categorizer v2 harness on SYNTHETIC personas A, B and C (train on A, run on B, C)")
    with tempfile.TemporaryDirectory() as d:
        split, settings = synthetic_split(Path(d))

        def offline_rag(row: pd.Series) -> str | None:  # no client: every answer is "unsure"
            return None

        score(split, settings, None, VARIANTS, offline_rag, dry_run=True)
    return 0


def write_candidates(args: argparse.Namespace) -> int:
    from lookahead.db import connect_readonly

    if args.db is None:
        print("--db is required (no default points at private data)")
        return 2
    if not is_private_path(args.out):
        print("--out must be inside data/private/ (the file names merchants)")
        return 2
    conn = connect_readonly(args.db)
    features = categorizer_features(conn, args.account)
    mine = training_rows(conn, args.account)
    groups = set(mine["group"]) if not mine.empty else set()
    picked = candidate_blind_rows(features, groups, args.since)
    picked = picked[~picked["fingerprint"].isin(holdout_fingerprints(conn))]
    conn.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    picked.assign(category="")[["fingerprint", "posted_date", "group", "category"]].to_csv(
        args.out, index=False
    )
    print(f"candidate blind rows since {args.since}: {len(picked)} rows from "
          f"{picked['group'].nunique() if len(picked) else 0} new merchants; written to "
          f"{args.out} (not shown here)")  # fmt: skip
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_v2")
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("score", help="score the variants on my blind set (or --synthetic)")
    s.add_argument("--synthetic", action="store_true", help="dry run on personas A, B and C")
    s.add_argument("--db", type=Path)
    s.add_argument("--labels", type=Path)
    s.add_argument("--choice", type=Path, help="model settings saved by step 7a")
    s.add_argument("--account", default="scotia-chequing")
    s.add_argument("--adopt-rule", type=Path, default=DEFAULT_ADOPT_RULE)
    s.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    s.add_argument("--rag", action="store_true", help="add variant (c), the RAG categorizer")
    s.add_argument("--send", action="store_true", help="with --rag: ask for missing answers")
    s.add_argument("--rag-cache", type=Path, default=DEFAULT_RAG_CACHE)
    s.add_argument("--env", type=Path, default=Path(".env"))
    s.add_argument("--again", action="store_true", help="score the same labels again")
    c = commands.add_parser("candidates", help="candidate rows for the fresh blind set")
    c.add_argument("--db", type=Path)
    c.add_argument("--account", default="scotia-chequing")
    c.add_argument("--since", type=date.fromisoformat, required=True)
    c.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "candidates":
        return write_candidates(args)
    if args.synthetic:
        return dry_run()
    return score_my_data(args)


if __name__ == "__main__":
    sys.exit(main())
