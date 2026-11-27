"""Transactions page: a read-only view of what the importer stored.

Search and filters sit on top, the table is trimmed to date, merchant, category, amount and
balance, and every other column is behind "Details". The look lives in app/theme.py.
"""

import hashlib
import sys
from datetime import date
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    account_setting,
    apply,
    data_settings,
    demo_database,
    fmt_date,
    footer,
    style_balance_chart,
    tag_html,
    top_bar,
    transactions_table_html,
)
from lookahead.db import connect, connect_readonly  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring_store import cache_current, ensure_current  # noqa: E402
from lookahead.taxonomy import CATEGORIES  # noqa: E402
from lookahead.ui import (  # noqa: E402
    BLANK,
    DISCLAIMER,
    balance_chart,
    daily_balance,
    display_table,
    private_db_path,
)
from lookahead.views import (  # noqa: E402
    accounts,
    has_categories,
    import_status,
    transaction_types,
    transactions,
)

PAGE_ROWS = 50  # rows shown at first, and each time "Show more" is pressed

apply("Transactions")
bar = st.container(key="topbar")  # the green bar: drawn into once the data date is known

# --- Which database ---
source, pseudonymize = data_settings()
if source == "Synthetic demo":
    db_path = demo_database()
else:
    db_path = private_db_path()
    if not db_path.exists():
        top_bar(bar, "Transactions", None, source)
        st.info(
            f"No database found at `{db_path}`. Import your exports first:\n\n"
            f"`python -m lookahead.ingest data/private/raw --db {db_path}`"
        )
        footer(DISCLAIMER)
        st.stop()

# --- Pseudonymize (on by default, so screenshots are safe) ---
key: bytes | None = None
hide_text = False
if pseudonymize:
    try:
        key = pseudonym_key()
    except PseudonymKeyError as e:
        st.warning(f"{e}. Merchant text is hidden until a key exists.")
        hide_text = True

conn = connect_readonly(db_path)
names = accounts(conn)
if not names:
    top_bar(bar, "Transactions", None, source)
    st.info("This database has no transactions yet.")
    footer(DISCLAIMER)
    st.stop()

account = account_setting(names)
if not cache_current(conn, account):  # the saved series are from older code or older data
    try:
        series_key: bytes | None = pseudonym_key()
    except PseudonymKeyError:
        series_key = None
    writer = connect(db_path)  # this page only reads, except to rebuild that saved copy
    ensure_current(writer, account, series_key)
    writer.close()
status = import_status(conn, account)
top_bar(bar, "Transactions", status.last_date, source)
categorized = has_categories(conn, account)

# --- Title row ---
imported = fmt_date(date.fromisoformat(status.last_import[:10]))
badge = (
    tag_html("Balances check out", "confirmed")
    if status.reconciled
    else tag_html("Balances do not add up", "neutral")
)
st.markdown(
    f'<div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">'
    f"<h1>Transactions</h1>{badge}</div>"
    f'<p class="la-sub">{status.rows:,} rows stored · {fmt_date(status.first_date)} to '
    f"{fmt_date(status.last_date)} · last import {imported}</p>",
    unsafe_allow_html=True,
)
if status.reconciled:
    st.markdown(
        f'<p class="la-note">Balances check out: reconciliation OK '
        f"({status.anchors_checked} anchors checked).</p>",
        unsafe_allow_html=True,
    )
else:
    where = "; ".join(f"between {a} and {b}" for a, b in status.breaks) or "conflicting anchors"
    st.warning(f"Balances do not add up: reconciliation found a problem {where}.")

# --- Search and filters, on top ---
with st.container(border=True, key="card-filters"):
    first = st.columns([2, 2, 2])
    search = first[0].text_input("Search merchant text", key="search")
    picked = first[1].date_input(
        "Dates", value=(status.first_date, status.last_date), min_value=status.first_date,
        max_value=status.last_date, key="dates",
    )  # fmt: skip
    types = first[2].multiselect("Transaction types", transaction_types(conn, account),
                                 key="types")  # fmt: skip
    categories: list[str] = []
    flagged_only = False
    if categorized:
        second = st.columns([4, 2], vertical_alignment="bottom")
        categories = second[0].multiselect("Categories", CATEGORIES, key="categories")
        flagged_only = second[1].toggle(
            "Only rows to check", value=False, key="flagged_only",
            help="Rows whose category the model is unsure about. They still have a category.",
        )  # fmt: skip
