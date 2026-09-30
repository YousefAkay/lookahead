"""Smoke tests for the Streamlit pages, run headlessly with Streamlit's AppTest.

All paths point into a temporary folder: these tests never open data/private/.
"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona

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
