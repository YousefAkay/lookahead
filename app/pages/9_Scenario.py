"""Scenario: "what if I spend X a day". A calculator, not a forecast.

The inputs start from what the database says (lookahead/scenario_prefill.py); the sums are exact
(lookahead/scenario.py). Two lines: "as things are now" (the prefilled values) and "my scenario".
No uncertainty band: this assumes exactly what was entered.

Scenario v2 (spec docs/specs/2026-11-10-scenario-v2.md): chequing and savings together by default,
from a savings balance typed here and remembered with the day it was entered; without one, chequing
only, with the top-ups field. The buffer applies to the balance the chart shows.
"""

import html
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    account_setting,
    apply,
    data_settings,
    fmt_date,
    footer,
    money,
    top_bar,
)
from lookahead.db import connect  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring_store import ensure_current  # noqa: E402
from lookahead.scenario import Flow, Inputs, OneOff, default_end, run  # noqa: E402
from lookahead.scenario_prefill import (  # noqa: E402
    ANCHOR_YEAR,
    counts_top_up_twice,
    prefill,
    starting_point,
)
from lookahead.settings import savings_balance, set_savings_balance  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    calculator_chart,
    demo_db_path,
    hidden_series,
    private_db_path,
    series_label,
    synthetic_dir,
)
from lookahead.views import accounts, transactions  # noqa: E402

TITLE = "Scenario"
NOT_A_FORECAST = "This assumes exactly what you entered. It's not a forecast."
SPREAD = "Spread evenly through the month"
SET_DAY = "On a set day"
RENT_DAY_NOTE = "No rent rule found: rent entered here is placed on the 1st of each month."
TWICE_WARNING = (
    "A confirmed top-up from savings is already in your recurring payments below, so this field "
    "may count the same money twice."
)
COMBINED_NOTE = (
    "Chequing and savings together: money you move between them cancels out, so it is neither "
    "income nor spending."
)
CHEQUING_ONLY_NOTE = (
    "Chequing only: no savings balance entered. Money you move in from savings counts only through "
    '"Top-ups from savings". Enter your savings balance to see both together.'
)


def cents(dollars: float) -> int:
    """Dollars typed on the page to integer cents (money stays in cents in code)."""
    return round(dollars * 100)


apply(TITLE)
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
        top_bar(bar, TITLE, None, source)
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

try:
    stored_key: bytes | None = pseudonym_key()
except PseudonymKeyError:
    stored_key = None
display_key = stored_key if pseudonymize else None

conn = connect(db_path)  # keeps the stored series current, as the other pages do
names = accounts(conn)
if not names:
    top_bar(bar, TITLE, None, source)
    st.info("This database has no transactions yet.")
    st.stop()
account = account_setting(names)
series = ensure_current(conn, account, stored_key)
base = prefill(conn, account, series)
rows = transactions(conn, account)
hidden = hidden_series(rows, series, display_key)
by_key = {s.key: s for s in series}
stored_savings = savings_balance(conn)
conn.close()

top_bar(bar, TITLE, base.data_date, source)
st.markdown(
    f"<h1>What if</h1>"
    f'<p class="la-sub">Change what you spend and see where your balance ends up. '
    f"{html.escape(NOT_A_FORECAST, quote=False)}</p>",
    unsafe_allow_html=True,
)
assert base.data_date is not None  # an account has at least one row
start = base.data_date
if start < date.today():
    st.markdown(
        f'<p class="la-note">Starting from {fmt_date(start)}, the latest day in your data.</p>',
        unsafe_allow_html=True,
    )
if base.missing:
    st.markdown(
        f'<p class="la-note">Could not be estimated from your data, so it starts at $0: '
        f"{html.escape(', '.join(base.missing))}.</p>",
        unsafe_allow_html=True,
    )


