"""Smoke tests for the Streamlit pages, run headlessly with Streamlit's AppTest.

All paths point into a temporary folder: these tests never open data/private/.
"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona

pytestmark = pytest.mark.slow  # run with the full suite, before commits

APP = Path(__file__).resolve().parents[1] / "app"
HOME = APP / "Home.py"
TRANSACTIONS = APP / "pages" / "1_Transactions.py"


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


def run(path: Path) -> AppTest:
    at = AppTest.from_file(str(path), default_timeout=60).run()
    assert not at.exception, at.exception
    return at


def page_text(at: AppTest) -> str:
    parts = [e.value for e in (*at.markdown, *at.caption, *at.info, *at.warning, *at.success)]
    return "\n".join(str(p) for p in parts)


def test_home_page_renders_with_the_disclaimer() -> None:
    at = run(HOME)
    assert "Not financial advice" in page_text(at)


def test_transactions_page_defaults_to_the_synthetic_demo() -> None:
    at = run(TRANSACTIONS)
    assert at.sidebar.radio[0].value == "Synthetic demo"
    assert at.sidebar.selectbox[0].value == "synthetic-A"
    assert len(at.dataframe[0].value) > 100
    assert "reconciliation OK" in page_text(at)
    assert "Not financial advice" in page_text(at)


def test_pseudonymize_is_on_by_default() -> None:
    at = run(TRANSACTIONS)
    assert at.sidebar.toggle[0].value is True
    at.sidebar.selectbox[0].set_value("synthetic-C").run()
    texts = at.dataframe[0].value["Text"]
    assert not texts.str.contains("Nadia").any()
    assert texts.str.contains("PERSON_").any()


def test_turning_pseudonymize_off_shows_raw_text() -> None:
    at = run(TRANSACTIONS)
    at.sidebar.selectbox[0].set_value("synthetic-C").run()
    at.sidebar.toggle[0].set_value(False).run()
    assert at.dataframe[0].value["Text"].str.contains("1/Nadia Haddad").any()


def test_search_narrows_the_table() -> None:
    at = run(TRANSACTIONS)
    everything = len(at.dataframe[0].value)
    at.sidebar.text_input[0].set_value("uber").run()
    narrowed = at.dataframe[0].value
    assert 0 < len(narrowed) < everything


def test_my_data_without_a_database_explains_how_to_import() -> None:
    at = run(TRANSACTIONS)
    at.sidebar.radio[0].set_value("My data").run()
    assert not at.exception
    assert "python -m lookahead.ingest" in page_text(at)
    assert len(at.dataframe) == 0


def test_app_is_only_reachable_from_this_computer() -> None:
    import tomllib

    config = tomllib.loads((APP.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["address"] == "localhost"  # not every network interface
    assert config["browser"]["gatherUsageStats"] is False


def test_deploy_button_is_hidden() -> None:
    import tomllib

    config = tomllib.loads((APP.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["toolbarMode"] == "minimal"


# --- Label review page ---

REVIEW = APP / "pages" / "2_Label_review.py"


def freeze_demo(tmp_path: Path, account: str = "synthetic-A") -> None:
    from lookahead.db import connect
    from lookahead.demo import build_demo_db
    from lookahead.labels import freeze_splits

    db = tmp_path / "demo.db"
    build_demo_db(db, tmp_path / "synthetic")
    conn = connect(db)
    freeze_splits(conn, account, seed=7)
    conn.close()


def metric(at: AppTest, label: str) -> str:
    return next(str(m.value) for m in at.metric if m.label == label)


def button(at: AppTest, start: str):  # type: ignore[no-untyped-def]
    return next(b for b in at.button if b.label.startswith(start))


def test_review_page_asks_to_freeze_the_splits_first() -> None:
    at = run(REVIEW)
    assert "python -m lookahead.labels freeze-splits" in page_text(at)
    assert len(at.dataframe) == 0


def test_review_page_labels_a_whole_merchant(isolated: Path) -> None:
    freeze_demo(isolated)
    at = run(REVIEW)
    total = metric(at, "Merchants done").split(" / ")[1]
    assert metric(at, "Merchants done") == f"0 / {total}"
    assert len(at.dataframe) == 1
    at.selectbox(key="category").set_value("Groceries")
    button(at, "Label all").click().run()
    assert not at.exception
    assert metric(at, "Merchants done") == f"1 / {total}"
    assert int(metric(at, "Labelled rows")) > 0


def test_review_page_skip(isolated: Path) -> None:
    freeze_demo(isolated)
    at = run(REVIEW)
    button(at, "Skip").click().run()
    assert int(metric(at, "Skipped rows")) > 0 and metric(at, "Labelled rows") == "0"


def test_review_page_shows_holdout_merchants_blind(isolated: Path) -> None:
    from lookahead.db import connect
    from lookahead.labels import review_queue

    freeze_demo(isolated)
    conn = connect(isolated / "demo.db")
    tasks = review_queue(conn, "synthetic-A")
    conn.close()
    blind = next(t for t in tasks if t.blind)
    at = run(REVIEW)
    at.selectbox(key="merchant").set_value(blind).run()
    assert not at.exception
    assert "shown blind, no model suggestion" in page_text(at)
    assert at.selectbox(key="category").value is None  # nothing pre-selected


def test_review_page_overrides_one_row(isolated: Path) -> None:
    freeze_demo(isolated)
    at = run(REVIEW)
    at.selectbox(key="row_category").set_value("Subscriptions")
    button(at, "Save for this row").click().run()
    assert not at.exception
    assert metric(at, "Labelled rows") == "1"


# --- Step 8a: categories on the Transactions page ---


def test_transactions_page_shows_categories_flow_types_and_flags() -> None:
    at = run(TRANSACTIONS)
    table = at.dataframe[0].value
    for column in ("Merchant", "Category", "Flow", "Flag", "From"):
        assert column in table.columns
    assert (table["Category"] == "Groceries").any()
    assert "says nothing about real accuracy" in page_text(at)
    everything = len(table)
    at.sidebar.multiselect[1].set_value(["Groceries"]).run()
    filtered = at.dataframe[0].value
    assert 0 < len(filtered) < everything and set(filtered["Category"]) == {"Groceries"}


# --- Step 8b: the "To check" page ---

TO_CHECK = APP / "pages" / "3_To_check.py"


def demo_categories(tmp_path: Path, account: str = "synthetic-A") -> dict[str, str]:
    from lookahead.db import connect_readonly

    conn = connect_readonly(tmp_path / "demo.db")
    rows = conn.execute(
        "SELECT fingerprint, category FROM category_predictions WHERE account = ?", (account,)
    ).fetchall()
    conn.close()
    return dict(rows)


def test_to_check_page_says_so_when_nothing_is_flagged() -> None:
    at = run(TO_CHECK)
    assert "Nothing to check" in page_text(at) or len(at.selectbox) >= 1
    assert "optional" in page_text(at)


def test_to_check_page_needs_a_confirmation_for_a_whole_merchant(isolated: Path) -> None:
    at = run(TO_CHECK)
    at.sidebar.toggle[1].set_value(True).run()  # show every merchant
    merchants = [o for o in at.selectbox(key="merchant").options if "no merchant" not in o]
    pick = next(o for o in merchants if o.startswith("Maxi"))
    at.selectbox(key="merchant").select(pick).run()
    before = demo_categories(isolated)
    at.selectbox(key="category").select("Shopping")
    button(at, "Apply to the merchant").click().run()
    assert "Tick the box to confirm" in page_text(at)
    assert demo_categories(isolated) == before  # nothing stored without the confirmation
    at.selectbox(key="category").select("Shopping")
    at.checkbox(key="confirm").check()
    button(at, "Apply to the merchant").click().run()
    after = demo_categories(isolated)
    changed = {f for f in after if after[f] != before[f]}
    assert changed and {after[f] for f in changed} == {"Shopping"}


def test_to_check_page_refuses_a_category_that_contradicts_the_flow(isolated: Path) -> None:
    at = run(TO_CHECK)
    at.sidebar.toggle[1].set_value(True).run()
    pick = next(o for o in at.selectbox(key="merchant").options if o.startswith("Maxi"))
    at.selectbox(key="merchant").select(pick).run()
    before = demo_categories(isolated)
    at.selectbox(key="row_category").select("Income")
    button(at, "Save for this row").click().run()
    assert any("does not fit" in str(e.value) for e in at.error)
    assert demo_categories(isolated) == before


# --- Step 8c: the Retrain button ---


def test_retrain_button_reports_what_changed(isolated: Path) -> None:
    at = run(TO_CHECK)
    at.sidebar.toggle[1].set_value(True).run()
    button(at, "Retrain the model").click().run()
    assert "Retrained on" in page_text(at) and "changed category" in page_text(at)
    assert not list(isolated.glob("**/*.joblib"))  # the demo model is not kept


# --- Step 8d: the LLM fallback setting ---


def test_llm_fallback_is_not_offered_for_the_demo() -> None:
    at = run(TO_CHECK)
    assert "not offered for the synthetic demo" in page_text(at)
    assert not [t for t in at.toggle if t.key == "llm_fallback"]


def test_llm_fallback_is_off_by_default_and_sends_only_on_click(
    db: Path, isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import lookahead.llm_categorizer as llm
    import lookahead.llm_fallback as fallback
    from lookahead.categorize import Settings, categorize
    from lookahead.db import connect
    from lookahead.labels import load_rows
    from tests.test_llm_categorizer import FakeClient

    private = isolated / "data" / "private"
    (private / "db").mkdir(parents=True)
    path = Path(shutil.copy(db, private / "db" / "lookahead.db"))
    conn = connect(path)
    rows = load_rows(conn, "scotia-chequing")
    with conn:  # unreviewed new merchants, all flagged
        conn.executemany(
            "DELETE FROM category_labels WHERE fingerprint = ?",
            [(f,) for f in rows.query("split == 'test_b'")["fingerprint"]],
        )
    categorize(conn, "scotia-chequing", Settings(threshold=1.1, synthetic_months=4))
    conn.close()
    monkeypatch.setenv("LOOKAHEAD_PRIVATE_DB", str(path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = FakeClient("Entertainment")
    monkeypatch.setattr(llm, "make_client", lambda *_: client)

    at = AppTest.from_file(str(TO_CHECK), default_timeout=60).run()
    at.sidebar.radio[0].set_value("My data").run()
    assert not at.exception, at.exception
    toggle = at.toggle(key="llm_fallback")
    assert toggle.value is False and client.requests == []  # off by default
    toggle.set_value(True).run()
    assert "would be sent" in page_text(at)
    assert client.requests == []  # turning it on sends nothing
    button(at, "Send ").click().run()
    assert not at.exception, at.exception
    assert client.requests and all(set(r) == {"model", "max_tokens", "system", "messages",
                                              "extra_body"} for r in client.requests)  # fmt: skip
    check = connect(path)
    assert fallback.is_on(check)
    assert (
        check.execute(
            "SELECT COUNT(*) FROM category_predictions WHERE source = 'LLM' AND flagged = 1"
        ).fetchone()[0]
        > 0
    )
    check.close()
    assert (private / "cache" / "llm_categories.json").exists()


# --- Step 3a-iii: the Recurring page ---

RECURRING = APP / "pages" / "4_Recurring.py"


def stored_series(tmp_path: Path, account: str) -> dict:  # type: ignore[type-arg]
    from lookahead.db import connect_readonly
    from lookahead.recurring_store import load_series

    conn = connect_readonly(tmp_path / "demo.db")
    series = {s.name or s.kind: s for s in load_series(conn, account)}
    conn.close()
    return series


def test_recurring_page_lists_detected_series_with_their_evidence(isolated: Path) -> None:
    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    assert not at.exception, at.exception
    table = at.dataframe[0].value
    assert set(table["Status"]) == {"detected"} and len(table) == 6
    assert {"semimonthly", "monthly"} <= set(table["How often"])
    assert "Transfer to my own account" in set(table["Name"])
    assert "estimates" in page_text(at) and "Not financial advice" in page_text(at)
    assert len(at.dataframe[1].value) >= 3  # the payments behind the first series


def test_recurring_page_confirms_rejects_and_adds(isolated: Path) -> None:
    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    first_key = at.selectbox(key="series").value

    def status() -> str:
        from lookahead.db import connect_readonly
        from lookahead.recurring_store import load_series

        conn = connect_readonly(isolated / "demo.db")
        found = {s.key: s.status for s in load_series(conn, "synthetic-B")}
        conn.close()
        return found[first_key]

    button(at, "Confirm").click().run()
    assert not at.exception, at.exception
    after = stored_series(isolated, "synthetic-B")
    assert after and status() == "confirmed"
    assert at.selectbox(key="series").value == first_key  # the selection stays put
    button(at, "Not recurring").click().run()
    assert status() == "rejected"
    assert len(at.dataframe[0].value) == 5  # rejected ones are hidden by default

    at.text_input(key="add_name").set_value("Bursary")
    at.radio(key="add_direction").set_value("Money in")
    at.number_input(key="add_amount").set_value(1200.0)
    button(at, "Add").click().run()
    assert not at.exception, at.exception
    added = stored_series(isolated, "synthetic-B")["Bursary"]
    assert (added.status, added.direction, added.expected_cents) == ("user-added", "in", 120000)


def test_recurring_page_hides_a_persons_name_when_pseudonymizing(isolated: Path) -> None:
    from lookahead.db import connect
    from lookahead.demo import build_demo_db
    from lookahead.recurring_store import add_series, refresh

    build_demo_db(isolated / "demo.db", isolated / "synthetic")
    conn = connect(isolated / "demo.db")
    for month in range(1, 7):  # made-up monthly credit memos from one person
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES ('synthetic-B', ?, 50000, 'credit memo', '1/Jane Doe ', 0, ?, 'f', 'x')",
            (f"2026-{month:02d}-07", f"memo{month}"),
        )
    conn.commit()
    add_series(conn, "synthetic-B", "Rent share", "in", "monthly", date(2026, 7, 1), 40000)
    refresh(conn, "synthetic-B", bytes(range(32)))
    conn.close()
    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    names = set(at.dataframe[0].value["Name"])
    assert "(hidden)" in names and not any("Jane" in str(n) for n in names)
    assert "Rent share" in names  # a name I typed myself stays
    at.sidebar.toggle[0].set_value(False).run()
    assert any("Jane Doe" in str(n) for n in at.dataframe[0].value["Name"])


# --- Recurring page: the edit form follows the series; category; continuation ---


def test_edit_form_shows_the_selected_series_values(isolated: Path) -> None:
    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    options = at.selectbox(key="series").options
    series = stored_series(isolated, "synthetic-B")
    by_label = {str(o): s for o in options for s in series.values()
                if (s.name or "") and (s.name or "") in str(o)}  # fmt: skip
    seen = set()
    for label in list(by_label)[:3]:
        at.selectbox(key="series").select(label).run()
        chosen = next(s for s in series.values()
                      if s.key == at.selectbox(key="series").value)  # fmt: skip
        shown_date = at.date_input(key=f"next_date|{chosen.key}").value
        shown_amount = at.number_input(key=f"amount|{chosen.key}").value
        assert shown_date == chosen.next_expected
        assert shown_amount == abs(chosen.expected_cents) / 100
        seen.add((shown_date, shown_amount))
    assert len(seen) >= 2  # the form really changed with the series


def test_a_confirmed_fixed_series_can_get_a_category(isolated: Path) -> None:
    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    pick = next(o for o in at.selectbox(key="series").options if "Netflix" in str(o))
    at.selectbox(key="series").select(pick).run()
    key = at.selectbox(key="series").value
    assert not [s for s in at.selectbox if s.key == f"series_category|{key}"]  # not confirmed yet
    button(at, "Confirm").click().run()
    at.selectbox(key=f"series_category|{key}").select("Entertainment")
    button(at, "Save category").click().run()
    assert not at.exception, at.exception
    assert stored_series(isolated, "synthetic-B")["Netflix"].category == "Entertainment"
    assert "Entertainment" in set(at.dataframe[0].value["Category"])


# --- Recurring page: estimated categories, data date, saved copy, rent form ---


def test_a_series_shows_an_estimated_category_until_i_choose_one(isolated: Path) -> None:
    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    categories = list(at.dataframe[0].value["Category"])
    assert any(str(c).endswith("(estimate)") for c in categories)
    assert "Data last updated: " in page_text(at)


def test_a_confirmed_series_can_get_a_rent_rule_form_and_a_category(isolated: Path) -> None:
    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    pick = next(o for o in at.selectbox(key="series").options if "Netflix" in str(o))
    at.selectbox(key="series").select(pick).run()
    key = at.selectbox(key="series").value
    assert not [t for t in at.text_input if t.key == f"rent_amounts|{key}"]  # not confirmed yet
    button(at, "Confirm").click().run()
    assert [t for t in at.text_input if t.key == f"rent_amounts|{key}"]


def test_the_page_refreshes_an_older_saved_copy_by_itself(isolated: Path) -> None:
    from lookahead.db import connect
    from lookahead.recurring_store import cache_current

    at = run(RECURRING)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    conn = connect(isolated / "demo.db")
    with conn:  # as if the saved copy came from an older version of the code
        conn.execute("UPDATE recurring_cache_info SET version = -1")
    assert not cache_current(conn, "synthetic-B")
    conn.close()
    run(RECURRING).sidebar.selectbox[0].set_value("synthetic-B").run()
    conn = connect(isolated / "demo.db")
    assert cache_current(conn, "synthetic-B")
    conn.close()


# --- Step 3b-iv: the Forecast page ---

FORECAST = APP / "pages" / "5_Forecast.py"


def test_forecast_page_shows_the_start_the_assumptions_and_the_placed_payments(
    isolated: Path,
) -> None:
    at = run(FORECAST)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    assert not at.exception, at.exception
    text = page_text(at)
    assert "Data last updated:" in text and "the day after the data ends" in text
    assert "Starting point:" in text and "Everyday spending:" in text  # the assumptions
    assert "Bank fees are not forecast yet" in text
    assert "Not financial advice" in text
    assert metric(at, "Balance when the data ends").startswith("$")
    assert metric(at, "Safety buffer") == "$200.00"  # the default buffer
    placed = next(d.value for d in at.dataframe if "Why" in d.value.columns)
    assert list(placed.columns) == ["Date", "Name", "Amount ($)", "Based on", "Why"]
    assert set(placed["Based on"]) <= {"detected", "confirmed", "entered"}


def test_forecast_page_says_so_when_no_income_is_assumed(isolated: Path) -> None:
    from lookahead.db import connect
    from lookahead.recurring_store import ensure_current, reject

    at = run(FORECAST)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    conn = connect(isolated / "demo.db")
    for s in ensure_current(conn, "synthetic-B"):
        if s.direction == "in":
            reject(conn, "synthetic-B", s.key)  # no income series left
    conn.close()
    at = run(FORECAST)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    assert "no regular income found; none assumed" in page_text(at)


def test_the_safety_buffer_can_be_changed_and_is_kept(isolated: Path) -> None:
    at = run(FORECAST)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    at.number_input(key="buffer").set_value(500.0)
    at.sidebar.button[0].click().run()  # "Save buffer"
    assert not at.exception, at.exception
    assert metric(at, "Safety buffer") == "$500.00"
    again = run(FORECAST)
    again.sidebar.selectbox[0].set_value("synthetic-B").run()
    assert metric(again, "Safety buffer") == "$500.00"


def test_forecast_page_can_switch_the_spending_baseline(isolated: Path) -> None:
    at = run(FORECAST)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    assert "average for each weekday" in page_text(at)
    at.sidebar.selectbox[1].set_value("recent").run()
    assert not at.exception, at.exception
    assert "average of the last 28 days" in page_text(at)


def test_forecast_page_shows_what_is_in_everyday_spending(isolated: Path) -> None:
    at = run(FORECAST)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    assert not at.exception, at.exception
    assert "adds these up over the weekdays" in page_text(at)
    tables = [d.value for d in at.dataframe if "Per week" in d.value.columns]
    assert tables and "All types" in set(tables[0]["Type"])


def test_forecast_page_lists_the_big_one_time_events_it_leaves_out(isolated: Path) -> None:
    at = run(FORECAST)
    at.sidebar.selectbox[0].set_value("synthetic-B").run()
    assert not at.exception, at.exception
    text = page_text(at)
    assert "Big one-time rows" in text  # the explanation, whether or not there are any
    events = [
        d.value for d in at.dataframe if list(d.value.columns) == ["Date", "Type", "Amount ($)"]
    ]
    assert events or "No big one-time rows in the last 13 weeks" in text
