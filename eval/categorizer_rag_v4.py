"""Categorizer RAG, adopt rule version 4 (replacing version 3, whose one run was void): does
RAG improve today's categorizer? (a) against (a)+RAG on a new split of the public DoDataThings
v2 set. The rule is docs/specs/2026-11-19-categorizer-rag-adopt-rule-v4.md; this file runs it.
Version 4 changes only how answers are read (`lookahead.rag_categorizer.parse_answer`, shared
with the app).

    python -m eval.categorizer_rag_v4 split                  # counts only: the new split
    python -m eval.categorizer_rag_v4 score --rag --send     # the one scoring
    python -m eval.categorizer_rag_v4 score --rag --send --again   # recorded, decides nothing

Everything public comes from `eval.categorizer_v2_external`: the pinned files, the merchant
key, the category table, the row order and `_take`. Nothing of mine is used.

The new split: version 2's 356 deciding-sample merchants always go to the train part; of the
other 716 merchants (sorted), `default_rng(1).choice(size=286)` picks the test merchants. The
sample is at most 3 rows per test merchant, then 600 rows, with one new `default_rng(1)`.

A void run (RAG answered no row, or more than 5% of its answers were refused as not usable)
stops before any accuracy is computed, is logged with its counts by reason, and does not count
as the scoring. Its cache file is moved aside, so the next run asks every answer again.

The comparison is recorded before any accuracy is shown, so a run that has shown a result
has always spent the scoring. One run at a time: two started together could both pass the
"already scored" check.
"""

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from eval import categorizer_v2 as h
from eval import categorizer_v2_external as x
from lookahead.merchant_clean import clean_merchant
from lookahead.merchants import is_private_path

ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "docs" / "specs" / "2026-11-19-categorizer-rag-adopt-rule-v4.md"
RULE_MARKER = "# Categorizer RAG, adopt rule version 4"
RUN = "external-rag-v4"  # the ledger's name for these runs (version 3's were "external-rag-v3")
COMPARISON = "(a)+RAG vs (a)"
DEFAULT_RAG_CACHE = (
    ROOT / "data" / "private" / "cache" / "rag_v4.json"
)  # its own file, empty at first

# The rule's numbers.
SPLIT_SEED = 1
TEST_SHARE = 0.4
SAMPLE_SEED = 1
SAMPLE_ROWS = 600
SAMPLE_PER_MERCHANT = 3
MIN_GAIN = 0.03
MAX_REFUSED_SHARE = 0.05  # of the answers (one per distinct request): above this, void


# --- The split ---


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    test: pd.DataFrame
    sample: pd.DataFrame
    old_sample: pd.DataFrame  # version 2's deciding sample: its merchants are all in train


def v3_split(rows: pd.DataFrame) -> Split:
    """The rule's split of the kept rows (see the module docstring)."""
    kept = rows[rows["mine"].notna()].reset_index(drop=True)
    _, old_test = x.split(rows)
    old_sample = x.deciding_sample(old_test)
    eligible = sorted(set(kept["merchant"]) - set(old_sample["merchant"]))
    size = round(TEST_SHARE * len(eligible))
    test_merchants = set(np.random.default_rng(SPLIT_SEED).choice(eligible, size=size,
                                                                  replace=False))  # fmt: skip
    in_test = kept["merchant"].isin(test_merchants)
    train, test = kept[~in_test].reset_index(drop=True), kept[in_test].reset_index(drop=True)
    return Split(train, test, sample(test), old_sample)


def sample(test: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SAMPLE_SEED)
    capped = [x._take(r, SAMPLE_PER_MERCHANT, rng) for _, r in sorted(test.groupby("merchant"))]
    return x._take(pd.concat(capped, ignore_index=True), SAMPLE_ROWS, rng).reset_index(drop=True)


def load_split(folder: Path = x.EXTERNAL_DIR) -> Split:
    keys = x.MerchantKeys(x.generator_names((folder / x.GENERATOR_FILE).read_text("utf-8")))
    return v3_split(x.load_rows(folder / x.DATASET_FILE, keys))


