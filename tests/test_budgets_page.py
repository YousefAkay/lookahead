"""The Budgets tab (spec docs/specs/2026-11-20-budgets-that-forecast.md), run with Streamlit's
AppTest on the synthetic demo personas (never held-out): Looks right, editing, recategorizing,
the stale-data line, budget alerts, and demo mode keeping everything in the session."""

import hashlib
import html
import shutil
import sqlite3
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
    assert any("You're already over this budget" in str(w.value) for w in at.warning)  # $5
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
    at.number_input(key=f"bu_add_limit_{category}").set_value(75.0)
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
    stored = {r[0] for r in conn.execute("SELECT fingerprint FROM transactions")}
    covered = set(merchant_fingerprints(conn, account, merchant)) & stored  # rows still stored
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


# --- Prefilled limits, $0 refused, the heading, figures and chart ---


def test_before_looks_right_the_limit_box_holds_the_proposal(isolated: Path) -> None:
    from lookahead.budgets import budget_forecast, category_rows, proposals_for

    at = opened()
    account = at.sidebar.selectbox[0].value
    conn = connect(isolated / "demo.db")
    f = budget_forecast(conn, account, futures=10)
    assert f is not None
    rows = category_rows(conn, account, f.start.data_last_updated)
    proposals = proposals_for(conn, account, rows, f)
    conn.close()
    boxes = {n.key: n.value for n in at.number_input if str(n.key).startswith("bu_typed_")}
    assert boxes
    for key, value in boxes.items():
        p = proposals[key.removeprefix("bu_typed_")]
        assert value == (p.limit_cents / 100 if p.limit_cents else 0.0)
    assert any(v > 0 for v in boxes.values())


def test_a_zero_limit_is_refused_with_a_message(isolated: Path) -> None:
    at = opened()
    account = at.sidebar.selectbox[0].value
    category = "Food delivery"
    at.number_input(key=f"bu_typed_{category}").set_value(0.0)
    (save,) = [b for b in at.button if b.label == "Save limit" and category in str(b.form_id)]
    save.click().run()
    assert not at.exception, at.exception
    assert any("must be above" in str(w.value) for w in at.warning)
    conn = connect(isolated / "demo.db")
    assert category not in load_limits(conn, account)
    conn.close()
    # The edit form after Looks right refuses $0 the same way and keeps the saved limit.
    button(at, "Looks right, accept all").click().run()
    conn = connect(isolated / "demo.db")
    kept = load_limits(conn, account)[category]
    conn.close()
    at.number_input(key=f"bu_new_{category}").set_value(0.0)
    (edit,) = [b for b in at.button if b.label == "Save" and category in str(b.form_id)]
    edit.click().run()
    assert any("must be above" in str(w.value) for w in at.warning)
    conn = connect(isolated / "demo.db")
    assert load_limits(conn, account)[category] == kept
    conn.close()


def test_the_heading_names_the_month_above_the_cards(isolated: Path) -> None:
    at = opened()
    texts = [str(m.value) for m in at.markdown]
    heading = next(i for i, t in enumerate(texts) if "<h1>Budgets for September 2026</h1>" in t)
    first_card = next(i for i, t in enumerate(texts) if "<h2>" in t)
    assert heading < first_card


def test_every_card_shows_four_figures_and_a_chart_with_the_limit(isolated: Path) -> None:
    import json

    from app.theme import BUDGET_BARS

    at = opened()
    button(at, "Looks right, accept all").click().run()
    labels = [m.label for m in at.metric]
    cards = labels.count("Spent so far")
    assert cards >= 3
    for label in ("Limit", "Likely end of month", "Chance of going over"):
        assert labels.count(label) == cards
    charts = at.get("plotly_chart")
    assert len(charts) == cards
    limits = [m.value for m in at.metric if m.label == "Limit"]
    for chart, limit in zip(charts, limits, strict=True):
        spec = json.loads(chart.proto.spec)
        (bars,) = spec["data"]
        assert sorted(bars["y"]) == sorted(BUDGET_BARS)  # spent, limit and likely end: 3 bars
        assert bars["text"][bars["y"].index("Limit")] == limit
        assert [s["x0"] for s in spec["layout"]["shapes"]] == [
            float(limit.strip("$").replace(",", ""))
        ]
    tags = [str(m.value) for m in at.markdown if "chance of going over</span>" in str(m.value)
            or "already over this budget</span>" in str(m.value)]  # fmt: skip
    assert len(tags) == cards


