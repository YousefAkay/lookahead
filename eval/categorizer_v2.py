"""Categorizer v2 evaluation harness, Part 2 of the adopt rule (version 2): the VETO on my
real rows. BUILT, NOT RUN on anything real. The deciding run is on public data
(eval/categorizer_v2_external.py, Part 1); my rows are drawn only after it is scored.

The veto set is drawn once at random with a fixed seed, from October 2026 on, and labelled by
me: group A, up to 150 rows of new merchants (at most 3 per merchant; at least 15), and group
K, 60 rows of merchants with training labels (at least 40 labelled). The variants are what I
would switch on in the app:
    (a) "current":            my app's model as deployed (my labels, my settings, synthetic rows);
    (b) "cleaner+directory":  (a) with the merchant-text cleaner and the chain directory (exact
                              and alias matches only; similarity matching stays off);
    (c) "+RAG":               (b) with the RAG categorizer on its flagged new merchants (opt-in:
                              only with --rag; it sends names only with --send).

    python -m eval.categorizer_v2 candidates --db PATH --account A \
        --out data/private/eval/categorizer_v2_blind_candidates.csv   # drawn once
    python -m eval.categorizer_v2 label --db PATH --selection CANDIDATES --labels LABELS
    python -m eval.categorizer_v2 score --db PATH --labels PATH --choice PATH [--rag [--send]]
    python -m eval.categorizer_v2 score --synthetic        # plumbing check, not a result

Rules, in code:
- --db, --labels and --choice have no default: nothing points at private data by itself.
- It refuses to score unless the adopt-rule file exists, is committed, is unchanged since
  that commit, and is not a draft (`adopt_rule_status`).
- It refuses any row from the old, used holdout (Test A, Test B) and any row the model could
  train on.
- It prints counts and accuracy with n only, never row text.
- The veto (`veto`): (b) and (c), each against (a), at most 2 rows net worse on group A and
  at most 1 more row wrong on group K. "Adopted" needs the public pass (`external_verdicts`,
  from the ledger) and no veto. `compare` (the public run's comparisons) lives here too.
- `candidates` refuses until the public deciding run is scored.
- `candidates --count` prints how many rows could be drawn, and draws nothing.
- Scoring refuses without a logged draw, if the labels file does not hold exactly the drawn
  rows, or if the rule changed after the draw.
- `candidates` and `label` refuse until the adopt rule is committed. The draw is logged in
  the ledger and refused a second time, whatever the output path.
- The draw leaves out rows I labelled or skipped in the app.
- It refuses to score with fewer than 15 labelled group A rows or 40 labelled group K rows,
  or when a group A merchant has training labels by the time of scoring.
- Only docs/specs/2026-11-18-categorizer-v2-adopt-rule.md is accepted as the adopt rule.
- Every variant run is appended to the one ledger (DEFAULT_LEDGER, in data/private/; there is
  no option to point at another), and so is every refused run.
  A blind selection (the rows of the labels file, labelled or not) is scored once: a run with
  any row of an earlier scored run is refused unless --again, which the ledger records.
- --synthetic runs everything on personas A, B and C (trained on A, B and C's rows as the
  "blind" set), prints counts only and "dry run, not a result". It writes no ledger, checks
  no adopt rule and never sends anything.
"""

import argparse
import csv
import hashlib
import json
import sqlite3
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
from lookahead.rag_categorizer import silent_warning
from lookahead.resolve import load_user_decisions
from lookahead.taxonomy import CATEGORIES

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADOPT_RULE = Path("docs/specs/2026-11-18-categorizer-v2-adopt-rule.md")  # once approved
APPROVED_ADOPT_RULE = ROOT / DEFAULT_ADOPT_RULE  # the only file accepted as the adopt rule
DEFAULT_RAG_CACHE = Path("data/private/cache/rag_categories.json")
VARIANTS = ("current", "cleaner+directory", "+RAG")
RAG_ALONE = "RAG alone"  # rule v4's veto variant: deployed model + RAG; cleaner, directory off
RAG_VARIANTS = frozenset({"+RAG", RAG_ALONE})
VETO_V4_RUN = "veto-rag-v4"  # the ledger's name for rule v4's veto runs
LEAKAGE_AT = 0.95  # any variant above this: check for leakage before reading anything
BOOTSTRAP_RESAMPLES = 2000
RULE_MARKER = "## Part 1: the deciding run, on public data only"  # version 2 of the rule
DRY_RUN = "dry run, not a result"
# The adopt rule's numbers (docs/specs/2026-11-18-categorizer-v2-adopt-rule.md).
SELECTION_SEED = 0  # fixed: the blind selection is drawn once and never redrawn
BLIND_SINCE = date(2026, 10, 1)  # fixed: both groups are rows from this day on
A_MAX_ROWS = 150  # group A, new merchants: at most this many rows
A_PER_MERCHANT = 3  # ... and at most this many rows of one merchant
A_MIN_ROWS = 15  # fewer rows for group A: nothing is drawn or scored (the veto needs rows)
K_ROWS = 60  # group K, known merchants: rows for the guard
K_MIN_ROWS = 40  # fewer labelled group K rows: the guard tests too little, nothing is scored
MIN_GAIN = 0.03  # a variant must beat its base by at least 3 points on group A
GUARD_EXTRA_WRONG = 1  # veto on my group K rows: at most 1 more row wrong than (a)
A_VETO_EXTRA_WRONG = 2  # veto on my group A rows: at most 2 rows net worse than (a)
# (b) is compared with (a), and (c) with (b): RAG must earn its place on top of (b)
COMPARISONS = (("cleaner+directory", "current"), ("+RAG", "cleaner+directory"))

# --- The adopt rule must be committed and unchanged ---


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def main_folder(start: Path = ROOT) -> Path:
    """The main repository folder, also when run from a worktree (git's shared folder is the
    main folder's .git): where my private files are."""
    out = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], start)
    common = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    return common.parent if common is not None and common.name == ".git" else start


def default_ledger() -> Path:
    """The one ledger, an absolute path in the main folder's data/private: the same file
    whichever folder the command is run from."""
    return main_folder() / "data" / "private" / "eval" / "categorizer_v2_ledger.jsonl"


DEFAULT_LEDGER = default_ledger()
# rule v4's veto: its own RAG cache, empty at first, in the main folder (never the app's)
DEFAULT_VETO_V4_CACHE = main_folder() / "data" / "private" / "cache" / "rag_veto_v4.json"


def ledger_is_private(path: Path) -> bool:
    """Inside this checkout's data/private, or the main folder's (from a worktree)."""
    return is_private_path(path) or path.resolve().is_relative_to(
        (main_folder() / "data" / "private").resolve()
    )


@dataclass(frozen=True)
class RuleStatus:
    ok: bool
    reason: str
    commit: str | None = None  # the last commit that changed the file
    sha256: str | None = None


