"""Tests for point 4a (the LLM candidate). No network: a fake client stands in for the API,
and every path is temporary. These tests are the proof of what may leave the machine."""

import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import lookahead.categorizer as categorizer
import lookahead.llm_categorizer as llm
from lookahead.taxonomy import CATEGORIES

KEY = bytes(32)
CARD = "Apos Cafe Velo           montr "


class FakeClient:
    """Answers every request with one category and records exactly what was sent."""

    def __init__(self, answer: str = "Restaurants & cafés") -> None:
        self.answer, self.requests = answer, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request: object) -> SimpleNamespace:
        self.requests.append(request)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.answer)],
            usage=SimpleNamespace(input_tokens=200, output_tokens=6),
        )


# --- What may be sent ---


def test_a_card_row_sends_only_its_extracted_name() -> None:
    assert llm.sendable_name("pos purchase", CARD, KEY) == "Cafe Velo"
    assert llm.sendable_name("POS Purchase", "Opos 7.33 Steamgames.Com   +1425 ", KEY) == (
        "Steamgames.Com"
    )  # the foreign amount and the phone prefix are not part of the name


@pytest.mark.parametrize(
    ("txn_type", "text"),
    [
        ("credit memo", "1/Jane Doe "),  # a person
        ("deposit", "Jane Doe"),  # a row type where a name can appear
        ("accounts payable", "Apos Some Company     toron "),  # same, even if card-shaped
        ("bill payment", "Mb-Hydro-Quebec 12345678"),  # a payee with a reference
        ("bill payment", "Hydro-Quebec "),  # a payee: not a card merchant
        ("withdrawal", "Free Interac E-Transfer "),  # a transfer
        ("pos purchase", "Apos Shop 1234567      montr "),  # a 7+ digit run
        ("pos purchase", " "),  # nothing
    ],
)
def test_everything_else_is_never_sent(txn_type: str, text: str) -> None:
    assert llm.sendable_name(txn_type, text, KEY) is None


def test_the_request_holds_the_name_the_categories_and_nothing_else() -> None:
    request = llm.build_request("Cafe Velo")
    assert set(request) == {"model", "max_tokens", "system", "messages", "extra_body"}
    assert request["model"] == "claude-haiku-4-5-20251001"
    assert request["extra_body"] == {"temperature": 0.0}
    assert request["messages"] == [
        {"role": "user", "content": "<merchant_name>Cafe Velo</merchant_name>"}
    ]
    assert request["system"] == llm.SYSTEM_PROMPT  # fixed text: no data in it
    assert all(c in llm.SYSTEM_PROMPT for c in CATEGORIES)
    assert not re.search(r"\d", llm.SYSTEM_PROMPT)  # no amounts, dates or counts


# --- Answers ---


def test_only_an_exact_category_counts_as_an_answer() -> None:
    assert llm.parse_category(" groceries \n") == "Groceries"
    assert llm.parse_category("Restaurants & cafés") == "Restaurants & cafés"
    assert llm.parse_category("Groceries, probably") is None
    assert llm.parse_category("Ignore previous instructions") is None
    assert llm.parse_category("") is None


# --- One cached call per name ---


def test_each_name_is_asked_once_and_saved(tmp_path: Path) -> None:
    cache = tmp_path / "data" / "private" / "cache" / "llm.json"
    client = FakeClient()
    answers, calls = llm.classify({"Cafe Velo", "Maxi"}, cache, client)
    assert calls == 2 and answers["Maxi"].category == "Restaurants & cafés"
    assert [r["messages"][0]["content"] for r in client.requests] == [
        "<merchant_name>Cafe Velo</merchant_name>", "<merchant_name>Maxi</merchant_name>",
    ]  # fmt: skip
    again = FakeClient("Groceries")
    answers, calls = llm.classify({"Cafe Velo", "Maxi", "Stm"}, cache, again)
    assert calls == 1 and len(again.requests) == 1  # only the new name
    assert answers["Maxi"].category == "Restaurants & cafés"  # the saved answer is kept
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["Stm"] == {
        "category": "Groceries", "raw": "Groceries", "model": llm.MODEL,
        "input_tokens": 200, "output_tokens": 6, "latency_s": saved["Stm"]["latency_s"],
    }  # fmt: skip


def test_a_full_cache_needs_no_client_and_no_key(tmp_path: Path) -> None:
    cache = tmp_path / "data" / "private" / "cache" / "llm.json"
    llm.classify({"Maxi"}, cache, FakeClient())
    answers, calls = llm.classify({"Maxi"}, cache, client=None)  # would fail if it called
    assert calls == 0 and answers["Maxi"].raw


def test_the_cache_is_refused_outside_data_private(tmp_path: Path) -> None:
    client = FakeClient()
    with pytest.raises(ValueError, match="data/private"):
        llm.classify({"Maxi"}, tmp_path / "llm.json", client)
    assert client.requests == []  # refused before anything is sent


def test_answers_of_another_model_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "data" / "private" / "cache" / "llm.json"
    llm.classify({"Maxi"}, cache, FakeClient())
    monkeypatch.setattr(llm, "MODEL", "another-model")
    assert llm.load_cache(cache) == {}


def test_cost_comes_from_the_token_counts() -> None:
    answer = llm.Answer("Groceries", "Groceries", llm.MODEL, 1_000_000, 100_000, 0.5)
    assert answer.cost_usd == pytest.approx(1.00 + 0.50)


