"""Budgets: monthly limits for a few categories, with the chance of going over read from the
forecast's own simulated futures (spec docs/specs/2026-11-20-budgets-that-forecast.md).

Every number comes from lookahead/budgets.py and lookahead/budget_alerts.py; this page only shows
them. Every figure is an estimate. In demo mode, accepted and edited limits stay in this visitor's
session and are never written; budget alerts are shown, not recorded.
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
    budget_chart,
    cached_safe_to_spend,
    data_settings,
    fmt_date,
    footer,
    money,
    tag_html,
    top_bar,
)
from lookahead import demo_mode  # noqa: E402
from lookahead.budget_alerts import (  # noqa: E402
    alert_message,
    budget_episodes,
    dismiss,
    record_look,
    restore,
    should_alert,
)
from lookahead.budgets import (  # noqa: E402
    BIG_ONE_OFF_NOTE,
    OPTIONAL_CATEGORIES,
    STALE_NOTE,
    addable,
    budget_forecast,
    budget_month,
    category_rows,
    check_limit,
    is_stale,
    load_limits,
    month_key,
    on_budget,
    proposals_for,
    remove,
    set_limit,
    shown_categories,
    waiting_proposals,
)
from lookahead.corrections import (  # noqa: E402
    correct_merchant,
    correct_row,
    merchant_fingerprints,
)
from lookahead.db import connect  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.demo_mode import is_demo  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.safe_to_spend import sentence  # noqa: E402
from lookahead.taxonomy import CATEGORIES  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    demo_db_path,
    display_table,
    private_db_path,
    synthetic_dir,
)
from lookahead.views import accounts, has_categories, transactions  # noqa: E402

TITLE = "Budgets"
JUST_THIS_ROW = "Just this row"
WHOLE_MERCHANT = "All rows of this merchant"
DEMO_LIMITS = "bu_demo_limits"  # demo mode: {account: {category: cents or None}} in the session


def cents(dollars: float) -> int:
    """Dollars typed on the page to integer cents (money stays in cents in code)."""
    return round(dollars * 100)


def note(text: str) -> None:
    """A small grey line. A dollar sign would start maths in markdown, so it is escaped."""
    escaped = html.escape(text).replace("$", "&#36;")
    st.markdown(f'<p class="la-note">{escaped}</p>', unsafe_allow_html=True)


def warn(text: str) -> None:
    st.warning(text.replace("$", "\\$"))


apply(TITLE)
bar = st.container(key="topbar")

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
        top_bar(bar, TITLE, None, source)
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

try:
    stored_key: bytes | None = pseudonym_key()
except PseudonymKeyError:
    stored_key = None
display_key = stored_key if pseudonymize else None

conn = connect(db_path)  # saves limits, corrections and the budget alerts' episodes
names = accounts(conn)
if not names:
    top_bar(bar, TITLE, None, source)
    st.info("This database has no transactions yet.")
    st.stop()
account = account_setting(names)
if not has_categories(conn, account):
    top_bar(bar, TITLE, None, source)
    st.info("Budgets need categories: run the categorizer on this account first.")
    st.stop()

forecast = budget_forecast(conn, account, stored_key)
if forecast is None or forecast.inputs is None:
    top_bar(bar, TITLE, None, source)
    st.info("This account has no transactions yet.")
    st.stop()
start = forecast.start
data_date, first_day = start.data_last_updated, start.first_day
top_bar(bar, TITLE, data_date, source)


# --- Limits: saved in the database, or in demo mode in this visitor's session only ---


def stored_limits() -> dict[str, int | None]:
    if is_demo():
        return dict(st.session_state.get(DEMO_LIMITS, {}).get(account, {}))
    return load_limits(conn, account)


def save_limit(category: str, amount: int | None) -> None:
    check_limit(category, amount)
    if is_demo():
        everything = dict(st.session_state.get(DEMO_LIMITS, {}))
        everything[account] = {**everything.get(account, {}), category: amount}
        st.session_state[DEMO_LIMITS] = everything
    else:
        set_limit(conn, account, category, amount)


def forget(category: str) -> None:
    if is_demo():
        everything = dict(st.session_state.get(DEMO_LIMITS, {}))
        everything[account] = {k: v for k, v in everything.get(account, {}).items()
                               if k != category}  # fmt: skip
        st.session_state[DEMO_LIMITS] = everything
    else:
        remove(conn, account, category)


rows = category_rows(conn, account, data_date)
stored = stored_limits()
proposals = proposals_for(conn, account, rows, forecast)
shown = shown_categories(stored, proposals)
limits = {s.category: s.limit_cents for s in shown if s.limit_cents is not None}
month = budget_month(forecast, rows, limits)
paces = {p.category: p for p in month.paces}
month_name = f"{month.month:%B %Y}"

st.markdown(
    f'<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
    f"<h1>Budgets for {html.escape(month_name)}</h1>{tag_html('estimate', 'neutral')}</div>",
    unsafe_allow_html=True,
)
if is_stale(first_day, demo_mode.today(data_date)):
    st.info(STALE_NOTE)
note(
    f"Spent so far counts rows up to {fmt_date(data_date)}. The rest of the month "
    f"({month.days_left} {'day' if month.days_left == 1 else 'days'}) comes from the forecast's "
    f"{forecast.inputs.futures} simulated futures, built from your last 13 weeks. "
    "An estimate, never a promise."
)
placed = tuple((i.day, i.cents, i.series_key) for i in forecast.items)
version = (start, forecast.buffer_cents, placed)
safe = cached_safe_to_spend(str(db_path), version, account, stored_key)
if safe is not None:
    note(f"Safe to spend, for all everyday spending (not changed by budgets): {sentence(safe)}")

# --- Budget alerts: recorded on this look (shown, not recorded, in demo mode) ---
if is_demo():
    alerting = [p for p in month.paces if should_alert(p, first_day)]
    for p in alerting:
        warn(
            f"Possible overspend in {p.category}: in {p.chance_over:.0%} of simulated futures you "
            f"end the month above your {money(p.limit_cents)} budget (an estimate)."
        )
else:
    record_look(conn, account, month, first_day)
    this_month = [e for e in budget_episodes(conn, account) if e.month == month_key(month.month)
                  and e.status != "closed"]  # fmt: skip
    for e in this_month:
        with st.container(border=True, key=f"card-balert-{e.episode_id}"):
            if e.status == "open":
                warn(alert_message(e) + " (an estimate)")
                if st.button("Dismiss this alert", key=f"bu_dismiss_{e.episode_id}"):
                    dismiss(conn, account, e.episode_id)
                    st.rerun()
            else:
                note(f"You dismissed the {e.category} alert. It comes back only if the chance of "
                     "going over enters a higher band than when it last spoke (the bands start "
                     "at 20, 50 and 80%).")  # fmt: skip
                if st.button("Bring it back", key=f"bu_restore_{e.episode_id}"):
                    restore(conn, account, e.episode_id)
                    st.rerun()

# --- Proposals: one "Looks right" ---
waiting = waiting_proposals(shown)
if waiting:
    with st.container(border=True, key="card-proposals"):
        st.markdown("<h2>Proposed limits</h2>", unsafe_allow_html=True)
        months = max((proposals[c].months for c in waiting), default=0)
        note(f"From your last {months} full months: the middle month's spending in each "
             "category, rounded up to the next $5. You can edit them after.")  # fmt: skip
        st.markdown("\n".join(f"- {c}: {money(v, markdown=True)} a month"
                              for c, v in waiting.items()))  # fmt: skip
        if st.button("Looks right, accept all", type="primary", key="bu_accept"):
            for category, amount in waiting.items():
                save_limit(category, amount)
            st.rerun()

# --- One card per category ---
by_fp = transactions(conn, account, end=data_date).set_index("fingerprint", drop=False)


def rows_table(fingerprints: list[str]):  # type: ignore[no-untyped-def]
    part = by_fp.loc[fingerprints].reset_index(drop=True)
    return part, display_table(part, display_key)


for s in shown:
    with st.container(border=True, key=f"card-budget-{CATEGORIES.index(s.category)}"):
        st.markdown(f"<h2>{html.escape(s.category)}</h2>", unsafe_allow_html=True)
        pace = paces.get(s.category)
        if pace is None:  # no limit saved yet
            p = s.proposal
            if p is not None and p.limit_cents is not None:
                note(f"Proposed: {money(p.limit_cents)} a month (waiting for Looks right above).")
            else:
                note("Not enough history for a proposal (it needs 2 full months with spending). "
                     "Enter a limit.")  # fmt: skip
            with st.form(f"bu_enter_{s.category}", border=False):
                typed = st.number_input("Monthly limit ($)", min_value=0.0, step=10.0,
                                        key=f"bu_typed_{s.category}")  # fmt: skip
                if st.form_submit_button("Save limit") and typed > 0:
                    save_limit(s.category, cents(typed))
                    st.rerun()
            if st.button("No budget for this category", key=f"bu_off_{s.category}"):
                if s.category in OPTIONAL_CATEGORIES:
                    forget(s.category)
                else:
                    save_limit(s.category, None)
                st.rerun()
            continue

        a, b, c, d = st.columns(4)
        a.metric("Spent so far", money(pace.spent_cents))
        b.metric("Limit", money(pace.limit_cents))
        c.metric("Likely end of month", money(pace.likely_end_cents))
        d.metric("Chance of going over", f"{pace.chance_over:.0%}")
        st.plotly_chart(budget_chart(pace.spent_cents, pace.likely_end_cents, pace.limit_cents),
                        key=f"bu_chart_{s.category}", config={"displayModeBar": False})  # fmt: skip
        if pace.big_one_off:
            note(BIG_ONE_OFF_NOTE)

        with st.expander("Edit the limit"):
            with st.form(f"bu_edit_{s.category}", border=False):
                new = st.number_input("Monthly limit ($)", min_value=0.0, step=10.0,
                                      value=pace.limit_cents / 100,
                                      key=f"bu_new_{s.category}")  # fmt: skip
                if st.form_submit_button("Save") and new > 0:
                    save_limit(s.category, cents(new))
                    st.rerun()
            if st.button("Turn this budget off", key=f"bu_turnoff_{s.category}"):
                if s.category in OPTIONAL_CATEGORIES:
                    forget(s.category)
                else:
                    save_limit(s.category, None)
                st.rerun()

        with st.expander(f"Rows this month ({len(pace.rows)})"):
            if not pace.rows:
                note("No rows in this category this month yet.")
            else:
                part, shown_rows = rows_table([r.fingerprint for r in pace.rows])
                st.dataframe(shown_rows, hide_index=True, width="stretch")
                labels = {
                    f: f"{day} · {amount:.2f} · {text}"
                    for f, day, amount, text in zip(
                        part["fingerprint"], shown_rows["Date"], shown_rows["Amount ($)"],
                        shown_rows["Text"], strict=True,
                    )
                }  # fmt: skip
                with st.form(f"bu_move_{s.category}", border=False):
                    fingerprint = st.selectbox("Row in the wrong category", list(labels),
                                               format_func=labels.get,
                                               key=f"bu_row_{s.category}")  # fmt: skip
                    target = st.selectbox(
                        "Move it to", [x for x in CATEGORIES if x != s.category], index=None,
                        placeholder="Choose a category", key=f"bu_target_{s.category}",
                    )  # fmt: skip
                    scope = st.radio("Which rows", [JUST_THIS_ROW, WHOLE_MERCHANT],
                                     horizontal=True, key=f"bu_scope_{s.category}")  # fmt: skip
                    move = st.form_submit_button("Move")
                if move and fingerprint is not None and target is not None:
                    merchant = by_fp.loc[fingerprint, "merchant"]
                    whole = scope == WHOLE_MERCHANT and isinstance(merchant, str) and merchant
                    if whole and not merchant_fingerprints(conn, account, merchant):
                        st.warning(  # a whole-merchant move would change nothing: say so
                            "A rule decides every row of this merchant, so a whole-merchant "
                            'move cannot change them. Choose "Just this row" instead.'
                        )
                    else:
                        try:
                            if whole:
                                correct_merchant(conn, account, merchant, target)
                            else:
                                correct_row(conn, account, fingerprint, target)
                        except ValueError as e:
                            st.error(str(e))
                        else:
                            st.rerun()
                note("A whole-merchant move skips rows a rule decided and rows you set one by "
                     "one; move those one row at a time.")  # fmt: skip

# --- Add a category: an optional one, or a default I turned off ---
available = addable(shown)
if available:
    with st.expander("Add a budget for a category"):
        category = st.selectbox("Category", available, key="bu_add_category")
        proposed = proposals.get(category) if category else None
        if proposed and proposed.limit_cents:
            note(f"Proposed from your history: {money(proposed.limit_cents)}. Leave the limit at "
                 "$0 to use it.")  # fmt: skip
        else:
            note("No proposal: too little history. Enter a limit.")
        with st.form("bu_add", border=False):
            typed = st.number_input("Monthly limit ($)", min_value=0.0, step=10.0,
                                    key="bu_add_limit")  # fmt: skip
            if st.form_submit_button("Add") and category is not None:
                amount = cents(typed) if typed > 0 else (proposed.limit_cents if proposed else None)
                if amount:
                    save_limit(category, amount)
                    st.rerun()
                else:
                    st.warning("Enter a limit above $0.")

# --- Staying on budget: the what-if ---
if month.paces:
    result = on_budget(forecast, month)
    with st.container(border=True, key="card-onbudget"):
        st.markdown(
            '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
            f"<h2>If you stay on budget</h2>{tag_html('estimate', 'neutral')}</div>",
            unsafe_allow_html=True,
        )
        now, kept = st.columns(2)
        now.metric("As things are now: lowest balance", money(result.low_now_cents))
        now.metric("Chance of dipping below your buffer", f"{result.chance_now:.0%}")
        kept.metric("On budget: lowest balance", money(result.low_on_budget_cents),
                    delta=money(result.low_on_budget_cents - result.low_now_cents, signed=True)
                    if result.low_on_budget_cents != result.low_now_cents else None)  # fmt: skip
        kept.metric("Chance of dipping below your buffer", f"{result.chance_on_budget:.0%}")
        note(
            "The next 30 days, the middle future's lowest balance. On budget: in each future, "
            "spending in each budgeted category stops at its limit for the rest of this month; "
            f"everything else is the same draw for draw ({result.futures} futures)."
        )

footer(DISCLAIMER)
conn.close()
