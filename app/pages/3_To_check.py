"""To check: categories the model is unsure about, and corrections.

Every row already has a category; this page never blocks anything. A correction is stored
only when you click, and the model learns from it at the next retrain.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `lookahead` importable

import streamlit as st  # noqa: E402

from lookahead.categorize import categorize, load_settings, model_dir_for  # noqa: E402
from lookahead.corrections import (  # noqa: E402
    NO_MERCHANT,
    correct_merchant,
    correct_row,
    merchant_fingerprints,
    to_check,
)
from lookahead.db import connect  # noqa: E402
from lookahead.demo import DEMO_SETTINGS, build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.taxonomy import CATEGORIES  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    HIDDEN,
    demo_db_path,
    display_table,
    private_db_path,
    synthetic_dir,
)
from lookahead.views import accounts, has_categories, transactions  # noqa: E402

st.set_page_config(page_title="To check · Lookahead", layout="wide")
st.title("To check")

# --- Which database ---
source = st.sidebar.radio("Data", ["Synthetic demo", "My data"])
if source == "Synthetic demo":
    db_path = demo_db_path()
    if not db_path.exists() or not demo_has_categories(db_path):
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

conn = connect(db_path)  # this page writes corrections
names = accounts(conn)
if not names:
    st.info("This database has no transactions yet.")
    st.stop()
account = st.sidebar.selectbox("Account", names)
if not has_categories(conn, account):
    st.info(
        "No categories yet for this account. To add them, run:\n\n"
        f"`python -m lookahead.categorize --db {db_path} --account {account}`"
    )
    st.stop()

# --- Retrain ---
settings = DEMO_SETTINGS if source == "Synthetic demo" else load_settings()
if st.sidebar.button(
    "Retrain the model",
    help="Learns from your corrections with the saved settings, then refreshes every "
    "category. Rows held out for evaluation are never used.",
):
    with st.spinner("Retraining..."):
        done = categorize(conn, account, settings, model_dir=model_dir_for(db_path))
    st.session_state["retrained"] = (
        f"Retrained on {done.training_rows} of your rows (before: {done.training_before}). "
        f"{done.changed} rows changed category; rows to check: {done.flagged_before} before, "
        f"{done.flagged} now."
    )
    st.rerun()
if "retrained" in st.session_state:
    st.success(st.session_state.pop("retrained"))

show_all = st.sidebar.toggle(
    "Show every merchant", value=False,
    help="Off: only merchants with a category to check. On: all of them, to correct any row.",
)  # fmt: skip
queue = to_check(conn, account, flagged_only=not show_all)
everything = transactions(conn, account)
total_flagged = int(everything["flagged"].sum())
st.caption(
    f"{total_flagged} of {len(everything)} rows have an estimated category with low "
    "confidence. They still count in every total; checking them is optional."
)
if queue.empty:
    st.success("Nothing to check. Turn on 'Show every merchant' to correct any other row.")
    st.caption(DISCLAIMER)
    st.stop()

shown = display_table(everything, key)  # same rows, as displayed (names hidden if asked)
raw_merchant = everything["merchant"].astype("object").fillna(NO_MERCHANT)
label_of = {NO_MERCHANT: NO_MERCHANT}
for raw, displayed in zip(raw_merchant, shown["Merchant"], strict=True):
    label_of.setdefault(raw, HIDDEN if displayed == HIDDEN else raw)
info = queue.set_index("merchant")


def describe(merchant: str) -> str:
    line = info.loc[merchant]
    return f"{label_of.get(merchant, merchant)} ({line['rows']} rows, {line['to_check']} to check)"


merchant = st.selectbox(
    "Merchant (least confident first)", list(queue["merchant"]), format_func=describe,
    key="merchant",
)  # fmt: skip
if merchant is None:
    st.stop()

mask = (raw_merchant == merchant).to_numpy()
part, part_shown = everything[mask], shown[mask]
st.dataframe(
    part_shown, hide_index=True, width="stretch",
    column_config={
        "Amount ($)": st.column_config.NumberColumn(format="%.2f"),
        "Balance ($)": st.column_config.NumberColumn(format="%.2f"),
    },
)  # fmt: skip

# --- The whole merchant ---
if merchant != NO_MERCHANT:
    covered = merchant_fingerprints(conn, account, merchant)
    if covered:
        with st.form("merchant_form"):
            category = st.selectbox(
                "Category for this merchant", CATEGORIES, index=None,
                placeholder="Choose a category", key="category",
            )  # fmt: skip
            confirm = st.checkbox(f"This changes {len(covered)} rows. I confirm.", key="confirm")
            apply_all = st.form_submit_button("Apply to the merchant")
        if apply_all:
            if category is None:
                st.warning("Choose a category first.")
            elif not confirm:
                st.warning(f"Tick the box to confirm: this changes {len(covered)} rows.")
            else:
                correct_merchant(conn, account, merchant, category)
                st.rerun()
        st.caption("Rows you set one by one keep their own category.")
    else:
        st.caption("A rule decides every row of this merchant; correct single rows below.")

# --- One row ---
with st.expander("Correct a single row", expanded=merchant == NO_MERCHANT):
    choices = list(part["fingerprint"])
    by_fp = {
        f: f"{day} · {amount:.2f} · {kind} · {text} · {current}"
        for f, day, amount, kind, text, current in zip(
            choices, part_shown["Date"], part_shown["Amount ($)"], part_shown["Type"],
            part_shown["Text"], part_shown["Category"], strict=True,
        )
    }  # fmt: skip
    with st.form("row_form"):
        fingerprint = st.selectbox("Row", choices, format_func=lambda f: by_fp[f], key="row")
        row_category = st.selectbox(
            "Category", CATEGORIES, index=None, placeholder="Choose", key="row_category"
        )
        save_row = st.form_submit_button("Save for this row")
    if save_row and fingerprint is not None and row_category is not None:
        try:
            correct_row(conn, account, fingerprint, row_category)
        except ValueError as e:
            st.error(str(e))
        else:
            st.rerun()

st.caption(
    "Corrections show at once. The model learns from them when it is retrained; rows held "
    "out for evaluation change on screen only and are never used for training."
)
st.caption(DISCLAIMER)
