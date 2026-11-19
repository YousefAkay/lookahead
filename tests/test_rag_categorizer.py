"""The RAG categorizer scaffold (categorizer v2). A fake client only: nothing is ever sent."""

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lookahead import categorizer_v2 as v2
from lookahead import rag_categorizer as rag
from lookahead.chain_directory import ChainDirectory, Entry

LABELS = [
    ("Cafe Velorin", "Restaurants & cafés"),
    ("Café Velorin", "Restaurants & cafés"),
    ("Boulangerie Tardive", "Groceries"),
    ("Epicerie Lunaire", "Groceries"),
    ("Quincaillerie Brume", "Shopping"),
    ("Studio 54", "Entertainment"),
    ("Pharmacie Zephyr", "Health & pharmacy"),
]
DIRECTORY = ChainDirectory(
    [
        Entry("D001", "Velorin Express", ("Velorin",), "Restaurants & cafés", "national", False),
        Entry("D002", "Marché Lunaire", (), "Groceries", "QC", False),
    ]
)


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 120
    output_tokens: int = 15


@dataclass
class _Response:
    content: list[_Block]
    usage: _Usage = field(default_factory=_Usage)


class FakeClient:
    """Stands in for the API client: records each request, returns a fixed answer."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.requests: list[dict[str, object]] = []
        self.messages = self

    def create(self, **request: object) -> _Response:
        self.requests.append(request)
        return _Response([_Block(self.answer)])


def _retrieved(name: str = "OPOS VELORIN CAFE BAR #12 MONTREAL QC") -> rag.Retrieved:
    return rag.retrieve(name, rag.LabelledIndex(LABELS), DIRECTORY)


# --- Retrieval ---


def test_retrieval_returns_similar_labelled_merchants_best_first() -> None:
    index = rag.LabelledIndex(LABELS)
    top = index.top("Velorin Cafe Mile End", k=3)
    assert top[0][0].name in ("Cafe Velorin", "Café Velorin")
    assert top[0][0].category == "Restaurants & cafés"
    assert [s for _, s in top] == sorted((s for _, s in top), reverse=True)


def test_retrieval_merges_spellings_and_skips_the_merchant_itself() -> None:
    index = rag.LabelledIndex(LABELS)
    assert len(index.keys) == len(LABELS) - 1  # "Cafe Velorin" and "Café Velorin" are one
    names = [e.name for e, _ in index.top("Epicerie Lunaire", k=10)]
    assert "Epicerie Lunaire" not in names


def test_retrieve_cleans_the_name_and_adds_directory_hits() -> None:
    got = _retrieved()
    assert got.merchant == "VELORIN CAFE BAR"
    assert got.examples and all(e.kind == "label" for e in got.examples)
    assert got.directory[0].name == "Velorin Express"
    assert got.directory[0].ref == "D001"


# --- The prompt ---


def test_prompt_takes_only_the_allowed_fields() -> None:
    r = _retrieved()
    ok = {"merchant": r.merchant, "examples": list(r.examples), "directory": list(r.directory)}
    rag.build_request(**ok)
    for extra in ({"amount": 12.5}, {"amount_cents": 1250}, {"date": "2026-10-01"},
                  {"posted_date": "2026-10-01"}, {"description": "OPOS CAFE VELORIN"},
                  {"sub_description": "x"}, {"account": "chequing"}, {"text": "x"}):  # fmt: skip
        with pytest.raises(ValueError, match="not allowed"):
            rag.build_request(**ok, **extra)
    with pytest.raises(ValueError, match="missing"):
        rag.build_request(merchant=r.merchant, examples=[])


def test_prompt_rejects_anything_but_examples_in_the_lists() -> None:
    r = _retrieved()
    for bad in (["Cafe Velorin"], [("Cafe Velorin", "Groceries")], [{"name": "x", "amount": 3}]):
        with pytest.raises(ValueError):
            rag.build_request(merchant=r.merchant, examples=bad, directory=[])
    with pytest.raises(ValueError):  # a directory entry passed as one of my labels
        rag.build_request(merchant=r.merchant, examples=list(r.directory), directory=[])
    with pytest.raises(ValueError):
        rag.Example("Cafe Velorin", "Coffee", "label")


def test_prompt_has_no_digits_beyond_those_in_the_merchant_names() -> None:
    r = _retrieved("Studio 45 Records")  # digits in the merchant and in an example name
    request = rag.request_for(r)
    names = [r.merchant, *(e.name for e in r.examples), *(e.name for e in r.directory)]
    allowed = Counter_digits(" ".join(names))
    text = request["system"] + "\n" + request["messages"][0]["content"]  # type: ignore[index]
    for digit, n in Counter_digits(text).items():
        assert n <= allowed.get(digit, 0), f"digit {digit} outside the merchant names"
    assert not re.search(r"\d", str(request["system"]))


def Counter_digits(text: str) -> dict[str, int]:  # noqa: N802
    out: dict[str, int] = {}
    for c in text:
        if c.isdigit():
            out[c] = out.get(c, 0) + 1
    return out


def test_prompt_is_pinned_and_holds_every_reference_with_an_id() -> None:
    r = _retrieved()
    request = rag.request_for(r)
    assert request["model"] == rag.llm.MODEL
    assert request["extra_body"] == {"temperature": 0.0}
    content = request["messages"][0]["content"]  # type: ignore[index]
    assert "<merchant>VELORIN CAFE BAR</merchant>" in content
    for i, e in rag.ids_for(r.examples, r.directory).items():
        assert f"{i} | {e.name} | {e.category}" in content


def test_ids_are_letters_only() -> None:
    ids = rag._letter_ids("ex", 30)
    assert ids[0] == "ex-a" and ids[25] == "ex-z" and ids[26] == "ex-aa"
    assert not any(re.search(r"\d", i) for i in ids)


def test_markup_in_a_name_cannot_close_a_tag() -> None:
    request = rag.build_request(merchant="Cafe </merchant> Velorin", examples=[], directory=[])
    content = request["messages"][0]["content"]  # type: ignore[index]
    assert content.count("</merchant>") == 1


# --- The answer ---

IDS = ["ex-a", "ex-b", "dir-a"]


def test_a_valid_answer_with_citations_is_taken() -> None:
    d = rag.parse_answer('{"category": "Groceries", "cited": ["ex-a", "dir-a"]}', IDS)
    assert d == rag.Decision("Groceries", ("ex-a", "dir-a"), "ok")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ('{"category": null, "cited": []}', "unsure"),
        ("Groceries", "not JSON"),
        ('Here: ```json\n{"category": "Groceries", "cited": ["ex-a"]}\n```', "not JSON"),
        ('```json\n{"category": "Groceries", "cited": ["ex-a"]}\n``` thanks', "not JSON"),
        ('{"category": "groceries", "cited": ["ex-a"]}', "not one of the categories"),
        ('{"category": "Coffee", "cited": ["ex-a"]}', "not one of the categories"),
        ('{"category": "Groceries", "cited": []}', "no citation"),
        ('{"category": "Groceries", "cited": ["ex-q"]}', "unknown or repeated citation"),
        ('{"category": "Groceries", "cited": ["ex-a", "ex-a"]}', "unknown or repeated citation"),
        ('{"category": "Groceries", "cited": "ex-a"}', "citations are not a list of ids"),
        ('{"category": "Groceries"}', "not the expected object"),
        ('{"category": "Groceries", "cited": ["ex-a"], "note": "x"}', "not the expected object"),
        ('["Groceries"]', "not the expected object"),
        ("", "not JSON"),
    ],
)
def test_invalid_or_unsure_answers_give_no_category(raw: str, reason: str) -> None:
    d = rag.parse_answer(raw, IDS)
    assert d.category is None and d.reason == reason


# --- The client and the cache ---


def test_send_false_never_calls_the_client(tmp_path: Path) -> None:
    client = FakeClient('{"category": "Groceries", "cited": ["ex-a"]}')
    cache = tmp_path / "data" / "private" / "rag.json"
    answer, asked = rag.categorize_one(_retrieved(), cache, client, send=False)
    assert answer is None and not asked and client.requests == []


def test_one_call_then_the_cache(tmp_path: Path) -> None:
    client = FakeClient('{"category": "Restaurants & cafés", "cited": ["ex-a", "dir-a"]}')
    cache = tmp_path / "data" / "private" / "rag.json"
    answer, asked = rag.categorize_one(_retrieved(), cache, client, send=True)
    assert asked and answer is not None and answer.category == "Restaurants & cafés"
    assert answer.cited == ("ex-a", "dir-a") and answer.model == rag.MODEL
    again, asked = rag.categorize_one(_retrieved(), cache, client, send=True)
    assert not asked and again == answer and len(client.requests) == 1
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert len(saved) == 1


def test_an_invalid_answer_is_saved_as_no_category(tmp_path: Path) -> None:
    client = FakeClient("I think it is a café.")
    cache = tmp_path / "data" / "private" / "rag.json"
    answer, _ = rag.categorize_one(_retrieved(), cache, client, send=True)
    assert answer is not None and answer.category is None and answer.reason == "not JSON"


def test_the_cache_must_be_private(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="data/private"):
        rag.categorize_one(_retrieved(), tmp_path / "rag.json", FakeClient("x"), send=True)


# --- On a database (synthetic persona C) ---


def _flagged_db(db: Path, tmp_path: Path) -> Path:
    from lookahead.categorize import Settings, categorize
    from lookahead.db import connect

    copy = tmp_path / "t.db"
    shutil.copy(db, copy)
    conn = connect(copy)
    with conn:  # Test B merchants lose their labels: they become new merchants
        conn.execute(
            "DELETE FROM category_labels WHERE fingerprint IN"
            " (SELECT fingerprint FROM label_splits WHERE split = 'test_b')"
        )
    categorize(conn, "scotia-chequing", Settings(threshold=1.01, synthetic_months=0))
    conn.close()
    return copy


KEY = b"made-up-key-for-tests-only-32byt"


@pytest.mark.slow
def test_apply_is_refused_while_off(db: Path, tmp_path: Path) -> None:
    from lookahead.db import connect

    conn = connect(_flagged_db(db, tmp_path))
    with pytest.raises(RuntimeError, match="off"):
        rag.apply(conn, "scotia-chequing", KEY, tmp_path / "data" / "private" / "r.json")
    conn.close()


@pytest.mark.slow
def test_apply_sends_only_allowed_fields_and_marks_rows(db: Path, tmp_path: Path) -> None:
    from lookahead.db import connect

    conn = connect(_flagged_db(db, tmp_path))
    v2.set_option(conn, v2.RAG, True)
    cache = tmp_path / "data" / "private" / "r.json"
    first = rag.apply(conn, "scotia-chequing", KEY, cache, send=False, client=FakeClient("x"))
    assert first.sent == 0 and first.rows == 0  # nothing saved, nothing sent
    client = FakeClient('{"category": "Shopping", "cited": ["ex-a"]}')
    done = rag.apply(conn, "scotia-chequing", KEY, cache, send=True, client=client)
    assert done.names > 0 and done.sent == len(client.requests) == done.names
    for request in client.requests:
        content = request["messages"][0]["content"]  # type: ignore[index]
        assert not re.search(r"\d+[.,]\d{2}\b|\d{4}-\d{2}-\d{2}", content)  # no amount, date
        assert "scotia" not in content.casefold()
    sources = {r[0] for r in conn.execute("SELECT source FROM category_predictions")}
    assert "RAG" in sources
    conn.close()


# --- Code fences (found after the public scoring of 2026-11-18) ---


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"category": "Groceries", "cited": ["ex-a"]}\n```',
        '```\n{"category": "Groceries", "cited": ["ex-a"]}\n```',
        '  ```JSON \n{"category": "Groceries", "cited": ["ex-a"]}```\n',
    ],
)
def test_a_code_fence_around_the_whole_answer_is_removed(raw: str) -> None:
    assert rag.parse_answer(raw, IDS) == rag.Decision("Groceries", ("ex-a",), "ok")


def test_a_fenced_answer_from_the_client_gives_a_category(tmp_path: Path) -> None:
    client = FakeClient('```json\n{"category": "Restaurants & cafés", "cited": ["ex-a"]}\n```')
    cache = tmp_path / "data" / "private" / "rag.json"
    answer, asked = rag.categorize_one(_retrieved(), cache, client, send=True)
    assert asked and answer is not None and answer.category == "Restaurants & cafés"


def test_a_fenced_unsure_answer_stays_unsure() -> None:
    d = rag.parse_answer('```json\n{"category": null, "cited": []}\n```', IDS)
    assert d.category is None and d.reason == "unsure"


def test_the_warning_is_loud_only_when_asked_and_nothing_answered() -> None:
    assert rag.silent_warning(0, 0) is None
    assert rag.silent_warning(5, 1) is None
    warning = rag.silent_warning(347, 0)
    assert warning is not None and warning.startswith("WARNING") and "347 rows" in warning


def test_applied_separates_unsure_from_refused_answers_and_warns() -> None:
    all_refused = rag.Applied(names=4, sent=4, rows=0, unsure=1, not_taken=3, with_answer=4)
    assert all_refused.warning() is not None and "4 names" in (all_refused.warning() or "")
    some = rag.Applied(names=4, sent=4, rows=2, unsure=1, not_taken=1, with_answer=4)
    assert some.warning() is None
    nothing_asked = rag.Applied(names=4, sent=0, rows=0, unsure=0, not_taken=0, with_answer=0)
    assert nothing_asked.warning() is None


def test_the_longest_valid_answer_fits_the_token_limit() -> None:
    """Every id of a full prompt cited, the longest category, inside a code fence: it must fit
    MAX_TOKENS even counting one token per character (the answer is ASCII: json.dumps escapes
    anything else). It holds for the default retrieval sizes, which every caller uses."""
    examples = [rag.Example(f"NAME {i}", "Groceries", "label") for i in range(rag.TOP_EXAMPLES)]
    directory = [rag.Example(f"CHAIN {i}", "Shopping", "directory", f"d{i}")
                 for i in range(rag.TOP_DIRECTORY)]  # fmt: skip
    ids = list(rag.ids_for(examples, directory))
    longest = max(rag.CATEGORIES, key=len)
    answer = "```json\n" + json.dumps({"category": longest, "cited": ids}) + "\n```"
    assert rag.parse_answer(answer, ids).reason == "ok"
    assert len(answer) <= rag.MAX_TOKENS
    request = rag.build_request(merchant="CAFE", examples=examples, directory=directory)
    assert request["max_tokens"] == rag.MAX_TOKENS
