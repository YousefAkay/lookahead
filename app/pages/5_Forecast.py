"""Forecast: the balance for the next 30 days, from the data's last date, with every assumption.

This is a point forecast (one estimated path). It needs no confirmations: recurring payments are
used as the app found them unless you changed them on the Recurring page. Ranges from simulated
spending paths come later.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `lookahead` importable

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from lookahead.db import connect  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.forecast.path import BASELINES, build_forecast  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring_store import rent_rule_of  # noqa: E402
from lookahead.rent import rent_cents  # noqa: E402
from lookahead.settings import safety_buffer_cents, set_safety_buffer_cents  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    breakdown_table,
    daily_balance,
    demo_db_path,
    events_table,
    forecast_chart,
    forecast_table,
    hidden_series,
    private_db_path,
    series_label,
    synthetic_dir,
)
from lookahead.views import accounts, import_status, transactions  # noqa: E402


def dollars(cents: int) -> str:
    """Money for text that Streamlit renders as markdown (a bare $ would start a formula)."""
    return f"\\${cents / 100:,.2f}"


st.set_page_config(page_title="Forecast · Lookahead", layout="wide")
st.title("Forecast")

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

conn = connect(db_path)  # this page saves one setting: the safety buffer
names = accounts(conn)
if not names:
    st.info("This database has no transactions yet.")
    st.stop()
account = st.sidebar.selectbox("Account", names)

method = st.sidebar.selectbox(
    "Everyday spending is estimated as the", list(BASELINES), format_func=BASELINES.get,
    index=list(BASELINES).index("weekday"),
)  # fmt: skip
with st.sidebar.form("buffer_form"):
    buffer_dollars = st.number_input(
        "Safety buffer ($)", min_value=0.0, step=50.0,
        value=safety_buffer_cents(conn) / 100, key="buffer",
    )  # fmt: skip
    if st.form_submit_button("Save buffer"):
        set_safety_buffer_cents(conn, round(buffer_dollars * 100))  # dollars only on screen
        st.rerun()

forecast = build_forecast(conn, account, method, None, stored_key)
if forecast is None:
    st.info("This account has no transactions yet.")
    st.stop()

start = forecast.start
status = import_status(conn, account)
if not status.reconciled:
    st.warning("Balances do not add up for this account (see the Transactions page). "
               "The starting balance may be wrong.")  # fmt: skip

# --- Where the forecast starts ---
today = date.today()
st.caption(
    f"Data last updated: {start.data_last_updated}. The forecast starts on {start.first_day}, "
    f"the day after the data ends, and runs {len(forecast.days)} days."
)
if today > start.first_day:
    st.caption(
        f"Today is {today}: the days from {start.first_day} up to today are forecast too, "
        "not observed. Import newer data to replace them."
    )

low_day, low_cents = forecast.lowest
first_below = forecast.first_below_buffer
metrics = st.columns(3)
metrics[0].metric("Balance when the data ends", f"${start.balance_cents / 100:,.2f}")
metrics[1].metric(
    "Lowest projected balance", f"${low_cents / 100:,.2f}", str(low_day), delta_color="off"
)
metrics[2].metric("Safety buffer", f"${forecast.buffer_cents / 100:,.2f}")
sim = forecast.simulation
if sim is not None:
    chance = st.columns(3)
    chance[0].metric("Chance of dipping below your buffer", f"{sim.shortfall_probability:.0%}")
    chance[1].metric("Chance of going below $0", f"{sim.below_zero_probability:.0%}")
    chance[2].metric(
        "Lowest balance in the middle future", f"${sim.median_lowest_cents / 100:,.2f}"
    )
    below = round(sim.shortfall_probability * sim.futures)
    message = (
        f"In {below} of {sim.futures} simulated futures the balance goes below your "
        f"{dollars(forecast.buffer_cents)} buffer on at least one day "
        f"({sim.shortfall_probability:.0%}); without irregular deposits it would be "
        f"{sim.shortfall_probability_without:.0%}. This is what the simulated futures say about "
        "the next 30 days, from your own past weeks; it is not tuned and not a guarantee."
    )
    (st.warning if below else st.info)(message)
elif first_below is not None:
    st.warning(
        "Possible shortfall: this estimate puts the balance below your "
        f"{dollars(forecast.buffer_cents)} buffer from {first_below}."
    )

# --- The chart: recent balance, then the forecast ---
rows = transactions(conn, account)
latest = rows["date"].max()
recent = rows[rows["date"] >= latest - pd.Timedelta(days=30)]
st.plotly_chart(
    forecast_chart(daily_balance(recent), forecast, forecast.buffer_cents), width="stretch"
)

# --- Every assumption ---
by_key = {s.key: s for s in forecast.series}
hidden = hidden_series(rows, list(forecast.series), display_key)
with st.expander("Assumptions behind this forecast", expanded=True):
    counts = {
        b: sum(i.basis == b for i in forecast.items) for b in ("detected", "confirmed", "entered")
    }
    st.markdown(
        f"- **Starting point:** the balance after the last posting on {start.data_last_updated}. "
        f"Only data posted on or before that date is used.\n"
        f"- **Recurring payments:** {len(forecast.items)} placed in the period "
        f"({counts['detected']} detected, {counts['confirmed']} confirmed, "
        f"{counts['entered']} entered by me). Detected ones are used as found; you can "
        "confirm or change them on the Recurring page.\n"
        f"- **Everyday spending:** the {forecast.method}. It includes transfers to people and to "
        "savings that are not part of a recurring series. Big one-time rows (below) are left "
        "out."
    )
    st.markdown(
        f"- **Bank fees:** estimated at their recent daily average "
        f"({dollars(round(forecast.fees_per_day))} a day over the last 28 days), until fees "
        "have their own model. Fees follow events such as an overdraft, which an average "
        "cannot see."
    )
    if forecast.has_income:
        st.markdown(
            "- **Income:** the income series on the Recurring page, on their expected dates; "
            "other money in is estimated from your history (below)."
        )
    else:
        st.markdown(
            "- **Income:** no regular income found; irregular deposits estimated from your "
            "history (below)."
        )
    if sim is not None:
        st.markdown(
            f"- **Simulated futures:** {sim.futures}. Each draws, for every week ahead, one "
            f"whole past week from the last 13 weeks ({sim.weeks_drawn_from} different weeks "
            "here) and takes that week's everyday spending and irregular deposits together. "
            "Irregular deposits are money in that is in no series, not a refund and not a big "
            "one-time row. Rent falls inside its window, weighted by your own past rent dates; "
            "a detected payment's date moves by its own past offsets; confirmed and entered "
            "dates stay. The draws are not rescaled to the point forecast. Middle future on the "
            f"last day: {dollars(sim.p50[-1])} with irregular deposits, "
            f"{dollars(sim.median_without[-1])} without."
        )
    for s in forecast.series:
        rule = rent_rule_of(s)
        if rule is not None and s.active and s.status != "rejected":
            st.markdown(
                f"- **{series_label(s, s.key in hidden)}:** assumed at the larger allowed amount "
                f"({dollars(rent_cents(rule))}), somewhere in its window (day {rule.from_day} to "
                f"day {rule.to_day}). The forecast places it on the earliest day of the window "
                "still ahead; a window that ended with no payment is expected on the first "
                "forecast day."
            )

with st.expander("What's in everyday spending", expanded=False):
    st.caption(
        "The average spent on each weekday over the last 8 weeks, by type. The weekday "
        "estimate adds these up over the weekdays of the forecast, so one large row on one "
        "weekday would show here as a high average for that weekday, so big one-time rows "
        "(listed below) are not in this table. Payments of recurring series are not in it "
        "either: they are placed on their own dates. Bank fees are not in it."
    )
    spending = breakdown_table(list(forecast.breakdown))
    if spending.empty:
        st.caption("No spending in the last 8 weeks.")
    else:
        st.dataframe(
            spending, hide_index=True, width="stretch",
            column_config={
                c: st.column_config.NumberColumn(format="%.2f") for c in spending.columns[1:]
            },
        )  # fmt: skip

with st.expander("Big one-time events (not forecast)", expanded=bool(forecast.big_events)):
    st.caption(
        "Big one-time rows of the last 13 weeks: a row of at least $300 that is at least 5 "
        "times the typical row, money out or money in. They are not everyday spending or "
        "irregular deposits, and they are not repeated in the forecast. Add a planned payment "
        "(for example a term payment) if one is coming."
    )
    if forecast.big_events:
        st.dataframe(
            events_table(list(forecast.big_events)), hide_index=True, width="stretch",
            column_config={"Amount ($)": st.column_config.NumberColumn(format="%.2f")},
        )  # fmt: skip
    else:
        st.caption("No big one-time rows in the last 13 weeks.")

st.subheader("Recurring payments placed in this period")
if forecast.items:
    st.dataframe(
        forecast_table(forecast.items, by_key, hidden), hide_index=True, width="stretch",
        column_config={"Amount ($)": st.column_config.NumberColumn(format="%.2f")},
    )  # fmt: skip
else:
    st.caption("None: no active recurring series has a payment in this period.")

st.caption(DISCLAIMER)
