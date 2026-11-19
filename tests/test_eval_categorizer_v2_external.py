"""The public deciding run (DoDataThings v2): pinned files, merchant keys, mapping, split,
sample, and that nothing of mine or of the merchant key reaches the variants. Tiny made-up
files only; no network."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eval import categorizer_v2 as h
from eval import categorizer_v2_external as x
from lookahead.categorizer import Prediction
from lookahead.taxonomy import CATEGORIES

GENERATOR = """
const US_CITIES = [["SEATTLE", "WA", "98101"], ["AUSTIN", "TX", "73301"]];
const FIRST_NAMES = ["JOHN", "MARIA"];
const merchants = {
  restaurants: {
    coffee: ["STARBUCKS", "PEET'S COFFEE"],
    fastFood: ["CHICK-FIL-A", "TACO BELL"],
  },
  groceries: {
    chains: ["TRADER JOE'S", "WHOLE FOODS", "SAFEWAY"],
  },
  shopping: { online: ["AMAZON", "TARGET", "EXPRESS"] },
  gas: ["BP", "76"],
};
const EMPLOYER_NAMES = ["ACME CORP"];
"""


@pytest.fixture
def keys() -> x.MerchantKeys:
    return x.MerchantKeys(x.generator_names(GENERATOR))


@pytest.fixture(autouse=True)
def _safe_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "DEFAULT_LEDGER", tmp_path / "data" / "private" / "ledger.jsonl")
    monkeypatch.setattr(h, "APPROVED_ADOPT_RULE", tmp_path / "repo" / "rule.md")


def test_generator_names_leave_out_cities_and_people() -> None:
    names = x.generator_names(GENERATOR)
    assert {"STARBUCKS", "TRADER JOE'S", "ACME CORP"} <= set(names)
    assert not {"SEATTLE", "JOHN", "WA"} & set(names)


@pytest.mark.parametrize(
    ("description", "key", "how"),
    [
        ("[debit] STARBUCKS #1234 SEATTLE", "STARBUCKS", "name"),
        ("[debit] Peet's Coffee - Austin", "PEET'S COFFEE", "name"),
        ("[debit] CHICKFILA 12345", "CHICK-FIL-A", "squashed name"),
        ("[debit] TRADERJOE LA #123", "TRADER JOE'S", "truncated name"),
        ("[debit] WHOLE-FOO #12 AUSTIN", "WHOLE FOODS", "truncated name"),
        ("[debit] WHOLEFDS #10467", "WHOLE FOODS", "alias"),
        ("[debit] AMZN MKTP US*AB12CD34", "AMAZON", "alias"),
        ("[debit] PAYPAL INST XFER STARBUCKS WEB ID: X", "STARBUCKS", "name"),  # wrapper skipped
        ("[debit] SQ *FRUIT STAND #12", "FRUIT STAND", "first two words"),
        ("[debit] BP*GZA9NLYVA2N5 BP GZA", "BP", "name"),  # a 2-letter chain is one merchant
        ("[debit] PAYPAL INST XFER TRADER JOE'SWEB ID: P", "TRADER JOE'S", "squashed name"),
        ("[debit] DR HARRIS PEDIATRICS", "DR HARRIS", "first two words"),
    ],
)
def test_merchant_keys(keys: x.MerchantKeys, description: str, key: str, how: str) -> None:
    assert keys.key(description) == (key, how)


def test_a_short_name_inside_a_word_is_not_a_merchant(keys: x.MerchantKeys) -> None:
    # "EXPRESS" is a generator store; inside "EXPRESSWAY TOLL" it is not a whole word
    assert keys.key("[debit] EXPRESSWAY TOLL 123")[0] != "EXPRESS"
    # the earliest name in the text wins over a longer one later
    assert keys.key("[debit] TARGET TRADER JOE'S")[0] == "TARGET"


def test_the_mapping_covers_their_17_categories_with_mine() -> None:
    assert len(x.MAPPING) == 17
    kept = {v for v in x.MAPPING.values() if v is not None}
    assert kept <= set(CATEGORIES)
    dropped = {k for k, v in x.MAPPING.items() if v is None}
    assert dropped == {"Transfer", "Income", "Fees", "Insurance", "Travel"}
    assert x.same("Food delivery") == "Restaurants & cafés" and x.same("Groceries") == "Groceries"


def _rows(merchants: int, each: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "description": [
                f"[debit] SHOP {m} #{r}" for m in range(merchants) for r in range(each)
            ],
            "theirs": "Shopping",
            "mine": "Shopping",
            "merchant": [f"SHOP {m}" for m in range(merchants) for _ in range(each)],
            "how": "name",
        }
    )


def test_the_split_is_by_merchant_seeded_and_drops_unmapped_rows() -> None:
    rows = _rows(50, 4)
    rows.loc[0, "mine"] = None  # an Insurance row, say
    train, test = x.split(rows)
    assert not set(train["merchant"]) & set(test["merchant"])
    assert test["merchant"].nunique() == round(x.TEST_SHARE * 50)
    assert len(train) + len(test) == len(rows) - 1
    again_train, _ = x.split(rows.sample(frac=1, random_state=4))
    assert set(again_train["merchant"]) == set(train["merchant"])


def test_the_sample_caps_each_merchant_and_is_seeded() -> None:
    test = _rows(300, 5)
    sample = x.deciding_sample(test)
    assert len(sample) == x.SAMPLE_ROWS == 600
    assert sample.groupby("merchant").size().max() <= x.SAMPLE_PER_MERCHANT == 3
    again = x.deciding_sample(test.sample(frac=1, random_state=2))
    assert list(again["description"]) == list(sample["description"])


def test_features_carry_the_text_not_the_merchant_key() -> None:
    rows = pd.DataFrame({"description": ["[credit] Refund Starbucks", "[debit] TACO BELL 1"],
                         "merchant": ["KEY ONE", "KEY TWO"]})  # fmt: skip
    f = x.features(rows)
    assert list(f["name"]) == ["Refund Starbucks", "TACO BELL 1"]
    assert list(f["direction"]) == ["in", "out"]
    assert set(f["txn_type"]) == {"external"} and set(f["amount_bucket"]) == {"unknown"}


class _Flagged:
    """A model that sends every row to the new-merchant route, flagged, as Shopping."""

    def predict_rows(self, rows: pd.DataFrame) -> list[Prediction]:
        return [Prediction("Shopping", "new merchant", True, 0.1) for _ in range(len(rows))]


def test_the_directory_reads_the_description_not_the_key() -> None:
    sample = pd.DataFrame({"description": ["[debit] STARBUCKS", "[debit] Golden Dragon"],
                           "merchant": ["SOME KEY", "STARBUCKS"]})  # fmt: skip
    a = x.predict("current", _Flagged(), sample)
    b = x.predict("cleaner+directory", _Flagged(), sample)
    assert a.categories == ["Shopping", "Shopping"]  # (a): no directory
    assert b.categories == ["Restaurants & cafés", "Shopping"]  # by text, not by key
    assert b.sources[0] == v2_directory_source()


def v2_directory_source() -> str:
    from lookahead.categorizer_v2 import DIRECTORY_SOURCE

    return DIRECTORY_SOURCE


def test_rag_asks_with_the_cleaned_description_and_train_merchants_only() -> None:
    asked: list[str] = []
    sample = pd.DataFrame({"description": ["[debit] Golden Dragon #12"], "merchant": ["K"]})
    c = x.predict("+RAG", _Flagged(), sample, lambda name: asked.append(name) or "Groceries")
    assert c.categories == ["Groceries"] and c.sources == ["RAG"]
    assert asked == ["Golden Dragon"]


def test_check_files_wants_both_pinned_hashes(tmp_path: Path) -> None:
    assert len(x.check_files(tmp_path)) == 2  # both missing
    (tmp_path / x.DATASET_FILE).write_text("description,category\n", encoding="utf-8")
    problems = x.check_files(tmp_path)
    assert any("does not match" in p for p in problems) and any("missing" in p for p in problems)


def test_scoring_waits_for_the_committed_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert x.main(["score", "--folder", str(tmp_path)]) == 2
    assert "refusing to score" in capsys.readouterr().out
    [entry] = h.ledger_entries(h.DEFAULT_LEDGER)
    assert entry["run"] == "external" and entry["status"] == "refused"


def _tiny_set(folder: Path) -> None:
    """A made-up public set: 40 merchants of two categories, 6 rows each."""
    lines = ["description,category"]
    for m in range(40):
        category = "Groceries" if m % 2 else "Restaurants"
        word = "MARKET" if m % 2 else "GRILL"
        name = chr(65 + m // 26) + chr(65 + m % 26)  # letters: keys drop digits
        lines += [f'"[debit] {word} {name}LAND #{r}",{category}' for r in range(6)]
    (folder / x.DATASET_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / x.GENERATOR_FILE).write_text(GENERATOR, encoding="utf-8")


@pytest.mark.slow
def test_the_public_run_scores_once_and_logs_every_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "public"
    folder.mkdir()
    _tiny_set(folder)
    for name, path in (("DATASET_SHA256", x.DATASET_FILE), ("GENERATOR_SHA256", x.GENERATOR_FILE)):
        monkeypatch.setattr(x, name, hashlib.sha256((folder / path).read_bytes()).hexdigest())
    monkeypatch.setattr(
        h, "adopt_rule_status", lambda _: h.RuleStatus(True, "committed", "abc", "def")
    )
    monkeypatch.setattr(x, "SAMPLE_ROWS", 30)
    args = ["score", "--folder", str(folder)]
    assert x.main(args) == 0
    out = capsys.readouterr().out
    assert "(n = 30" in out and "passes on the public set" in out
    for word in ("MARKET AA", "GRILL AB"):
        assert word not in out  # no description is printed
    scored = [e for e in h.ledger_entries(h.DEFAULT_LEDGER) if e.get("status") == "scored"]
    assert [e.get("variant") or e.get("comparison") for e in scored] == [
        "current", "cleaner+directory", "cleaner+directory vs current"]  # fmt: skip
    assert h.external_verdicts(scored, "def").keys() == {"cleaner+directory"}
    assert x.main(args) == 2 and "scored once" in capsys.readouterr().out
    assert x.main([*args, "--again"]) == 0


def test_the_pins_are_the_values_in_the_rule() -> None:
    rule = (x.ROOT / h.DEFAULT_ADOPT_RULE).read_text(encoding="utf-8")
    for value in (x.DATASET_REVISION, x.DATASET_SHA256, x.GENERATOR_COMMIT, x.GENERATOR_SHA256):
        assert value in rule
    assert (x.TEST_SHARE, x.SPLIT_SEED, x.SAMPLE_ROWS, x.SAMPLE_PER_MERCHANT) == (0.4, 0, 600, 3)
    assert json.dumps(x.MAPPING, sort_keys=True)  # serializable: written in the rule's table
    assert np.isclose(h.MIN_GAIN, 0.03)


def test_an_unknown_category_stops_the_run(tmp_path: Path, keys: x.MerchantKeys) -> None:
    path = tmp_path / "set.csv"
    path.write_text('description,category\n"[debit] TARGET",Pets\n', encoding="utf-8")
    with pytest.raises(ValueError, match="not in the mapping"):
        x.load_rows(path, keys)


def test_fetch_deletes_a_file_with_the_wrong_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b"not the pinned file"

    monkeypatch.setattr(x.urllib.request, "urlopen", lambda *_, **__: Response())
    assert x.fetch(tmp_path) == 2
    assert "wrong sha256, deleted" in capsys.readouterr().out
    assert not (tmp_path / x.DATASET_FILE).exists()


def test_summary_prints_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tiny_set(tmp_path)
    for name, path in (("DATASET_SHA256", x.DATASET_FILE), ("GENERATOR_SHA256", x.GENERATOR_FILE)):
        monkeypatch.setattr(x, name, hashlib.sha256((tmp_path / path).read_bytes()).hexdigest())
    monkeypatch.setattr(x, "SAMPLE_ROWS", 30)
    assert x.main(["summary", "--folder", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    got = json.loads(out)
    assert got["rows"] == 240 and got["sample"][0] == 30
    assert got["cleaned_names_in_train_and_sample"] == 0
    assert "MARKET AA" not in out  # no description