def test_a_budget_already_over_says_so(isolated: Path) -> None:
    """N2: spent so far above the limit: the card and the alert say "You're already over this
    budget" and quote no chance of simulated futures."""
    from lookahead.budgets import ALREADY_OVER, set_limit

    at = opened()
    account = at.sidebar.selectbox[0].value
    conn = connect(isolated / "demo.db")
    set_limit(conn, account, "Restaurants & cafés", 100)  # $1: already spent more this month
    conn.close()
    at = opened()
    assert [m.value for m in at.metric if m.label == "Chance of going over"] == ["Already over"]
    tag = html.escape(ALREADY_OVER)  # the tag beside the name (its text is HTML-escaped)
    assert any(tag in str(m.value) for m in at.markdown)
    warnings = [str(w.value) for w in at.warning]
    assert any(w.startswith("You're already over this budget: Restaurants") for w in warnings)
    assert not any("simulated futures" in w for w in warnings)


def series_with_a_category(isolated: Path, account: str) -> tuple[str, str]:
    """Give the Fizz phone series the category Shopping (as the Recurring page does): its
    September payment then shows on the Shopping card. Returns (that payment, the merchant)."""
    from lookahead.recurring_store import load_series, refresh, set_category

    conn = connect(isolated / "demo.db")
    refresh(conn, account)
    fizz = next(s for s in load_series(conn, account) if "Fizz" in s.key)
    set_category(conn, account, fizz, "Shopping")
    refresh(conn, account)
    september = {r[0] for r in conn.execute(
        "SELECT fingerprint FROM transactions WHERE account = ? AND posted_date >= '2026-09-01'",
        (account,))}  # fmt: skip
    payment = next(f for f in fizz.evidence if f in september)
    merchant = conn.execute("SELECT merchant FROM category_predictions WHERE fingerprint = ?",
                            (payment,)).fetchone()[0]  # fmt: skip
    conn.close()
    return payment, merchant


def corrections_count(isolated: Path) -> int:
    conn = connect(isolated / "demo.db")
    n = conn.execute("SELECT COUNT(*) FROM category_corrections").fetchone()[0]
    conn.close()
    return int(n)


def move(at: AppTest, category: str, fingerprint: str, target: str, scope: str) -> None:
    at.selectbox(key=f"bu_row_{category}").set_value(fingerprint)
    at.selectbox(key=f"bu_target_{category}").set_value(target)
    at.radio(key=f"bu_scope_{category}").set_value(scope)
    (button_,) = [b for b in at.button if b.label == "Move" and category in str(b.form_id)]
    button_.click().run()
    assert not at.exception, at.exception


def test_a_row_whose_category_comes_from_its_series_is_not_moved_silently(isolated: Path) -> None:
    """A series' category applies to every payment of the series (DECISIONS 2026-10-02), so a
    one-row move of such a row is refused with a pointer to the Recurring page, not stored."""
    from lookahead.budgets import SERIES_NOTE

    at = opened()
    account = at.sidebar.selectbox[0].value
    payment, _ = series_with_a_category(isolated, account)
    at = opened()
    button(at, "Looks right, accept all").click().run()
    before = corrections_count(isolated)
    move(at, "Shopping", payment, "Groceries", "Just this row")
    assert any(SERIES_NOTE in str(w.value) for w in at.warning)
    assert any('href="Recurring' in str(m.value) for m in at.markdown)
    assert corrections_count(isolated) == before


def test_a_whole_merchant_move_of_only_series_payments_moves_nothing(isolated: Path) -> None:
    """Every stored Fizz row is a payment of the series given a category: nothing is stored and
    the page says why, with the Recurring link."""
    at = opened()
    account = at.sidebar.selectbox[0].value
    payment, _ = series_with_a_category(isolated, account)
    at = opened()
    button(at, "Looks right, accept all").click().run()
    before = corrections_count(isolated)
    move(at, "Shopping", payment, "Groceries", "All rows of this merchant")
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "0 moved, 0 already there," in warnings and "kept by their series" in warnings
    assert "A series' category is changed on the Recurring page." in warnings
    assert any('href="Recurring' in str(m.value) for m in at.markdown)
    assert corrections_count(isolated) == before


