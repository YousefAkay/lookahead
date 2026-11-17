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


def test_top_ups_raise_the_headline_and_the_timing_choice_shows_a_day() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    assert at.number_input(key="sc_top_ups").value == 0.0
    assert at.radio(key="sc_timing").value == "Spread evenly through the month"
    assert not [n for n in at.number_input if n.key == "sc_pay_day"]
    before = headline(at)
    at.number_input(key="sc_top_ups").set_value(300.0).run()
    assert not at.exception, at.exception
    assert headline(at) != before
    at.radio(key="sc_timing").set_value("On a set day").run()
    assert not at.exception, at.exception
    assert at.number_input(key="sc_pay_day").value >= 1


def test_the_estimates_say_what_they_are_based_on() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    assert "The average day over" in text_of(at)


# --- Scenario v2 (spec docs/specs/2026-11-10-scenario-v2.md) ---


def notes(at: AppTest) -> str:
    return "\n".join(str(m.value) for m in at.markdown) + "\n".join(
        str(c.value) for c in at.caption
    )


def test_a_savings_balance_shows_both_together_hides_top_ups_and_is_remembered() -> None:
    """Definition of done 1, 3 and 4."""
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    assert "Chequing only: no savings balance entered" in notes(at)
    assert [n for n in at.number_input if n.key == "sc_top_ups"]
    chequing_only = headline(at)
    assert "in chequing on" in chequing_only

    at.number_input(key="sc_savings").set_value(5_000.0).run()
    assert not at.exception, at.exception
    assert "Chequing and savings together" in notes(at)
    assert not [n for n in at.number_input if n.key == "sc_top_ups"]
    assert "in chequing and savings on" in headline(at) and headline(at) != chequing_only
    assert "Entered " in notes(at)

    again = AppTest.from_file(str(PAGE), default_timeout=90).run()  # a fresh visit
    assert again.number_input(key="sc_savings").value == 5_000.0
    assert "Entered " in notes(again) and "Chequing and savings together" in notes(again)

    again.button(key="sc_savings_clear").click().run()
    assert not again.exception, again.exception
    assert "Chequing only: no savings balance entered" in notes(again)
    assert [n for n in again.number_input if n.key == "sc_top_ups"]
    assert again.number_input(key="sc_savings").value is None and not again.warning
    third = AppTest.from_file(str(PAGE), default_timeout=90).run()
    assert third.number_input(key="sc_savings").value is None


def test_both_basis_notes_are_shown() -> None:
    """Definition of done 7, on the page."""
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    text = text_of(at)
    assert "The average day over" in text
    assert "deposit" in text  # the income note, whatever it found


def test_both_notes_show_their_counts_one_per_labelled_line() -> None:
    """After my real-data check of 2026-11-17: I could not find the counts in the notes."""
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    text = text_of(at)
    for line in ("How the daily spend was estimated", "How the income was estimated",
                 "Days in the window: ", "Payments counted: ", "Deposits counted: ",
                 "Left out, transfers between your own accounts: ",
                 "Left out, one-off deposits (big lump sums): "):  # fmt: skip
        assert line in text, line


def add_one_off(at: AppTest, day: date, dollars: float) -> AppTest:
    at.text_input(key="sc_extra_name").set_value("Family")
    at.date_input(key="sc_extra_day").set_value(day)
    at.number_input(key="sc_extra_amount").set_value(dollars)
    at.radio(key="sc_extra_dir").set_value("Money in")
    return at.button(key="sc_extra_add").click().run()


def test_a_one_off_after_the_end_date_says_it_is_not_counted() -> None:
    """My real-data check of 2026-11-17: a January money in made no visible change, because it
    fell after the end date (90 days after the data) and was skipped without a word."""
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    end = at.date_input(key="sc_end").value
    before = headline(at)
    add_one_off(at, end + timedelta(days=10), 2000.0)
    assert not at.exception, at.exception
    assert headline(at) == before  # still not counted ...
    assert "after the end date: not counted" in text_of(at)  # ... but it says so
    assert any("not counted" in str(w.value) for w in at.warning)
    at.date_input(key="sc_end").set_value(end + timedelta(days=10)).run()
    assert "after the end date" not in text_of(at)  # inside the window now: counted
    assert not any("not counted" in str(w.value) for w in at.warning)


def test_a_one_off_inside_the_window_raises_my_scenario_with_no_warning() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    end = at.date_input(key="sc_end").value
    before = headline(at)
    add_one_off(at, end - timedelta(days=5), 2000.0)
    assert headline(at) != before
    assert not any("not counted" in str(w.value) for w in at.warning)


def test_the_end_date_defaults_to_90_days_after_the_data(isolated: Path) -> None:
    """Definition of done 8."""
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    conn = connect(isolated / "demo.db")
    account = at.sidebar.selectbox[0].value
    last = conn.execute(
        "SELECT MAX(posted_date) FROM transactions WHERE account = ?", (account,)
    ).fetchone()[0]
    conn.close()
    assert at.date_input(key="sc_end").value == date.fromisoformat(last) + timedelta(days=90)


def test_a_listed_top_up_and_the_top_ups_field_show_a_warning(isolated: Path) -> None:
    """Chequing-only view with a confirmed top-up from savings: typing a top-up as well warns that
    the same money may be counted twice (my decision of 2026-11-10)."""
    from tests.test_scenario_prefill import ACCOUNT, confirm, insert, refresh

    db = isolated / "private" / "lookahead.db"
    db.parent.mkdir()
    conn = connect(db)
    today = date.today()
    for back in range(6, 0, -1):  # the 10th of each of the last six months: recent, so active
        y, m = divmod(today.year * 12 + today.month - 1 - back, 12)
        insert(conn, date(y, m + 1, 10), 50_000, "Customer Transfer Cr. MB-Transfer From 1234",
               kind="customer transfer cr.", flow="transfer")  # fmt: skip
    insert(conn, today - timedelta(days=1), -1_000, "Opos Shop", flow="everyday", balance=99_000)
    for s in refresh(conn, ACCOUNT):
        confirm(conn, ACCOUNT, s, today - timedelta(days=1))
    refresh(conn, ACCOUNT)  # as the Recurring page does after a decision
    conn.close()
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    at.sidebar.radio[0].set_value("My data").run()
    assert not at.exception, at.exception
    assert not at.warning
    at.number_input(key="sc_top_ups").set_value(300.0).run()
    assert any("count the same money twice" in str(w.value) for w in at.warning)
    at.number_input(key="sc_top_ups").set_value(0.0).run()
    assert not at.warning
