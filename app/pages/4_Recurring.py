"""Recurring: payments and income that repeat, found by rules, for you to confirm or fix.

Detected series are assumptions until you confirm them. Nothing here blocks anything: a
series you never look at is still used as "detected" in the forecast, and says so.
The look (cards, tags, the payments table) lives in app/theme.py.
"""

import html
import sys
from datetime import date
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    COLORS,
    EVERY,
    account_setting,
    add_card_html,
    apply,
    data_settings,
    fmt_date,
    footer,
    money_short,
    monthly_totals,
    payments_table_html,
    series_card_html,
    tag_html,
    top_bar,
)
from lookahead.db import connect  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring import CADENCES  # noqa: E402
from lookahead.recurring_store import (  # noqa: E402
    add_series,
    confirm,
    confirm_all,
    edit,
    end,
    ensure_current,
    link_continuation,
    refresh,
    reject,
    rent_rule_of,
    set_category,
    set_name,
    set_rent_rule,
)
from lookahead.rent import RentRule, parse_dollars, rent_cents  # noqa: E402
from lookahead.setting_proposals import accept, current_proposals, describe, dismiss  # noqa: E402
from lookahead.taxonomy import CATEGORIES  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    demo_db_path,
    display_table,
    hidden_series,
    private_db_path,
    series_label,
    synthetic_dir,
)
from lookahead.views import accounts, series_categories, transactions  # noqa: E402

OWN_CATEGORY = "Each payment's own category"
ADD = "__add__"  # the selection when "Add one I pay" is chosen

apply("Recurring")
bar = st.container(key="topbar")  # the green bar: drawn into once the data date is known

# --- Which database ---
source, pseudonymize = data_settings()
if source == "Synthetic demo":
    db_path = demo_db_path()
    if not db_path.exists() or not demo_has_categories(db_path):
        with st.spinner("Building the synthetic demo database..."):
            build_demo_db(db_path, synthetic_dir())
else:
    db_path = private_db_path()
    if not db_path.exists():
        top_bar(bar, "Recurring", None, source)
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

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
    top_bar(bar, "Recurring", None, source)
    st.info("This database has no transactions yet.")
    st.stop()
account = account_setting(names)

if st.sidebar.button("Detect again", help="Run the detection on the stored transactions."):
    refresh(conn, account, stored_key)
    st.rerun()
series = ensure_current(conn, account, stored_key)  # rebuilt first if older code or data
show_rejected = st.sidebar.toggle("Show the ones I rejected", value=False)

rows = transactions(conn, account)
shown_rows = display_table(rows, display_key)
# A series whose payments show a replaced name (a person) keeps its own name hidden too.
hidden = hidden_series(rows, series, display_key)
listed = [s for s in series if show_rejected or s.status != "rejected"]
waiting = sum(s.status == "detected" and s.active for s in listed)
last_date = rows["date"].max().date() if not rows.empty else None
top_bar(bar, "Recurring", last_date, source)
categories = series_categories(rows, listed)

# --- Title row ---
out_cents, in_cents = monthly_totals(listed)
review = (
    f'<strong style="color: {COLORS.blue}">{waiting} to review</strong>'
    if waiting
    else "nothing left to review"
)
totals = (
    f" · about {money_short(out_cents)} out and {money_short(in_cents)} in each month"
    if out_cents or in_cents
    else ""
)
title_col, action_col = st.columns([4, 1.3], vertical_alignment="bottom")
with title_col:
    st.markdown(
        "<h1>Recurring payments</h1>"
        f'<p class="la-sub">{len(listed)} found in your history · {review}{totals}</p>'
        '<p class="la-note">Expected dates and amounts are estimates from past payments.</p>',
        unsafe_allow_html=True,
    )
with action_col:
    confirm_help = (
        "Confirms every detected series that is still active, one decision each, "
        "as if you had confirmed them one by one."
    )
    if waiting and st.button("Looks right, confirm all", type="primary", width="stretch",
                             help=confirm_help):  # fmt: skip
        confirm_all(conn, account, listed)
        refresh(conn, account, stored_key)
        st.rerun()