def test_the_add_form_never_mentions_a_zero_limit(isolated: Path) -> None:
    """N4: the Add form fills in the proposal and refuses $0 like the cards."""
    at = opened()
    account = at.sidebar.selectbox[0].value
    at.selectbox(key="bu_add_category").set_value("Groceries").run()
    notes = " ".join(str(m.value) for m in at.markdown)
    assert "Leave the limit" not in notes and "at &#36;0" not in notes
    box = at.number_input(key="bu_add_limit_Groceries")
    assert box.value > 0  # the proposal, filled in
    box.set_value(0.0)
    button(at, "Add").click().run()
    assert any("must be above" in str(w.value) for w in at.warning)
    conn = connect(isolated / "demo.db")
    assert "Groceries" not in load_limits(conn, account)
    conn.close()


# --- Home's sentence and the mixed whole-merchant move (this round) ---


def test_home_says_youre_already_over_like_the_tab(isolated: Path) -> None:
    from lookahead.budget_alerts import record_look
    from lookahead.budgets import BudgetMonth, Pace

    home = ROOT / "app" / "Home.py"
    at = AppTest.from_file(str(home), default_timeout=300).run()
    account = at.sidebar.selectbox[0].value
    conn = connect(isolated / "demo.db")
    over = Pace("Shopping", 2_000, 3_000, 4_000, 1.0, 100, False, ())
    record_look(conn, account, BudgetMonth(date(2026, 9, 1), date(2026, 9, 14), 16, (over,)),
                date(2026, 9, 15))  # fmt: skip
    conn.close()
    at = AppTest.from_file(str(home), default_timeout=300).run()
    assert not at.exception, at.exception
    text = " ".join(str(m.value) for m in at.markdown)
    assert "You're already over this budget: Shopping" in text
    assert "of simulated futures end the month above" not in text


def test_a_mixed_whole_merchant_move_on_the_tab(isolated: Path) -> None:
    """Built here: the Fizz series gets the category Shopping (its payments show Shopping), and
    one more Fizz row this month that is not in the series is added as Shopping. A whole-merchant
    move to Groceries stores a correction for that one row only and says the series' payments
    keep their category, after the page reloads."""
    from lookahead.corrections import corrections

    at = opened()
    account = at.sidebar.selectbox[0].value
    payment, merchant = series_with_a_category(isolated, account)
    conn = connect(isolated / "demo.db")
    in_series = {r[0] for r in conn.execute(
        "SELECT p.fingerprint FROM category_predictions p JOIN transactions t"
        " ON t.fingerprint = p.fingerprint WHERE t.account = ? AND p.merchant = ?",
        (account, merchant))}  # fmt: skip
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, '2026-09-12', -2500, 'pos purchase', 'Fizz extra', 0, 'fizz-extra',"
            " 'x.csv', '2026-10-02')",
            (account,),
        )
        conn.execute(
            "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
            " category, source, detail, flagged, confidence, model_version, predicted_at)"
            " VALUES ('fizz-extra', ?, ?, 'everyday', 'Shopping', 'model', '', 0, 0.9, 'test',"
            " '2026-10-02')",
            (account, merchant),
        )
    before = dict(corrections(conn, account))
    conn.close()
    at = opened()
    button(at, "Looks right, accept all").click().run()
    move(at, "Shopping", "fizz-extra", "Groceries", "All rows of this merchant")
    warnings = " ".join(str(w.value) for w in at.warning)
    kept = len(in_series)
    assert f"1 moved, 0 already there, {kept} kept by their series." in warnings
    conn = connect(isolated / "demo.db")
    after = corrections(conn, account)
    conn.close()
    assert {f for f in after if after[f] != before.get(f)} == {"fizz-extra"}
    assert payment in in_series