def adopt_rule_status(path: Path, marker: str = RULE_MARKER) -> RuleStatus:
    """OK only if the file exists, is tracked by git, has no change since its last commit
    (staged or not), does not say it is a draft or not yet in force, and holds `marker`
    (the heading of the rule version the caller runs)."""
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
    if "DRAFT" in text or "not yet in force" in text.casefold() or "⟨Q" in text:
        return RuleStatus(False, "the adopt rule is still a draft", commit)
    if marker not in text:
        return RuleStatus(False, "the adopt rule is not the version this harness runs", commit)
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


def load_blind_sets(path: Path) -> dict[str, str]:
    """fingerprint -> "A" (new merchant) or "K" (known merchant), for every row of the labels
    file, labelled or not."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "set" not in reader.fieldnames:
            raise ValueError("the labels file has no set column (A or K): select the rows again")
        out = {row["fingerprint"]: row["set"] for row in reader}
    if set(out.values()) - {"A", "K"}:
        raise ValueError("the labels file's set column holds something other than A or K")
    return out


def selection_fingerprints(path: Path) -> list[str]:
    """Every row of the labels file, labelled or not, sorted: the blind set's row selection."""
    with path.open(encoding="utf-8", newline="") as f:
        return sorted({row["fingerprint"] for row in csv.DictReader(f)})


def selection_key(fingerprints: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(fingerprints)).encode("utf-8")).hexdigest()


def scored_fingerprints(entries: Sequence[dict[str, object]]) -> set[str]:
    """Rows of every earlier scoring run (refused runs and dry runs scored nothing)."""
    out: set[str] = set()
    for e in entries:
        if e.get("status", "scored") == "scored" and not e.get("dry_run"):
            out |= set(e.get("selection", []))  # type: ignore[arg-type]
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


def decided_fingerprints(conn, account: str) -> set[str]:  # type: ignore[no-untyped-def]
    """Rows whose current label-review decision is a category or "Skip / don't know": they
    are never drawn for the blind set (a skipped row is one I already found hard)."""
    from lookahead.labels import current_labels

    try:
        return set(current_labels(conn, account))
    except sqlite3.OperationalError as e:  # no labels table: nothing was decided
        if "no such table" not in str(e):
            raise
        return set()


def holdout_fingerprints(conn) -> set[str]:  # type: ignore[no-untyped-def]
    """Rows of the old, used holdout (Test A and Test B), in any account."""
    try:
        rows = conn.execute(
            "SELECT fingerprint FROM label_splits WHERE split IN ('test_a', 'test_b')"
        ).fetchall()
    except sqlite3.OperationalError as e:  # no splits table: nothing was held out
        if "no such table" not in str(e):
            raise
        return set()
    return {r[0] for r in rows}


def candidate_blind_rows(
    features: pd.DataFrame, training_groups: set[str], since: date
) -> pd.DataFrame:
    """Every row that may go in group A: rows of merchants first seen on or after `since`
    (over all of the account's categorizer rows) whose merchant has no training label.
    Sorted by date."""
    if features.empty:
        return features
    first = features.groupby("group")["posted_date"].min()
    new_groups = {g for g, d in first.items() if d >= since and g not in training_groups}
    picked = features[features["group"].isin(new_groups)]
    return picked.sort_values(["posted_date", "fingerprint"]).reset_index(drop=True)