def split_counts(s: Split) -> dict[str, object]:
    """Counts only: no description, no accuracy."""
    names = {clean_merchant(x.body(d)).key for d in s.sample["description"]}  # match form
    old_names = {clean_merchant(x.body(d)).key for d in s.old_sample["description"]}
    return {
        "train": [len(s.train), int(s.train["merchant"].nunique())],
        "test": [len(s.test), int(s.test["merchant"].nunique())],
        "sample": [len(s.sample), int(s.sample["merchant"].nunique())],
        "v2_sample_merchants_in_test": len(set(s.old_sample["merchant"]) & set(s.test["merchant"])),
        "merchants_in_train_and_test": len(set(s.train["merchant"]) & set(s.test["merchant"])),
        "sample_names_in_the_v2_sample": len(names & old_names),
    }


# --- RAG, with every answer's reason kept ---


def usable(reason: str) -> bool:
    """A usable answer: a category ("ok") or any kind of "unsure" (the row keeps (a)'s guess).
    Everything else is refused and counts toward the void rule: not JSON, several objects none
    of them an answer, not the expected object, citations not a list of ids, not one of the
    categories, an unknown or repeated citation."""
    from lookahead.rag_categorizer import is_unsure

    return reason == "ok" or is_unsure(reason)


@dataclass
class AnswerLog:
    reasons: dict[str, str]  # request key -> reason ("ok", "unsure", "not JSON", ...)

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(self.reasons.values()).items()))

    def refused(self) -> int:
        return sum(not usable(r) for r in self.reasons.values())


def rag_answer_function(
    train: pd.DataFrame, cache: Path, send: bool, client: object | None = None
) -> tuple[Callable[[str], str | None], AnswerLog]:
    """RAG with the train part's merchants as examples; every answer's reason is logged."""
    from lookahead import rag_categorizer as rag

    index = rag.LabelledIndex(zip((clean_merchant(x.body(d)).name for d in train["description"]),
                                  train["mine"], strict=True))  # fmt: skip
    log = AnswerLog({})

    def answer(name: str) -> str | None:
        retrieved = rag.retrieve(name, index)
        got, _ = rag.categorize_one(retrieved, cache, client, send)
        if got is None:
            return None
        log.reasons[rag.request_key(rag.request_for(retrieved))] = got.reason
        return got.category

    return answer, log


def void_reason(rows_asked: int, rows_answered: int, log: AnswerLog) -> str | None:
    """Why the run is void, or None. Answers are counted once per distinct request."""
    if rows_asked and not rows_answered:
        return f"RAG was asked about {rows_asked} rows and gave a category for none"
    answers = len(log.reasons)
    if answers and log.refused() > MAX_REFUSED_SHARE * answers:
        return (f"{log.refused()} of {answers} answers refused as not usable (more than "
                f"{MAX_REFUSED_SHARE:.0%})")  # fmt: skip
    return None


# --- Predictions ---


@dataclass(frozen=True)
class Predictions:
    a: list[str]
    with_rag: list[str]
    rows_asked: int
    rows_answered: int


def predict(
    model: object, rows: pd.DataFrame, rag_answer: Callable[[str], str | None]
) -> Predictions:
    """(a)'s categories, and the same with RAG on every row (a) flags as a new merchant. RAG
    looks up the cleaned description, never the merchant key; there is no directory step."""
    feats = x.features(rows)
    preds = model.predict_rows(feats)  # type: ignore[attr-defined]
    a = [x.same(p.category) for p in preds]
    with_rag, asked, answered = list(a), 0, 0
    for i, (p, name) in enumerate(zip(preds, feats["name"], strict=True)):
        if p.flagged and p.route == "new merchant":
            asked += 1
            got = rag_answer(clean_merchant(name).name)
            if got is not None:
                with_rag[i], answered = x.same(got), answered + 1
    return Predictions(a, with_rag, asked, answered)


# --- Scoring ---


def sample_key(s: pd.DataFrame) -> str:
    return "external-rag-v4:" + hashlib.sha256("\n".join(s["description"]).encode()).hexdigest()


def verdict(entries: list[dict[str, object]], rule_sha256: str | None) -> bool | None:
    """The deciding verdict: the first scored comparison under this exact rule text that is
    not an --again run (--again, refused, void and crashed runs decide nothing). None if not
    scored yet."""
    if rule_sha256 is None:
        return None
    for e in entries:
        deciding = e.get("run") == RUN and e.get("status") == "scored" and not e.get("again")
        if deciding and e.get("comparison") and e.get("adopt_rule_sha256") == rule_sha256:
            return bool(e.get("passes"))
    return None


