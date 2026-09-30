"""Transactions page: a read-only view of what the importer stored."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `lookahead` importable

import streamlit as st  # noqa: E402

from lookahead.db import connect_readonly  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    balance_chart,
    daily_balance,
    demo_db_path,
    display_table,
    private_db_path,
    synthetic_dir,
)
from lookahead.views import accounts, import_status, transaction_types, transactions  # noqa: E402

st.set_page_config(page_title="Transactions · Lookahead", layout="wide")
st.title("Transactions")

# --- Which database ---
source = st.sidebar.radio("Data", ["Synthetic demo", "My data"])
if source == "Synthetic demo":
    db_path = demo_db_path()
    if not db_path.exists():
        with st.spinner("Building the synthetic demo database..."):
            build_demo_db(db_path, synthetic_dir())
else:
    db_path = private_db_path()
    if not db_path.exists():
        st.info(
            f"No database found at `{db_path}`. Import your exports first:\n\n"
            f"`python -m lookahead.ingest data/private/raw --db {db_path}`"
        )
        st.caption(DISCLAIMER)
        st.stop()

# --- Pseudonymize (on by default, so screenshots are safe) ---
pseudonymize = st.sidebar.toggle(
    "Pseudonymize text",
    value=True,
    help="Replace names and reference numbers with tokens. Keep this on for screenshots and demos.",
)
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
    st.info("This database has no transactions yet.")
    st.caption(DISCLAIMER)
    st.stop()

# --- Filters ---
account = st.sidebar.selectbox("Account", names)
status = import_status(conn, account)
picked = st.sidebar.date_input(
    "Dates",
    value=(status.first_date, status.last_date),
    min_value=status.first_date,
    max_value=status.last_date,
)
start, end = (picked[0], picked[-1]) if isinstance(picked, tuple) and picked else (None, None)
search = st.sidebar.text_input("Search merchant text")
types = st.sidebar.multiselect("Transaction types", transaction_types(conn, account))

# --- Import status ---
st.caption(
    f"{status.rows} rows stored · {status.first_date} to {status.last_date} · "
    f"last import {status.last_import}"
)
if status.reconciled:
    st.success(f"Balances check out: reconciliation OK ({status.anchors_checked} anchors checked).")
else:
    where = "; ".join(f"between {a} and {b}" for a, b in status.breaks) or "conflicting anchors"
    st.warning(f"Balances do not add up: reconciliation found a problem {where}.")

# --- Balance chart (ignores the search and type filters) ---
in_range = transactions(conn, account, start=start, end=end)
if not in_range.empty:
    st.plotly_chart(balance_chart(daily_balance(in_range)), width="stretch")

# --- Table ---
rows = transactions(conn, account, start=start, end=end, text=search or None, types=types or None)
conn.close()
table = display_table(rows, key)
if hide_text:
    table["Text"] = "(hidden)"
st.markdown(f"**{len(table)} transactions**")
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "Amount ($)": st.column_config.NumberColumn(format="%.2f"),
        "Balance ($)": st.column_config.NumberColumn(format="%.2f"),
    },
)
st.caption(DISCLAIMER)
