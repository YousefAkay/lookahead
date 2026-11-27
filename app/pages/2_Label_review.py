"""Label review: confirm the category of every merchant the rules could not decide.

One decision labels all of a merchant's rows; single rows can be overridden; "skip / don't
know" keeps rows out of training and testing. Holdout merchants are shown blind.
The look lives in app/theme.py.
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
    data_settings,
    demo_database,
    footer,
    show_table,
    tag_html,
    top_bar,
)
from lookahead.db import connect  # noqa: E402
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
    private_db_path,
    review_table,
)
from lookahead.views import accounts, last_posted  # noqa: E402

SKIP = "Skip / don't know"

apply("Label review")
bar = st.container(key="topbar")  # the green bar: drawn into once the data date is known

# --- Which database ---
source, pseudonymize = data_settings()
if source == "Synthetic demo":
    db_path = demo_database()
else:
    db_path = private_db_path()
    if not db_path.exists():
        top_bar(bar, "Label review", None, source)
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

key: bytes | None = None
if pseudonymize:
    try:
        key = pseudonym_key()
    except PseudonymKeyError as e:
        st.warning(f"{e}. Showing raw text.")

conn = connect(db_path)  # this page writes labels
names = accounts(conn)
if not names:
    top_bar(bar, "Label review", None, source)
    st.info("This database has no transactions yet.")
    st.stop()
account = account_setting(names)
top_bar(bar, "Label review", last_posted(conn, account), source)

st.markdown(
    "<h1>Label review</h1>"
    '<p class="la-sub">Confirm the category of each merchant the rules could not decide. '
    "One choice labels all of its rows.</p>",
    unsafe_allow_html=True,
)

# --- Splits must be frozen first ---
if conn.execute("SELECT 1 FROM label_split_info WHERE account = ?", (account,)).fetchone() is None:
    st.info(
        "Freeze the evaluation splits once, before labelling anything:\n\n"
        f"`python -m lookahead.labels freeze-splits --db {db_path} --account {account}`\n\n"
        "Then reload this page."
    )
    footer("Labels are stored only when you click.")
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


with st.container(border=True, key="card-merchant"):
    chosen = st.selectbox(
        "Merchant (next in queue first; finished ones at the end)",
        queue,
        format_func=describe,
        key="merchant",
    )
    if chosen is None:
        st.stop()

    # --- The merchant ---
    if chosen.blind:
        hint = tag_html("Holdout", "neutral")
        note = "Holdout merchant: shown blind, no model suggestion."
    elif chosen.suggestion is not None:
        hint = tag_html(f"Suggestion: {chosen.suggestion}", "detected")
        note = f"Suggestion: **{chosen.suggestion}** (model confidence {chosen.confidence:.0%})"
    else:
        hint = ""
        note = "No suggestion yet: label a few development merchants first."
    st.markdown(
        f'<div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">'
        f"<h2>{html.escape(chosen.group)}</h2>{hint}</div>",
        unsafe_allow_html=True,
    )  # the merchant name is bank text: escaped
    st.caption(note)

    rows = load_rows(conn, account)
    part = rows[rows["group"] == chosen.group].sort_values("posted_date", ascending=False)
    labels = current_labels(conn, account)
    show_table(review_table(part, labels, key), money=["Amount ($)"], dates=["Date"],
               money_in=["Amount ($)"])  # fmt: skip

    with st.form("merchant_form", border=False):
        default = None if chosen.suggestion is None else CATEGORIES.index(chosen.suggestion)
        category = st.selectbox("Category for all rows", CATEGORIES, index=default,
                                placeholder="Choose a category", key="category")  # fmt: skip
        label_all = st.form_submit_button(f"Label all {count(len(part), 'row')}", type="primary")
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
        with st.form("row_form", border=False):
            fingerprint = st.selectbox("Row", choices, format_func=lambda f: by_fp[f], key="row")
            row_category = st.selectbox("Category", [*CATEGORIES, SKIP], index=None,
                                        placeholder="Choose", key="row_category")  # fmt: skip
            save_row = st.form_submit_button("Save for this row")
        if save_row and fingerprint is not None and row_category is not None:
            label_row(conn, account, fingerprint, None if row_category == SKIP else row_category)
            st.rerun()

footer(
    "Labels are stored only when you click."
    + (
        " This page shows your real merchant text: do not screenshot it."
        if source == "My data"
        else ""
    )  # fmt: skip
)
