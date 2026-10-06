"""Recurring: payments and income that repeat, found by rules, for you to confirm or fix.

Detected series are assumptions until you confirm them. Nothing here blocks anything: a
series you never look at is still used as "detected" in the forecast, and says so.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `lookahead` importable

import streamlit as st  # noqa: E402

from lookahead.db import connect  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring import CADENCES  # noqa: E402
from lookahead.recurring_store import (  # noqa: E402
    add_series,
    confirm,
    edit,
    end,
    link_continuation,
    load_series,
    refresh,
    reject,
    rent_rule_of,
    set_category,
    set_name,
    set_rent_rule,
)
from lookahead.rent import RentRule, parse_dollars, rent_cents  # noqa: E402
from lookahead.taxonomy import CATEGORIES  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    demo_db_path,
    display_table,
    private_db_path,
    recurring_table,
    series_label,
    synthetic_dir,
)
from lookahead.views import accounts, transactions  # noqa: E402

OWN_CATEGORY = "Each payment's own category"

st.set_page_config(page_title="Recurring · Lookahead", layout="wide")
st.title("Recurring")

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
try:
    stored_key: bytes | None = pseudonym_key()
except PseudonymKeyError:
    stored_key = None
display_key = stored_key if pseudonymize else None
if pseudonymize and stored_key is None:
    st.warning("No PSEUDONYM_KEY: showing raw text.")

conn = connect(db_path)  # this page writes your decisions
names = accounts(conn)
if not names:
    st.info("This database has no transactions yet.")
    st.stop()
account = st.sidebar.selectbox("Account", names)

if st.sidebar.button("Detect again", help="Run the detection on the stored transactions."):
    refresh(conn, account, stored_key)
    st.rerun()
series = load_series(conn, account) or refresh(conn, account, stored_key)
show_rejected = st.sidebar.toggle("Show the ones I rejected", value=False)

rows = transactions(conn, account)
shown_rows = display_table(rows, display_key)
# A series whose payments show a replaced name (a person) keeps its own name hidden too.
changed = set(rows.loc[(shown_rows["Text"] != rows["text"]).to_numpy(), "fingerprint"])
hidden = {
    s.key for s in series
    if display_key is not None and changed & set(s.evidence) and not s.named_by_me
}  # fmt: skip

listed = [s for s in series if show_rejected or s.status != "rejected"]
waiting = sum(s.status == "detected" for s in listed)
st.caption(
    f"{len(listed)} series · {waiting} detected and not "
    "confirmed yet. Expected dates and amounts are estimates from past payments."
)
if listed:
    st.dataframe(
        recurring_table(listed, hidden), hide_index=True, width="stretch",
        column_config={
            "Expected ($)": st.column_config.NumberColumn(format="%.2f"),
            "Usual low ($)": st.column_config.NumberColumn(format="%.2f"),
            "Usual high ($)": st.column_config.NumberColumn(format="%.2f"),
        },
    )  # fmt: skip
else:
    st.info("No recurring series found yet. You can add one by hand below.")

# --- One series: its evidence, and what you can change ---
if listed:
    # The options are the series keys, not the series themselves: a key stays the same
    # when the series changes (confirmed, edited), so the selection stays where it was.
    by_key = {s.key: s for s in listed}
    chosen_key = st.selectbox(
        "Series", list(by_key), key="series",
        format_func=lambda k: series_label(by_key[k], k in hidden),
    )  # fmt: skip
    chosen = by_key.get(chosen_key)
    if chosen is not None:
        evidence = rows["fingerprint"].isin(chosen.evidence).to_numpy()
        if evidence.any():
            st.markdown(f"**Payments found: {int(evidence.sum())}**")
            st.dataframe(
                shown_rows[evidence], hide_index=True, width="stretch",
                column_config={
                    "Text": st.column_config.TextColumn(width="large"),
                    "Merchant": st.column_config.TextColumn(width="medium"),
                    "Amount ($)": st.column_config.NumberColumn(format="%.2f"),
                    "Balance ($)": st.column_config.NumberColumn(format="%.2f"),
                },
            )  # fmt: skip
        else:
            st.caption("No payments in the data yet for this series.")
        rule = rent_rule_of(chosen)
        if rule is not None:
            amounts = " or ".join(f"{a / 100:.2f}" for a in rule.amounts_cents)
            st.caption(
                f"Rent rule: {amounts} dollars, sent between day {rule.from_day} of a month "
                f"and day {rule.to_day} of the next. Rent is assumed at the larger amount "
                f"({rent_cents(rule) / 100:.2f}), somewhere in that window. Any payment of "
                "an allowed amount counts as rent, on any day; the window only places the "
                "next one."
            )
        elif chosen.loose and chosen.offsets:
            spread = sorted(abs(d) for d in chosen.offsets)
            st.caption(
                "A candidate: same amount, about once a month, on varying days. Nothing uses "
                "it until you confirm it. Once confirmed it is expected around day "
                f"{chosen.anchor_day} of the month; past payments were a median of "
                f"{spread[len(spread) // 2]} days from that date, at most {spread[-1]}, and "
                "the forecast lets the date vary by that much."
            )
        if chosen.previous_cents is not None:
            st.caption(
                f"The amount changed once: {abs(chosen.previous_cents) / 100:.2f} before, "
                f"{abs(chosen.expected_cents) / 100:.2f} now."
            )
        cols = st.columns(3)
        if cols[0].button("Confirm", disabled=chosen.status == "confirmed"):
            confirm(conn, account, chosen)
            refresh(conn, account, stored_key)
            st.rerun()
        if cols[1].button("Not recurring", disabled=chosen.status == "rejected"):
            reject(conn, account, chosen.key)
            refresh(conn, account, stored_key)
            st.rerun()
        if cols[2].button("Mark as ended", disabled=not chosen.active):
            end(conn, account, chosen.key)
            refresh(conn, account, stored_key)
            st.rerun()
        with st.form("edit_form"):
            st.markdown("**Change the next expected payment**")
            # Keyed by the series, so switching series shows that series' own values.
            next_date = st.date_input("Next expected date", value=chosen.next_expected,
                                      key=f"next_date|{chosen.key}")  # fmt: skip
            amount = st.number_input(
                "Expected amount ($)", min_value=0.0, step=1.0,
                value=abs(chosen.expected_cents) / 100, key=f"amount|{chosen.key}",
            )  # fmt: skip
            save = st.form_submit_button("Save")
        if save and isinstance(next_date, date):
            sign = 1 if chosen.direction == "in" else -1
            cents = sign * round(amount * 100)  # dollars only on screen; cents are stored
            new_date = next_date if next_date != chosen.next_expected else None
            new_cents = cents if cents != chosen.expected_cents else None
            if new_date is not None or new_cents is not None:
                edit(conn, account, chosen.key, next_date=new_date, amount_cents=new_cents)
                refresh(conn, account, stored_key)
                st.rerun()
        st.caption(
            "Your date or amount is used until the next payment posts; then the detected "
            "values take over again."
        )
        if chosen.status == "confirmed" and chosen.amount_type == "fixed":
            with st.form("category_form"):
                st.markdown("**Category of this series**")
                options = [OWN_CATEGORY, *CATEGORIES]
                current = options.index(chosen.category) if chosen.category else 0
                picked = st.selectbox(
                    "Every payment in the series gets it; your other purchases at the same "
                    "merchant keep their own.",
                    options, index=current, key=f"series_category|{chosen.key}",
                )  # fmt: skip
                save_category = st.form_submit_button("Save category")
            if save_category:
                set_category(conn, account, chosen.key,
                             None if picked == OWN_CATEGORY else picked)  # fmt: skip
                refresh(conn, account, stored_key)
                st.rerun()
        with st.form("name_form"):
            new_name = st.text_input(
                "Your own name for this series (the label only; empty gives the detected "
                "name back)", value=chosen.name if chosen.named_by_me else "",
                key=f"name|{chosen.key}",
            )  # fmt: skip
            save_name = st.form_submit_button("Save name")
        if save_name:
            try:
                set_name(conn, account, chosen.key, new_name)
            except ValueError as problem:
                st.warning(f"Could not save: {problem}")
            else:
                refresh(conn, account, stored_key)
                st.rerun()
        if chosen.status in ("confirmed", "user-added") and chosen.direction == "out":
            with st.form("rent_form"):
                st.markdown("**Rent rule (payments sent by hand in one of a few amounts)**")
                amounts_text = st.text_input(
                    "Allowed amounts in dollars, separated by commas",
                    value=", ".join(f"{a / 100:.2f}" for a in chosen.allowed_cents),
                    key=f"rent_amounts|{chosen.key}",
                )  # fmt: skip
                days = st.columns(2)
                first_day = days[0].number_input(
                    "From day of the month", 1, 31, chosen.window[0] if chosen.window else 30,
                    key=f"rent_from|{chosen.key}",
                )  # fmt: skip
                last_day = days[1].number_input(
                    "To day (next month if smaller)", 1, 31,
                    chosen.window[1] if chosen.window else 7, key=f"rent_to|{chosen.key}",
                )  # fmt: skip
                st.caption(
                    "Any outgoing e-Transfer of an allowed amount counts as this series, on any "
                    "day, one per month (a late payment counts for its own month). The window "
                    "only says where the next one is expected: the forecast assumes the larger "
                    "amount, somewhere in the window, and never uses anything posted after "
                    "the forecast date."
                )
                save_rent = st.form_submit_button("Save rent rule")
                clear_rent = st.form_submit_button("Remove rent rule", disabled=rule is None)
            if save_rent:
                try:
                    set_rent_rule(conn, account, chosen.key,
                                  RentRule(parse_dollars(amounts_text), int(first_day),
                                           int(last_day)))  # fmt: skip
                except ValueError as problem:
                    st.warning(f"Could not save: {problem}")
                else:
                    refresh(conn, account, stored_key)
                    st.rerun()
            if clear_rent:
                set_rent_rule(conn, account, chosen.key, None)
                refresh(conn, account, stored_key)
                st.rerun()
        ended = [s for s in listed if not s.active and s.key != chosen.key]
        if ended:
            with st.form("continue_form"):
                st.markdown("**This series continues an ended one**")
                previous = st.selectbox(
                    "For example the same subscription under a new merchant name. The two "
                    "are then shown as one series with one history. No merchant is merged.",
                    ended, index=None, placeholder="Choose the ended series",
                    format_func=lambda s: series_label(s, s.key in hidden),
                    key=f"continues|{chosen.key}",
                )  # fmt: skip
                link = st.form_submit_button("Link them")
            if link and previous is not None:
                link_continuation(conn, account, chosen.key, previous.key)
                refresh(conn, account, stored_key)
                st.rerun()
        if chosen.continues:
            st.caption(f"Continues {len(chosen.continues)} earlier series (one history).")

# --- Add one by hand ---
with st.expander("Add a series by hand (for example income that has no fixed schedule)"):
    with st.form("add_form"):
        name = st.text_input("Name", key="add_name")
        direction = st.radio("Direction", ["Money out", "Money in"], horizontal=True,
                             key="add_direction")  # fmt: skip
        cadence = st.selectbox("How often", CADENCES, index=CADENCES.index("monthly"),
                               key="add_cadence")  # fmt: skip
        first = st.date_input("A date it is due", value=date.today(), key="add_date")
        size = st.number_input("Amount ($)", min_value=0.0, step=1.0, key="add_amount")
        add = st.form_submit_button("Add")
    if add:
        if not name.strip() or size <= 0 or not isinstance(first, date):
            st.warning("Give a name, a date and an amount above zero.")
        else:
            add_series(conn, account, name, "in" if direction == "Money in" else "out",
                       cadence, first, round(size * 100))  # fmt: skip
            refresh(conn, account, stored_key)
            st.rerun()

st.caption(DISCLAIMER)
