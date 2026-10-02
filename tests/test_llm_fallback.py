"""Tests for step 8d: the opt-in LLM fallback in the app. Fake client, temporary files."""

import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import lookahead.llm_categorizer as llm
import lookahead.llm_fallback as fallback
from lookahead.categorize import Settings, categorize
from lookahead.categorize import main as categorize_main
from lookahead.db import connect
from lookahead.labels import load_rows
from tests.test_llm_categorizer import FakeClient

ACCOUNT = "scotia-chequing"
SETTINGS = Settings(threshold=0.5, synthetic_months=4)
KEY = bytes.fromhex("ab" * 32)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "data" / "private" / "cache" / "llm.json"


@pytest.fixture
def conn(db: Path, tmp_path: Path) -> sqlite3.Connection:
    """A categorized copy where the Test B merchants are unreviewed, flagged model rows."""
    c = connect(Path(shutil.copy(db, tmp_path / "t.db")))
    rows = load_rows(c, ACCOUNT)
    with c:
        c.executemany(
            "DELETE FROM category_labels WHERE fingerprint = ?",
            [(f,) for f in rows.query("split == 'test_b'")["fingerprint"]],
        )
    categorize(c, ACCOUNT, Settings(threshold=1.1, synthetic_months=4), date(2026, 10, 2))
    return c


def sources(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT source, COUNT(*) FROM category_predictions GROUP BY source"))


def test_the_fallback_is_off_by_default_and_refuses_to_run(
    conn: sqlite3.Connection, cache: Path
) -> None:
    assert fallback.is_on(conn) is False
    client = FakeClient()
    with pytest.raises(RuntimeError, match="off"):
        fallback.apply(conn, ACCOUNT, KEY, cache, send=True, client=client)
    assert client.requests == [] and "LLM" not in sources(conn)


def test_only_flagged_new_merchant_card_names_are_candidates(conn: sqlite3.Connection) -> None:
    names = fallback.candidates(conn, ACCOUNT, KEY)
    assert names
    marks = ", ".join("?" for f in names.values() for _ in f)
    rows = conn.execute(
        "SELECT p.source, p.flagged, t.description FROM category_predictions p JOIN"
        f" transactions t ON t.fingerprint = p.fingerprint WHERE p.fingerprint IN ({marks})",
        [f for fs in names.values() for f in fs],
    ).fetchall()
    assert {(r[0], r[1]) for r in rows} == {("model", 1)}  # never a rule row or a known merchant
    assert {r[2].casefold() for r in rows} <= {"pos purchase", "pos return", "correction"}
    assert not any("Nadia" in n for n in names)


def test_nothing_is_sent_until_asked_and_each_name_once(
    conn: sqlite3.Connection, cache: Path
) -> None:
    fallback.set_on(conn, True)
    waiting = fallback.not_asked_yet(conn, ACCOUNT, KEY, cache)
    assert waiting == sorted(fallback.candidates(conn, ACCOUNT, KEY))
    client = FakeClient("Entertainment")
    assert fallback.apply(conn, ACCOUNT, KEY, cache, client=client).rows == 0  # saved only
    assert client.requests == []
    done = fallback.apply(conn, ACCOUNT, KEY, cache, send=True, client=client)
    assert done.sent == len(waiting) == len(client.requests) and done.rows > 0
    sent = {r["messages"][0]["content"] for r in client.requests}
    assert sent == {f"<merchant_name>{n}</merchant_name>" for n in waiting}
    stored = conn.execute(
        "SELECT category, flagged, detail FROM category_predictions WHERE source = 'LLM'"
    ).fetchall()
    assert len(stored) == done.rows
    assert {tuple(r) for r in stored} == {("Entertainment", 1, fallback.DETAIL)}  # still flagged
    assert fallback.not_asked_yet(conn, ACCOUNT, KEY, cache) == []
    again = FakeClient()
    assert fallback.apply(conn, ACCOUNT, KEY, cache, send=True, client=again).sent == 0
    assert again.requests == []


def test_an_answer_that_is_not_a_category_leaves_the_models_answer(
    conn: sqlite3.Connection, cache: Path
) -> None:
    fallback.set_on(conn, True)
    before = sources(conn)
    done = fallback.apply(conn, ACCOUNT, KEY, cache, send=True, client=FakeClient("no idea"))
    assert done.rows == 0 and sources(conn) == before


def test_retraining_reuses_saved_answers_without_sending(
    conn: sqlite3.Connection, cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    fallback.set_on(conn, True)
    first = fallback.apply(conn, ACCOUNT, KEY, cache, send=True, client=FakeClient("Shopping"))
    conn.close()
    env = tmp_path / ".env"
    env.write_text(f"PSEUDONYM_KEY={'ab' * 32}\n", encoding="utf-8")
    monkeypatch.setattr(llm, "DEFAULT_CACHE", cache)
    monkeypatch.setattr(fallback.apply, "__defaults__", (cache, False, None))

    def no_client(*_: object) -> object:
        raise AssertionError("the categorize command must never send anything")

    monkeypatch.setattr(llm, "make_client", no_client)
    choice = tmp_path / "choice.json"
    choice.write_text(
        '{"params": {"model__C": 1.0, "features__name__ngram_range": [2, 5]},'
        ' "flag_threshold": 1.1, "synthetic_months": 4}', encoding="utf-8",
    )  # fmt: skip
    path = tmp_path / "t.db"
    assert categorize_main(["--db", str(path), "--choice", str(choice), "--env", str(env)]) == 0
    out = capsys.readouterr().out
    assert f"saved LLM answers (fallback is on): {first.rows}" in out
    check = connect(path)
    assert sources(check)["LLM"] == first.rows
    fallback.set_on(check, False)  # off again: the command leaves the model's own answers
    check.close()
    assert categorize_main(["--db", str(path), "--choice", str(choice), "--env", str(env)]) == 0
    check = connect(path)
    assert "LLM" not in sources(check)
    check.close()


def test_api_key_presence_is_read_without_exposing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(llm.KEY_NAME, raising=False)
    env = tmp_path / ".env"
    assert fallback.has_api_key(env) is False
    env.write_text("ANTHROPIC_API_KEY=sk-ant-test\n", encoding="utf-8")
    assert fallback.has_api_key(env) is True
