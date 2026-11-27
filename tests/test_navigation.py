"""The navigation rework: the sections and a settings gear in the top bar, a tab row for the
sections with several pages, every page kept at its own address, and Home's "Needs a look" card.
Made-up data only."""

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import theme
from app.navigation import APP_PAGES, SECTIONS, section_of, tabs_of
from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.needs_a_look import needs_a_look

APP = Path(__file__).resolve().parents[1] / "app"
ROUTER = APP / "Home.py"
OLD_ADDRESSES = {"", "Transactions", "Label_review", "To_check", "Recurring", "Forecast",
                 "Scenario", "Time_machine", "Model_report", "Since_last_time",
                 "Budgets", "Ask"}  # fmt: skip


def links_of(nav_html: str) -> list[tuple[str, bool]]:
    """(label, current) of every link in a piece of HTML."""
    return [(label, "aria-current" in attrs)
            for attrs, label in re.findall(r"<a ([^>]*)>([^<]*)</a>", nav_html)]  # fmt: skip


def main_nav(html: str) -> str:
    return html[html.index('class="la-nav"') : html.index("</nav>")]


# --- The top bar ---


@pytest.mark.parametrize(("page", "section"), [
    ("Home", "Home"), ("Since last time", "Home"), ("Transactions", "Transactions"),
    ("To check", "Transactions"), ("Label review", "Transactions"), ("Recurring", "Recurring"),
    ("Forecast", "Forecast"), ("Scenario", "Forecast"), ("Budgets", "Forecast"), ("Ask", "Ask"),
    ("Model report", "How it works"),
    ("Time machine", "How it works"),
])  # fmt: skip
def test_the_bar_has_the_sections_in_order_with_the_current_one_marked(
    page: str, section: str
) -> None:
    html = theme.top_bar_html(page, theme.PAGES, date(2026, 10, 1), "Synthetic demo")
    links = links_of(main_nav(html))
    assert [label for label, _ in links] == list(SECTIONS)
    assert [label for label, current in links if current] == [section]


def test_settings_is_a_gear_not_a_word() -> None:
    """The gear is Streamlit's popover button, its name ("Settings") kept for screen readers but
    drawn as the gear character."""
    rule = theme.CSS[theme.CSS.index(".st-key-la-gear button::after") :].split("}", 1)[0]
    assert '"\\2699\\FE0E"' in rule  # the gear character, text style
    hidden = theme.CSS[theme.CSS.index(".st-key-la-gear button > *") :].split("}", 1)[0]
    assert "clip-path: inset(50%)" in hidden and "display: none" not in hidden  # still read aloud
    assert theme.SETTINGS == "Settings"
    assert "url(" not in theme.CSS  # still nothing fetched


@pytest.mark.parametrize(("page", "tabs"), [
    ("Transactions", [("All", True), ("To check", False), ("Label review", False)]),
    ("To check", [("All", False), ("To check", True), ("Label review", False)]),
    ("Label review", [("All", False), ("To check", False), ("Label review", True)]),
    ("Forecast", [("Forecast", True), ("Scenario", False), ("Budgets", False)]),
    ("Scenario", [("Forecast", False), ("Scenario", True), ("Budgets", False)]),
    ("Budgets", [("Forecast", False), ("Scenario", False), ("Budgets", True)]),
    ("Model report", [("Model report", True), ("Time machine", False)]),
    ("Time machine", [("Model report", False), ("Time machine", True)]),
])  # fmt: skip
def test_a_section_with_several_pages_has_a_tab_row_with_the_current_one_marked(
    page: str, tabs: list[tuple[str, bool]]
) -> None:
    assert [(label, current) for label, _, current in tabs_of(page)] == tabs
    html = theme.section_tabs_html(page, "data=Synthetic+demo")
    assert links_of(html) == tabs
    assert "?data=Synthetic+demo" in html  # the data and account choice follow the links


