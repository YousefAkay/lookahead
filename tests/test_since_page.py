"""The "Since last time" page and its card on Home (week 5, item 4). Made-up data only: the
synthetic demo database, with new rows added after "Got it"."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.since import marker

pytestmark = pytest.mark.slow  # app pages: run with the full suite

APP = Path(__file__).resolve().parents[1] / "app"
PAGE = APP / "pages" / "8_Since_last_time.py"
HOME = APP / "Home.py"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    synthetic = tmp_path / "synthetic"
    for pid in "ABC":
        write_persona(
            PERSONAS[pid], synthetic, seed=42, start=date(2026, 6, 1), export_date=date(2026, 9, 30)
        )
    monkeypatch.setenv("LOOKAHEAD_SYNTHETIC_DIR", str(synthetic))
    monkeypatch.setenv("LOOKAHEAD_DEMO_DB", str(tmp_path / "demo.db"))
    monkeypatch.setenv("LOOKAHEAD_PRIVATE_DB", str(tmp_path / "private" / "missing.db"))
    monkeypatch.setenv("PSEUDONYM_KEY", bytes(range(32)).hex())
    return tmp_path


def text_of(at: AppTest) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def first_account(path: Path) -> str:
    conn = sqlite3.connect(path)
    name = conn.execute("SELECT MIN(account) FROM transactions").fetchone()[0]
    conn.close()
    return str(name)


def add_new_merchant(path: Path, account: str) -> date:
    """Two rows after the data ends at a merchant never seen, the newer one flagged for checking."""
    conn = connect(path)
    last = date.fromisoformat(
        conn.execute(
            "SELECT MAX(posted_date) FROM transactions WHERE account = ?", (account,)
        ).fetchone()[0]  # fmt: skip
    )
    with conn:
        for n, flagged in ((1, 0), (2, 1)):
            fp = f"since-fp{n}"
            conn.execute(
                "INSERT INTO transactions (account, posted_date, amount_cents, description,"
                " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
                " VALUES (?, ?, -4200, 'pos purchase', 'Opos Brand New Shop', 100000, ?, 'f',"
                " '2026-10-01')",
                (account, (last + timedelta(days=n)).isoformat(), fp),
            )
            conn.execute(
                "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
                " category, source, detail, flagged, confidence, model_version, predicted_at)"
                " VALUES (?, ?, 'Brand New Shop', 'everyday', 'Shopping', 'model', '', ?, 0.4,"
                " 'test', '2026-10-01')",
                (fp, account, flagged),
            )
    conn.close()
    return last + timedelta(days=2)


def test_with_no_marker_there_is_nothing_to_compare_and_nothing_is_set(isolated: Path) -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    assert not at.exception, at.exception
    assert "Nothing to compare yet" in text_of(at)
    db = isolated / "demo.db"
    account = first_account(db)
    conn = sqlite3.connect(db)
    assert marker(conn, account) is None  # only "Got it" sets it
    looks = conn.execute("SELECT COUNT(*) FROM recurring_alert_state WHERE account = ?",
                         (account,)).fetchone()[0]  # fmt: skip
    conn.close()
    assert looks == 1  # opening the page recorded a recurring-alerts look


def test_got_it_sets_the_marker_then_new_rows_show_up(isolated: Path) -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    at.button(key="got_it").click().run()
    assert not at.exception, at.exception
    db = isolated / "demo.db"
    account = first_account(db)
    conn = sqlite3.connect(db)
    m = marker(conn, account)
    assert m is not None
    conn.close()
    text = text_of(at)
    assert "Balance" in text and "No new data since then" in "\n".join(
        str(i.value) for i in at.info
    )

    newest = add_new_merchant(db, account)
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    assert not at.exception, at.exception
    text = text_of(at)
    assert "Brand New Shop" in text and "/To_check" in text and "to check" in text
    assert "2 new transactions" in text
    assert "missed" not in text.lower() and "guarantee" not in text.lower()
    conn = sqlite3.connect(db)
    assert marker(conn, account) == m  # opening the page did not move it
    conn.close()
    at.button(key="got_it").click().run()
    conn = sqlite3.connect(db)
    assert marker(conn, account).data_date == newest  # type: ignore[union-attr]
    conn.close()


def test_without_my_data_the_page_says_how_to_import() -> None:
    at = AppTest.from_file(str(PAGE), default_timeout=90).run()
    at.sidebar.radio[0].set_value("My data").run()
    assert not at.exception, at.exception
    assert any("No database found" in str(i.value) for i in at.info)


def test_home_shows_the_card_and_links_to_the_page(isolated: Path) -> None:
    at = AppTest.from_file(str(HOME), default_timeout=90).run()
    assert not at.exception, at.exception
    text = text_of(at)
    assert "Since last time" in text and "Nothing to compare yet" in text
    assert "/Since_last_time" in text
    db = isolated / "demo.db"
    account = first_account(db)
    conn = connect(db)
    from lookahead.since import acknowledge

    acknowledge(conn, account)
    conn.close()
    add_new_merchant(db, account)
    at = AppTest.from_file(str(HOME), default_timeout=90).run()
    assert not at.exception, at.exception
    text = text_of(at)
    assert "New transactions" in text and "New merchants" in text
    assert "Balance change since then" in text
