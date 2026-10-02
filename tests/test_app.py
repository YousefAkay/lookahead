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
