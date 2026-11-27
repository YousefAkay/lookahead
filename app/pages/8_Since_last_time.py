"""Since last time: what is new in the data since I last clicked "Got it" (week 5, item 4).

Five sections, all counted in lookahead/since.py: the balance change, new merchants, recurring
changes, category spending changes, and alerts opened since. Opening this page records a
recurring-alerts look (as the Recurring page does); only "Got it" moves the marker.
"""

import html
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    account_setting,
    apply,
    choice_query,
    data_settings,
    demo_database,
    fmt_date,
    footer,
    link_button_html,
    money,
    money_short,
    tag_html,
    top_bar,
)
from lookahead.chance import chance_text  # noqa: E402
from lookahead.db import connect  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring_alerts import message, record_look  # noqa: E402
from lookahead.recurring_store import ensure_current  # noqa: E402
from lookahead.since import (  # noqa: E402
    CHANGE_CENTS,
    CHANGE_SHARE,
    PROVISIONAL,
    acknowledge,
    data_date,
    marker,
    summarize,
)
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    HIDDEN,
    display_table,
    hidden_series,
    private_db_path,
    series_label,
)
from lookahead.views import accounts, transactions  # noqa: E402

TITLE = "Since last time"

apply(TITLE)
bar = st.container(key="topbar")  # the green bar: drawn into once the data date is known

# --- Which database ---
source, pseudonymize = data_settings()
if source == "Synthetic demo":
    db_path = demo_database()
else:
    db_path = private_db_path()
    if not db_path.exists():
        top_bar(bar, TITLE, None, source)
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

try:
    stored_key: bytes | None = pseudonym_key()
except PseudonymKeyError:
    stored_key = None
display_key = stored_key if pseudonymize else None
if pseudonymize and stored_key is None:
    st.warning("No PSEUDONYM_KEY: showing raw text.")

conn = connect(db_path)  # this page records a recurring-alerts look and, on "Got it", the marker
names = accounts(conn)
if not names:
    top_bar(bar, TITLE, None, source)
    st.info("This database has no transactions yet.")
    st.stop()
account = account_setting(names)

# The same look the Recurring page records, so alerts are current after an import.
series = ensure_current(conn, account, stored_key)
record_look(conn, account, series)

last = data_date(conn, account)
top_bar(bar, TITLE, last, source)
query = choice_query()
st.markdown(f"<h1>{TITLE}</h1>", unsafe_allow_html=True)


def got_it(label: str) -> None:
    help_text = "Marks everything up to the latest data as seen. Only this button moves it."
    if st.button(label, type="primary", key="got_it", help=help_text):
        acknowledge(conn, account)
        st.rerun()


m = marker(conn, account)
if m is None:
    st.markdown(
        '<p class="la-sub">Nothing to compare yet.</p>'
        '<p class="la-note">Click "Got it" to mark where things stand now. After your next '
        "import, this page shows what changed since then.</p>",
        unsafe_allow_html=True,
    )
    got_it("Got it")
    footer(DISCLAIMER)
    st.stop()

rows = transactions(conn, account)
shown = display_table(rows, display_key)
hidden_rows = (shown["Text"] != rows["text"]) if not rows.empty else None
summary = summarize(conn, account, rows, hidden_rows)
assert summary is not None  # there is a marker and data

st.markdown(
    f'<p class="la-sub">Since you last clicked "Got it": data to '
    f"{fmt_date(summary.marker.data_date)} then, to {fmt_date(summary.data_date)} now.</p>",
    unsafe_allow_html=True,
)
if summary.new_rows == 0:
    st.info("No new data since then. Import a newer export to see what changed.")
if summary.marker.last_row_id is None:
    st.markdown(
        '<p class="la-note">Counted by posted date this time: rows that arrived later with an '
        'older date are not counted. After you click "Got it" again, every row added since is '
        "counted.</p>",
        unsafe_allow_html=True,
    )