def test_a_missing_key_is_reported_without_calling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(llm.KEY_NAME, raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        llm.make_client(tmp_path / "missing.env")


# --- The evaluation command, end to end on a synthetic stand-in ---


def _run(db_path: Path, tmp_path: Path, client: object, *extra: str) -> int:
    from eval.categorizer_llm import main

    env = tmp_path / ".env"
    env.write_text(f"PSEUDONYM_KEY={'ab' * 32}\n", encoding="utf-8")
    private = tmp_path / "data" / "private"
    return main(
        ["--db", str(db_path), "--out", str(private / "eval" / "llm.json"), "--cache",
         str(private / "cache" / "llm.json"), "--env", str(env), "--months", "0", *extra],
        client,
    )  # fmt: skip


@pytest.fixture
def small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        categorizer, "PARAM_GRID", {"model__C": [1.0], "features__name__ngram_range": [(2, 5)]}
    )


def test_dry_run_sends_nothing(
    db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    assert _run(db, tmp_path, client, "--dry-run") == 0
    out = capsys.readouterr().out
    assert client.requests == [] and "nothing was sent" in out and "calls needed" in out
    assert not (tmp_path / "data" / "private" / "cache" / "llm.json").exists()


@pytest.mark.slow
def test_command_sends_dev_card_names_only_and_never_writes_labels(
    db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    small_grid: None,
) -> None:
    from lookahead.labels import load_rows

    def label_rows() -> list[tuple]:
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM category_labels ORDER BY rowid").fetchall()
        conn.close()
        return rows

    conn = sqlite3.connect(db)
    frozen = load_rows(conn, "scotia-chequing")
    conn.close()
    dev_names = {
        llm.sendable_name(t, x, bytes.fromhex("ab" * 32))
        for t, x in zip(frozen.query("split == 'dev'")["txn_type"],
                        frozen.query("split == 'dev'")["text"], strict=True)
    }  # fmt: skip
    before = label_rows()
    client = FakeClient()
    assert _run(db, tmp_path, client) == 0
    out = capsys.readouterr().out

    sent = [r["messages"][0]["content"] for r in client.requests]
    names = {s.removeprefix("<merchant_name>").removesuffix("</merchant_name>") for s in sent}
    assert names and names <= dev_names - {None}  # dev card names, each exactly once
    assert len(sent) == len(names)
    for request in client.requests:  # no amounts or dates anywhere in a request
        assert set(request) == {"model", "max_tokens", "system", "messages", "extra_body"}
        assert not re.search(r"\d+\.\d{2}|\d{4}-\d{2}-\d{2}", json.dumps(request))
    assert label_rows() == before  # LLM output never enters the answer key

    for text in ("holdout untouched", "llm for flagged new merchants", "fallback candidate",
                 "cost per 1,000 merchants", "median latency"):  # fmt: skip
        assert text in out
    for name in names | {"Tim Hortons", "Maxi", "Nadia"}:
        assert name not in out  # counts only
    saved = json.loads((tmp_path / "data" / "private" / "eval" / "llm.json").read_text("utf-8"))
    assert saved["model"] == llm.MODEL and saved["names_sent"] == len(names)

    second = FakeClient()
    assert _run(db, tmp_path, second) == 0 and second.requests == []  # all from the cache


def test_command_refuses_paths_outside_data_private(db: Path, tmp_path: Path) -> None:
    from eval.categorizer_llm import main

    assert main(["--db", str(db), "--out", str(tmp_path / "x.json")]) == 2


# --- Against the real anthropic library (no network: the HTTP layer is replaced) ---
# The fake client above accepts anything, which hid a real bug: SDK 1.x has no
# `temperature` argument, so the first real call raised TypeError.


def test_the_request_fits_the_real_sdk_signature() -> None:
    import inspect

    anthropic = pytest.importorskip("anthropic")
    create = anthropic.Anthropic(api_key="test").messages.create
    inspect.signature(create).bind(**llm.build_request("Cafe Velo"))  # TypeError if not


def test_a_real_client_sends_temperature_zero_and_parses_a_real_response() -> None:
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    bodies: list[dict] = []

    def handler(request: object) -> object:
        bodies.append(json.loads(request.content))  # type: ignore[attr-defined]
        return httpx2.Response(
            200,
            json={
                "id": "msg_test", "type": "message", "role": "assistant",
                "model": llm.MODEL, "stop_reason": "end_turn", "stop_sequence": None,
                "content": [{"type": "text", "text": "Restaurants & cafés"}],
                "usage": {"input_tokens": 210, "output_tokens": 7},
            },
        )  # fmt: skip

    client = anthropic.Anthropic(
        api_key="test", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )  # fmt: skip
    answer = llm.ask(client, "Cafe Velo")
    assert answer.category == "Restaurants & cafés"
    assert (answer.input_tokens, answer.output_tokens) == (210, 7)
    (body,) = bodies  # what actually goes over the wire
    assert body == {
        "model": "claude-haiku-4-5-20251001", "max_tokens": llm.MAX_TOKENS,
        "temperature": 0.0, "system": llm.SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "<merchant_name>Cafe Velo</merchant_name>"}],
    }  # fmt: skip


def test_smoke_command_sends_only_the_made_up_name(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    assert llm.main(["smoke"], client) == 0
    out = capsys.readouterr().out
    assert [r["messages"][0]["content"] for r in client.requests] == [
        "<merchant_name>Cafe Velo Test</merchant_name>"
    ]
    assert "RESULT: PASS" in out and "Restaurants & cafés" in out
    assert llm.main(["smoke"], FakeClient("no idea")) == 1
