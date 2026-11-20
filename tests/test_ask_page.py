"""The Ask page (spec docs/specs/2026-11-20-ask.md, definition of done 8), run with Streamlit's
AppTest on the synthetic demo personas (never held-out), LLM off: every example chip answers,
anything else gets the honest refusal with examples, several matching merchants ask back with
buttons, and the page writes nothing."""

import hashlib
import html
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.ask.answer import CANT_YET
from lookahead.ask.words import EXAMPLES
from lookahead.db import connect
from lookahead.demo import build_demo_db
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "app" / "pages" / "11_Ask.py"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("ask_page")
    for pid in "ABC":
        write_persona(PERSONAS[pid], tmp / "synthetic", seed=42, start=date(2026, 3, 1),
                      export_date=date(2026, 9, 30))  # fmt: skip
    build_demo_db(tmp / "demo.db", tmp / "synthetic")
    conn = connect(tmp / "demo.db")
    with conn:
        conn.execute("DELETE FROM transactions WHERE posted_date > '2026-09-14'")
    conn.close()
    return tmp


@pytest.fixture
def isolated(built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(built / "synthetic", tmp_path / "synthetic")
    shutil.copy(built / "demo.db", tmp_path / "demo.db")
    monkeypatch.setenv("LOOKAHEAD_SYNTHETIC_DIR", str(tmp_path / "synthetic"))
    monkeypatch.setenv("LOOKAHEAD_DEMO_DB", str(tmp_path / "demo.db"))
    monkeypatch.setenv("LOOKAHEAD_PRIVATE_DB", str(tmp_path / "private" / "missing.db"))
    monkeypatch.setenv("PSEUDONYM_KEY", bytes(range(32)).hex())
    return tmp_path


def opened() -> AppTest:
    at = AppTest.from_file(str(PAGE), default_timeout=300).run()
    assert not at.exception, at.exception
    return at


def answer_of(at: AppTest) -> str:
    """The answer's heading (the first h2 on the page)."""
    for m in at.markdown:
        found = re.search(r"<h2>(.*?)</h2>", str(m.value), re.S)
        if found:
            return html.unescape(found.group(1))
    raise AssertionError("no answer shown")


def text_of(at: AppTest) -> str:
    return "\n".join(str(e.value) for e in (*at.markdown, *at.caption))


def asked(question: str) -> AppTest:
    at = opened()
    at.text_input(key="ask_question").set_value(question)
    at.button[0].click()  # the form's Ask button
    at = at.run()
    assert not at.exception, at.exception
    return at


def test_opens_with_examples_and_hides_the_budget_one_without_budgets(isolated: Path) -> None:
    at = opened()
    labels = [b.label for b in at.button]
    assert [e for e in EXAMPLES if "budget" not in e.casefold()] == [
        x for x in labels if x in EXAMPLES
    ]


def test_every_example_chip_gives_an_answer(isolated: Path) -> None:
    shown = [e for e in EXAMPLES if "budget" not in e.casefold()]
    for example in shown:
        at = opened()
        (chip,) = [b for b in at.button if b.label == example]
        at = chip.click().run()
        assert not at.exception, (example, at.exception)
        heading = answer_of(at)
        assert CANT_YET not in heading and "can't" not in heading, (example, heading)
        assert "How I worked it out" in text_of(at), example


def test_an_answer_shows_its_working_and_rows(isolated: Path) -> None:
    at = asked("How much did I spend on groceries last month?")
    text = text_of(at)
    assert answer_of(at).startswith("You spent $")
    assert "1. Find money out in Groceries, last month" in text
    assert "Rows behind this" in text and "Data through" in text


def test_anything_else_gets_the_honest_refusal_with_examples(isolated: Path) -> None:
    at = asked("Tell me a joke")
    assert answer_of(at) == CANT_YET
    assert "Here is what I can answer" in text_of(at)
    assert any(b.label == EXAMPLES[0] for b in at.button)
    at = asked("Should I buy a car?")
    assert "advice" in answer_of(at)


def test_a_merchant_not_in_the_data(isolated: Path) -> None:
    at = asked("How much did I spend at Zzyzx Emporium last month?")
    assert answer_of(at) == "No merchant like “zzyzx emporium” in your data."


def test_several_matching_merchants_ask_back_with_buttons(isolated: Path) -> None:
    conn = connect(isolated / "demo.db")
    with conn:  # two made-up merchants that share a word
        for i, (name, cents) in enumerate([("Cafe Velisaro", -1250), ("Velisaro Bakery", -800)]):
            fp = f"ask-test-{i}"
            conn.execute(
                "INSERT INTO transactions (account, posted_date, amount_cents, description,"
                " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
                " VALUES ('synthetic-A', '2026-09-10', ?, 'Point of Sale', ?, 0, ?, 'x.csv',"
                " '2026-09-30')",
                (cents, name, fp),
            )
            conn.execute(
                "INSERT INTO category_predictions VALUES (?, 'synthetic-A', ?, 'everyday',"
                " 'Restaurants & cafés', 'rule', '', 0, NULL, 'test', '2026-09-30')",
                (fp, name),
            )
    conn.close()
    at = asked("How much did I spend at velisaro this year?")
    assert answer_of(at) == "More than one merchant matches “velisaro”. Which one do you mean?"
    candidates = [b for b in at.button if b.key and b.key.startswith("pick_")]
    assert [b.label for b in candidates] == ["Cafe Velisaro", "Velisaro Bakery"]
    at = candidates[1].click().run()
    assert not at.exception
    assert answer_of(at) == "You spent $8.00 (at Velisaro Bakery, this year so far)."
    assert "“velisaro” matched Velisaro Bakery (your pick)." in text_of(at)


def test_the_page_writes_nothing(isolated: Path) -> None:
    before = hashlib.sha256((isolated / "demo.db").read_bytes()).hexdigest()
    asked("How much did I spend on groceries last month?")
    asked("What are my recurring payments?")
    asked("What will my balance be in 2 weeks?")
    assert hashlib.sha256((isolated / "demo.db").read_bytes()).hexdigest() == before
