"""The Scenario page ("what if I spend X a day"): it loads on the synthetic demo and on an empty
database, and says it is not a forecast. Made-up data only."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona

pytestmark = pytest.mark.slow  # an app page: run with the full suite

PAGE = Path(__file__).resolve().parents[1] / "app" / "pages" / "9_Scenario.py"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    synthetic = tmp_path / "synthetic"
    for pid in "ABC":
        write_persona(
            PERSONAS[pid], synthetic, seed=42, start=date(2026, 6, 1), export_date=date(2026, 9, 30)
        )
    monkeypatch.setenv("LOOKAHEAD_SYNTHETIC_DIR", str(synthetic))
    monkeypatch.setenv("LOOKAHEAD_DEMO_DB", str(tmp_path / "demo.db"))
    monkeypatch.setenv("LOOKAHEAD_PRIVATE_DB", str(tmp_path / "private" / "lookahead.db"))
    monkeypatch.setenv("PSEUDONYM_KEY", bytes(range(32)).hex())
    return tmp_path


def text_of(at: AppTest) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def headline(at: AppTest) -> str:
    return next(str(m.value) for m in at.markdown if "You'd have" in str(m.value))


def test_the_page_loads_on_the_demo_and_says_it_is_not_a_forecast() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    assert not at.exception, at.exception
    text = text_of(at)
    assert "You'd have" in text
    assert "This assumes exactly what you entered. It's not a forecast." in text
    assert "/Scenario" in text  # in the top bar
    assert len(at.get("plotly_chart")) == 1
    lowered = text.lower()
    assert "guarantee" not in lowered and "missed" not in lowered


def test_spending_more_a_day_changes_the_headline() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    before = headline(at)
    at.number_input(key="sc_spend").set_value(at.number_input(key="sc_spend").value + 50).run()
    assert not at.exception, at.exception
    assert headline(at) != before


def test_a_one_off_can_be_added_and_removed() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    before = headline(at)
    at.text_input(key="sc_extra_name").set_value("Concert")
    at.number_input(key="sc_extra_amount").set_value(500.0)
    at.button(key="sc_extra_add").click().run()
    assert not at.exception, at.exception
    assert "Concert" in text_of(at) and headline(at) != before
    at.button(key="sc_extra_remove|0").click().run()
    assert headline(at) == before


def test_an_end_date_on_or_before_the_data_says_so() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    at.date_input(key="sc_end").set_value(date(2026, 1, 1)).run()  # before the data ends
    assert not at.exception, at.exception
    assert any("Pick an end date after" in str(i.value) for i in at.info)


def test_an_empty_database_says_there_is_nothing_yet(isolated: Path) -> None:
    db = isolated / "private" / "lookahead.db"
    db.parent.mkdir()
    connect(db).close()  # the tables, no rows
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    at.sidebar.radio[0].set_value("My data").run()
    assert not at.exception, at.exception
    assert any("no transactions yet" in str(i.value) for i in at.info)


def test_no_database_says_how_to_import() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    at.sidebar.radio[0].set_value("My data").run()
    assert not at.exception, at.exception
    assert any("No database found" in str(i.value) for i in at.info)


def test_the_one_off_day_starts_the_day_after_the_data(isolated: Path) -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    conn = connect(isolated / "demo.db")
    account = at.sidebar.selectbox[0].value
    last = conn.execute(
        "SELECT MAX(posted_date) FROM transactions WHERE account = ?", (account,)
    ).fetchone()[0]
    conn.close()
    assert at.date_input(key="sc_extra_day").value == date.fromisoformat(last) + timedelta(days=1)
