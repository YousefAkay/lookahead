"""Categorizer v2, the deciding run on public data: DoDataThings "US Bank Transaction
Categories v2" (synthetic, MIT), split by merchant. Nothing of mine is used here: no label,
no saved setting, no synthetic row from my generator.

    python -m eval.categorizer_v2_external fetch     # download the two pinned files, check them
    python -m eval.categorizer_v2_external summary   # counts only: rows, merchants, the sample
    python -m eval.categorizer_v2_external score [--rag [--send]] [--again]

What is pinned (the adopt rule names the same values):
- the dataset file at its Hugging Face revision, and its sha256;
- the generator's merchant-name list (wnstnb/foliome, scripts/generate-training-data.js,
  Apache-2.0) at the only public commit of that file, and its sha256. It is read as text to
  find merchant names; it is never run.
Both are downloaded into data/public/dodatathings/ (git-ignored), not committed.

How a row's merchant is found (`MerchantKeys`, frozen with the rule):
1. a generator merchant name as a whole word (earliest in the text, then the longest);
2. the same name with spaces and punctuation removed, covering whole words of the text
   ("CHICKFILA"; not "EXPRESS" inside "EXPRESSWAY"); a name of two or more words may also
   run into the next field, as the generator's fixed-width formats do ("BATH & BODY
   WORKSWEB ID", "AVALON COMMUNITIESAPT");
3. a truncated form: the text's first word, punctuation removed, of 7+ letters, that
   begins exactly one squashed generator name ("TRADERJOE", "STOP&SHO", "WINN-DIX");
4. the fixed spellings the generator writes in its own code (`ALIASES`);
5. otherwise the first two words of the text.
The key is used for the split, the per-merchant cap, the bootstrap and the known-merchant
lookup only: it is never a model feature, and the directory looks up the cleaned
description, not the key.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from eval import categorizer_v2 as h
from lookahead import categorizer_v2 as v2
from lookahead.merchant_clean import clean_merchant

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_DIR = ROOT / "data" / "public" / "dodatathings"
DATASET_REVISION = "3e8d052cb7b362fb1a04b6064d2331982553ea56"
DATASET_FILE = "transactions-synthetic.csv"
DATASET_URL = (
    "https://huggingface.co/datasets/DoDataThings/us-bank-transaction-categories-v2/resolve/"
    f"{DATASET_REVISION}/{DATASET_FILE}"
)
DATASET_SHA256 = "0424aed6e76f74a5b3b1ff61ccec43bc321622e6806da353b910b3b2c8108f6e"
GENERATOR_COMMIT = "edde93d3550e9a2699fa8be4ceea2ce205d4ab43"
GENERATOR_FILE = "generate-training-data.js"
GENERATOR_URL = (
    f"https://raw.githubusercontent.com/wnstnb/foliome/{GENERATOR_COMMIT}/scripts/{GENERATOR_FILE}"
)
GENERATOR_SHA256 = "d380e4bbdd0d34ecee025f029ca713ae75577d4eb46a0eed0aa7f82fd5d3f547"
DEFAULT_RAG_CACHE = ROOT / "data" / "private" / "cache" / "rag_external.json"  # must be private

# Their category -> mine. None: dropped (no category of mine, or decided by my bank rules,
# not by the model: Transfers, Income, Fees come from Scotiabank transaction types).
MAPPING: dict[str, str | None] = {
    "Restaurants": "Restaurants & cafés", "Groceries": "Groceries", "Shopping": "Shopping",
    "Transportation": "Transport", "Entertainment": "Entertainment",
    "Utilities": "Phone, internet & utilities", "Subscription": "Subscriptions",
    "Healthcare": "Health & pharmacy", "Education": "Education", "Personal Care": "Personal care",
    "Mortgage": "Rent & housing", "Rent": "Rent & housing",
    "Transfer": None, "Income": None, "Fees": None, "Insurance": None, "Travel": None,
}  # fmt: skip
SAME_WHEN_SCORING = {"Food delivery": "Restaurants & cafés"}  # this set has no Food delivery
TEST_SHARE = 0.4  # of the merchants, drawn with SPLIT_SEED
SPLIT_SEED = 0
SAMPLE_ROWS = 600  # the deciding sample, from test merchants only
SAMPLE_PER_MERCHANT = 3
SAMPLE_SEED = 0
PREFIX_MIN = 7  # a truncated name ("TRADERJOE") needs at least this many letters
# Fixed spellings written in the generator's own code (edde93d, lines 816 and 867-870, 1070-1074)
ALIASES: dict[str, str] = {
    "AMAZONCOM": "AMAZON", "AMAZONMKTPL": "AMAZON", "AMAZONMARKTPLACE": "AMAZON",
    "AMZNMKTP": "AMAZON", "AMZNDIGITAL": "AMAZON", "KINDLEUNLTD": "AMAZON",
    "WHOLEFDS": "WHOLE FOODS", "TRADERJOES": "TRADER JOE'S", "SAVEMART": "SAVE MART",
    "SMARTFINAL": "SMART & FINAL", "FOODMAXX": "FOODMAXX", "GROCOUTLT": "GROCERY OUTLET",
    "APPLECOMBILL": "APPLE",
}  # fmt: skip
NOT_MERCHANTS = {"PAYPAL", "ZELLE", "VENMO", "SQ", "TST", "CLV", "PP"}  # payment wrappers
_TAG = re.compile(r"^\s*\[(debit|credit)\]\s*", re.I)
_WRAPPER = re.compile(r"^(PAYPAL\s+INST XFER|SQ\s*\*|TST\s*\*|CLV\s*\*|PP\s*\*)\s*", re.I)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)+)"')


# --- The two pinned files ---


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_files(folder: Path = EXTERNAL_DIR) -> dict[str, tuple[Path, str, str]]:
    return {
        "dataset": (folder / DATASET_FILE, DATASET_URL, DATASET_SHA256),
        "generator": (folder / GENERATOR_FILE, GENERATOR_URL, GENERATOR_SHA256),
    }


def check_files(folder: Path = EXTERNAL_DIR) -> list[str]:
    """What is wrong with the pinned files (empty: both present, both hashes right)."""
    problems = []
    for name, (path, _, expected) in pinned_files(folder).items():
        if not path.exists():
            problems.append(f"{name}: {path.name} is missing (run fetch)")
        elif sha256_of(path) != expected:
            problems.append(f"{name}: {path.name} does not match its pinned sha256")
    return problems


def fetch(folder: Path = EXTERNAL_DIR) -> int:
    """Download each pinned file that is missing; a file with the wrong hash is deleted."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, (path, url, expected) in pinned_files(folder).items():
        if not path.exists():
            request = urllib.request.Request(url, headers={"User-Agent": "dataset-download"})
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (pinned https)
                path.write_bytes(response.read())
        if sha256_of(path) != expected:
            path.unlink()
            print(f"{name}: wrong sha256, deleted. Nothing is scored on an unpinned file.")
            return 2
        print(f"{name}: {path.name} matches its pinned sha256")
    return 0


