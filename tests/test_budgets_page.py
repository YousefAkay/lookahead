"""The Budgets tab (spec docs/specs/2026-11-20-budgets-that-forecast.md), run with Streamlit's
AppTest on the synthetic demo personas (never held-out): Looks right, editing, recategorizing,
the stale-data line, budget alerts, and demo mode keeping everything in the session."""

import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.budgets import STALE_NOTE, load_limits
from lookahead.db import connect
from lookahead.demo import build_demo_db
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "app" / "pages" / "10_Budgets.py"
SNAPSHOTS = ROOT / "data" / "demo" / "timemachine"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("budgets_page")
    synthetic = tmp / "synthetic"
    for pid in "ABC":
        write_persona(PERSONAS[pid], synthetic, seed=42, start=date(2026, 6, 1),
                      export_date=date(2026, 9, 30))  # fmt: skip
    build_demo_db(tmp / "demo.db", synthetic)
    return tmp


@pytest.fixture
def isolated(built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(built / "synthetic", tmp_path / "synthetic")
    shutil.copy(built / "demo.db", tmp_path / "demo.db")
    conn = connect(tmp_path / "demo.db")
    with conn:  # the data ends mid-September: a month with rows so far and days left
        conn.execute("DELETE FROM transactions WHERE posted_date > '2026-09-14'")
    conn.close()
    monkeypatch.setenv("LOOKAHEAD_SYNTHETIC_DIR", str(tmp_path / "synthetic"))
    monkeypatch.setenv("LOOKAHEAD_DEMO_DB", str(tmp_path / "demo.db"))
    monkeypatch.setenv("LOOKAHEAD_PRIVATE_DB", str(tmp_path / "private" / "missing.db"))
    monkeypatch.setenv("PSEUDONYM_KEY", bytes(range(32)).hex())
    return tmp_path


def text_of(at: AppTest) -> str:
    return "\n".join(str(e.value) for e in (*at.markdown, *at.info, *at.warning, *at.caption))


def opened() -> AppTest:
    at = AppTest.from_file(str(PAGE), default_timeout=300).run()
    assert not at.exception, at.exception
    return at


def button(at: AppTest, label: str):  # type: ignore[no-untyped-def]
    (b,) = [b for b in at.button if b.label == label]
    return b


def metric(at: AppTest, label: str) -> str:
    return next(str(m.value) for m in at.metric if m.label == label)


def test_looks_right_saves_the_proposals_and_shows_the_budgets(isolated: Path) -> None:
    at = opened()
    text = text_of(at)
    assert "Budgets for September 2026" in text
    assert "Proposed limits" in text
    account = at.sidebar.selectbox[0].value
    button(at, "Looks right, accept all").click().run()
    assert not at.exception, at.exception
    conn = connect(isolated / "demo.db")
    saved = load_limits(conn, account)
    conn.close()
    assert saved and all(v is not None and v > 0 and v % 500 == 0 for v in saved.values())
    assert "Proposed limits" not in text_of(at)
    labels = {m.label for m in at.metric}
    assert {"Spent so far", "Limit", "Likely end of month", "Chance of going over"} <= labels
    assert "If you stay on budget" in text_of(at)
    assert at.get("plotly_chart")  # one bar chart per budget


def test_an_edited_limit_is_saved(isolated: Path) -> None:
    at = opened()
    account = at.sidebar.selectbox[0].value
    button(at, "Looks right, accept all").click().run()
    conn = connect(isolated / "demo.db")
    first = next(iter(load_limits(conn, account)))
    conn.close()
    at.number_input(key=f"bu_new_{first}").set_value(123.0)
    (save,) = [b for b in at.button if b.label == "Save" and first in str(b.form_id)]
    save.click().run()
    assert not at.exception, at.exception
    conn = connect(isolated / "demo.db")
    assert load_limits(conn, account)[first] == 12_300
    conn.close()


def test_recategorizing_a_row_moves_it_out_of_the_budget(isolated: Path) -> None:
    at = opened()
    button(at, "Looks right, accept all").click().run()
    category = "Food delivery"
    before = metric_for(at, category, isolated)
    at.selectbox(key=f"bu_target_{category}").set_value("Groceries")
    (move,) = [b for b in at.button if b.label == "Move" and category in str(b.form_id)]
    move.click().run()
    assert not at.exception, at.exception
    assert metric_for(at, category, isolated) < before


def metric_for(at: AppTest, category: str, isolated: Path) -> float:
    """Spent so far of one category's card: the cards with a limit come in a fixed order."""
    from lookahead.budgets import BUDGETABLE

    conn = connect(isolated / "demo.db")
    saved = load_limits(conn, at.sidebar.selectbox[0].value)
    conn.close()
    order = [c for c in BUDGETABLE if saved.get(c)]
    spent = [m for m in at.metric if m.label == "Spent so far"]
    assert len(spent) == len(order)
    value = str(spent[order.index(category)].value)
    return float(value.replace("$", "").replace(",", "").replace("\u2212", "-"))


def test_the_stale_line_shows_when_the_data_is_from_an_earlier_month(isolated: Path) -> None:
    # The synthetic data ends in September 2026; outside demo mode "today" is the real date.
    at = opened()
    if date.today() < date(2026, 10, 1):
        pytest.skip("the real date is still in the data's month")
    assert STALE_NOTE in text_of(at)


def test_in_demo_mode_nothing_is_saved_and_the_line_is_not_stale(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOKAHEAD_DEMO", "1")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(SNAPSHOTS.glob("*.db"))}  # fmt: skip
    at = opened()
    assert STALE_NOTE not in text_of(at)  # demo "today" is the day after the data ends
    account = at.sidebar.selectbox[0].value
    button(at, "Looks right, accept all").click().run()
    assert not at.exception, at.exception
    assert "Spent so far" in {m.label for m in at.metric}  # accepted, in this session
    conn = connect(isolated / "demo.db")
    assert load_limits(conn, account) == {}  # never written
    assert conn.execute("SELECT COUNT(*) FROM budget_alert_episodes").fetchone()[0] == 0
    conn.close()
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(SNAPSHOTS.glob("*.db"))}  # fmt: skip
    assert before == after and len(before) == 7  # the committed snapshots are untouched
    fresh = opened()  # a new session starts with the proposals again
    assert "Proposed limits" in text_of(fresh)


def test_a_budget_alert_is_recorded_and_can_be_dismissed(isolated: Path) -> None:
    at = opened()
    account = at.sidebar.selectbox[0].value
    conn = connect(isolated / "demo.db")
    from lookahead.budgets import DEFAULT_CATEGORIES, set_limit

    for category in DEFAULT_CATEGORIES:
        set_limit(conn, account, category, 500)  # $5: almost surely over
    conn.close()
    at = opened()
    episodes = [b for b in at.button if b.label == "Dismiss this alert"]
    assert any("Possible overspend" in str(w.value) for w in at.warning)
    assert episodes
    episodes[0].click().run()
    assert not at.exception, at.exception
    assert any(b.label == "Bring it back" for b in at.button)


def test_a_turned_off_default_category_can_be_turned_back_on(isolated: Path) -> None:
    """N2: turning a default category off is never final; the add list offers it again."""
    at = opened()
    account = at.sidebar.selectbox[0].value
    button(at, "Looks right, accept all").click().run()
    category = "Restaurants & cafés"
    (off,) = [b for b in at.button if b.key == f"bu_turnoff_{category}"]
    off.click().run()
    assert not at.exception, at.exception
    conn = connect(isolated / "demo.db")
    assert load_limits(conn, account)[category] is None
    conn.close()
    assert category in at.selectbox(key="bu_add_category").options
    at.selectbox(key="bu_add_category").set_value(category).run()
    at.number_input(key="bu_add_limit").set_value(75.0)
    button(at, "Add").click().run()
    assert not at.exception, at.exception
    conn = connect(isolated / "demo.db")
    assert load_limits(conn, account)[category] == 7_500
    conn.close()
    assert any(b.key == f"bu_turnoff_{category}" for b in at.button)  # its card is back


def test_moving_a_whole_merchant_from_the_budget_view(isolated: Path) -> None:
    """The "All rows of this merchant" path: every row of the merchant that a rule did not decide
    takes the new category, and leaves the budget's rows."""
    from lookahead.corrections import merchant_fingerprints

    at = opened()
    account = at.sidebar.selectbox[0].value
    button(at, "Looks right, accept all").click().run()
    category = "Restaurants & cafés"
    rows_box = at.selectbox(key=f"bu_row_{category}")
    conn = connect(isolated / "demo.db")
    this_month = conn.execute(  # the rows the card lists (the data ends on 14 September)
        "SELECT p.fingerprint, p.merchant FROM category_predictions p JOIN transactions t"
        " ON t.fingerprint = p.fingerprint WHERE t.account = ? AND p.category = ?"
        " AND t.posted_date >= '2026-09-01' AND p.merchant IS NOT NULL",
        (account, category),
    ).fetchall()
    fingerprint, merchant = next(
        (f, m) for f, m in this_month if len(merchant_fingerprints(conn, account, m)) > 1
    )
    covered = set(merchant_fingerprints(conn, account, merchant))
    conn.close()
    before = metric_for(at, category, isolated)
    rows_box.set_value(fingerprint)
    at.selectbox(key=f"bu_target_{category}").set_value("Groceries")
    at.radio(key=f"bu_scope_{category}").set_value("All rows of this merchant")
    (move,) = [b for b in at.button if b.label == "Move" and category in str(b.form_id)]
    move.click().run()
    assert not at.exception, at.exception
    conn = connect(isolated / "demo.db")
    after = dict(conn.execute(
        "SELECT fingerprint, category FROM category_predictions WHERE account = ? AND merchant = ?",
        (account, merchant),
    ).fetchall())  # fmt: skip
    conn.close()
    assert all(after[f] == "Groceries" for f in covered)
    assert metric_for(at, category, isolated) < before


def test_a_whole_merchant_move_on_a_rule_merchant_says_why(isolated: Path) -> None:
    """Every Food delivery row of the demo personas is decided by a rule: the whole-merchant move
    changes nothing, and the page says so instead of doing nothing silently."""
    at = opened()
    account = at.sidebar.selectbox[0].value
    button(at, "Looks right, accept all").click().run()
    category = "Food delivery"
    conn = connect(isolated / "demo.db")
    before = conn.execute("SELECT COUNT(*) FROM category_corrections").fetchone()[0]
    conn.close()
    at.selectbox(key=f"bu_target_{category}").set_value("Groceries")
    at.radio(key=f"bu_scope_{category}").set_value("All rows of this merchant")
    (move,) = [b for b in at.button if b.label == "Move" and category in str(b.form_id)]
    move.click().run()
    assert not at.exception, at.exception
    assert any("A rule decides every row" in str(w.value) for w in at.warning)
    conn = connect(isolated / "demo.db")
    assert conn.execute("SELECT COUNT(*) FROM category_corrections").fetchone()[0] == before
    conn.close()
    assert account