def move_cache_aside(cache: Path, now: str) -> Path | None:
    """After a void run: keep the answers (renamed), so the next run starts empty."""
    if not cache.exists():
        return None
    target = cache.with_name(f"{cache.stem}.void-{now.replace(':', '')}{cache.suffix}")
    cache.rename(target)
    return target


def score(
    s: Split, model: object, rag_answer: Callable[[str], str | None], log: AnswerLog,
    record: Callable[[dict[str, object]], None], on_void: Callable[[], None] = lambda: None,
) -> bool | None:  # fmt: skip
    """The rule's run. Returns whether (a)+RAG passes, or None for a void run."""
    preds = predict(model, s.sample, rag_answer)
    rag_note = {"rows_asked": preds.rows_asked, "rows_answered": preds.rows_answered,
                "answers": len(log.reasons), "answer_reasons": log.counts()}  # fmt: skip
    print(
        f"RAG: asked about {preds.rows_asked} rows, gave a category for "
        f"{preds.rows_answered}; {len(log.reasons)} answers by reason {log.counts()}"
    )
    void = void_reason(preds.rows_asked, preds.rows_answered, log)
    if void:
        print(f"VOID RUN: {void}. No accuracy computed; this run does not count as the scoring.")
        record({"status": "void", "reason": void, **rag_note})
        on_void()
        return None
    truth = [x.same(c) for c in s.sample["mine"]]
    a = np.array([p == t for p, t in zip(preds.a, truth, strict=True)])
    c = np.array([p == t for p, t in zip(preds.with_rag, truth, strict=True)])
    gain, low = h.paired_bootstrap(a, c, list(s.sample["merchant"]))
    passes = gain >= MIN_GAIN - 1e-9 and low > 0
    # The comparison is recorded BEFORE anything is shown: once an accuracy is visible, the
    # scoring is spent (a crash before this point shows nothing, so a re-run is fair).
    record({"status": "scored", "comparison": COMPARISON, "a_n": len(a), "gain": gain,
            "bootstrap_low": low, "passes": passes, "a_correct": int(a.sum()),
            "rag_correct": int(c.sum()), **rag_note})  # fmt: skip
    for name, right in (("(a)", a), ("(a)+RAG", c)):
        print(f"variant {name}: accuracy {right.mean():6.1%} (n = {len(right)}; "
              f"{int(right.sum())} right)")  # fmt: skip
        if right.mean() > h.LEAKAGE_AT:
            print(f"  above {h.LEAKAGE_AT:.0%}: check for leakage before reading anything")
        record({"status": "scored", "variant": name, "a_n": len(right),
                "a_correct": int(right.sum()), **rag_note})  # fmt: skip
    print(f"  {COMPARISON}: {gain * 100:+.1f} points (needs {MIN_GAIN * 100:+.0f}), bootstrap "
          f"2.5th percentile {low * 100:+.1f} (needs above 0; n = {len(a)} rows, "
          f"{s.sample['merchant'].nunique()} merchants resampled); passes on the public set: "
          f"{'yes' if passes else 'no'}")  # fmt: skip
    print("  if it passes, RAG still needs the veto on my real rows before the app offers it")
    return passes


def code_state() -> tuple[str, bool]:
    """(the commit the harness runs from, whether tracked files have uncommitted changes)."""
    head = h._git(["rev-parse", "HEAD"], ROOT)
    state = h._git(["status", "--porcelain", "--untracked-files=no"], ROOT)
    if head.returncode or state.returncode:
        return "", True  # git cannot tell: treated as not committed
    return head.stdout.strip(), bool(state.stdout.strip())