start, end = (picked[0], picked[-1]) if isinstance(picked, tuple) and picked else (None, None)

# --- Balance chart (ignores the search and type filters) ---
in_range = transactions(conn, account, start=start, end=end)
if not in_range.empty:
    with st.container(border=True, key="card-balance"):
        st.markdown("<h2>Balance</h2>", unsafe_allow_html=True)
        figure = style_balance_chart(balance_chart(daily_balance(in_range)))
        figure.update_layout(title=None, height=280)
        st.plotly_chart(figure, width="stretch", theme=None, config={"displayModeBar": False})

# --- Table ---
rows = transactions(
    conn, account, start=start, end=end, text=search or None, types=types or None,
    categories=categories or None, flagged_only=flagged_only,
)  # fmt: skip
conn.close()
table = display_table(rows, key)
if hide_text:
    table["Text"] = "(hidden)"
to_check = int(rows["flagged"].sum())

# "Show more" restarts at the first page whenever a filter changes.
fingerprint = hashlib.sha1(  # noqa: S324 (a change detector, not security)
    repr((account, search, start, end, types, categories, flagged_only)).encode()
).hexdigest()
if st.session_state.get("txn_filters") != fingerprint:
    st.session_state["txn_filters"] = fingerprint
    st.session_state["txn_shown"] = PAGE_ROWS
shown = min(st.session_state.get("txn_shown", PAGE_ROWS), len(table))

with st.container(border=True, key="card-txns"):
    note = f" · {to_check} to check (estimated categories, low confidence)" if categorized else ""
    st.markdown(f"<h2>{len(table):,} transactions</h2>", unsafe_allow_html=True)
    if note:
        st.markdown(f'<p class="la-note">{note[3:]}</p>', unsafe_allow_html=True)
    if not categorized:
        st.info(
            "No categories yet for this account. To add them, run:\n\n"
            f"`python -m lookahead.categorize --db {db_path} --account {account}`"
        )
    elif source == "Synthetic demo":
        st.markdown(
            '<p class="la-note">Demo categories come from a model trained on the generator\'s own '
            "answer keys, so they are nearly always right here. That says nothing about real "
            "accuracy.</p>",
            unsafe_allow_html=True,
        )

    def merchant_of(i: int) -> str:
        merchant = table["Merchant"].iloc[i] if "Merchant" in table else BLANK
        return table["Text"].iloc[i] if merchant == BLANK else merchant

    category_of = table["Category"] if "Category" in table else None
    flags = rows["flagged"].tolist() if categorized else [False] * len(rows)
    html_rows = []
    for i in range(shown):
        category = (
            None if category_of is None or category_of.iloc[i] == BLANK else category_of.iloc[i]
        )
        balance = rows["balance_cents"].iloc[i]
        html_rows.append((
            fmt_date(rows["date"].iloc[i].date()), merchant_of(i), category,
            int(rows["amount_cents"].iloc[i]),
            None if balance != balance else int(balance), bool(flags[i]),
        ))  # fmt: skip
    st.markdown(transactions_table_html(html_rows), unsafe_allow_html=True)
    if shown < len(table):
        st.markdown(
            f'<p class="la-note">Showing {shown:,} of {len(table):,}.</p>', unsafe_allow_html=True
        )
        if st.button(f"Show {min(PAGE_ROWS, len(table) - shown)} more", key="show_more"):
            st.session_state["txn_shown"] = shown + PAGE_ROWS
            st.rerun()

    with st.expander("Details: every column, all rows"):
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={
                # Wide enough for the bank's 31-character text; double-click a cell for all of it.
                "Text": st.column_config.TextColumn(width="large"),
                "Merchant": st.column_config.TextColumn(width="medium"),
                "Amount ($)": st.column_config.NumberColumn(format="%.2f"),
                "Balance ($)": st.column_config.NumberColumn(format="%.2f"),
            },
        )
footer(DISCLAIMER)