if last_date is not None and any(last_date < s.next_expected < date.today() for s in listed):
    st.markdown(
        '<p class="la-note">A next expected date after the data ends but before today is a '
        "payment due since then that has not been seen yet; it may already have posted.</p>",
        unsafe_allow_html=True,
    )

# --- Suggestions: what I used to type by hand, prefilled from history ---
suggestions = current_proposals(conn, account, stored_key, series=series)
if suggestions:
    st.markdown(
        "<h3>Suggestions</h3>"
        '<p class="la-note">Prefilled from your history. Nothing changes until you click '
        "Looks right.</p>",
        unsafe_allow_html=True,
    )
    for suggestion in suggestions:
        with st.container(border=True):
            st.markdown(describe(suggestion, hide_names=display_key is not None))
            st.caption(
                f"{suggestion.confidence.capitalize()} · based on {len(suggestion.evidence)} "
                "payments · an estimate from past payments"
            )
            ok_col, no_col, _ = st.columns([1, 1, 4])
            if ok_col.button("Looks right", key=f"suggest_ok|{suggestion.key}", type="primary"):
                accept(conn, account, suggestion, series)
                refresh(conn, account, stored_key)
                st.rerun()
            if no_col.button("Not this", key=f"suggest_no|{suggestion.key}"):
                dismiss(conn, account, suggestion)
                st.rerun()

# --- The cards ---
by_key = {s.key: s for s in listed}
picked = st.session_state.get("series")
if picked != ADD and picked not in by_key:  # first visit, or the series is gone or hidden now
    picked = listed[0].key if listed else ADD
    st.session_state["series"] = picked
with st.container(key="seriesgrid"):
    for i, s in enumerate(listed):
        label = series_label(s, s.key in hidden)
        category, is_estimate = categories[s.key]
        shown_category = f"{category} (estimate)" if category and is_estimate else category
        with st.container(key=f"seriescard-{i}"):
            st.markdown(
                series_card_html(s, label, shown_category, selected=s.key == picked),
                unsafe_allow_html=True,
            )
            if st.button(f"Open {label}", key=f"open|{s.key}"):
                st.session_state["series"] = s.key
                st.rerun()
    with st.container(key="seriescard-add"):
        st.markdown(add_card_html(), unsafe_allow_html=True)
        if st.button("Add one I pay", key="open_add"):
            st.session_state["series"] = ADD
            st.rerun()

# --- Add one by hand ---
if picked == ADD:
    with st.container(border=True, key="card-detail"):
        st.markdown(
            "<h2>Add one I pay</h2>"
            '<p class="la-sub">For example income that has no fixed schedule.</p>',
            unsafe_allow_html=True,
        )
        with st.form("add_form", border=False):
            name = st.text_input("Name", key="add_name")
            direction = st.radio("Direction", ["Money out", "Money in"], horizontal=True,
                                 key="add_direction")  # fmt: skip
            cadence = st.selectbox("How often", CADENCES, index=CADENCES.index("monthly"),
                                   key="add_cadence")  # fmt: skip
            first = st.date_input("A date it is due", value=date.today(), key="add_date")
            size = st.number_input("Amount ($)", min_value=0.0, step=1.0, key="add_amount")
            add = st.form_submit_button("Add", type="primary")
        if add:
            if not name.strip() or size <= 0 or not isinstance(first, date):
                st.warning("Give a name, a date and an amount above zero.")
            else:
                new_key = add_series(conn, account, name, "in" if direction == "Money in"
                                     else "out", cadence, first, round(size * 100))  # fmt: skip
                refresh(conn, account, stored_key)
                st.session_state["series"] = new_key
                st.rerun()