def forget_savings() -> None:
    """The Clear button: forget the stored savings balance and empty the field (a callback runs
    before the page, so the field is drawn empty and nothing is written back)."""
    write = connect(db_path)
    set_savings_balance(write, None, date.today())
    write.close()
    st.session_state["sc_savings"] = None


def basis_note(title: str, sentence: str, days: int, counts: tuple[tuple[str, int], ...]) -> str:
    """An estimate's basis as a labelled block: one count per line, then the rule in words."""
    lines = [f"<strong>{html.escape(title)}</strong>"]
    if counts:
        lines.append(f"Days in the window: {days}")
        (counted, n), *left_out = counts
        lines.append(f"{html.escape(counted[:1].upper() + counted[1:])}: {n}")
        lines += [f"Left out, {html.escape(name)}: {k}" for name, k in left_out]
    lines.append(html.escape(sentence))
    return f'<p class="la-note">{"<br>".join(lines)}</p>'


def outside(day: date) -> str:
    """Why a one-off item is not counted, or "" when it is (it must fall after the data's last
    day and on or before the end date)."""
    if day <= start:
        return f"on or before {fmt_date(start)}, the last day of your data: not counted"
    if day > end:
        return f"after the end date: not counted. Move the end date to {fmt_date(day)} or later"
    return ""


def label(flow: Flow) -> str:
    s = by_key.get(flow.key)
    return series_label(s, hide=s.key in hidden) if s is not None else flow.name


