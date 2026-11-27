"""The "Needs a look" card on the Recurring page (week 5, item 3). Made-up rows only: a bill is
confirmed, then stops showing up, in an extra account added to the synthetic demo database."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.recurring_alerts import episodes, evidence_lines, message
from lookahead.recurring_store import confirm, refresh

pytestmark = pytest.mark.slow  # an app page: run with the full suite

RECURRING = Path(__file__).resolve().parents[1] / "app" / "pages" / "4_Recurring.py"
ACCOUNT = "alerts-test"


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


def add_account(path: Path) -> None:
    """An extra account in the demo database: a bill paid January to June, then silence."""
    conn = connect(path)
    rows = [(shift_to_business_day(monthly_date(2026, m, 10), "later"), -6500, "Phone Co ")
            for m in range(1, 7)]  # fmt: skip
    rows += [(date(2026, 7, 20), -1234, "Opos Bookshop            toron ")]
    with conn:
        for n, (day, cents, text) in enumerate(rows):
            conn.execute(
                "INSERT INTO transactions (account, posted_date, amount_cents, description,"
                " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
                " VALUES (?, ?, ?, 'bill payment', ?, 0, ?, 'f', '2026-10-01')",
                (ACCOUNT, day.isoformat(), cents, text, f"alert-fp{n}"),
            )
    series = refresh(conn, ACCOUNT)
    confirm(conn, ACCOUNT, next(s for s in series if s.name == "Phone Co"), date(2026, 1, 1))
    refresh(conn, ACCOUNT)  # the page reads the saved copy, which must hold the confirmation
    conn.close()


def test_the_card_shows_the_alert_and_dismiss_keeps_it_quiet(isolated: Path) -> None:
    AppTest.from_file(str(RECURRING), default_timeout=60).run()  # builds the demo database
    add_account(isolated / "demo.db")
    at = AppTest.from_file(str(RECURRING), default_timeout=60).run()
    assert not at.exception, at.exception
    at.selectbox[0].set_value(ACCOUNT).run()
    assert not at.exception, at.exception
    text = "\n".join(str(m.value) for m in at.markdown)
    assert "Needs a look" in text and "Expected payment not seen yet" in text
    assert "missed" not in text.lower()
    at.button(key=next(b.key for b in at.button if str(b.key).startswith("dismiss_alert"))).click()
    at.run()
    assert not at.exception, at.exception
    keys = [str(b.key) for b in at.button]
    assert not any(k.startswith("dismiss_alert") for k in keys)  # nothing left to dismiss
    assert any(k.startswith("restore_alert") for k in keys)  # it is listed under "Dismissed"
    conn = connect(isolated / "demo.db")
    mine = episodes(conn, ACCOUNT)
    assert [e.status for e in mine] == ["dismissed"]
    lines = evidence_lines(mine[0])
    assert any("Last payment seen" in line for line in lines)
    assert "not seen yet" in message(mine[0], "x").lower()
    conn.close()