# --- One series: its payments, and what you can change ---
chosen = by_key.get(picked) if picked != ADD else None
if chosen is not None:
    evidence = rows["fingerprint"].isin(chosen.evidence).to_numpy()
    found = rows[evidence]
    label = series_label(chosen, chosen.key in hidden)
    if not chosen.active:
        tag = tag_html("Ended", "ended")
    elif chosen.status == "confirmed":
        tag = tag_html("Confirmed", "confirmed")
    elif chosen.status in ("user-added", "rejected"):
        tag = tag_html("Added by me" if chosen.status == "user-added" else "Rejected")
    else:
        tag = tag_html("Detected", "detected")
    cents = abs(chosen.expected_cents)
    low, high = sorted(abs(c) for c in chosen.range_cents)
    size = (
        f"{money_short(cents)} {EVERY[chosen.cadence]}"
        if chosen.amount_type == "fixed"
        else f"About {money_short(cents)} {EVERY[chosen.cadence]} "
        f"(usually {money_short(low)}–{money_short(high)})"
    )
    if len(found):
        first_seen = found["date"].min()
        since = f"{first_seen:%B}" + ("" if first_seen.year == found["date"].max().year
                                      else f" {first_seen.year}")  # fmt: skip
        times = "time" if len(found) == 1 else "times"
        seen = f" Found {len(found)} {times} since {since}."
    else:
        seen = " No payments in the data yet."
    next_up = f" Next expected {fmt_date(chosen.next_expected)}." if chosen.active else ""
    with st.container(border=True, key="card-detail"):
        head, actions = st.columns([2.2, 3], vertical_alignment="top")
        with head:
            st.markdown(
                f'<div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">'
                f"<h2>{html.escape(label)}</h2>{tag}</div>"
                f'<p class="la-sub">{html.escape(size)}.{seen}{next_up}</p>',
                unsafe_allow_html=True,
            )
        with actions:
            cols = st.columns(4)
            if cols[0].button("Confirm", type="primary", width="stretch",
                              disabled=chosen.status == "confirmed"):  # fmt: skip
                confirm(conn, account, chosen)
                refresh(conn, account, stored_key)
                st.rerun()
            if cols[1].button("Edit", width="stretch"):
                st.session_state["editing"] = chosen.key
                st.rerun()
            if cols[2].button("Not recurring", width="stretch",
                              disabled=chosen.status == "rejected"):  # fmt: skip
                reject(conn, account, chosen)
                refresh(conn, account, stored_key)
                st.rerun()
            if cols[3].button("It ended", width="stretch", disabled=not chosen.active):
                end(conn, account, chosen)
                refresh(conn, account, stored_key)
                st.rerun()

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
        if chosen.continues:
            st.caption(f"Continues {len(chosen.continues)} earlier series (one history).")

        if st.session_state.get("editing") == chosen.key:
            with st.form("edit_form", border=False):
                st.markdown("**Change the next expected payment**")
                # Keyed by the series, so switching series shows that series' own values.
                next_date = st.date_input("Next expected date", value=chosen.next_expected,
                                          key=f"next_date|{chosen.key}")  # fmt: skip
                amount = st.number_input(
                    "Expected amount ($)", min_value=0.0, step=1.0,
                    value=abs(chosen.expected_cents) / 100, key=f"amount|{chosen.key}",
                )  # fmt: skip
                save = st.form_submit_button("Save", type="primary")
            st.caption(
                "Your date or amount is used until the next payment posts; then the detected "
                "values take over again."
            )
            if save and isinstance(next_date, date):
                sign = 1 if chosen.direction == "in" else -1
                new_cents = sign * round(amount * 100)  # dollars only on screen; cents are stored
                changed_date = next_date if next_date != chosen.next_expected else None
                changed_cents = new_cents if new_cents != chosen.expected_cents else None
                st.session_state.pop("editing", None)
                if changed_date is not None or changed_cents is not None:
                    edit(conn, account, chosen, next_date=changed_date,
                         amount_cents=changed_cents)  # fmt: skip
                    refresh(conn, account, stored_key)
                st.rerun()

        table_rows = []
        for (_, row), text in zip(found.iterrows(), shown_rows["Text"][evidence], strict=True):
            table_rows.append((fmt_date(row["date"].date()), text, int(row["amount_cents"]),
                               None if row["balance_cents"] != row["balance_cents"]
                               else int(row["balance_cents"])))  # fmt: skip
        st.markdown(payments_table_html(table_rows), unsafe_allow_html=True)
        st.markdown(
            '<p class="la-note">Confirming it means the forecast trusts this date and amount. '
            "You can change either at any time.</p>",
            unsafe_allow_html=True,
        )

        with st.expander("More about this series: category, name, rent rule, linking"):
            if chosen.status in ("confirmed", "user-added") and chosen.amount_type == "fixed":
                estimate, is_estimate = categories[chosen.key]
                if estimate and is_estimate:
                    st.caption(
                        f"Estimated from its payments: {estimate}. Your choice below overrides it."
                    )
                with st.form("category_form", border=False):
                    st.markdown("**Category of this series**")
                    options = [OWN_CATEGORY, *CATEGORIES]
                    current = options.index(chosen.category) if chosen.category else 0
                    choice = st.selectbox(
                        "Every payment in the series gets it; your other purchases at the same "
                        "merchant keep their own.",
                        options, index=current, key=f"series_category|{chosen.key}",
                    )  # fmt: skip
                    save_category = st.form_submit_button("Save category")
                if save_category:
                    set_category(conn, account, chosen,
                                 None if choice == OWN_CATEGORY else choice)  # fmt: skip
                    refresh(conn, account, stored_key)
                    st.rerun()
            with st.form("name_form", border=False):
                new_name = st.text_input(
                    "Your own name for this series (the label only; empty gives the detected "
                    "name back)", value=chosen.name if chosen.named_by_me else "",
                    key=f"name|{chosen.key}",
                )  # fmt: skip
                save_name = st.form_submit_button("Save name")
            if save_name:
                try:
                    set_name(conn, account, chosen, new_name)
                except ValueError as problem:
                    st.warning(f"Could not save: {problem}")
                else:
                    refresh(conn, account, stored_key)
                    st.rerun()
            if chosen.status in ("confirmed", "user-added") and chosen.direction == "out":
                with st.form("rent_form", border=False):
                    st.markdown("**Rent rule (payments sent by hand in one of a few amounts)**")
                    amounts_text = st.text_input(
                        "Allowed amounts in dollars, separated by commas",
                        value=", ".join(f"{a / 100:.2f}" for a in chosen.allowed_cents),
                        key=f"rent_amounts|{chosen.key}",
                    )  # fmt: skip
                    days = st.columns(2)
                    first_day = days[0].number_input(
                        "From day of the month", 1, 31,
                        chosen.window[0] if chosen.window else 30,
                        key=f"rent_from|{chosen.key}",
                    )  # fmt: skip
                    last_day = days[1].number_input(
                        "To day (next month if smaller)", 1, 31,
                        chosen.window[1] if chosen.window else 7, key=f"rent_to|{chosen.key}",
                    )  # fmt: skip
                    st.caption(
                        "Any outgoing e-Transfer of an allowed amount counts as this series, on "
                        "any day, one per month (a late payment counts for its own month). The "
                        "window only says where the next one is expected: the forecast assumes "
                        "the larger amount, somewhere in the window, and never uses anything "
                        "posted after the forecast date."
                    )
                    save_rent = st.form_submit_button("Save rent rule")
                    clear_rent = st.form_submit_button("Remove rent rule", disabled=rule is None)
                if save_rent:
                    try:
                        set_rent_rule(conn, account, chosen,
                                      RentRule(parse_dollars(amounts_text), int(first_day),
                                               int(last_day)))  # fmt: skip
                    except ValueError as problem:
                        st.warning(f"Could not save: {problem}")
                    else:
                        refresh(conn, account, stored_key)
                        st.rerun()
                if clear_rent:
                    set_rent_rule(conn, account, chosen, None)
                    refresh(conn, account, stored_key)
                    st.rerun()
            ended = [s for s in listed if not s.active and s.key != chosen.key]
            if ended:
                with st.form("continue_form", border=False):
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
                    link_continuation(conn, account, chosen, previous)
                    refresh(conn, account, stored_key)
                    st.rerun()

footer(DISCLAIMER)