# --- Inputs ---
with st.container(border=True, key="card-inputs"):
    st.markdown("<h2>Your scenario</h2>", unsafe_allow_html=True)

    # --- Starting point: chequing from the data, savings typed here (remembered) ---
    first_col, savings_col, total_col = st.columns(3)
    with first_col:
        st.markdown(
            f'<p class="la-note">Chequing on {fmt_date(start)} (from your data)<br>'
            f"<strong>{html.escape(money(base.balance_cents))}</strong></p>",
            unsafe_allow_html=True,
        )
    with savings_col:
        savings_in = st.number_input(
            "Savings balance ($)", min_value=0.0, step=10.0, key="sc_savings",
            value=stored_savings[0] / 100 if stored_savings else None, placeholder="not entered",
        )  # fmt: skip
        savings_cents = None if savings_in is None else cents(savings_in)
        if savings_cents != (stored_savings[0] if stored_savings else None):
            write = connect(db_path)
            set_savings_balance(write, savings_cents, date.today())
            write.close()
            stored_savings = None if savings_cents is None else (savings_cents, date.today())
        if stored_savings:
            st.caption(f"Entered {fmt_date(stored_savings[1])}.")
            st.button("Clear (chequing only)", key="sc_savings_clear", on_click=forget_savings)
        else:
            st.caption("Not entered: the calculator shows chequing only.")
    view = starting_point(base, savings_cents)
    with total_col:
        st.markdown(
            f'<p class="la-note">{"Together" if view.combined else "Chequing only"}<br>'
            f"<strong>{html.escape(money(view.balance_cents))}</strong></p>",
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<p class="la-note">{html.escape(COMBINED_NOTE if view.combined else CHEQUING_ONLY_NOTE)}'
        "</p>",
        unsafe_allow_html=True,
    )

    left, middle, right = st.columns(3)
    with left:
        spend = st.number_input("Daily spend ($)", min_value=0.0, step=1.0, key="sc_spend",
                                value=base.daily_spend_cents / 100)  # fmt: skip
        st.markdown(
            basis_note("How the daily spend was estimated", base.spend_basis, base.window_days,
                       base.spend_counts),
            unsafe_allow_html=True,
        )  # fmt: skip
        income_in = st.number_input(
            "Income per month ($)", min_value=0.0, step=10.0, key="sc_income",
            value=(base.income.cents / 100) if base.income else 0.0,
        )  # fmt: skip
        st.markdown(
            basis_note("How the income was estimated", base.income_basis, base.window_days,
                       base.income_counts),
            unsafe_allow_html=True,
        )  # fmt: skip
        top_ups_in = 0.0  # in the combined view a top-up only moves money between my accounts
        if not view.combined:
            top_ups_in = st.number_input(
                "Top-ups from savings per month ($)", min_value=0.0, step=10.0, key="sc_top_ups",
                value=0.0,
                help="Money you move from savings into this account: counted as money in.",
            )  # fmt: skip
            if counts_top_up_twice(base, cents(top_ups_in)):
                st.warning(TWICE_WARNING)
    with middle:
        rent_in = st.number_input("Rent per month ($)", min_value=0.0, step=10.0, key="sc_rent",
                                  value=-base.rent.cents / 100 if base.rent else 0.0)  # fmt: skip
        if base.rent is None:
            st.caption(RENT_DAY_NOTE)
        timing = st.radio("Income and top-ups arrive", [SPREAD, SET_DAY], key="sc_timing")
        usual = base.income.anchor.day if base.income else 1
        pay_day = (
            st.number_input(
                "Day of the month", min_value=1, max_value=31, step=1, value=usual, key="sc_pay_day"
            )  # fmt: skip
            if timing == SET_DAY
            else usual
        )
    with right:
        end = st.date_input("End date", value=default_end(start), key="sc_end")
        buffer_in = st.number_input("Buffer ($)", min_value=0.0, step=10.0, key="sc_buffer",
                                    value=base.buffer_cents / 100)  # fmt: skip

    switches: list[Flow] = []
    if view.flows:
        st.markdown(
            '<p class="la-note"><strong>Recurring payments</strong> (confirmed or added by you): '
            "switch one off to see the difference.</p>",
            unsafe_allow_html=True,
        )
        columns = st.columns(2)
        for i, flow in enumerate(view.flows):
            with columns[i % 2]:
                on = st.toggle(f"{label(flow)} ({money(flow.cents, signed=True)} {flow.cadence})",
                               value=True, key=f"sc_flow|{flow.key or i}")  # fmt: skip
            switches.append(replace(flow, on=on))
    else:
        st.markdown('<p class="la-note">No confirmed recurring payments yet: confirm them on the '
                    "Recurring page to see them here.</p>", unsafe_allow_html=True)  # fmt: skip

    st.markdown('<p class="la-note"><strong>One-off items</strong></p>', unsafe_allow_html=True)
    extras: list[dict[str, object]] = st.session_state.setdefault("sc_extras", [])
    # A form: what, day, amount and direction are sent together with the Add click, so a value
    # still being typed cannot be lost (my real-data check of 2026-11-17).
    with st.form("sc_extra_form", border=False, enter_to_submit=False):
        a, b, c, d = st.columns([1.2, 1, 1, 0.8], vertical_alignment="bottom")
        with a:
            what = st.text_input("What", key="sc_extra_name", placeholder="e.g. concert")
        with b:
            when = st.date_input("Day", value=start + timedelta(days=1), key="sc_extra_day")
        with c:
            size = st.number_input("Amount ($)", min_value=0.0, step=10.0, key="sc_extra_amount")
            direction = st.radio("Direction", ["Money out", "Money in"], horizontal=True,
                                 key="sc_extra_dir",
                                 label_visibility="collapsed")  # fmt: skip
        with d:
            added = st.form_submit_button("Add", key="sc_extra_add")
    if added:
        if size > 0:
            sign = 1 if direction == "Money in" else -1
            extras.append({"name": what, "day": when, "cents": sign * cents(size)})
            st.rerun()
        st.warning("Enter an amount above $0, then Add.")
    skipped = 0
    for i, item in enumerate(list(extras)):
        text_col, remove_col = st.columns([4, 1], vertical_alignment="center")
        why = outside(item["day"])  # type: ignore[arg-type]
        skipped += bool(why)
        with text_col:
            st.markdown(f"{fmt_date(item['day'])}: {html.escape(str(item['name']) or 'one-off')} "  # type: ignore[arg-type]
                        f"{money(int(item['cents']), signed=True, markdown=True)}"  # type: ignore[call-overload]
                        + (f" *({html.escape(why)})*" if why else ""))  # fmt: skip
        with remove_col:
            if st.button("Remove", key=f"sc_extra_remove|{i}"):
                extras.pop(i)
                st.rerun()
    if skipped:
        st.warning(
            f"{skipped} one-off {'item is' if skipped == 1 else 'items are'} outside "
            f"{fmt_date(start + timedelta(days=1))} to {fmt_date(end)} and not counted: "
            "see the note beside each."
        )