# --- 1. Balance ---
with st.container(border=True, key="card-balance"):
    st.markdown("<h2>Balance</h2>", unsafe_allow_html=True)
    then, now = summary.balance_then, summary.balance_now
    if then is not None and now is not None:
        rows_word = "transaction" if summary.new_rows == 1 else "transactions"
        st.markdown(
            f"{money(then, markdown=True)} on {fmt_date(summary.marker.data_date)}, "
            f"{money(now, markdown=True)} on {fmt_date(summary.data_date)}: "
            f"**{money(now - then, signed=True, markdown=True)}**. "
            f"{summary.new_rows:,} new {rows_word}."
        )
    else:
        st.markdown(f"{summary.new_rows:,} new transactions.")

# --- 2. New merchants ---
with st.container(border=True, key="card-merchants"):
    st.markdown("<h2>New merchants</h2>", unsafe_allow_html=True)
    if not summary.merchants:
        st.markdown("None: every merchant since then was seen before.")
    for nm in summary.merchants:
        name = HIDDEN if nm.hidden else nm.merchant
        category = f"{nm.category} (estimate)" if nm.category else "no category yet"
        check = (
            f' · <a href="{html.escape("/To_check" + (f"?{query}" if query else ""))}" '
            f'target="_top">to check</a>'
            if nm.flagged
            else ""
        )
        st.markdown(
            f"<p><strong>{html.escape(name)}</strong> · first seen {fmt_date(nm.first_date)} · "
            f"{nm.rows} {'row' if nm.rows == 1 else 'rows'} · {html.escape(category)}{check}</p>",
            unsafe_allow_html=True,
        )

# --- 3. Recurring changes ---
by_id = {(s.id or s.key): s for s in series}
hidden_keys = hidden_series(rows, series, display_key)
with st.container(border=True, key="card-recurring"):
    st.markdown(
        f'<div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">'
        f"<h2>Recurring changes</h2>{tag_html(PROVISIONAL, 'neutral')}</div>",
        unsafe_allow_html=True,
    )
    if not summary.recurring:
        st.markdown("Nothing new about your recurring payments.")
    for ep in summary.recurring:
        s = by_id.get(ep.series_id)
        label = series_label(s, hide=s.key in hidden_keys) if s else "A recurring payment"
        st.markdown(f"<p>{html.escape(message(ep, label))}</p>", unsafe_allow_html=True)
    st.markdown(link_button_html("Recurring payments", "/Recurring", query), unsafe_allow_html=True)

# --- 4. Category spending ---
with st.container(border=True, key="card-categories"):
    st.markdown(
        f'<div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">'
        f"<h2>Category spending</h2>{tag_html(PROVISIONAL, 'neutral')}</div>"
        f'<p class="la-note">Money out by category since then, against the same number of days '
        f"just before. Shown only if it changed by at least {money_short(CHANGE_CENTS)} and "
        f"{CHANGE_SHARE:.0%}. Categories are estimates unless you labelled them.</p>",
        unsafe_allow_html=True,
    )
    if summary.categories is None:
        st.markdown("Not enough history before then to compare.")
    elif not summary.categories:
        st.markdown("No category changed by that much.")
    for c in summary.categories or []:
        st.markdown(
            f"**{c.category}**: {money(c.before_cents, markdown=True)} before, "
            f"{money(c.after_cents, markdown=True)} since "
            f"({money(c.change_cents, signed=True, markdown=True)})."
        )

# --- 5. Alerts ---
with st.container(border=True, key="card-alerts"):
    st.markdown("<h2>Alerts opened since then</h2>", unsafe_allow_html=True)
    if not summary.shortfall and not summary.recurring:
        st.markdown("No new alerts.")
    for e in summary.shortfall:
        st.markdown(
            f"Possible shortfall (opened {fmt_date(e.opened_on)}): {chance_text(e.probability)} of "
            f"simulated futures dipped below your {money(e.buffer_cents, markdown=True)} buffer; "
            f"the middle future's lowest balance was an estimate of about "
            f"{money(e.low_cents, markdown=True)} around {fmt_date(e.low_day)}."
        )
    if summary.recurring:
        st.markdown(
            f"{len(summary.recurring)} recurring "
            f"{'alert' if len(summary.recurring) == 1 else 'alerts'}: see Recurring changes above."
        )
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; gap: 12px">'
        + link_button_html("Forecast", "/Forecast", query)
        + link_button_html("Recurring payments", "/Recurring", query)
        + "</div>",
        unsafe_allow_html=True,
    )

got_it("Got it: mark these as seen")
footer(DISCLAIMER)