def test_a_later_categorizer_run_cannot_split_a_moved_merchant(isolated: Path) -> None:
    """The model may put one of a merchant's rows in the target already, and the next run may
    not learn from my corrections (a holdout merchant is never trained on; here: a model trained
    before the move). Then only a correction holds a row in place. The old move (corrections
    only for rows not already in the target) leaves that row to the model, which takes it back:
    a split. The move locks it in, so the same run keeps every row of the merchant in the target."""

    from lookahead.budgets import move_merchant
    from lookahead.categorize import categorize, train
    from lookahead.corrections import correct_merchant, merchant_fingerprints
    from lookahead.demo import DEMO_SETTINGS
    from lookahead.views import transactions

    account = "synthetic-A"
    conn = connect(isolated / "demo.db")
    model, _ = train(conn, account, DEMO_SETTINGS)  # trained before any move: knows no correction
    present = {r[0] for r in conn.execute(
        "SELECT fingerprint FROM transactions WHERE account = ?", (account,))}  # fmt: skip
    merchants = conn.execute(
        "SELECT merchant FROM category_predictions WHERE account = ? AND merchant IS NOT NULL"
        " AND category != 'Groceries' GROUP BY merchant ORDER BY COUNT(*) DESC",
        (account,),
    ).fetchall()
    merchant, covered = next(
        (m[0], rows) for m in merchants
        if len(rows := [f for f in merchant_fingerprints(conn, account, m[0]) if f in present]) >= 3
    )  # fmt: skip
    crafted = covered[0]
    with conn:  # as if the model had put this one row in Groceries already
        conn.execute("UPDATE category_predictions SET category = 'Groceries'"
                     " WHERE fingerprint = ?", (crafted,))  # fmt: skip
    conn.close()
    old_db = Path(shutil.copy(isolated / "demo.db", isolated / "old.db"))

    def after_run(db: Path, move) -> dict[str, str]:  # type: ignore[no-untyped-def]
        c = connect(db)
        move(c)
        categorize(c, account, DEMO_SETTINGS, model=model)  # the later run
        df = transactions(c, account)
        c.close()
        return dict(zip(df["fingerprint"], df["category"], strict=True))

    def old_move(c: sqlite3.Connection) -> None:  # the previous version: rows not in the target
        correct_merchant(
            c, account, merchant, "Groceries", only=[f for f in covered if f != crafted]
        )

    def new_move(c: sqlite3.Connection) -> None:
        result = move_merchant(c, account, merchant, "Groceries")
        assert result.already_there == 1 and result.moved == len(covered) - 1

    old = after_run(old_db, old_move)
    assert old[crafted] != "Groceries"  # the old move splits the merchant
    assert all(old[f] == "Groceries" for f in covered if f != crafted)
    new = after_run(isolated / "demo.db", new_move)
    assert all(new[f] == "Groceries" for f in covered)  # the move does not


# --- A month where refunds are larger than spending ---


def test_refunds_larger_than_spending_show_as_minus_dollars_with_a_note(isolated: Path) -> None:
    import json

    from lookahead.budgets import REFUNDS_CHART_NOTE, REFUNDS_NOTE, set_limit

    at = opened()
    account = at.sidebar.selectbox[0].value
    conn = connect(isolated / "demo.db")
    with conn:  # a $500 Shopping refund this month: larger than the month's Shopping purchases
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, '2026-09-10', 50000, 'pos return', 'Big Store return', 0,"
            " 'big-refund', 'x.csv', '2026-10-02')",
            (account,),
        )
        conn.execute(
            "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
            " category, source, detail, flagged, confidence, model_version, predicted_at)"
            " VALUES ('big-refund', ?, 'Big Store', 'refund', 'Shopping', 'model', '', 0, 0.9,"
            " 'test', '2026-10-02')",
            (account,),
        )
    set_limit(conn, account, "Shopping", 10_000)
    conn.close()
    at = opened()
    assert not at.exception, at.exception
    [spent] = [m.value for m in at.metric if m.label == "Spent so far"]
    assert spent.startswith("\u2212$")  # −$X: a real minus sign before the dollar sign
    assert REFUNDS_NOTE in [str(c.value) for c in at.caption]
    notes = " ".join(str(m.value) for m in at.markdown)
    assert REFUNDS_CHART_NOTE.replace("$", "&#36;") in notes
    [chart] = at.get("plotly_chart")
    bars = json.loads(chart.proto.spec)["data"][0]
    label = bars["text"][bars["y"].index("Spent so far")]
    assert label == spent and bars["x"][bars["y"].index("Spent so far")] < 0  # sign kept
    shown = [notes, *(str(m.value) for m in at.metric), *(str(w.value) for w in at.warning),
             *bars["text"]]  # fmt: skip
    assert not any("$-" in text for text in shown)