# --- The two lines ---
# The timing choice applies to both lines: it is how money arrives, not part of the what-if.
rent_flow = base.rent or Flow("Rent", 0, "monthly", date(ANCHOR_YEAR, 1, 1), card=True)
arrives = {"anchor": date(ANCHOR_YEAR, 1, int(pay_day)), "spread": timing == SPREAD}
income_flow = replace(base.income or Flow("Income", 0, "monthly", date(ANCHOR_YEAR, 1, 1)),
                      **arrives)  # fmt: skip
top_ups = Flow("Top-ups from savings", cents(top_ups_in), "monthly", **arrives)  # type: ignore[arg-type]
now = Inputs(
    balance_cents=view.balance_cents, start=start, end=end,
    flows=(*view.flows, rent_flow, income_flow), daily_spend_cents=base.daily_spend_cents,
    buffer_cents=base.buffer_cents,
)  # fmt: skip
mine = Inputs(
    balance_cents=view.balance_cents, start=start, end=end,
    flows=(*switches, replace(rent_flow, cents=-cents(rent_in)),
           replace(income_flow, cents=cents(income_in)), top_ups),
    daily_spend_cents=cents(spend), buffer_cents=cents(buffer_in),
    one_offs=tuple(OneOff(i["day"], int(i["cents"]), str(i["name"])) for i in extras),  # type: ignore[arg-type, call-overload]
)  # fmt: skip

if end <= start:
    st.info(f"Pick an end date after {fmt_date(start)}, the latest day in your data.")
    footer(DISCLAIMER)
    st.stop()

now_result, mine_result = run(now), run(mine)
where = "in chequing and savings" if view.combined else "in chequing"
headline = f"You'd have {money(mine_result.end_balance, markdown=True)} {where} on {fmt_date(end)}"
if mine_result.first_below is not None:
    headline += f", and you'd drop below your buffer on {fmt_date(mine_result.first_below)}"
st.markdown(f"### {headline}.")
counted_items = [i for i in extras if not outside(i["day"])]  # type: ignore[arg-type]
if extras:
    net = sum(int(i["cents"]) for i in counted_items)  # type: ignore[call-overload]
    st.markdown(
        f'<p class="la-note">My scenario counts {len(counted_items)} of {len(extras)} one-off '
        f"{'item' if len(extras) == 1 else 'items'}: {html.escape(money(net, signed=True))} "
        "in all.</p>",
        unsafe_allow_html=True,
    )
st.markdown(
    f'<p class="la-note">As things are now: {html.escape(money(now_result.end_balance))} on '
    f"{fmt_date(end)}.</p>",
    unsafe_allow_html=True,
)
# The buffer and its marker are measured on the balance the chart shows (run() does this).
title = (
    "End-of-day balance, chequing and savings" if view.combined else "End-of-day balance, chequing"
)
chart = calculator_chart(start, view.balance_cents, now_result, mine_result, cents(buffer_in),
                         title=title)  # fmt: skip
st.plotly_chart(chart, width="stretch", theme=None, config={"displayModeBar": False})
st.markdown(
    f'<p class="la-note">{html.escape(NOT_A_FORECAST, quote=False)}</p>', unsafe_allow_html=True
)
footer(DISCLAIMER)