# --- Merchants ---


def generator_names(js: str) -> list[str]:
    """Merchant names in the generator's source text: the `merchants` object and the
    employer, compressed-chain and bank lists. Cities and people's names are left out."""
    start = js.index("const merchants = {")
    end = js.index("\n};", start)
    names = set(_QUOTED.findall(js[start:end]))
    for m in re.finditer(r"const (\w+) = \[(.*?)\];", js, re.S):
        if m.group(1) in ("EMPLOYER_NAMES", "compressedChains", "ddaBanks"):
            names.update(_QUOTED.findall(m.group(2)))
    return sorted(names)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.upper()).strip()


def _flat(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def body(description: str) -> str:
    """The description without its "[debit]" / "[credit]" tag: what the model reads."""
    return _TAG.sub("", description).strip()


class MerchantKeys:
    """The frozen merchant key of a description (module docstring, steps 1 to 5)."""

    def __init__(self, names: Sequence[str]) -> None:
        keep = {_norm(n) for n in names if len(_norm(n)) >= 2 and _norm(n) not in NOT_MERCHANTS}
        ordered = sorted(keep, key=lambda n: (-len(n), n))
        self.words = [(n, re.compile(r"(?<![A-Z0-9])" + re.escape(n) + r"(?![A-Z0-9])"))
                      for n in ordered]  # fmt: skip
        self.flat = [(_flat(n), n) for n in ordered if len(_flat(n)) >= 5]
        self.by_flat = {f: n for f, n in reversed(self.flat)}  # the longest name wins a tie
        self.multiword = [(f, n) for f, n in self.flat if " " in n]  # longest first

    def key(self, description: str) -> tuple[str, str]:
        """(merchant key, which step found it)."""
        text = _norm(_WRAPPER.sub("", body(description)))
        found = [(m.start(), -len(n), n) for n, p in self.words if (m := p.search(text))]
        if found:
            return min(found)[2], "name"
        flat = _flat(text)
        tokens = [_flat(t) for t in re.split(r"[\s#*]+", text) if _flat(t)]
        for i in range(len(tokens)):  # whole words only: "CHICKFILA", not "EXPRESS" in "EXPRESSWAY"
            for j in range(i + 1, min(i + 4, len(tokens)) + 1):
                if (name := self.by_flat.get("".join(tokens[i:j]))) is not None:
                    return name, "squashed name"
        for i in range(len(tokens)):  # a name of 2+ words glued to the next field ("...WORKSWEB")
            joined = "".join(tokens[i : i + 4])
            for f, name in self.multiword:
                if joined.startswith(f):
                    return name, "squashed name"
        first = _flat(text.split()[0]) if text.split() else ""
        first = re.match(r"[A-Z]*", first).group()  # type: ignore[union-attr]
        if len(first) >= PREFIX_MIN:  # "STOP&SHO", "WINN-DIX", "SAMSCLU": one name begins so
            starts = {f for f, _ in self.flat if f.startswith(first)}
            if len(starts) == 1:
                return self.by_flat[starts.pop()], "truncated name"
        for alias, name in ALIASES.items():
            if flat.startswith(alias):
                return name, "alias"
        words = re.sub(r"[^A-Z ]", " ", text).split()
        return (" ".join(words[:2]) or "?"), "first two words"


# --- Rows, split, sample ---


def load_rows(csv_path: Path, keys: MerchantKeys) -> pd.DataFrame:
    """Every row with its category, my category (None: dropped) and its merchant key."""
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    unknown = {r["category"] for r in rows} - set(MAPPING)
    if unknown:
        raise ValueError(f"categories not in the mapping: {sorted(unknown)}")
    found = [keys.key(r["description"]) for r in rows]
    return pd.DataFrame(
        {
            "description": [r["description"] for r in rows],
            "theirs": [r["category"] for r in rows],
            "mine": [MAPPING[r["category"]] for r in rows],
            "merchant": [k for k, _ in found],
            "how": [how for _, how in found],
        }
    )


def split(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(train, test) of the kept rows: TEST_SHARE of the merchants, drawn with SPLIT_SEED,
    go to test with all their rows. No merchant is in both."""
    kept = rows[rows["mine"].notna()]
    merchants = np.array(sorted(kept["merchant"].unique()))
    order = np.random.default_rng(SPLIT_SEED).permutation(len(merchants))
    test = set(merchants[order[: round(TEST_SHARE * len(merchants))]])
    in_test = kept["merchant"].isin(test)
    return kept[~in_test].reset_index(drop=True), kept[in_test].reset_index(drop=True)


def _take(rows: pd.DataFrame, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """`n` rows at random (all if fewer), from rows sorted by description first, so only the
    data and the generator decide."""
    rows = rows.sort_values(["description", "merchant"]).reset_index(drop=True)
    if len(rows) <= n:
        return rows
    return rows.iloc[np.sort(rng.choice(len(rows), size=n, replace=False))]


def deciding_sample(test: pd.DataFrame) -> pd.DataFrame:
    """At most SAMPLE_PER_MERCHANT rows per test merchant, then SAMPLE_ROWS of those, both
    drawn with SAMPLE_SEED."""
    rng = np.random.default_rng(SAMPLE_SEED)
    capped = [_take(rows, SAMPLE_PER_MERCHANT, rng) for _, rows in sorted(test.groupby("merchant"))]
    return _take(pd.concat(capped, ignore_index=True), SAMPLE_ROWS, rng).reset_index(drop=True)


def features(rows: pd.DataFrame) -> pd.DataFrame:
    """The categorizer's columns. This set has no bank transaction type, amount or channel:
    each gets the same fixed value for every row and every variant."""
    return pd.DataFrame(
        {
            "name": [body(d) for d in rows["description"]],
            "group": list(rows["merchant"]),
            "channel": "none",
            "txn_type": "external",
            "direction": [
                "in" if d.strip().casefold().startswith("[credit]") else "out"
                for d in rows["description"]
            ],  # fmt: skip
            "amount_bucket": "unknown",
        }
    )


def same(category: str | None) -> str | None:
    return SAME_WHEN_SCORING.get(category, category) if category else category


# --- Variants, trained on the train part only ---


def train_variant(variant: str, train: pd.DataFrame) -> object:
    """(a) today's design, (b) with the cleaner, both trained on every train row; settings
    and the review threshold are tuned on the train part by grouped cross-validation."""
    model = v2.make_hybrid(h.variant_options(variant))
    return model.fit(features(train), list(train["mine"]))


def predict(
    variant: str, model: object, sample: pd.DataFrame,
    rag_answer: Callable[[str], str | None] | None = None,
) -> h.VariantResult:  # fmt: skip
    """Predictions for the sample. The directory and RAG look up the cleaned description
    (what (b) and (c) would see), never the merchant key."""
    x = features(sample)
    preds = model.predict_rows(x)  # type: ignore[attr-defined]
    looked_up = [clean_merchant(n).name for n in x["name"]]
    table = pd.DataFrame(
        {
            "merchant": looked_up,
            "category": [p.category for p in preds],
            "source": ["model" if p.route == "new merchant" else "known merchant" for p in preds],
            "detail": [p.route for p in preds],
            "flagged": [p.flagged for p in preds],
        }
    )
    table = v2.apply_directory(table, h.variant_options(variant))
    categories, sources = list(table["category"]), list(table["source"])
    if variant == "+RAG":
        if rag_answer is None:
            raise ValueError("the RAG variant needs an answer function")
        for i, (flagged, source) in enumerate(zip(table["flagged"], sources, strict=True)):
            if flagged and source == "model":
                got = rag_answer(looked_up[i])
                if got is not None:
                    categories[i], sources[i] = got, "RAG"
    return h.VariantResult(variant, categories, sources)


def rag_answer_function(
    train: pd.DataFrame, cache: Path, send: bool, client: object | None = None
) -> Callable[[str], str | None]:
    """RAG with the train part's merchants as the labelled examples (never my labels)."""
    from lookahead import rag_categorizer as rag

    index = rag.LabelledIndex(zip((clean_merchant(body(d)).name for d in train["description"]),
                                  train["mine"], strict=True))  # fmt: skip

    def answer(name: str) -> str | None:
        got, _ = rag.categorize_one(rag.retrieve(name, index), cache, client, send)
        return None if got is None else got.category

    return answer


# --- Scoring ---


def sample_key(sample: pd.DataFrame) -> str:
    return "external:" + hashlib.sha256("\n".join(sample["description"]).encode()).hexdigest()


@dataclass(frozen=True)
class Prepared:
    train: pd.DataFrame
    sample: pd.DataFrame


def prepare(folder: Path = EXTERNAL_DIR) -> Prepared:
    keys = MerchantKeys(generator_names((folder / GENERATOR_FILE).read_text(encoding="utf-8")))
    rows = load_rows(folder / DATASET_FILE, keys)
    train, test = split(rows)
    return Prepared(train, deciding_sample(test))


def score_variants(
    prepared: Prepared, variants: Sequence[str], rag_answer: Callable[[str], str | None] | None,
    record: Callable[[dict[str, object]], None] | None = None,
) -> list[h.Comparison]:  # fmt: skip
    """Run the variants on the deciding sample and the adopt rule's comparisons; prints
    accuracy with n only, never a description."""
    sample = prepared.sample
    truth = [same(c) for c in sample["mine"]]
    blind = pd.DataFrame({"truth": truth, "set": "A", "group": list(sample["merchant"])})
    print(f"deciding sample: {len(sample)} rows from {sample['merchant'].nunique()} test "
          f"merchants; trained on {len(prepared.train)} rows from "
          f"{prepared.train['merchant'].nunique()} train merchants")  # fmt: skip
    results: dict[str, h.VariantResult] = {}
    model_b = None
    for variant in variants:
        if variant == "+RAG" and model_b is not None:
            model = model_b  # (c) is (b) plus RAG: the same trained model
        else:
            model = train_variant(variant, prepared.train)
            model_b = model if variant == "cleaner+directory" else model_b
        got = predict(variant, model, sample, rag_answer)
        got = h.VariantResult(variant, [same(c) for c in got.categories], got.sources)
        results[variant] = got
        right = np.array([c == t for c, t in zip(got.categories, truth, strict=True)])
        by_source = pd.Series(got.sources).value_counts().to_dict()
        print(f"variant {variant}: accuracy {right.mean():6.1%} (n = {len(right)}); by source "
              f"{dict(sorted(by_source.items()))}")  # fmt: skip
        if right.mean() > h.LEAKAGE_AT:
            print(f"  above {h.LEAKAGE_AT:.0%}: check for leakage before reading anything")
        if record is not None:
            record({"run": "external", "variant": variant, "a_n": len(right),
                    "a_correct": int(right.sum())})  # fmt: skip
    comparisons = h.compare(results, blind)
    for c in comparisons:
        print(f"  {c.variant} vs {c.base}: {c.gain * 100:+.1f} points (needs "
              f"{h.MIN_GAIN * 100:+.0f}), bootstrap 2.5th percentile {c.low * 100:+.1f} (needs "
              f"above 0; n = {c.a_n}, merchants resampled); passes on the public set: "
              f"{'yes' if c.passes else 'no'}"
              f"{'' if c.base_passes else f' ({c.base} did not pass)'}")  # fmt: skip
        if record is not None:
            record({"run": "external", "comparison": f"{c.variant} vs {c.base}", "a_n": c.a_n,
                    "gain": c.gain, "bootstrap_low": c.low, "base_passes": c.base_passes,
                    "passes": c.passes})  # fmt: skip
    return comparisons


def score_command(args: argparse.Namespace) -> int:
    ledger = h.DEFAULT_LEDGER
    now = datetime.now().isoformat(timespec="seconds")

    def refuse(reason: str) -> int:
        print(f"refusing to score: {reason}")
        h.add_to_ledger(ledger, {"at": now, "run": "external", "status": "refused",
                                 "reason": reason, "again": args.again})  # fmt: skip
        return 2

    status = h.adopt_rule_status(h.APPROVED_ADOPT_RULE)
    if not status.ok:
        return refuse(f"{status.reason} ({h.DEFAULT_ADOPT_RULE})")
    problems = check_files(args.folder)
    if problems:
        return refuse("; ".join(problems))
    prepared = prepare(args.folder)
    key = sample_key(prepared.sample)
    earlier = [e for e in h.ledger_entries(ledger)
               if e.get("run") == "external" and e.get("status") == "scored"
               and e.get("selection_key") == key]  # fmt: skip
    if earlier and not args.again:
        return refuse("the public sample was already scored: it is scored once. --again "
                      "scores it again and is recorded.")  # fmt: skip
    variants = list(h.VARIANTS[:2])
    rag_answer = None
    if args.rag:
        variants.append("+RAG")
        rag_answer = rag_answer_function(prepared.train, args.rag_cache, args.send)

    def record(entry: dict[str, object]) -> None:
        h.add_to_ledger(ledger, {**entry, "at": now, "status": "scored", "selection_key": key,
                                 "dataset_sha256": DATASET_SHA256,
                                 "generator_sha256": GENERATOR_SHA256,
                                 "adopt_rule_commit": status.commit,
                                 "adopt_rule_sha256": status.sha256, "again": args.again,
                                 "send": bool(args.rag and args.send)})  # fmt: skip

    print(f"Categorizer v2, deciding run on the public set; adopt rule at commit "
          f"{(status.commit or '')[:10]}")  # fmt: skip
    score_variants(prepared, variants, rag_answer, record)
    return 0


def summary_command(args: argparse.Namespace) -> int:
    """Counts only: no description, no accuracy."""
    problems = check_files(args.folder)
    if problems:
        print("; ".join(problems))
        return 2
    keys = MerchantKeys(generator_names((args.folder / GENERATOR_FILE).read_text("utf-8")))
    rows = load_rows(args.folder / DATASET_FILE, keys)
    train, test = split(rows)
    sample = deciding_sample(test)
    print(json.dumps({
        "rows": len(rows), "kept_rows": int(rows["mine"].notna().sum()),
        "merchants_kept": int(rows.loc[rows["mine"].notna(), "merchant"].nunique()),
        "how_found": rows["how"].value_counts().to_dict(),
        "train": [len(train), int(train["merchant"].nunique())],
        "test": [len(test), int(test["merchant"].nunique())],
        "sample": [len(sample), int(sample["merchant"].nunique())],
        "sample_per_category": sample["mine"].value_counts().to_dict(),
        "cleaned_names_in_train_and_sample": len(
            {clean_merchant(body(d)).key for d in train["description"]}
            & {clean_merchant(body(d)).key for d in sample["description"]}
        ),
    }, indent=1, ensure_ascii=False))  # fmt: skip
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.categorizer_v2_external")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "summary", "score"):
        c = commands.add_parser(name)
        c.add_argument("--folder", type=Path, default=EXTERNAL_DIR)
        if name == "score":
            c.add_argument("--rag", action="store_true", help="add variant (c)")
            c.add_argument("--send", action="store_true", help="with --rag: ask Haiku")
            c.add_argument("--rag-cache", type=Path, default=DEFAULT_RAG_CACHE)
            c.add_argument("--again", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        return fetch(args.folder)
    if args.command == "summary":
        return summary_command(args)
    return score_command(args)


if __name__ == "__main__":
    sys.exit(main())
