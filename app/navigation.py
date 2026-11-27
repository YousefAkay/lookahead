"""The app's sections and pages, in one place: the router (app/Home.py), the top bar and the tab
rows all read this list.

Six sections in the top bar, plus the settings gear: Home, Transactions, Recurring, Forecast, Ask
and How it works. A section with more than one page shows a row of tab links under the bar. Every
page keeps its own file and its own address (the `url_path`), so old links keep working: a page is
never merged into another one's script, because Streamlit runs every st.tabs tab on every rerun
and a tab has no address of its own.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppPage:
    name: str  # what the page passes to top_bar() (also its browser title)
    file: str  # relative to app/
    url_path: str  # the address after the host; "" is Home
    section: str  # the top-bar link it belongs to
    tab: str | None = None  # its label in the section's tab row (None: not in a tab row)


SECTIONS: tuple[str, ...] = (
    "Home", "Transactions", "Recurring", "Forecast", "Ask", "How it works",
)  # fmt: skip

APP_PAGES: tuple[AppPage, ...] = (
    AppPage("Home", "home_page.py", "", "Home"),
    AppPage("Since last time", "pages/8_Since_last_time.py", "Since_last_time", "Home"),
    AppPage("Transactions", "pages/1_Transactions.py", "Transactions", "Transactions", "All"),
    AppPage("To check", "pages/3_To_check.py", "To_check", "Transactions", "To check"),
    AppPage(
        "Label review", "pages/2_Label_review.py", "Label_review", "Transactions", "Label review"
    ),
    AppPage("Recurring", "pages/4_Recurring.py", "Recurring", "Recurring"),
    AppPage("Forecast", "pages/5_Forecast.py", "Forecast", "Forecast", "Forecast"),
    AppPage("Scenario", "pages/9_Scenario.py", "Scenario", "Forecast", "Scenario"),
    AppPage("Budgets", "pages/10_Budgets.py", "Budgets", "Forecast", "Budgets"),
    AppPage("Ask", "pages/11_Ask.py", "Ask", "Ask"),
    AppPage(
        "Model report", "pages/7_Model_report.py", "Model_report", "How it works", "Model report"
    ),
    AppPage(
        "Time machine", "pages/6_Time_machine.py", "Time_machine", "How it works", "Time machine"
    ),
)

BY_NAME = {p.name: p for p in APP_PAGES}


def path_of(page: AppPage) -> str:
    return "/" + page.url_path


def section_links() -> list[tuple[str, str]]:
    """The top-bar links: each section opens on its first page."""
    return [(s, path_of(next(p for p in APP_PAGES if p.section == s))) for s in SECTIONS]


def section_of(name: str) -> str:
    """The section a page belongs to (an unknown name is its own section)."""
    page = BY_NAME.get(name)
    return page.section if page else name


def tabs_of(name: str) -> list[tuple[str, str, bool]]:
    """The tab row of a page's section: (label, address, current), or [] if it has none."""
    page = BY_NAME.get(name)
    if page is None or page.tab is None:
        return []
    return [(p.tab, path_of(p), p.name == name) for p in APP_PAGES
            if p.section == page.section and p.tab is not None]  # fmt: skip


def menu_entries() -> list[tuple[str, str, bool, str]]:
    """The phone menu's list of every page: (label, address, indented, page name). Each section
    first (opening on its first page), then the other pages of its tab row, indented."""
    out = []
    for section in SECTIONS:
        pages = [p for p in APP_PAGES if p.section == section]
        first = pages[0]
        out.append((section, path_of(first), False, first.name))
        out += [(p.tab or p.name, path_of(p), True, p.name) for p in pages[1:]
                if p.tab is not None]  # fmt: skip
    return out