def score_command(args: argparse.Namespace) -> int:
    ledger = h.DEFAULT_LEDGER
    now = datetime.now().isoformat(timespec="seconds")
    head, changed = code_state()

    def refuse(reason: str) -> int:
        print(f"refusing to score: {reason}")
        h.add_to_ledger(ledger, {"at": now, "run": RUN, "status": "refused", "reason": reason,
                                 "again": args.again, "code_commit": head})  # fmt: skip
        return 2

    status = h.adopt_rule_status(RULE, RULE_MARKER)
    if not status.ok:
        return refuse(f"{status.reason} ({RULE.relative_to(ROOT)})")
    if not (args.rag and args.send):
        return refuse("the one scoring is `score --rag --send`")
    if changed:
        return refuse("tracked files have uncommitted changes (or git cannot tell): the code "
                      "that runs is committed")  # fmt: skip
    if not is_private_path(args.rag_cache):
        return refuse("the RAG cache must be inside data/private/")
    # The cache starts empty. Two exceptions: right after a crashed v4 run (its saved answers
    # come from this rule and parser), and an --again run (a second scoring decides nothing).
    runs = [e for e in h.ledger_entries(ledger)
            if e.get("run") == RUN and e.get("status") != "refused"]  # fmt: skip
    after_crash = bool(runs) and runs[-1].get("status") == "crashed"
    if args.rag_cache.exists() and not (after_crash or args.again):
        return refuse("the RAG cache must start empty (every answer asked again): "
                      f"{args.rag_cache.name} already exists")  # fmt: skip
    problems = x.check_files(args.folder)
    if problems:
        return refuse("; ".join(problems))
    try:
        s = load_split(args.folder)
    except BaseException as e:
        h.add_to_ledger(ledger, {"at": now, "run": RUN, "status": "crashed",
                                 "reason": type(e).__name__, "code_commit": head})  # fmt: skip
        raise
    key = sample_key(s.sample)
    # scored = a comparison was recorded, for any sample and any rule text: this rule has one
    # public scoring (a run that crashed before recording it showed nothing and decided nothing)
    earlier = [e for e in h.ledger_entries(ledger) if e.get("run") == RUN
               and e.get("status") == "scored" and e.get("comparison")]  # fmt: skip
    if earlier and not args.again:
        return refuse("the sample was already scored: it is scored once. --again scores it "
                      "again, is recorded, and decides nothing.")  # fmt: skip
    if args.again and not earlier:
        return refuse("--again is only for a second scoring: the first scoring is the one "
                      "that decides, run without it")  # fmt: skip

    def record(entry: dict[str, object]) -> None:
        h.add_to_ledger(ledger, {"at": now, "run": RUN, "selection_key": key,
                                 "dataset_sha256": x.DATASET_SHA256,
                                 "generator_sha256": x.GENERATOR_SHA256,
                                 "adopt_rule_commit": status.commit,
                                 "adopt_rule_sha256": status.sha256, "again": args.again,
                                 "code_commit": head, "send": True, **entry})  # fmt: skip

    def on_void() -> None:
        moved = move_cache_aside(args.rag_cache, now)
        if moved is not None:
            print(f"the cache was moved aside ({moved.name}): the next run asks every answer again")

    print(f"Categorizer RAG v4, deciding run on the public set; adopt rule at commit "
          f"{(status.commit or '')[:10]}")  # fmt: skip
    try:
        counts = split_counts(s)
        print(f"split: train {counts['train']}, test {counts['test']}, sample "
              f"{counts['sample']} (rows, merchants)")  # fmt: skip
        model = x.train_variant("current", s.train)
        rag_answer, log = rag_answer_function(s.train, args.rag_cache, send=True)
        result = score(s, model, rag_answer, log, record, on_void)
    except BaseException as e:  # Ctrl-C too; the kind of error only: a message may quote a name
        record({"status": "crashed", "reason": type(e).__name__})
        print(f"the run stopped ({type(e).__name__}); recorded in the ledger as crashed")
        raise
    return 3 if result is None else 0


def split_command(args: argparse.Namespace) -> int:
    problems = x.check_files(args.folder)
    if problems:
        print("; ".join(problems))
        return 2
    print(json.dumps(split_counts(load_split(args.folder)), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_rag_v4")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("split", "score"):
        c = commands.add_parser(name)
        c.add_argument("--folder", type=Path, default=x.EXTERNAL_DIR)
        if name == "score":
            c.add_argument("--rag", action="store_true")
            c.add_argument("--send", action="store_true", help="with --rag: ask Haiku")
            c.add_argument("--rag-cache", type=Path, default=DEFAULT_RAG_CACHE)
            c.add_argument("--again", action="store_true")
    args = parser.parse_args(argv)
    return split_command(args) if args.command == "split" else score_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