def _take(rows: pd.DataFrame, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """`n` rows at random (all of them if there are fewer), from rows sorted by fingerprint
    first, so the draw depends only on the rows and the generator."""
    rows = rows.sort_values("fingerprint").reset_index(drop=True)
    if len(rows) <= n:
        return rows
    return rows.iloc[np.sort(rng.choice(len(rows), size=n, replace=False))]


@dataclass(frozen=True)
class PoolCounts:
    a_rows: int  # rows that qualify for group A, after the cap of A_PER_MERCHANT
    a_merchants: int
    k_rows: int  # rows that qualify for group K
    k_merchants: int


def pool_counts(
    features: pd.DataFrame, training_groups: set[str], excluded: set[str], since: date
) -> PoolCounts:
    """How many rows could be drawn, without drawing: the count before the one-time draw."""
    pool_a = candidate_blind_rows(features, training_groups, since)
    pool_a = pool_a[~pool_a["fingerprint"].isin(excluded)] if not pool_a.empty else pool_a
    per = pool_a.groupby("group").size() if not pool_a.empty else pd.Series(dtype=int)
    free = features[~features["fingerprint"].isin(excluded)]
    pool_k = free[(free["posted_date"] >= since) & free["group"].isin(training_groups)]
    return PoolCounts(int(per.clip(upper=A_PER_MERCHANT).sum()), len(per), len(pool_k),
                      pool_k["group"].nunique())  # fmt: skip


def select_blind_rows(
    features: pd.DataFrame, training_groups: set[str], excluded: set[str], since: date,
    seed: int = SELECTION_SEED,
) -> pd.DataFrame:  # fmt: skip
    """The blind selection, drawn at random with a fixed seed: group A (new merchants, at
    most A_PER_MERCHANT rows each, then at most A_MAX_ROWS rows) and group K (K_ROWS rows of
    merchants with training labels, from `since` on). `excluded` holds the rows that may not
    be picked (the old holdout and every row with a label). Both groups are shuffled together,
    so the labelling order does not tell which group a row is in. Raises ValueError when
    fewer than A_MIN_ROWS rows qualify for group A (after the per-merchant cap)."""
    rng = np.random.default_rng(seed)
    free = features[~features["fingerprint"].isin(excluded)]
    # "first seen" counts every row of the merchant, excluded or not
    pool_a = candidate_blind_rows(features, training_groups, since)
    pool_a = pool_a[~pool_a["fingerprint"].isin(excluded)] if not pool_a.empty else pool_a
    capped = [_take(rows, A_PER_MERCHANT, rng) for _, rows in sorted(pool_a.groupby("group"))]
    capped_a = pd.concat(capped, ignore_index=True) if capped else pool_a.iloc[0:0]
    if len(capped_a) < A_MIN_ROWS:
        raise ValueError(
            f"only {len(capped_a)} rows qualify for group A (new merchants, at most "
            f"{A_PER_MERCHANT} per merchant); at least {A_MIN_ROWS} are needed. Wait for more "
            "data: nothing was selected."
        )
    group_a = _take(capped_a, A_MAX_ROWS, rng).assign(set="A")
    pool_k = free[(free["posted_date"] >= since) & free["group"].isin(training_groups)]
    group_k = _take(pool_k, K_ROWS, rng).assign(set="K")
    both = pd.concat([group_a, group_k], ignore_index=True)
    return both.iloc[rng.permutation(len(both))].reset_index(drop=True)


# --- Variants ---


@dataclass
class VariantResult:
    variant: str
    categories: list[str | None]  # per blind row, in order
    sources: list[str]
    rag_asked: int = 0  # rows RAG was asked about (flagged, not answered by the directory)

    @property
    def rag_answered(self) -> int:
        return self.sources.count("RAG")

    def rag_note(self) -> dict[str, object]:
        """For the ledger: what RAG was asked and answered, and the warning if it was silent."""
        if self.variant not in RAG_VARIANTS:
            return {}
        return {"rag_asked": self.rag_asked, "rag_answered": self.rag_answered,
                "rag_warning": silent_warning(self.rag_asked, self.rag_answered)}  # fmt: skip


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


def variant_options(variant: str) -> v2.Options:
    """(a) and rule v4's "RAG alone" have everything off; (b) and (c) have the cleaner and the
    directory with exact and alias matches only (no similarity matching)."""
    on = variant not in ("current", RAG_ALONE)
    return v2.Options(cleaner=on, directory=on, directory_similar=False)


def run_variant(
    variant: str, blind: pd.DataFrame, train_x: pd.DataFrame, train_y: Sequence[str],
    settings: Settings, extra: pd.DataFrame | None,
    rag_answer: Callable[[pd.Series], str | None] | None = None,
) -> VariantResult:  # fmt: skip
    """Predictions of one variant for the blind rows (in memory: nothing is stored)."""
    options = variant_options(variant)
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
    asked = 0
    if variant in RAG_VARIANTS:
        if rag_answer is None:
            raise ValueError("the RAG variant needs an answer function")
        for i, (flagged, source) in enumerate(zip(table["flagged"], sources, strict=True)):
            if flagged and source == "model":
                asked += 1
                got = rag_answer(blind.iloc[i])
                if got is not None:
                    categories[i], sources[i] = got, "RAG"
    return VariantResult(variant, categories, sources, asked)


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
    if not ledger_is_private(path):
        raise ValueError("the ledger holds results on my data: keep it in data/private/")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


# --- Scoring ---


@dataclass(frozen=True)
class Split:
    blind: pd.DataFrame  # the blind rows that can be scored, with "truth", "set" and "known"
    train_x: pd.DataFrame
    train_y: list[str]
    refused_holdout: int
    refused_training: int
    not_categorizer: int
    a_now_known: int = 0  # group A rows whose merchant has training labels by now


def prepare(
    features: pd.DataFrame, labels: dict[str, str], train_x: pd.DataFrame,
    train_y: Sequence[str], holdout: set[str], sets: dict[str, str] | None = None,
) -> Split:  # fmt: skip
    """The scorable blind rows. `sets` gives each row's group from the frozen selection (A or
    K); without it (the synthetic dry run), a row is K when its merchant has training labels."""
    in_holdout = {f for f in labels if f in holdout}
    in_training = {f for f in labels if f in set(train_x.get("fingerprint", []))}
    usable = features[features["fingerprint"].isin(set(labels) - in_holdout - in_training)].copy()
    usable["truth"] = [labels[f] for f in usable["fingerprint"]]
    known_groups = set(train_x["group"]) if not train_x.empty else set()
    usable["known"] = usable["group"].isin(known_groups)
    if sets is None:
        usable["set"] = np.where(usable["known"], "K", "A")
    else:
        usable["set"] = [sets[f] for f in usable["fingerprint"]]
    a_now_known = int(((usable["set"] == "A") & usable["known"]).sum())
    not_categorizer = len(set(labels) - set(features["fingerprint"]) - in_holdout)
    return Split(usable.reset_index(drop=True), train_x, list(train_y), len(in_holdout),
                 len(in_training), not_categorizer, a_now_known)  # fmt: skip


def _accuracy_line(name: str, correct: np.ndarray) -> str:
    if len(correct) == 0:
        return f"  {name}: n = 0"
    return f"  {name}: accuracy {correct.mean():6.1%} (n = {len(correct)})"


@dataclass(frozen=True)
class Comparison:
    """One line of the adopt rule: `variant` against `base` on group A, and the guard on
    group K against (a)."""

    variant: str
    base: str
    a_n: int
    gain: float  # accuracy points on group A, as a fraction (0.03 = 3 points)
    low: float  # the bootstrap's 2.5th percentile of the gain
    k_n: int
    extra_wrong: int  # rows of group K wrong under `variant` minus wrong under (a)
    base_passes: bool = True  # (c) is adopted only if (b), its base, passes too

    @property
    def gain_ok(self) -> bool:
        return self.gain >= MIN_GAIN - 1e-9 and self.low > 0

    @property
    def guard_ok(self) -> bool:
        return self.extra_wrong <= GUARD_EXTRA_WRONG

    @property
    def passes(self) -> bool:
        return self.a_n > 0 and self.gain_ok and self.guard_ok and self.base_passes


def compare(results: dict[str, VariantResult], blind: pd.DataFrame) -> list[Comparison]:
    """(b) against (a) and (c) against (b) on group A (merchant-level paired bootstrap), each
    with the guard on group K against (a). Only the pairs that were both run. (c) passes only
    if (b) passes too: (c) contains (b)."""
    truth = blind["truth"].to_numpy()
    in_a, in_k = (blind["set"] == "A").to_numpy(), (blind["set"] == "K").to_numpy()
    groups = blind["group"].to_numpy()

    def right(variant: str) -> np.ndarray:
        return np.array(results[variant].categories, dtype=object) == truth

    out: list[Comparison] = []
    passed: dict[str, bool] = {"current": True}  # (a) is the starting point
    for variant, base in COMPARISONS:
        if variant not in results or base not in results or "current" not in results:
            continue
        gain, low = (0.0, 0.0)
        if in_a.any():
            gain, low = paired_bootstrap(right(base)[in_a], right(variant)[in_a],
                                         list(groups[in_a]))  # fmt: skip
        extra = int((~right(variant)[in_k]).sum() - (~right("current")[in_k]).sum())
        c = Comparison(variant, base, int(in_a.sum()), gain, low, int(in_k.sum()), extra,
                       base_passes=passed.get(base, False))  # fmt: skip
        passed[variant] = c.passes
        out.append(c)
    return out


@dataclass(frozen=True)
class Veto:
    """A variant against (a) on my real rows: rows it gets wrong beyond (a)'s, per group."""

    variant: str
    a_n: int
    a_extra_wrong: int
    k_n: int
    k_extra_wrong: int

    @property
    def ok(self) -> bool:
        return (self.a_extra_wrong <= A_VETO_EXTRA_WRONG
                and self.k_extra_wrong <= GUARD_EXTRA_WRONG)  # fmt: skip


def veto(results: dict[str, VariantResult], blind: pd.DataFrame) -> list[Veto]:
    """(b) and (c), each against (a), on my group A and group K rows."""
    truth = blind["truth"].to_numpy()
    in_a, in_k = (blind["set"] == "A").to_numpy(), (blind["set"] == "K").to_numpy()

    def wrong(variant: str, mask: np.ndarray) -> int:
        return int((np.array(results[variant].categories, dtype=object)[mask] != truth[mask]).sum())

    return [
        Veto(variant, int(in_a.sum()), wrong(variant, in_a) - wrong("current", in_a),
             int(in_k.sum()), wrong(variant, in_k) - wrong("current", in_k))
        for variant in results
        if variant != "current" and "current" in results
    ]  # fmt: skip


def adoptions(vetoes: Sequence[Veto], public: dict[str, bool]) -> dict[str, bool]:
    """variant -> adopted: it passed on the public set AND is not vetoed on my rows; (c)
    contains (b), so (c) is adopted only if (b) is adopted too."""
    ok = {v.variant: bool(public.get(v.variant)) and v.ok for v in vetoes}
    if "+RAG" in ok:
        ok["+RAG"] = ok["+RAG"] and ok.get("cleaner+directory", False)
    return ok


def external_verdicts(
    entries: Sequence[dict[str, object]], rule_sha256: str | None
) -> dict[str, bool]:
    """variant -> whether it passed on the public set, from the FIRST scored public run under
    this exact rule text: a later `--again` run is recorded but decides nothing, and a run
    under another rule text counts for nothing."""
    out: dict[str, bool] = {}
    for e in entries:
        if (
            e.get("run") == "external"
            and e.get("status") == "scored"
            and "comparison" in e
            and rule_sha256 is not None
            and e.get("adopt_rule_sha256") == rule_sha256
        ):
            out.setdefault(str(e["comparison"]).split(" vs ")[0], bool(e.get("passes")))
    return out


def v2_has_something_to_veto(entries: Sequence[dict[str, object]], rule_sha256: str | None) -> bool:
    """Whether any variant passed rule v2's public run (v2 mode vetoes only those)."""
    return any(external_verdicts(entries, rule_sha256).values())


def rag_passed() -> bool:
    """Whether RAG passed the public run of adopt rule version 4. The one draw of my rows is
    that rule's veto (version 2 adopted nothing), so it waits for this."""
    from eval import categorizer_rag_v4 as rag_rule

    status = adopt_rule_status(rag_rule.RULE, rag_rule.RULE_MARKER)
    return status.ok and rag_rule.verdict(ledger_entries(DEFAULT_LEDGER), status.sha256) is True


def score(
    split: Split, settings: Settings, extra: pd.DataFrame | None, variants: Sequence[str],
    rag_answer: Callable[[pd.Series], str | None] | None, dry_run: bool,
    record: Callable[[dict[str, object]], None] | None = None,
    rule_sha256: str | None = None,
) -> dict[str, VariantResult]:  # fmt: skip
    """Run each variant on the blind rows, then the adopt rule's comparisons. A dry run prints
    counts only."""
    blind = split.blind
    in_a = (blind["set"] == "A").to_numpy()
    print(f"blind rows: {len(blind)} (group A, new merchants: {int(in_a.sum())}; group K, "
          f"known merchants: {int((~in_a).sum())}); merchants: "
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
        if warning := result.rag_note().get("rag_warning"):
            print(f"  {warning}")
        correct = np.array([c == t for c, t in zip(result.categories, blind["truth"],
                                                   strict=True)])  # fmt: skip
        entry = {"variant": variant, "rows": len(blind), "dry_run": dry_run, **result.rag_note()}
        if not dry_run:
            a, k = correct[in_a], correct[~in_a]
            print(_accuracy_line("group A, new-merchant rows", a))
            print(_accuracy_line("group K, known-merchant rows", k))
            entry |= {"a_n": len(a), "a_correct": int(a.sum()),
                      "k_n": len(k), "k_correct": int(k.sum())}  # fmt: skip
            if (len(a) and a.mean() > LEAKAGE_AT) or (len(k) and k.mean() > LEAKAGE_AT):
                print(f"  above {LEAKAGE_AT:.0%}: check for leakage before reading anything")
        if record is not None:
            record(entry)
    if not dry_run:
        decided = external_verdicts(ledger_entries(DEFAULT_LEDGER), rule_sha256)
        vetoes = veto(results, blind)
        adopted_by = adoptions(vetoes, decided)
        for v in vetoes:
            public = decided.get(v.variant)
            adopted = adopted_by[v.variant]
            print(f"  veto, {v.variant} vs current on my rows: group A {v.a_extra_wrong:+d} rows "
                  f"wrong (at most {A_VETO_EXTRA_WRONG:+d}; n = {v.a_n}), group K "
                  f"{v.k_extra_wrong:+d} (at most {GUARD_EXTRA_WRONG:+d}; n = {v.k_n}): "
                  f"{'no veto' if v.ok else 'VETO'}; passed on the public set: "
                  f"{'yes' if public else 'no' if public is not None else 'not scored'}; "
                  f"adopted: {'yes' if adopted else 'no'}")  # fmt: skip
            if record is not None:
                record({"veto": v.variant, "dry_run": False, "a_n": v.a_n, "k_n": v.k_n,
                        "a_extra_wrong": v.a_extra_wrong, "k_extra_wrong": v.k_extra_wrong,
                        "veto_ok": v.ok, "public_passes": public,
                        "adopted": adopted})  # fmt: skip
    if dry_run:
        print(DRY_RUN)
    return results


def score_rag_veto(
    split: Split, settings: Settings, extra: pd.DataFrame | None,
    rag_answer: Callable[[pd.Series], str | None], reasons: dict[str, str],
    record: Callable[[dict[str, object]], None], on_void: Callable[[], None] = lambda: None,
    again: bool = False,
) -> bool | None:  # fmt: skip
    """Rule v4's veto: the deployed model with RAG alone against the deployed model, on my
    blind rows. None for a void run (the same void rule as the public run). The veto is
    recorded before anything is shown. Returns whether RAG is adopted (an --again run
    decides nothing)."""
    from eval import categorizer_rag_v4 as rag_rule

    blind = split.blind
    base = run_variant("current", blind, split.train_x, split.train_y, settings, extra)
    with_rag = run_variant(RAG_ALONE, blind, split.train_x, split.train_y, settings, extra,
                           rag_answer)  # fmt: skip
    log = rag_rule.AnswerLog(dict(reasons))
    note = {"rows_asked": with_rag.rag_asked, "rows_answered": with_rag.rag_answered,
            "answers": len(log.reasons), "answer_reasons": log.counts()}  # fmt: skip
    print(
        f"RAG: asked about {with_rag.rag_asked} of my blind rows, gave a category for "
        f"{with_rag.rag_answered}; {len(log.reasons)} answers by reason {log.counts()}"
    )
    if with_rag.rag_asked == 0:
        print("  WARNING: the deployed model flagged no new-merchant row, so RAG was asked about "
              "none: this run cannot show what RAG does on my rows")  # fmt: skip
    void = rag_rule.void_reason(with_rag.rag_asked, with_rag.rag_answered, log)
    if void:
        print(f"VOID RUN: {void}. Nothing scored; this run does not count.")
        record({"status": "void", "reason": void, **note})
        on_void()
        return None
    results = {"current": base, RAG_ALONE: with_rag}
    [v] = veto(results, blind)
    in_a = (blind["set"] == "A").to_numpy()
    truth = blind["truth"].to_numpy()
    right = {name: np.array(r.categories, dtype=object) == truth for name, r in results.items()}
    adopted = v.ok and not again  # the public pass is checked before this run starts
    decision = ("an --again run decides nothing" if again
                else f"adopted: {'yes' if adopted else 'no'}")  # fmt: skip
    record({"status": "scored", "veto": RAG_ALONE, "a_n": v.a_n, "k_n": v.k_n,
            "a_extra_wrong": v.a_extra_wrong, "k_extra_wrong": v.k_extra_wrong,
            "veto_ok": v.ok, "public_passes": True, "adopted": adopted, **note,
            **{f"{g}_correct_{'rag' if n == RAG_ALONE else 'a'}": int(r[m].sum())
               for n, r in right.items() for g, m in (("a", in_a), ("k", ~in_a))}})  # fmt: skip
    for name, r in right.items():
        print(f"variant {name}: group A {int(r[in_a].sum())} of {int(in_a.sum())} right, "
              f"group K {int(r[~in_a].sum())} of {int((~in_a).sum())} right")  # fmt: skip
        for group, mask in (("A", in_a), ("K", ~in_a)):
            if mask.sum() and r[mask].mean() > LEAKAGE_AT:
                print(f"  group {group} above {LEAKAGE_AT:.0%}: check for leakage first")
    print(f"  veto, {RAG_ALONE} vs current on my rows: group A {v.a_extra_wrong:+d} rows wrong "
          f"(at most {A_VETO_EXTRA_WRONG:+d}; n = {v.a_n}), group K {v.k_extra_wrong:+d} (at "
          f"most {GUARD_EXTRA_WRONG:+d}; n = {v.k_n}): {'no veto' if v.ok else 'VETO'}; passed "
          f"on the public set: yes; {decision}")  # fmt: skip
    return adopted


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rag_answer_function(  # type: ignore[no-untyped-def]
    conn,
    account: str,
    key: bytes,
    cache: Path,
    send: bool,
    client,
    blind=frozenset(),
    reasons: dict[str, str] | None = None,
):
    """RAG with my labelled merchants as examples (the blind rows never among them).
    `reasons`, if given, collects each answer's reason by request (for the void rule)."""
    import lookahead.llm_categorizer as llm
    from lookahead import rag_categorizer as rag

    pairs = rag.labelled_pairs(conn, account, key, exclude=blind)
    index = rag.LabelledIndex(pairs)

    def answer(row: pd.Series) -> str | None:
        name = llm.sendable_name(row["bank_type"], row["text"], key)
        if not name:
            return None
        retrieved = rag.retrieve(name, index)
        got, _ = rag.categorize_one(retrieved, cache, client, send)
        if got is None:
            return None
        if reasons is not None:
            reasons[rag.request_key(rag.request_for(retrieved))] = got.reason
        return got.category

    return answer


def score_my_data(args: argparse.Namespace) -> int:
    from lookahead.db import connect_readonly
    from lookahead.synthetic_training import synthetic_examples

    args.ledger = DEFAULT_LEDGER  # one ledger: a fresh file must not reset "scored once"
    if not ledger_is_private(args.ledger):
        print("the ledger must be inside data/private/ (nothing scored, nothing logged)")
        return 2
    now = datetime.now().isoformat(timespec="seconds")

    def refuse(reason: str, **extra: object) -> int:
        """Every refused run goes in the ledger too, with its reason."""
        print(f"refusing to score: {reason}")
        add_to_ledger(args.ledger, {"at": now, "status": "refused", "reason": reason,
                                    "again": args.again, **extra})  # fmt: skip
        return 2

    for flag in ("db", "labels", "choice"):
        if getattr(args, flag) is None:
            return refuse(f"--{flag} is required (no default points at private data)")
    if args.rule_v4:
        if not (args.rag and args.send):
            return refuse("rule v4's veto is one run with `--rule-v4 --rag --send`",
                          run=VETO_V4_RUN)  # fmt: skip
        if args.rag_cache == DEFAULT_RAG_CACHE:
            args.rag_cache = DEFAULT_VETO_V4_CACHE  # its own cache, never the app's
        apps = {(main_folder() / DEFAULT_RAG_CACHE).resolve(), DEFAULT_RAG_CACHE.resolve()}
        if args.rag_cache.resolve() in apps:
            return refuse("rule v4's veto never uses the app's RAG cache", run=VETO_V4_RUN)
        if not is_private_path(args.rag_cache):
            return refuse("the RAG cache must be inside data/private/", run=VETO_V4_RUN)
        gate = _rule_v4_gate(args)
        if isinstance(gate, str):
            return refuse(gate, run=VETO_V4_RUN)
    rule = args.adopt_rule if args.adopt_rule.is_absolute() else ROOT / args.adopt_rule
    if rule.resolve() != APPROVED_ADOPT_RULE.resolve():
        return refuse(f"only {DEFAULT_ADOPT_RULE} is accepted as the adopt rule")
    status = adopt_rule_status(rule)
    if not status.ok:
        return refuse(f"{status.reason} ({DEFAULT_ADOPT_RULE})")
    drawn = [e for e in ledger_entries(args.ledger) if e.get("status") == "drawn"]
    if not drawn:
        return refuse("the ledger has no draw: the blind rows come from `candidates` only")
    if drawn[-1].get("adopt_rule_sha256") != status.sha256:
        return refuse("the adopt rule changed after the draw: the draw's rule is the one in force")
    if not args.rule_v4 and not v2_has_something_to_veto(ledger_entries(args.ledger),
                                                         status.sha256):  # fmt: skip
        return refuse(
            "nothing passed rule v2's public run, so v2 mode has nothing to veto. The "
            "one draw is RAG rule v4's veto: score it with --rule-v4 --rag --send"
        )
    try:
        labels = load_blind_labels(args.labels)
        selection = selection_fingerprints(args.labels)
        sets = load_blind_sets(args.labels)
    except (OSError, ValueError, KeyError) as e:
        return refuse(f"the labels file cannot be read ({type(e).__name__}: {e})")
    key = selection_key(selection)
    if key != drawn[-1].get("selection_key"):
        return refuse("the labels file does not hold the drawn rows", selection_key=key)
    labels_hash = _sha256(args.labels)
    again = scored_fingerprints(ledger_entries(args.ledger)) & set(selection)
    if again and not args.again:
        return refuse(
            f"{len(again)} rows of this blind selection were already scored: a selection is "
            "scored once, whatever its labels. --again scores it again and is recorded.",
            selection_key=key,
        )
    settings = load_settings(args.choice)
    conn = connect_readonly(args.db)
    features = categorizer_features(conn, args.account)
    train = training_rows(conn, args.account)
    split = prepare(features, labels, train, list(train.get("label", [])),
                    holdout_fingerprints(conn), sets)  # fmt: skip
    if split.refused_training:
        # a blind row I labelled in the app would reach training and the RAG examples
        conn.close()
        return refuse(
            f"{split.refused_training} blind rows carry a label saved in the app. Score before "
            "labelling them in the app, or take them out of the labels file.",
            selection_key=key,
        )
    if split.refused_holdout:
        conn.close()
        return refuse(
            f"{split.refused_holdout} labelled rows are from the old holdout (Test A or "
            "Test B). Remove them from the labels file.",
            selection_key=key,
        )
    if split.a_now_known:
        conn.close()
        return refuse(
            f"{split.a_now_known} group A rows have a merchant with training labels by now: "
            "they are not new merchants any more.",
            selection_key=key,
        )
    a_rows = int((split.blind["set"] == "A").sum())
    if a_rows < A_MIN_ROWS:
        conn.close()
        return refuse(
            f"only {a_rows} labelled group A rows can be scored; at least {A_MIN_ROWS} are "
            "needed. Wait for more data.",
            selection_key=key,
        )
    k_rows = int((split.blind["set"] == "K").sum())
    if k_rows < K_MIN_ROWS:
        conn.close()
        return refuse(
            f"only {k_rows} labelled group K rows can be scored; the guard needs at least "
            f"{K_MIN_ROWS}. Label more of them (skipped rows do not count).",
            selection_key=key,
        )
    extra = synthetic_examples(settings.synthetic_months) if settings.synthetic_months else None
    variants = list(VARIANTS[:2])
    rag_answer = None
    if args.rag and not args.rule_v4:  # rule v4 builds its own RAG, below
        from lookahead.pseudonymize import pseudonym_key

        variants.append("+RAG")
        rag_answer = _rag_answer_function(conn, args.account, pseudonym_key(args.env),
                                          args.rag_cache, args.send, None,
                                          frozenset(labels))  # fmt: skip

    def record(entry: dict[str, object]) -> None:
        add_to_ledger(args.ledger, {"at": now, "status": "scored",
                                    "selection_key": key, "selection": selection,
                                    "labels_sha256": labels_hash,
                                    "adopt_rule_commit": status.commit,
                                    "adopt_rule_sha256": status.sha256, "again": args.again,
                                    "send": bool(args.rag and args.send), **entry})  # fmt: skip

    if args.rule_v4:
        return _score_rule_v4(args, conn, split, settings, extra, selection, record, gate, now)
    print(f"Categorizer v2 on the blind set: account {args.account}; adopt rule at commit "
          f"{(status.commit or '')[:10]}")  # fmt: skip
    score(split, settings, extra, variants, rag_answer, dry_run=False, record=record,
          rule_sha256=status.sha256)  # fmt: skip
    conn.close()
    return 0


def _rule_v4_gate(args: argparse.Namespace) -> str | tuple[RuleStatus, str]:
    """Rule v4's checks that need only the ledger and git, run before anything private is
    read: the refusal reason, or (the rule's status, the code commit)."""
    from eval import categorizer_rag_v4 as rag_rule

    v4 = adopt_rule_status(rag_rule.RULE, rag_rule.RULE_MARKER)
    entries = ledger_entries(args.ledger)
    if not v4.ok:
        return f"RAG adopt rule version 4: {v4.reason}"
    if rag_rule.verdict(entries, v4.sha256) is not True:
        return "RAG did not pass rule version 4's public run: there is nothing to veto"
    head, changed = rag_rule.code_state()
    if changed:
        return "tracked files have uncommitted changes (or git cannot tell)"
    scored = [e for e in entries if e.get("run") == VETO_V4_RUN
              and e.get("status") == "scored" and e.get("veto")]  # fmt: skip
    if args.again and not scored:
        return "--again is only for a second scoring: the first veto run decides"
    runs = [e for e in entries if e.get("run") == VETO_V4_RUN and e.get("status") != "refused"]
    after_crash = bool(runs) and runs[-1].get("status") == "crashed"
    if args.rag_cache.exists() and not (after_crash or args.again):
        return f"the veto's RAG cache must start empty: {args.rag_cache.name} exists"
    return v4, head


def _score_rule_v4(  # type: ignore[no-untyped-def]
    args, conn, split: Split, settings: Settings, extra: pd.DataFrame | None,
    blind_rows: Sequence[str], record, gate: tuple[RuleStatus, str], now: str,
) -> int:  # fmt: skip
    """Rule v4's veto run, after every check of `score_my_data` (draw, labels, minimums).
    `blind_rows` is the whole drawn selection: none of it is ever a RAG example."""
    from eval import categorizer_rag_v4 as rag_rule
    from lookahead.pseudonymize import pseudonym_key

    v4, head = gate

    def record_v4(entry: dict[str, object]) -> None:
        record({"run": VETO_V4_RUN, "rag_rule_commit": v4.commit,
                "rag_rule_sha256": v4.sha256, "code_commit": head, **entry})  # fmt: skip

    def on_void() -> None:
        moved = rag_rule.move_cache_aside(args.rag_cache, now)
        if moved is not None:
            print(f"the cache was moved aside ({moved.name}): the next run asks every answer again")

    print(f"RAG adopt rule v4, the veto on my blind rows: account {args.account}; rule at "
          f"commit {(v4.commit or '')[:10]}")  # fmt: skip
    reasons: dict[str, str] = {}
    try:
        rag_answer = _rag_answer_function(conn, args.account, pseudonym_key(args.env),
                                          args.rag_cache, True, None, frozenset(blind_rows),
                                          reasons)  # fmt: skip
        result = score_rag_veto(split, settings, extra, rag_answer, reasons, record_v4, on_void,
                                args.again)  # fmt: skip
    except BaseException as e:  # Ctrl-C too; the error type only (a message may quote a name)
        record_v4({"status": "crashed", "reason": type(e).__name__})
        print(f"the run stopped ({type(e).__name__}); recorded in the ledger as crashed")
        raise
    finally:
        conn.close()
    return 3 if result is None else 0


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


# --- Blind labelling: merchant text only, never a prediction ---


def _read_labels_file(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [{"fingerprint": r["fingerprint"], "set": r.get("set") or "",
                 "category": r.get("category") or ""} for r in csv.DictReader(f)]  # fmt: skip


def _write_labels_file(path: Path, rows: Sequence[dict[str, str]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fingerprint", "set", "category"])
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def freeze_selection(selection: Path, labels: Path) -> list[str]:
    """The blind rows, in the selection file's order. The first call writes the labels file
    with every row, its group (A or K, never shown while labelling) and an empty category (the
    selection is then frozen); a later call checks the labels file holds exactly the same rows,
    and refuses otherwise."""
    with selection.open(encoding="utf-8", newline="") as f:
        group = {r["fingerprint"]: r.get("set") or "" for r in csv.DictReader(f)}
    if any(g not in ("A", "K") for g in group.values()):
        raise ValueError("the selection has no set column (A or K): make it with `candidates`")
    picked = list(group)
    if labels.exists():
        frozen = [r["fingerprint"] for r in _read_labels_file(labels)]
        if set(frozen) != set(picked):
            raise ValueError("the labels file holds another set of rows than the selection")
        return frozen
    labels.parent.mkdir(parents=True, exist_ok=True)
    _write_labels_file(labels, [{"fingerprint": fp, "set": group[fp], "category": ""}
                                for fp in picked])  # fmt: skip
    return picked


@dataclass(frozen=True)
class Session:
    labelled: int  # in this session
    skipped: int
    left: int  # rows still without a category


def label_session(
    texts: dict[str, str], labels: Path, ask: Callable[[str], str],
    show: Callable[[str], None] = print,
) -> Session:  # fmt: skip
    """Ask for a category for each row of the labels file that has none, showing the
    merchant text only. A number saves a category at once, `s` skips, `q` stops."""
    rows = _read_labels_file(labels)
    show("Categories: " + "; ".join(f"{i} {c}" for i, c in enumerate(CATEGORIES, 1)))
    todo = [r for r in rows if not r["category"]]
    done = skipped = 0
    for n, row in enumerate(todo, 1):
        while True:
            answer = ask(f"[{n} of {len(todo)}] {texts[row['fingerprint']].strip()}\n"
                         "category number, s to skip, q to stop: ").strip().casefold()  # fmt: skip
            if answer in ("q", "s") or (answer.isdigit() and 1 <= int(answer) <= len(CATEGORIES)):
                break
            show("not a category number")
        if answer == "q":
            break
        if answer == "s":
            skipped += 1
            continue
        row["category"] = CATEGORIES[int(answer) - 1]
        _write_labels_file(labels, rows)  # saved at once
        done += 1
    left = sum(not r["category"] for r in rows)
    show(f"labelled {done}, skipped {skipped}; {left} rows still without a category")
    return Session(done, skipped, left)


def relabel_session(
    texts: dict[str, str], labels: Path, wrong: str, ask: Callable[[str], str],
    show: Callable[[str], None] = print,
) -> tuple[str, str] | None:  # fmt: skip
    """Correct ONE label of mine: walk the rows labelled `wrong`, showing the merchant text
    only, until I say "y"; then ask the right category number and save it at once. Returns
    (old, new), or None when nothing was changed. Shows no prediction."""
    rows = _read_labels_file(labels)
    todo = [r for r in rows if r["category"] == wrong]
    show(f"{len(todo)} rows labelled {wrong!r}. Categories: "
         + "; ".join(f"{i} {c}" for i, c in enumerate(CATEGORIES, 1)))  # fmt: skip
    for n, row in enumerate(todo, 1):
        answer = ""
        while answer not in ("y", "n", "q"):
            answer = ask(f"[{n} of {len(todo)}] {texts[row['fingerprint']].strip()}\n"
                         "is this the row to correct? y / n / q: ").strip().casefold()  # fmt: skip
        if answer == "q":
            break
        if answer == "n":
            continue
        while True:
            number = ask("the right category number: ").strip()
            if number.isdigit() and 1 <= int(number) <= len(CATEGORIES):
                break
            show("not a category number")
        new = CATEGORIES[int(number) - 1]
        if new == wrong:
            show("the same category: nothing changed")
            return None
        row["category"] = new
        _write_labels_file(labels, rows)
        show(f"saved: one label corrected, {wrong} -> {new}")
        return wrong, new
    show("nothing changed")
    return None


def relabel_command(args: argparse.Namespace) -> int:
    """`relabel`: correct one blind label of mine before scoring, recorded in the ledger."""
    from lookahead.db import connect_readonly

    for flag in ("db", "labels", "wrong"):
        if getattr(args, flag) is None:
            print(f"--{flag} is required (no default points at private data)")
            return 2
    if not is_private_path(args.labels):
        print("--labels must be inside data/private/ (it names merchants)")
        return 2
    if args.wrong not in CATEGORIES:
        print("--wrong must be a category written exactly as in the list")
        return 2
    selection = selection_fingerprints(args.labels)
    if scored_fingerprints(ledger_entries(DEFAULT_LEDGER)) & set(selection):
        print("refusing: this selection was already scored. A label is never changed after it.")
        return 2
    conn = connect_readonly(args.db)
    texts = {fp: text for fp, text in conn.execute(
        "SELECT fingerprint, sub_description FROM transactions WHERE account = ?",
        (args.account,))}  # fmt: skip
    conn.close()
    if any(fp not in texts for fp in selection):
        print("refusing: some rows of the labels file are not in the database")
        return 2
    changed = relabel_session(texts, args.labels, args.wrong, input)
    if changed is not None:
        add_to_ledger(DEFAULT_LEDGER, {
            "at": datetime.now().isoformat(timespec="seconds"), "status": "relabelled",
            "selection_key": selection_key(selection), "labels_corrected": 1,
            "from": changed[0], "to": changed[1], "before_scoring": True,
            "reason": "my own slip while labelling, corrected before any scoring and "
                      "without seeing any prediction",
        })  # fmt: skip
    return 0


def label_command(args: argparse.Namespace) -> int:
    from lookahead.db import connect_readonly

    for flag in ("db", "selection", "labels"):
        if getattr(args, flag) is None:
            print(f"--{flag} is required (no default points at private data)")
            return 2
    if not (is_private_path(args.selection) and is_private_path(args.labels)):
        print("--selection and --labels must be inside data/private/ (they name merchants)")
        return 2
    if not args.db.is_file():
        print(f"refusing to label: no database at {args.db}")
        return 2
    status = adopt_rule_status(APPROVED_ADOPT_RULE)
    if not status.ok:
        print(f"refusing to label: {status.reason} ({DEFAULT_ADOPT_RULE}). The rule comes first.")
        return 2
    try:
        frozen = freeze_selection(args.selection, args.labels)
    except ValueError as e:
        print(f"refusing to label: {e}")
        return 2
    conn = connect_readonly(args.db)
    texts = {fp: text for fp, text in conn.execute(
        "SELECT fingerprint, sub_description FROM transactions WHERE account = ?",
        (args.account,))}  # fmt: skip
    conn.close()
    missing = [fp for fp in frozen if fp not in texts]
    if missing:
        print(f"refusing to label: {len(missing)} selected rows are not in the database")
        return 2
    print(f"blind selection frozen: {len(frozen)} rows. Merchant text only: no prediction.")
    label_session(texts, args.labels, input)
    return 0


def _draw_inputs(db: Path, account: str) -> tuple[pd.DataFrame, set[str], set[str]]:
    """The categorizer rows, the merchants with training labels, and the rows never drawn
    (old holdout, rows with a label, rows I labelled or skipped in the label review)."""
    from lookahead.db import connect_readonly

    conn = connect_readonly(db)
    features = categorizer_features(conn, account)
    mine = training_rows(conn, account)
    groups = set(mine["group"]) if not mine.empty else set()
    labelled = set(mine["fingerprint"]) if not mine.empty else set()
    excluded = holdout_fingerprints(conn) | labelled | decided_fingerprints(conn, account)
    conn.close()
    return features, groups, excluded


def count_candidates(args: argparse.Namespace) -> int:
    """Counts only, before the draw: nothing is drawn, written or logged."""
    features, groups, excluded = _draw_inputs(args.db, args.account)
    c = pool_counts(features, groups, excluded, BLIND_SINCE)
    print(f"could be drawn since {BLIND_SINCE} (nothing drawn): group A {c.a_rows} rows after "
          f"the cap of {A_PER_MERCHANT}, from {c.a_merchants} new merchants (needs at least "
          f"{A_MIN_ROWS}); group K {c.k_rows} rows from {c.k_merchants} known merchants "
          f"(the guard needs at least {K_MIN_ROWS} labelled)")  # fmt: skip
    return 0


def write_candidates(args: argparse.Namespace) -> int:
    """Draw the blind selection (groups A and K, seeded) and write it inside data/private/.
    An existing selection file is never overwritten: the draw is made once."""

    if args.db is None:
        print("--db is required (no default points at private data)")
        return 2
    if args.count:
        return count_candidates(args)
    if args.out is None:
        print("--out is required to draw")
        return 2
    if not is_private_path(args.out):
        print("--out must be inside data/private/ (the file names merchants)")
        return 2
    if args.out.exists():
        print(f"refusing: {args.out} exists. The selection is drawn once and never redrawn.")
        return 2
    status = adopt_rule_status(APPROVED_ADOPT_RULE)
    if not status.ok:
        print(f"refusing: {status.reason} ({DEFAULT_ADOPT_RULE}). The rule comes before the draw.")
        return 2
    if not ledger_is_private(DEFAULT_LEDGER):
        print("the ledger must be inside data/private/ (nothing drawn)")
        return 2
    if not external_verdicts(ledger_entries(DEFAULT_LEDGER), status.sha256):
        print(
            "refusing: the public deciding run comes first, under this exact rule text. My "
            "rows are drawn last, right before the veto."
        )
        return 2
    if not rag_passed():
        print(
            "refusing: the one draw of my rows is the veto of adopt rule version 4 (RAG alone). "
            "It waits until RAG passes that rule's public run."
        )
        return 2
    if any(e.get("status") == "drawn" for e in ledger_entries(DEFAULT_LEDGER)):
        print("refusing: the ledger already has a blind selection drawn. It is never redrawn.")
        return 2
    features, groups, excluded = _draw_inputs(args.db, args.account)
    try:
        picked = select_blind_rows(features, groups, excluded, BLIND_SINCE)
    except ValueError as e:
        print(f"refusing: {e}")
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    picked.assign(category="")[["fingerprint", "posted_date", "group", "set", "category"]].to_csv(
        args.out, index=False
    )
    a, k = picked[picked["set"] == "A"], picked[picked["set"] == "K"]
    add_to_ledger(DEFAULT_LEDGER, {
        "at": datetime.now().isoformat(timespec="seconds"), "status": "drawn",
        "selection_key": selection_key(list(picked["fingerprint"])), "seed": SELECTION_SEED,
        "since": BLIND_SINCE.isoformat(), "a_rows": len(a), "a_merchants": a["group"].nunique(),
        "k_rows": len(k), "k_merchants": k["group"].nunique(),
        "adopt_rule_commit": status.commit, "adopt_rule_sha256": status.sha256,
    })  # fmt: skip
    print(f"blind selection since {BLIND_SINCE} (seed {SELECTION_SEED}): group A {len(a)} rows "
          f"from {a['group'].nunique()} new merchants; group K {len(k)} rows from "
          f"{k['group'].nunique()} known merchants; written to {args.out} "
          "(not shown here)")  # fmt: skip
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
    s.add_argument("--rag", action="store_true", help="add variant (c), the RAG categorizer")
    s.add_argument("--send", action="store_true", help="with --rag: ask for missing answers")
    s.add_argument("--rag-cache", type=Path, default=DEFAULT_RAG_CACHE)
    s.add_argument("--env", type=Path, default=Path(".env"))
    s.add_argument("--again", action="store_true", help="score the same labels again")
    s.add_argument(
        "--rule-v4",
        action="store_true",
        help="RAG adopt rule v4's veto: the deployed model + RAG alone vs the model",
    )
    c = commands.add_parser("candidates", help="draw the blind selection (groups A and K)")
    c.add_argument("--db", type=Path)
    c.add_argument("--account", default="scotia-chequing")
    c.add_argument("--out", type=Path, help="the selection file (data/private/); not with --count")
    c.add_argument("--count", action="store_true", help="counts only: nothing is drawn")
    lab = commands.add_parser("label", help="label the frozen blind selection (text only)")
    lab.add_argument("--db", type=Path)
    lab.add_argument("--account", default="scotia-chequing")
    lab.add_argument("--selection", type=Path, help="the candidates file (data/private/)")
    lab.add_argument("--labels", type=Path, help="my labels file (data/private/)")
    fix = commands.add_parser("relabel", help="correct one blind label of mine, before scoring")
    fix.add_argument("--db", type=Path)
    fix.add_argument("--account", default="scotia-chequing")
    fix.add_argument("--labels", type=Path, help="my labels file (data/private/)")
    fix.add_argument("--wrong", help="the category I gave by mistake, exactly as in the list")
    args = parser.parse_args(argv)
    if args.command == "label":
        return label_command(args)
    if args.command == "relabel":
        return relabel_command(args)
    if args.command == "candidates":
        return write_candidates(args)
    if args.synthetic:
        return dry_run()
    return score_my_data(args)


if __name__ == "__main__":
    sys.exit(main())
