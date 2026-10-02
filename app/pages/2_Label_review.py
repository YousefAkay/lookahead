"""Label review: confirm the category of every merchant the rules could not decide.

One decision labels all of a merchant's rows; single rows can be overridden; "skip / don't
know" keeps rows out of training and testing. Holdout merchants are shown blind.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `lookahead` importable

import streamlit as st  # noqa: E402

from lookahead.db import connect  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.labels import (  # noqa: E402
    current_labels,
    label_merchant,
    label_row,
    load_rows,
    progress,
    review_queue,
    suggestion_model,
)
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.taxonomy import CATEGORIES  # noqa: E402
from lookahead.ui import (  # noqa: E402
    count,
    demo_db_path,
    private_db_path,
    review_table,
    synthetic_dir,
)
from lookahead.views import accounts  # noqa: E402

SKIP = "Skip / don't know"

st.set_page_config(page_title="Label review · Lookahead", layout="wide")
st.title("Label review")

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
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

pseudonymize = st.sidebar.toggle(
    "Pseudonymize text", value=True,
    help="Hides people's names and reference numbers; business names stay readable.",
)  # fmt: skip
key: bytes | None = None
if pseudonymize:
    try:
        key = pseudonym_key()
    except PseudonymKeyError as e:
        st.warning(f"{e}. Showing raw text.")

conn = connect(db_path)  # this page writes labels
names = accounts(conn)
if not names:
    st.info("This database has no transactions yet.")
    st.stop()
account = st.sidebar.selectbox("Account", names)

# --- Splits must be frozen first ---
if conn.execute("SELECT 1 FROM label_split_info WHERE account = ?", (account,)).fetchone() is None:
    st.info(
        "Freeze the evaluation splits once, before labelling anything:\n\n"
        f"`python -m lookahead.labels freeze-splits --db {db_path} --account {account}`\n\n"
        "Then reload this page."
    )
    st.stop()

# --- Progress ---
p = progress(conn, account)
cols = st.columns(4)
cols[0].metric("Merchants done", f"{p.merchants_done} / {p.merchants_total}")
cols[1].metric("Rows covered", f"{p.rows_covered} / {p.rows_total}")
cols[2].metric("Labelled rows", p.labelled)
cols[3].metric("Skipped rows", p.skipped)

model = suggestion_model(conn, account)
queue = review_queue(conn, account, model)
open_tasks = [t for t in queue if not t.done]
if not open_tasks:
    st.success("Every merchant has been reviewed. You can still revisit one below.")


def describe(t) -> str:  # type: ignore[no-untyped-def]
    return f"{'✓ ' if t.done else ''}{t.group} ({count(t.rows, 'row')})"


chosen = st.selectbox(
    "Merchant (next in queue first; finished ones at the end)",
    queue,
    format_func=describe,
    key="merchant",
)
if chosen is None:
    st.stop()

# --- The merchant ---
st.subheader(chosen.group)
if chosen.blind:
    st.caption("Holdout merchant: shown blind, no model suggestion.")
elif chosen.suggestion is not None:
    st.caption(f"Suggestion: **{chosen.suggestion}** (model confidence {chosen.confidence:.0%})")
else:
    st.caption("No suggestion yet: label a few development merchants first.")

rows = load_rows(conn, account)
part = rows[rows["group"] == chosen.group].sort_values("posted_date", ascending=False)
labels = current_labels(conn, account)
st.dataframe(
    review_table(part, labels, key), hide_index=True, width="stretch",
    column_config={"Amount ($)": st.column_config.NumberColumn(format="%.2f")},
)  # fmt: skip

with st.form("merchant_form"):
    default = None if chosen.suggestion is None else CATEGORIES.index(chosen.suggestion)
    category = st.selectbox("Category for all rows", CATEGORIES, index=default,
                            placeholder="Choose a category", key="category")  # fmt: skip
    label_all = st.form_submit_button(f"Label all {count(len(part), 'row')}")
    skip_all = st.form_submit_button(SKIP)
if label_all:
    if category is None:
        st.warning("Choose a category first.")
    else:
        label_merchant(conn, account, chosen.group, category)
        st.rerun()
if skip_all:
    label_merchant(conn, account, chosen.group, None)
    st.rerun()

with st.expander("Override a single row (e.g. Amazon Prime among Amazon purchases)"):
    choices = list(part["fingerprint"])
    shown = review_table(part, labels, key)
    by_fp = {
        f: f"{day} · {amount:.2f} · {text}"
        for f, day, amount, text in zip(
            choices, shown["Date"], shown["Amount ($)"], shown["Text"], strict=True
        )
    }
    with st.form("row_form"):
        fingerprint = st.selectbox("Row", choices, format_func=lambda f: by_fp[f], key="row")
        row_category = st.selectbox("Category", [*CATEGORIES, SKIP], index=None,
                                    placeholder="Choose", key="row_category")  # fmt: skip
        save_row = st.form_submit_button("Save for this row")
    if save_row and fingerprint is not None and row_category is not None:
        label_row(conn, account, fingerprint, None if row_category == SKIP else row_category)
        st.rerun()

st.caption("Labels are stored only when you click. This page shows your real merchant text "
           "when 'My data' is selected: do not screenshot it.")  # fmt: skip
