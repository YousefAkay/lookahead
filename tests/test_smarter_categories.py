"""The "smarter categories" setup question (categorizer v2, 4b): asked once, remembered, a
missing key explained, off until I answer yes. A fake client only: nothing is ever sent."""

import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead import categorizer_v2 as v2
from lookahead import smarter_categories as smarter
from lookahead.db import connect

TO_CHECK = Path(__file__).resolve().parents[1] / "app" / "pages" / "3_To_check.py"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_the_question_says_what_is_sent_and_what_never_is() -> None:
    assert smarter.QUESTION == (
        "Smarter categories for new merchants: sends merchant names and your category labels "
        "(never amounts, dates or descriptions) to Haiku 4.5. Turn on?"
    )
    assert "ANTHROPIC_API_KEY" in smarter.NEEDS_KEY


def test_never_answered_with_a_key_asks_and_stays_off(conn: sqlite3.Connection) -> None:
    assert smarter.state(conn, has_key=True, demo=False) == "ask"
    assert v2.load_options(conn).rag is False


def test_no_key_says_what_is_needed_instead_of_asking(conn: sqlite3.Connection) -> None:
    assert smarter.state(conn, has_key=False, demo=False) == "needs key"
    assert v2.load_options(conn).rag is False


def test_the_demo_is_never_asked(conn: sqlite3.Connection) -> None:
    assert smarter.state(conn, has_key=True, demo=True) == "hidden"


def test_the_answer_is_remembered(conn: sqlite3.Connection) -> None:
    smarter.answer(conn, False)
    assert smarter.state(conn, has_key=True, demo=False) == "off"  # not asked again
    assert v2.load_options(conn).rag is False
    smarter.answer(conn, True)
    assert smarter.state(conn, has_key=True, demo=False) == "on"
    assert v2.load_options(conn).rag is True


# --- On the To check page ---


def _my_data(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: bool) -> Path:
    from lookahead.categorize import Settings, categorize
    from lookahead.generator.personas import PERSONAS
    from lookahead.generator.writer import write_persona
    from lookahead.labels import load_rows

    synthetic = tmp_path / "synthetic"
    for pid in "ABC":
        write_persona(PERSONAS[pid], synthetic, seed=42, start=date(2026, 6, 1),
                      export_date=date(2026, 9, 30))  # fmt: skip
    private = tmp_path / "data" / "private"
    (private / "db").mkdir(parents=True)
    path = Path(shutil.copy(db, private / "db" / "lookahead.db"))
    c = connect(path)
    rows = load_rows(c, "scotia-chequing")
    with c:  # unreviewed new merchants, all flagged
        c.executemany(
            "DELETE FROM category_labels WHERE fingerprint = ?",
            [(f,) for f in rows.query("split == 'test_b'")["fingerprint"]],
        )
    categorize(c, "scotia-chequing", Settings(threshold=1.1, synthetic_months=4))
    c.close()
    monkeypatch.setenv("LOOKAHEAD_SYNTHETIC_DIR", str(synthetic))
    monkeypatch.setenv("LOOKAHEAD_DEMO_DB", str(tmp_path / "demo.db"))
    monkeypatch.setenv("LOOKAHEAD_PRIVATE_DB", str(path))
    monkeypatch.setenv("PSEUDONYM_KEY", bytes(range(32)).hex())
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # no .env here either
    return path


def _text(at: AppTest) -> str:
    parts = [e.value for e in (*at.markdown, *at.caption, *at.info, *at.warning, *at.success)]
    return "\n".join(str(p) for p in parts)


def _page() -> AppTest:
    at = AppTest.from_file(str(TO_CHECK), default_timeout=60).run()
    at.sidebar.radio[0].set_value("My data").run()
    assert not at.exception, at.exception
    return at


@pytest.mark.slow
def test_page_asks_once_and_remembers(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lookahead.llm_categorizer as llm
    from tests.test_rag_categorizer import FakeClient

    path = _my_data(db, tmp_path, monkeypatch, key=True)
    client = FakeClient('{"category": "Shopping", "cited": ["ex-a"]}')
    monkeypatch.setattr(llm, "make_client", lambda *_: client)
    at = _page()
    assert smarter.QUESTION in _text(at)
    assert at.toggle(key="smarter_categories").value is False  # off until I say yes
    at.button(key="smarter_yes").click().run()
    assert not at.exception, at.exception
    check = connect(path)
    assert v2.load_options(check).rag is True
    check.close()
    assert smarter.QUESTION not in _text(at)  # answered: not asked again
    assert at.toggle(key="smarter_categories").value is True
    assert client.requests == []  # saying yes sends nothing
    next(b for b in at.button if b.label.startswith("Send ") and "Haiku" in b.label).click().run()
    assert not at.exception, at.exception
    assert client.requests  # only the send button sends
    check = connect(path)
    assert check.execute(
        "SELECT COUNT(*) FROM category_predictions WHERE source = 'RAG'"
    ).fetchone()[0]
    check.close()


@pytest.mark.slow
def test_page_not_now_is_remembered_as_off(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _my_data(db, tmp_path, monkeypatch, key=True)
    at = _page()
    at.button(key="smarter_no").click().run()
    assert not at.exception, at.exception
    assert smarter.QUESTION not in _text(at)
    check = connect(path)
    assert v2.stored_answer(check, v2.RAG) is False
    check.close()


@pytest.mark.slow
def test_page_without_a_key_explains_instead_of_asking(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _my_data(db, tmp_path, monkeypatch, key=False)
    at = _page()
    assert smarter.QUESTION not in _text(at)
    assert smarter.NEEDS_KEY in _text(at)
    assert at.toggle(key="smarter_categories").disabled
    check = connect(path)
    assert v2.stored_answer(check, v2.RAG) is None  # nothing answered for me
    check.close()


@pytest.mark.slow
def test_page_on_but_without_a_key_hides_the_send_buttons(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _my_data(db, tmp_path, monkeypatch, key=False)
    c = connect(path)
    smarter.answer(c, True)  # said yes earlier; the key is gone since
    c.close()
    at = _page()
    assert smarter.NEEDS_KEY in _text(at)  # what is needed, instead of the button
    assert not [b for b in at.button if b.label.startswith("Send ")]
