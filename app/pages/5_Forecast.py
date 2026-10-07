"""Forecast: the balance for the next 30 days, from the data's last date, with every assumption.

This is a point forecast (one estimated path). It needs no confirmations: recurring payments are
used as the app found them unless you changed them on the Recurring page. Ranges from simulated
spending paths come later.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `lookahead` importable

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from lookahead.db import connect  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.forecast.path import BASELINES, build_forecast  # noqa: E402
from lookahead.forecast.scenario import (  # noqa: E402
    ExtraItem,
    MovedBill,
    Scenario,
    SpendingChange,
    run_scenario,
)
from lookahead.planned import add_planned, remove_planned  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring_store import rent_rule_of  # noqa: E402
from lookahead.rent import rent_cents  # noqa: E402
from lookahead.settings import safety_buffer_cents, set_safety_buffer_cents  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    breakdown_table,
    daily_balance,
    demo_db_path,
    deposit_weeks_table,
    events_table,
    forecast_chart,
    forecast_table,
    hidden_series,
    planned_table,
    private_db_path,
    scenario_chart,
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

# --- What if? A conditional scenario, run on the same simulated futures ---
st.subheader("What if…?")
st.caption(
    "Change one or more things and the same simulated futures run again, so the difference is the "
    "change itself. Nothing here is saved; saved planned items are entered further down."
)
if sim is None:
    st.caption("No simulated futures for this account yet.")
else:
    pay_labels = forecast_table(forecast.items, {s.key: s for s in forecast.series}, set())["Name"]
    controls = st.columns(3)
    with controls[0]:
        no_other = st.checkbox(
            "No other money expected (irregular deposits off)", key="wi_no_other",
            help="Only income series, planned deposits and deposits added here count as money in.",
        )  # fmt: skip
        delay = st.number_input("Money in arrives this many days later", 0, 60, 0, step=1,
                                key="wi_delay")  # fmt: skip
        pick = st.selectbox(
            "Move a payment", list(range(len(forecast.items) + 1)), key="wi_move_pick",
            format_func=lambda i: "Don't move anything" if i == 0 else (
                f"{forecast.items[i - 1].day} · {pay_labels.iloc[i - 1]} · "
                f"${forecast.items[i - 1].cents / 100:,.2f}"
            ),
        )  # fmt: skip
        move_to = st.date_input("…to this day", value=start.first_day + timedelta(days=7),
                                key="wi_move_to")  # fmt: skip
    with controls[1]:
        mode = st.radio("Everyday spending", ["No change", "By a percent", "By dollars a week"],
                        key="wi_spend_mode")  # fmt: skip
        amount = st.number_input("Lower by (percent, or dollars a week)", min_value=0.0, value=10.0,
                                 step=5.0, key="wi_spend_value")  # fmt: skip
        spend_from = st.date_input("From", value=start.first_day, key="wi_spend_from")
        spend_to = st.date_input("To", value=start.last_day, key="wi_spend_to")
    with controls[2]:
        st.markdown("**Add a purchase or a deposit (this scenario only)**")
        extra_day = st.date_input("Day", value=start.first_day, key="wi_extra_date")
        extra_way = st.radio("Direction", ["Money out", "Money in"], horizontal=True,
                             key="wi_extra_way")  # fmt: skip
        extra_amount = st.number_input("Amount ($, 0 for none)", min_value=0.0, value=0.0,
                                       step=50.0, key="wi_extra_amount")  # fmt: skip
        extra_what = st.text_input("What it is", key="wi_extra_what")
    changed = bool(no_other or delay or mode != "No change" or extra_amount > 0 or pick > 0)
    if not changed:
        st.caption("Change something above to see what would happen.")
    else:
        try:
            result = run_scenario(
                forecast,
                Scenario(
                    spending=None
                    if mode == "No change"
                    else SpendingChange(
                        spend_from,
                        spend_to,
                        percent=amount if mode == "By a percent" else None,
                        per_week_cents=round(amount * 100) if mode == "By dollars a week" else None,
                    ),
                    extras=()
                    if extra_amount <= 0
                    else (
                        ExtraItem(
                            extra_day,
                            (1 if extra_way == "Money in" else -1) * round(extra_amount * 100),
                            extra_what,
                        ),
                    ),
                    delay_money_in_days=int(delay),
                    moved=()
                    if pick == 0
                    else (
                        MovedBill(
                            forecast.items[pick - 1].series_key,
                            forecast.items[pick - 1].day,
                            move_to,
                        ),
                    ),
                    no_other_money=bool(no_other),
                ),
            )
        except ValueError as problem:
            st.warning(f"Could not run the scenario: {problem}")
        else:
            st.info(
                "Conditional scenario: this shows what would happen if these changes were made, "
                "from the same simulated futures. It is not a forecast of what will happen."
            )
            st.markdown("\n".join(f"- {line}".replace("$", "\\$") for line in result.changes))
            o, m = result.original, result.modified

            def points(change: float) -> str:
                return f"{change * 100:+.0f} points"

            def money_change(cents: int) -> str:
                return f"{'+' if cents >= 0 else '-'}${abs(cents) / 100:,.2f}"

            cols = st.columns(4)
            cols[0].metric("Scenario: chance of dipping below your buffer",
                           f"{m.shortfall_probability:.0%}", points(result.shortfall_change),
                           delta_color="inverse")  # fmt: skip
            cols[1].metric("Scenario: chance of going below $0", f"{m.below_zero_probability:.0%}",
                           points(result.below_zero_change), delta_color="inverse")  # fmt: skip
            lowest = f"${m.median_lowest_cents / 100:,.2f}"
            cols[2].metric("Scenario: median lowest balance", lowest,
                           money_change(result.lowest_change))  # fmt: skip
            last = f"${m.p50[-1] / 100:,.2f}"
            cols[3].metric("Scenario: balance on the last day (middle future)", last,
                           money_change(result.final_change))  # fmt: skip
            st.caption(
                f"Original: {o.shortfall_probability:.0%} chance of dipping below the buffer, "
                f"{o.below_zero_probability:.0%} of going below $0, median lowest balance "
                f"{dollars(o.median_lowest_cents)}, middle future on the last day "
                f"{dollars(o.p50[-1])}."
            )
            st.plotly_chart(scenario_chart(forecast, o, m, forecast.buffer_cents), width="stretch")

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
    if forecast.planned:
        inside = sum(start.first_day <= p.day <= start.last_day for p in forecast.planned)
        st.markdown(
            f"- **Planned items:** {len(forecast.planned)} entered by me, {inside} inside this "
            "period, placed exactly on their days."
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
        "Big one-time rows of the last 13 weeks: a row of at least $500 that is at least 5 "
        "times the typical row, money out or money in. They are not everyday spending or "
        "irregular deposits, and they are not repeated in the forecast. A big deposit that "
        "went straight back out (outflows adding up to 85 to 115 percent of it within 14 "
        "days) is shown with those outflows as a numbered pass-through. A big deposit with no "
        "such outflows is a large deposit: when one arrives cannot be predicted from history."
    )
    if forecast.big_events:
        st.dataframe(
            events_table(list(forecast.big_events)), hide_index=True, width="stretch",
            column_config={"Amount ($)": st.column_config.NumberColumn(format="%.2f")},
        )  # fmt: skip
    else:
        st.caption("No big one-time rows in the last 13 weeks.")
    if forecast.large_deposits:
        st.info(
            "You sometimes receive large deposits. If you expect one, add it as a planned "
            'deposit. Use "Planned items" below.'
        )

with st.expander("What's in irregular deposits", expanded=False):
    st.caption(
        "The simulated futures draw whole past weeks. For each week ahead a future picks one of "
        "the weeks below at random and takes that week's irregular deposits (and its spending). "
        "A week with a large deposit in the stream therefore adds it to about one in "
        f"{max(len(forecast.deposit_weeks), 1)} of the weeks ahead. Big deposits and "
        "pass-throughs are set aside (last column) and are never drawn. Deposits of recurring "
        "income series are not here: they are placed on their own dates."
    )
    if forecast.deposit_weeks:
        st.dataframe(
            deposit_weeks_table(list(forecast.deposit_weeks)), hide_index=True, width="stretch",
            column_config={"In the stream ($)": st.column_config.NumberColumn(format="%.2f")},
        )  # fmt: skip
    else:
        st.caption("Not enough history for a whole past week.")

with st.expander("Planned items (money in or out that I enter)", expanded=bool(forecast.planned)):
    st.caption(
        "A payment or deposit you know is coming: a term payment, an expected deposit, a "
        'purchase. It is placed exactly on its day, labelled "entered", and never guessed '
        "or moved. Only days inside the forecast period change the forecast."
    )
    if forecast.planned:
        st.dataframe(
            planned_table(list(forecast.planned), start.first_day, start.last_day),
            hide_index=True, width="stretch",
            column_config={"Amount ($)": st.column_config.NumberColumn(format="%.2f")},
        )  # fmt: skip
    else:
        st.caption("No planned items.")
    with st.form("planned_form"):
        planned_day = st.date_input("Day", value=start.first_day, key="planned_date")
        planned_way = st.radio("Direction", ["Money out", "Money in"], horizontal=True,
                               key="planned_direction")  # fmt: skip
        planned_size = st.number_input("Amount ($)", min_value=0.0, step=50.0, key="planned_amount")
        planned_what = st.text_input("What it is", key="planned_description")
        add_one = st.form_submit_button("Add planned item")
    if add_one and isinstance(planned_day, date):
        sign = 1 if planned_way == "Money in" else -1
        try:  # dollars only on screen; cents are stored
            add_planned(conn, account, planned_day, sign * round(planned_size * 100), planned_what)
        except ValueError as problem:
            st.warning(f"Could not save: {problem}")
        else:
            st.rerun()
    if forecast.planned:
        by_id = {p.item_id: p for p in forecast.planned}
        gone = st.selectbox(
            "Remove an item", list(by_id), key="planned_remove",
            format_func=lambda i: f"{by_id[i].day} · {by_id[i].description}",
        )  # fmt: skip
        if st.button("Remove planned item"):
            remove_planned(conn, account, gone)
            st.rerun()

st.subheader("Recurring payments placed in this period")
if forecast.items:
    st.dataframe(
        forecast_table(forecast.items, by_key, hidden), hide_index=True, width="stretch",
        column_config={"Amount ($)": st.column_config.NumberColumn(format="%.2f")},
    )  # fmt: skip
else:
    st.caption("None: no active recurring series has a payment in this period.")

st.caption(DISCLAIMER)