@pytest.mark.parametrize("page", ["Home", "Since last time", "Recurring"])
def test_a_section_with_one_page_has_no_tab_row(page: str) -> None:
    assert tabs_of(page) == [] and theme.section_tabs_html(page) == ""


# --- Every page kept, at its old address ---


def test_every_old_address_still_has_its_page() -> None:
    assert {p.url_path for p in APP_PAGES} == OLD_ADDRESSES
    assert len({p.url_path for p in APP_PAGES}) == len(APP_PAGES)  # no address used twice


def test_every_page_file_belongs_to_exactly_one_section() -> None:
    files = {f"pages/{f.name}" for f in (APP / "pages").glob("*.py")} | {"home_page.py"}
    listed = [p.file for p in APP_PAGES]
    assert sorted(listed) == sorted(files)  # every page listed once, no page lost
    assert all((APP / f).exists() for f in listed)
    assert {p.section for p in APP_PAGES} == set(SECTIONS)  # every section has a page
    assert section_of("Scenario") == "Forecast"


@pytest.fixture
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
    return "\n".join(str(e.value) for e in (*at.markdown, *at.info, *at.caption))


@pytest.mark.slow
@pytest.mark.parametrize("page", [p for p in APP_PAGES if p.url_path], ids=lambda p: p.url_path)
def test_the_router_opens_every_page(isolated: Path, page) -> None:  # type: ignore[no-untyped-def]
    at = AppTest.from_file(str(ROUTER), default_timeout=300).run()
    assert not at.exception, at.exception
    at.switch_page(page.file).run()
    assert not at.exception, at.exception
    bar = next(str(m.value) for m in at.markdown if 'class="la-nav"' in str(m.value))
    assert [label for label, current in links_of(main_nav(bar)) if current] == [page.section]
    if page.tab is not None:  # this very page is the marked tab
        tabs = bar[bar.index('class="la-tabs"') :]
        assert [label for label, current in links_of(tabs) if current] == [page.tab]


# --- Home: Needs a look (stored alerts only; nothing is run) ---


def add_alerts(conn: sqlite3.Connection, account: str) -> None:
    """One open recurring alert and one open shortfall alert, made up."""
    with conn:
        recurring = conn.execute(
            "INSERT INTO recurring_alert_episodes (account, series_id, kind, opened_on,"
            " last_seen_on) VALUES (?, 'made-up', 'not_seen', '2026-09-30', '2026-09-30')",
            (account,),
        ).lastrowid
        conn.execute(
            "INSERT INTO recurring_alert_events (episode_id, account, data_date, level,"
            " amount_cents, notified, reason, evidence)"
            " VALUES (?, ?, '2026-09-30', 1, NULL, 1, 'made up', ?)",
            (recurring, account, json.dumps({"due": ["2026-09-25"]})),
        )
        episode = conn.execute(
            "INSERT INTO alert_episodes (account, opened_on, last_seen_on)"
            " VALUES (?, '2026-09-30', '2026-09-30')", (account,),
        ).lastrowid  # fmt: skip
        conn.execute(
            "INSERT INTO alert_events (episode_id, account, data_date, low_day, low_cents,"
            " median_lowest_cents, probability, buffer_cents, notified, reason, evidence)"
            " VALUES (?, ?, '2026-09-30', '2026-10-08', 12632, 12632, 0.97, 50000, 1, 'made up',"
            " ?)",
            (episode, account, json.dumps({})),
        )  # fmt: skip


