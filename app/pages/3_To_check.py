"""To check: categories the model is unsure about, and corrections.

Every row already has a category; this page never blocks anything. A correction is stored
only when you click, and the model learns from it at the next retrain.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `lookahead` importable

import streamlit as st  # noqa: E402

import lookahead.llm_fallback as llm_fallback  # noqa: E402
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
    count,
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
private_dir = db_path.parent.parent  # data/private/ for "My data"
llm_cache = private_dir / "cache" / "llm_categories.json"
try:
    fallback_key: bytes | None = pseudonym_key()  # needed whatever the display toggle says
except PseudonymKeyError:
    fallback_key = None
settings = (
    DEMO_SETTINGS
    if source == "Synthetic demo"
    else load_settings(private_dir / "eval" / "categorizer_choice.json")
)
if st.sidebar.button(
    "Retrain the model",
    help="Learns from your corrections with the saved settings, then refreshes every "
    "category. Rows held out for evaluation are never used.",
):
    with st.spinner("Retraining..."):
        done = categorize(conn, account, settings, model_dir=model_dir_for(db_path))
    reused = ""
    if llm_fallback.is_on(conn) and fallback_key is not None:  # saved answers only
        again = llm_fallback.apply(conn, account, fallback_key, llm_cache)
        reused = f" {count(again.rows, 'row')} now from saved LLM answers again (nothing was sent)."
    st.session_state["retrained"] = (
        f"Retrained on {done.training_rows} of your rows (before: {done.training_before}). "
        f"{count(done.changed or 0, 'row')} changed category; rows to check: "
        f"{done.flagged_before} before, "
        f"{done.flagged} now.{reused}"
    )
    st.rerun()
if "retrained" in st.session_state:
    st.success(st.session_state.pop("retrained"))

# --- LLM fallback: opt-in, off by default ---
st.sidebar.divider()
if source == "Synthetic demo":
    st.sidebar.caption("LLM fallback: not offered for the synthetic demo.")
else:
    on = llm_fallback.is_on(conn)
    has_key = llm_fallback.has_api_key()
    wanted = st.sidebar.toggle(
        "LLM fallback for new merchants",
        value=on,
        disabled=not (on or (has_key and fallback_key is not None)),
        help="Off by default. When on, Claude can suggest a category for new merchants the "
        "model is unsure about. Only the merchant name of a card purchase is sent: never "
        "amounts, dates or people's names. Nothing is sent until you click the send button.",
        key="llm_fallback",
    )
    if not has_key:
        st.sidebar.caption("To use it, add ANTHROPIC_API_KEY to your .env file.")
    elif fallback_key is None:
        st.sidebar.caption("It needs PSEUDONYM_KEY (to decide which names may be sent).")
    if wanted != on:
        llm_fallback.set_on(conn, wanted)
        if not wanted:  # back to the trained model's own answers
            categorize(conn, account, settings, model_dir=model_dir_for(db_path))
        st.rerun()
    if on and fallback_key is not None:
        waiting = llm_fallback.not_asked_yet(conn, account, fallback_key, llm_cache)
        asked = len(llm_fallback.candidates(conn, account, fallback_key)) - len(waiting)
        st.sidebar.caption(
            f"{count(len(waiting), 'merchant name')} would be sent; "
            f"{asked} already answered and saved."
        )
        if waiting:
            with st.sidebar.expander("Names that would be sent (shown only here)"):
                st.write(waiting)
            if st.sidebar.button(
                f"Send {count(len(waiting), 'name')} to Claude", disabled=not has_key
            ):
                try:
                    result = llm_fallback.apply(conn, account, fallback_key, llm_cache, send=True)
                except Exception as e:  # show the kind of error only: a message may quote a name
                    st.session_state["llm_note"] = f"Stopped: {type(e).__name__}. Nothing lost."
                else:
                    st.session_state["llm_note"] = (
                        f"Sent {count(result.sent, 'name')}; "
                        f"{count(result.rows, 'row')} now with an estimated category from "
                        "the LLM. They stay on the list to check."
                    )
                st.rerun()
        elif asked:
            llm_fallback.apply(conn, account, fallback_key, llm_cache)  # saved answers only
    if "llm_note" in st.session_state:
        st.sidebar.info(st.session_state.pop("llm_note"))

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
    rows = count(int(line["rows"]), "row")
    return f"{label_of.get(merchant, merchant)} ({rows}, {line['to_check']} to check)"


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
            confirm = st.checkbox(
                f"This changes {count(len(covered), 'row')}. I confirm.", key="confirm"
            )
            apply_all = st.form_submit_button("Apply to the merchant")
        if apply_all:
            if category is None:
                st.warning("Choose a category first.")
            elif not confirm:
                st.warning(f"Tick the box to confirm: this changes {count(len(covered), 'row')}.")
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
