"""Home (opened by the router, app/Home.py): what the app is, where the data stands, what changed
since last time, what needs a look, and buttons to the main pages. The look lives in
app/theme.py.
"""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # make `lookahead` and `app` importable

import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    account_setting,
    apply,
    cached_safe_to_spend,
    choice_query,
    data_settings,
    demo_database,
    fmt_date,
    footer,
    link_button_html,
    money,
    safe_to_spend_card,
    status_card_html,
    top_bar,
)
from lookahead.budget_alerts import home_line  # noqa: E402
from lookahead.db import connect_readonly  # noqa: E402
from lookahead.demo_mode import is_demo  # noqa: E402
from lookahead.needs_a_look import NeedsALook, needs_a_look  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring_store import load_series  # noqa: E402
from lookahead.settings import safety_buffer_cents  # noqa: E402
from lookahead.since import card_counts, summarize  # noqa: E402
from lookahead.ui import DISCLAIMER, private_db_path  # noqa: E402
from lookahead.views import accounts, import_status, transactions  # noqa: E402


def safety_buffer_now(path: Path) -> int:
    """The saved buffer (read-only), so a changed buffer gives a new safe-to-spend."""
    c = connect_readonly(path)
    try:
        return safety_buffer_cents(c)
    finally:
        c.close()


apply("Home")
bar = st.container(key="topbar")  # the green bar: drawn into once the data date is known

source, _ = data_settings()
if source == "Synthetic demo":
    db_path = demo_database()
else:
    db_path = private_db_path()

st.markdown(
    "<h1>Lookahead</h1>"
    '<p class="la-sub">A local-first cashflow planner: it reads your bank exports, forecasts '
    "your balance 30 days ahead, and shows the evidence behind every warning.</p>",
    unsafe_allow_html=True,
)

facts: list[tuple[str, str]] = []
since_facts: list[tuple[str, str]] | None = None  # None: no marker yet
since_note = ""
look: NeedsALook | None = None
last_day = None
account = None
if not db_path.exists():
    top_bar(bar, "Home", None, source)
    st.info(
        f"No database found at `{db_path}`. Import your exports first:\n\n"
        f"`python -m lookahead.ingest data/private/raw --db {db_path}`"
    )
else:
    conn = connect_readonly(db_path)
    names = accounts(conn)
    if not names:
        top_bar(bar, "Home", None, source)
        st.info("This database has no transactions yet.")
    else:
        account = account_setting(names)
        status = import_status(conn, account)
        last_day = status.last_date
        found = [s for s in load_series(conn, account) if s.status != "rejected"]
        confirmed = sum(s.status in ("confirmed", "user-added") for s in found)
        facts = [
            ("Data to", fmt_date(last_day) if last_day else "No data yet"),
            ("Account", account),
            ("Transactions", f"{status.rows:,}"),
            ("Confirmed recurring", f"{confirmed} of {len(found)}" if found else "None found yet"),
        ]
        top_bar(bar, "Home", last_day, source)
        summary = summarize(conn, account, transactions(conn, account))
        if summary is not None:
            since_facts = card_counts(summary)
            if summary.balance_then is not None and summary.balance_now is not None:
                change = money(summary.balance_now - summary.balance_then, signed=True)
                since_note = f"Balance change since then: {change}."
        look = needs_a_look(conn, account)
    conn.close()

if facts:
    with st.container(border=True, key="card-status"):
        st.markdown("<h2>Where things stand</h2>", unsafe_allow_html=True)
        st.markdown(status_card_html(facts), unsafe_allow_html=True)
        if st.session_state.get("data_source") == "Synthetic demo":
            st.markdown(
                '<p class="la-note">This is made-up data from the generator, for trying the app.'
                + ("" if is_demo() else " Choose My data under the gear (settings) for your own.")
                + "</p>",
                unsafe_allow_html=True,
            )

if facts and account is not None and last_day is not None:
    try:
        stored_key: bytes | None = pseudonym_key()
    except PseudonymKeyError:
        stored_key = None
    version = (db_path.stat().st_mtime, safety_buffer_now(db_path))
    safe_to_spend_card(cached_safe_to_spend(str(db_path), version, account, stored_key))

query = choice_query()
if facts:
    with st.container(border=True, key="card-since"):
        st.markdown("<h2>Since last time</h2>", unsafe_allow_html=True)
        if since_facts is None:
            st.markdown(
                '<p class="la-note">Nothing to compare yet. Open the page and click "Got it" to '
                "mark where things stand now.</p>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(status_card_html(since_facts), unsafe_allow_html=True)
            if since_note:
                st.markdown(f'<p class="la-note">{since_note}</p>', unsafe_allow_html=True)
        st.markdown(link_button_html("What changed", "/Since_last_time", query),
                    unsafe_allow_html=True)  # fmt: skip
if look is not None:
    with st.container(border=True, key="card-look"):
        st.markdown("<h2>Needs a look</h2>", unsafe_allow_html=True)
        if not look.anything:
            st.markdown("Nothing needs a look: no alert is open.")
        links = []
        if look.recurring:
            word = "alert" if look.recurring == 1 else "alerts"
            st.markdown(f"{look.recurring} recurring {word} open: a payment you repeat may have "
                        "changed, stopped or not been seen yet.")  # fmt: skip
            links.append(link_button_html("Recurring payments", "/Recurring", query))
        if look.shortfall is not None:
            e = look.shortfall
            st.markdown(
                f"Possible shortfall: {e.probability:.0%} of simulated futures dip below your "
                f"{money(e.buffer_cents, markdown=True)} buffer, lowest around "
                f"{fmt_date(e.low_day)} (an estimate)."
            )
            links.append(link_button_html("See the forecast", "/Forecast", query))
        for b in look.budgets:  # a dollar sign would start maths in markdown
            st.markdown(home_line(b).replace("$", "\\$"))
        if look.budgets:
            links.append(link_button_html("Budgets", "/Budgets", query))
        if links:
            st.markdown('<div style="display: flex; flex-wrap: wrap; gap: 12px">' + "".join(links)
                        + "</div>", unsafe_allow_html=True)  # fmt: skip
st.markdown(
    '<div style="display: flex; flex-wrap: wrap; gap: 12px">'
    + link_button_html("See the forecast", "/Forecast", query, primary=True)
    + link_button_html("Recurring payments", "/Recurring", query)
    + link_button_html("Transactions", "/Transactions", query)
    + link_button_html("To check", "/To_check", query)
    + link_button_html("Label review", "/Label_review", query)
    + "</div>",
    unsafe_allow_html=True,
)
st.markdown(
    '<p class="la-note">'
    + (
        "This public demo shows made-up personas only. "
        if is_demo()
        else "Everything runs on this computer. "
    )
    + "The forecast is an estimate from past weeks, never a promise.</p>",
    unsafe_allow_html=True,
)
footer(DISCLAIMER)
