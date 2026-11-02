"""Scenario: "what if I spend X a day". A calculator, not a forecast.

The inputs start from what the database says (lookahead/scenario_prefill.py); the sums are exact
(lookahead/scenario.py). Two lines: "as things are now" (the prefilled values) and "my scenario".
No uncertainty band: this assumes exactly what was entered.
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
from lookahead.scenario_prefill import ANCHOR_YEAR, prefill  # noqa: E402
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
RENT_DAY_NOTE = "No rent rule found: rent entered here is placed on the 1st of each month."


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
        f'<p class="la-note">Starting from your balance on {fmt_date(start)}, the latest day in '
        f"your data: {money(base.balance_cents)}.</p>",
        unsafe_allow_html=True,
    )
if base.missing:
    st.markdown(
        f'<p class="la-note">Could not be estimated from your data, so it starts at $0: '
        f"{html.escape(', '.join(base.missing))}.</p>",
        unsafe_allow_html=True,
    )


def label(flow: Flow) -> str:
    s = by_key.get(flow.key)
    return series_label(s, hide=s.key in hidden) if s is not None else flow.name


# --- Inputs ---
with st.container(border=True, key="card-inputs"):
    st.markdown("<h2>Your scenario</h2>", unsafe_allow_html=True)
    left, middle, right = st.columns(3)
    with left:
        spend = st.number_input("Daily spend ($)", min_value=0.0, step=1.0, key="sc_spend",
                                value=base.daily_spend_cents / 100)  # fmt: skip
        income_in = st.number_input(
            "Income per month ($)", min_value=0.0, step=10.0, key="sc_income",
            value=(base.income.cents / 100) if base.income else 0.0,
            help="Income not already in a confirmed series, on its usual day.",
        )  # fmt: skip
    with middle:
        rent_in = st.number_input("Rent per month ($)", min_value=0.0, step=10.0, key="sc_rent",
                                  value=-base.rent.cents / 100 if base.rent else 0.0)  # fmt: skip
        if base.rent is None:
            st.caption(RENT_DAY_NOTE)
    with right:
        end = st.date_input("End date", value=default_end(date.today()), key="sc_end")
        buffer_in = st.number_input("Buffer ($)", min_value=0.0, step=10.0, key="sc_buffer",
                                    value=base.buffer_cents / 100)  # fmt: skip

    switches: list[Flow] = []
    if base.flows:
        st.markdown(
            '<p class="la-note"><strong>Recurring payments</strong> (confirmed or added by you): '
            "switch one off to see the difference.</p>",
            unsafe_allow_html=True,
        )
        columns = st.columns(2)
        for i, flow in enumerate(base.flows):
            with columns[i % 2]:
                on = st.toggle(f"{label(flow)} ({money(flow.cents, signed=True)} {flow.cadence})",
                               value=True, key=f"sc_flow|{flow.key or i}")  # fmt: skip
            switches.append(replace(flow, on=on))
    else:
        st.markdown('<p class="la-note">No confirmed recurring payments yet: confirm them on the '
                    "Recurring page to see them here.</p>", unsafe_allow_html=True)  # fmt: skip

    st.markdown('<p class="la-note"><strong>One-off items</strong></p>', unsafe_allow_html=True)
    extras: list[dict[str, object]] = st.session_state.setdefault("sc_extras", [])
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
        if st.button("Add", key="sc_extra_add") and size > 0:
            sign = 1 if direction == "Money in" else -1
            extras.append({"name": what, "day": when, "cents": sign * cents(size)})
            st.rerun()
    for i, item in enumerate(list(extras)):
        text_col, remove_col = st.columns([4, 1], vertical_alignment="center")
        with text_col:
            st.markdown(f"{fmt_date(item['day'])}: {html.escape(str(item['name']) or 'one-off')} "  # type: ignore[arg-type]
                        f"{money(int(item['cents']), signed=True, markdown=True)}")  # type: ignore[call-overload]  # fmt: skip
        with remove_col:
            if st.button("Remove", key=f"sc_extra_remove|{i}"):
                extras.pop(i)
                st.rerun()

# --- The two lines ---
rent_flow = base.rent or Flow("Rent", 0, "monthly", date(ANCHOR_YEAR, 1, 1), card=True)
income_flow = base.income or Flow("Income", 0, "monthly", date(ANCHOR_YEAR, 1, 1))
now = Inputs(
    balance_cents=base.balance_cents, start=start, end=end,
    flows=(*base.flows, rent_flow, income_flow), daily_spend_cents=base.daily_spend_cents,
    buffer_cents=base.buffer_cents,
)  # fmt: skip
mine = Inputs(
    balance_cents=base.balance_cents, start=start, end=end,
    flows=(*switches, replace(rent_flow, cents=-cents(rent_in)),
           replace(income_flow, cents=cents(income_in))),
    daily_spend_cents=cents(spend), buffer_cents=cents(buffer_in),
    one_offs=tuple(OneOff(i["day"], int(i["cents"]), str(i["name"])) for i in extras),  # type: ignore[arg-type, call-overload]
)  # fmt: skip

if end <= start:
    st.info(f"Pick an end date after {fmt_date(start)}, the latest day in your data.")
    footer(DISCLAIMER)
    st.stop()

now_result, mine_result = run(now), run(mine)
headline = f"You'd have {money(mine_result.end_balance, markdown=True)} on {fmt_date(end)}"
if mine_result.first_below is not None:
    headline += f", and you'd drop below your buffer on {fmt_date(mine_result.first_below)}"
st.markdown(f"### {headline}.")
st.markdown(
    f'<p class="la-note">As things are now: {html.escape(money(now_result.end_balance))} on '
    f"{fmt_date(end)}.</p>",
    unsafe_allow_html=True,
)
chart = calculator_chart(start, base.balance_cents, now_result, mine_result, cents(buffer_in))
st.plotly_chart(chart, width="stretch", theme=None, config={"displayModeBar": False})
st.markdown(
    f'<p class="la-note">{html.escape(NOT_A_FORECAST, quote=False)}</p>', unsafe_allow_html=True
)
footer(DISCLAIMER)