def test_needs_a_look_counts_the_stored_alerts(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    assert not needs_a_look(conn, "acct").anything
    add_alerts(conn, "acct")
    look = needs_a_look(conn, "acct")
    assert look.recurring == 1 and look.shortfall is not None
    assert look.shortfall.low_day == date(2026, 10, 8)
    conn.close()


def test_needs_a_look_on_a_database_from_before_the_alert_tables(tmp_path: Path) -> None:
    old = sqlite3.connect(tmp_path / "old.db")
    look = needs_a_look(old, "acct")
    assert (look.recurring, look.shortfall) == (0, None)
    old.close()


@pytest.mark.slow
def test_home_shows_needs_a_look_without_running_the_forecast(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lookahead.forecast.path as forecast_path

    def no_forecast(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Home must not run the forecast")

    monkeypatch.setattr(forecast_path, "build_forecast", no_forecast)
    at = AppTest.from_file(str(ROUTER), default_timeout=300).run()
    assert not at.exception, at.exception
    assert "Nothing needs a look" in text_of(at)
    account = at.selectbox[0].value
    conn = connect(isolated / "demo.db")
    add_alerts(conn, account)
    conn.close()
    at = AppTest.from_file(str(ROUTER), default_timeout=300).run()
    assert not at.exception, at.exception
    text = text_of(at)
    assert "Needs a look" in text and "1 recurring alert open" in text
    assert "Possible shortfall: 97%" in text


# --- The demo click path (docs/DEMO.md), on made-up data ---


@pytest.mark.slow
def test_the_demo_click_path_works(isolated: Path) -> None:
    at = AppTest.from_file(str(ROUTER), default_timeout=300).run()
    at.selectbox[0].set_value("synthetic-C").run()
    assert not at.exception, at.exception
    assert "synthetic-C" in text_of(at)
    at.switch_page("pages/1_Transactions.py").run()
    assert not at.exception, at.exception
    at.switch_page("pages/4_Recurring.py").run()
    assert not at.exception, at.exception
    (confirm,) = [b for b in at.button if b.label == "Looks right, confirm all"]
    confirm.click().run()
    assert not at.exception, at.exception
    assert "nothing left to review" in text_of(at)
    at.switch_page("pages/5_Forecast.py").run()
    assert not at.exception, at.exception
    at.switch_page("pages/9_Scenario.py").run()
    assert not at.exception, at.exception
    assert "You'd have" in text_of(at)
    at.switch_page("pages/7_Model_report.py").run()
    assert not at.exception, at.exception


# --- Narrow screens ---


def test_on_a_phone_a_menu_lists_every_page_and_nothing_scrolls_sideways() -> None:
    narrow = theme.CSS[theme.CSS.index("@media (max-width: 760px)") :]
    assert ".la-nav, .la-tabs { display: none; }" in narrow
    assert ".la-menu { display: flex; }" in narrow
    assert "overflow-x: auto" not in theme.CSS  # no row of the bar scrolls sideways
    html = theme.top_bar_html("Scenario", theme.PAGES, None, "Synthetic demo", "data=x")
    menu = html[html.index('class="la-menu-list"') :]
    for page in APP_PAGES:
        if page.tab is not None or page.url_path in ("", "Recurring", "Ask"):
            assert f'href="{page.url_path or "./"}?data=x"' in menu, page.name
    assert 'aria-current="page">Scenario<' in menu


def test_on_the_hosted_app_cloud_s_toolbar_has_its_own_strip_above_the_bar() -> None:
    """Community Cloud puts its own buttons (Fork, GitHub; Share, star, edit, Stop for the owner)
    in Streamlit's header, at a width our code cannot know: they get a row of their own."""
    css = theme.CSS
    assert ".stApp { --la-strip: 0px; }" in css
    assert '.stApp:has([data-testid="stToolbarActions"] > *) { --la-strip: 44px; }' in css
    assert "margin-top: var(--la-strip)" in css  # the page scrolls below the strip
    assert "top: calc(var(--la-strip) + 10px)" in css  # the gear sits in the bar, not the strip
    assert "--la-room" not in css


@pytest.mark.slow
def test_once_the_forecast_is_there_its_waiting_message_is_gone(isolated: Path) -> None:
    at = AppTest.from_file(str(ROUTER), default_timeout=300).run()
    at.switch_page("pages/5_Forecast.py").run()
    assert not at.exception, at.exception
    assert not [i for i in at.info if "Simulating" in i.value]  # shown while it works, then gone
    assert at.metric  # the forecast itself is there
