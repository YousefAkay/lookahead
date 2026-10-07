"""Forecast: the balance for the next 30 days, from the data's last date, with every assumption.

A range from simulated futures built from your own past weeks, plus one estimate with no
randomness. It needs no confirmations: recurring payments are used as the app found them unless
you changed them on the Recurring page. The look lives in app/theme.py.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    apply,
    fit_y_range,
    fmt_date,
    fmt_day,
    humanize_dates,
    money,
    page_header,
    show_table,
    side_label,
    sidebar_brand,
    style_figure,
)
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

DESCRIPTION = "Your balance for the next 30 days, as a range, with every assumption shown."

apply("Forecast")
sidebar_brand()
header = st.container()  # filled once the data date is known, so the chip can sit in it


def show_header(updated: date | None = None) -> None:
    with header:
        page_header("Forecast", DESCRIPTION, updated)


# --- Sidebar: Data, Account, Forecast settings ---
side_label("Data")
source = st.sidebar.radio("Data", ["Synthetic demo", "My data"], label_visibility="collapsed")
if source == "Synthetic demo":
    db_path = demo_db_path()
    if not db_path.exists() or not demo_has_categories(db_path):
        with st.spinner("Building the synthetic demo database..."):
            build_demo_db(db_path, synthetic_dir())
else:
    db_path = private_db_path()
    if not db_path.exists():
        show_header()
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
    show_header()
    st.info("This database has no transactions yet.")
    st.stop()
side_label("Account")
account = st.sidebar.selectbox("Account", names, label_visibility="collapsed")

side_label("Forecast settings")
METHOD_LABELS = {"recent": "28-day average", "weekday": "Weekday average (8 weeks)"}
method = st.sidebar.selectbox(
    "Everyday spending estimate", list(BASELINES), format_func=METHOD_LABELS.get,
    index=list(BASELINES).index("weekday"),
)  # fmt: skip
st.sidebar.metric("Safety buffer", money(safety_buffer_cents(conn)))
with st.sidebar.form("buffer_form"):
    buffer_dollars = st.number_input(
        "New safety buffer ($)", min_value=0.0, step=50.0,
        value=safety_buffer_cents(conn) / 100, key="buffer",
    )  # fmt: skip
    if st.form_submit_button("Save buffer"):
        set_safety_buffer_cents(conn, round(buffer_dollars * 100))  # dollars only on screen
        st.rerun()

forecast = build_forecast(conn, account, method, None, stored_key)
if forecast is None:
    show_header()
    st.info("This account has no transactions yet.")
    st.stop()

start = forecast.start
show_header(start.data_last_updated)
status = import_status(conn, account)
if not status.reconciled:
    st.warning("Balances do not add up for this account (see the Transactions page). "
               "The starting balance may be wrong.")  # fmt: skip

# --- Where the forecast starts ---
today = date.today()
st.caption(
    f"The forecast starts on {fmt_date(start.first_day)}, the day after the data ends, and runs "
    f"{len(forecast.days)} days."
)
if today > start.first_day:
    st.caption(
        f"Today is {fmt_date(today)}: the days from {fmt_date(start.first_day)} up to today are "
        "forecast too, not observed. Import newer data to replace them."
    )

# --- The hero row, then the shortfall message ---
low_day, low_cents = forecast.lowest
first_below = forecast.first_below_buffer
sim = forecast.simulation
hero = st.columns(4 if sim is not None else 2)
hero[0].metric("Balance when the data ends", money(start.balance_cents))
hero[1].metric(
    "Lowest projected balance",
    money(low_cents),
    fmt_day(low_day),
    delta_color="off",
    delta_arrow="off",
)
if sim is not None:
    hero[2].metric("Chance of dipping below your buffer", f"{sim.shortfall_probability:.0%}")
    hero[3].metric("Chance of going below $0", f"{sim.below_zero_probability:.0%}")
    below = round(sim.shortfall_probability * sim.futures)
    message = (
        f"In {below} of {sim.futures} simulated futures the balance goes below your "
        f"{money(forecast.buffer_cents, markdown=True)} buffer on at least one day "
        f"({sim.shortfall_probability:.0%}); without irregular deposits it would be "
        f"{sim.shortfall_probability_without:.0%}. This is what the simulated futures say about "
        "the next 30 days, from your own past weeks; it is not tuned and not a guarantee."
    )
    if below:
        st.warning(message, icon=":material/warning:")
    else:
        st.success(message, icon=":material/check_circle:")
    st.caption(
        f"Lowest balance in the middle future: {money(sim.median_lowest_cents, markdown=True)}."
    )
elif first_below is not None:
    st.warning(
        "Possible shortfall: this estimate puts the balance below your "
        f"{money(forecast.buffer_cents, markdown=True)} buffer from {fmt_date(first_below)}.",
        icon=":material/warning:",
    )

# --- The chart: recent balance, then the forecast ---
rows = transactions(conn, account)
latest = rows["date"].max()
recent = rows[rows["date"] >= latest - pd.Timedelta(days=30)]
last_days = daily_balance(recent).tail(10)["balance ($)"].tolist()  # a few days of history
shown_values = [d.balance_cents / 100 for d in forecast.days] + [
    start.balance_cents / 100, 0.0, forecast.buffer_cents / 100, *last_days
]  # fmt: skip
if sim is not None:
    shown_values += [c / 100 for c in (*sim.p10, *sim.p90)]
chart = fit_y_range(
    style_figure(forecast_chart(daily_balance(recent), forecast, forecast.buffer_cents)),
    shown_values,
)
chart.update_layout(height=440)
st.plotly_chart(chart, width="stretch", theme=None, config={"displayModeBar": False})

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
    with st.container(border=True):
        controls = st.columns(3)
        with controls[0]:
            no_other = st.checkbox(
                "No other money expected (irregular deposits off)", key="wi_no_other",
                help="Only income series, planned deposits and deposits added here count as "
                "money in.",
            )  # fmt: skip
            delay = st.number_input("Money in arrives this many days later", 0, 60, 0, step=1,
                                    key="wi_delay")  # fmt: skip
            pick = st.selectbox(
                "Move a payment", list(range(len(forecast.items) + 1)), key="wi_move_pick",
                format_func=lambda i: "Don't move anything" if i == 0 else (
                    f"{fmt_date(forecast.items[i - 1].day)} · {pay_labels.iloc[i - 1]} · "
                    f"{money(forecast.items[i - 1].cents)}"
                ),
            )  # fmt: skip
            move_to = st.date_input("…to this day", value=start.first_day + timedelta(days=7),
                                    key="wi_move_to", format="YYYY/MM/DD")  # fmt: skip
        with controls[1]:
            mode = st.radio("Everyday spending", ["No change", "By a percent", "By dollars a week"],
                            key="wi_spend_mode")  # fmt: skip
            amount = st.number_input("Lower by (percent, or dollars a week)", min_value=0.0,
                                     value=10.0, step=5.0, key="wi_spend_value")  # fmt: skip
            spend_from = st.date_input("From", value=start.first_day, key="wi_spend_from",
                                       format="YYYY/MM/DD")  # fmt: skip
            spend_to = st.date_input("To", value=start.last_day, key="wi_spend_to",
                                     format="YYYY/MM/DD")  # fmt: skip
        with controls[2]:
            st.markdown("**Add a purchase or a deposit (this scenario only)**")
            extra_day = st.date_input("Day", value=start.first_day, key="wi_extra_date",
                                      format="YYYY/MM/DD")  # fmt: skip
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
                "from the same simulated futures. It is not a forecast of what will happen.",
                icon=":material/science:",
            )
            st.markdown("\n".join(f"- {line}".replace("$", "\\$") for line in result.changes))
            o, m = result.original, result.modified

            def points(change: float) -> str:
                return f"{change * 100:+.0f} points"

            cols = st.columns(4)
            cols[0].metric("Scenario: chance of dipping below your buffer",
                           f"{m.shortfall_probability:.0%}", points(result.shortfall_change),
                           delta_color="inverse")  # fmt: skip
            cols[1].metric("Scenario: chance of going below $0", f"{m.below_zero_probability:.0%}",
                           points(result.below_zero_change), delta_color="inverse")  # fmt: skip
            cols[2].metric("Scenario: median lowest balance", money(m.median_lowest_cents),
                           money(result.lowest_change, signed=True))  # fmt: skip
            cols[3].metric("Scenario: balance on the last day (middle future)", money(m.p50[-1]),
                           money(result.final_change, signed=True))  # fmt: skip
            st.caption(
                f"Original: {o.shortfall_probability:.0%} chance of dipping below the buffer, "
                f"{o.below_zero_probability:.0%} of going below $0, median lowest balance "
                f"{money(o.median_lowest_cents, markdown=True)}, middle future on the last day "
                f"{money(o.p50[-1], markdown=True)}."
            )
            compare = style_figure(scenario_chart(forecast, o, m, forecast.buffer_cents))
            compare.update_layout(height=420)
            st.plotly_chart(compare, width="stretch", theme=None, config={"displayModeBar": False})

# --- The explanations, as expanders ---
st.subheader("How this was worked out")
by_key = {s.key: s for s in forecast.series}
hidden = hidden_series(rows, list(forecast.series), display_key)
with st.expander("Assumptions behind this forecast", expanded=False, icon=":material/rule:"):
    counts = {
        b: sum(i.basis == b for i in forecast.items) for b in ("detected", "confirmed", "entered")
    }
    st.markdown(
        f"- **Starting point:** the balance after the last posting on "
        f"{fmt_date(start.data_last_updated)}. Only data posted on or before that date is used.\n"
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
        f"({money(round(forecast.fees_per_day), markdown=True)} a day over the last 28 days), "
        "until fees have their own model. Fees follow events such as an overdraft, which an "
        "average cannot see."
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
            f"last day: {money(sim.p50[-1], markdown=True)} with irregular deposits, "
            f"{money(sim.median_without[-1], markdown=True)} without."
        )
    for s in forecast.series:
        rule = rent_rule_of(s)
        if rule is not None and s.active and s.status != "rejected":
            st.markdown(
                f"- **{series_label(s, s.key in hidden)}:** assumed at the larger allowed amount "
                f"({money(rent_cents(rule), markdown=True)}), somewhere in its window (day "
                f"{rule.from_day} to day {rule.to_day}). The forecast places it on the earliest "
                "day of the window still ahead; a window that ended with no payment is expected "
                "on the first forecast day."
            )

with st.expander("What's in everyday spending", expanded=False, icon=":material/shopping_cart:"):
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
        show_table(spending, money=list(spending.columns[1:]))

with st.expander("What's in irregular deposits", expanded=False, icon=":material/savings:"):
    st.caption(
        "The simulated futures draw whole past weeks. For each week ahead a future picks one of "
        "the weeks below at random and takes that week's irregular deposits (and its spending). "
        "A week with a large deposit in the stream therefore adds it to about one in "
        f"{max(len(forecast.deposit_weeks), 1)} of the weeks ahead. Big deposits and "
        "pass-throughs are set aside (last column) and are never drawn. Deposits of recurring "
        "income series are not here: they are placed on their own dates."
    )
    if forecast.deposit_weeks:
        weeks = deposit_weeks_table(list(forecast.deposit_weeks))
        weeks["Week"] = [f"{fmt_day(w.first)} to {fmt_day(w.last)}" for w in forecast.deposit_weeks]
        weeks["Set aside"] = weeks["Set aside"].map(humanize_dates)
        show_table(weeks, money=["In the stream ($)"], whole=True,
                   widths={"Week": 125, "In the stream ($)": 105, "What": 230,
                           "Set aside": 300})  # fmt: skip
    else:
        st.caption("Not enough history for a whole past week.")

with st.expander("Big one-time events (not forecast)", expanded=bool(forecast.big_events),
                 icon=":material/priority_high:"):  # fmt: skip
    st.caption(
        "Big one-time rows of the last 13 weeks: a row of at least $500 that is at least 5 "
        "times the typical row, money out or money in. They are not everyday spending or "
        "irregular deposits, and they are not repeated in the forecast. A big deposit that "
        "went straight back out (outflows adding up to 85 to 115 percent of it within 14 "
        "days) is shown with those outflows as a numbered pass-through. A big deposit with no "
        "such outflows is a large deposit: when one arrives cannot be predicted from history."
    )
    if forecast.big_events:
        show_table(events_table(list(forecast.big_events)), money=["Amount ($)"], dates=["Date"])
    else:
        st.caption("No big one-time rows in the last 13 weeks.")
    if forecast.large_deposits:
        st.info(
            "You sometimes receive large deposits. If you expect one, add it as a planned "
            'deposit. Use "Planned items" below.'
        )

with st.expander("Planned items (money in or out that I enter)", expanded=bool(forecast.planned),
                 icon=":material/event:"):  # fmt: skip
    st.caption(
        "A payment or deposit you know is coming: a term payment, an expected deposit, a "
        'purchase. It is placed exactly on its day, labelled "entered", and never guessed '
        "or moved. Only days inside the forecast period change the forecast."
    )
    if forecast.planned:
        show_table(
            planned_table(list(forecast.planned), start.first_day, start.last_day),
            money=["Amount ($)"],
            dates=["Date", "Entered on"],
        )
    else:
        st.caption("No planned items.")
    with st.form("planned_form"):
        planned_day = st.date_input("Day", value=start.first_day, key="planned_date",
                                    format="YYYY/MM/DD")  # fmt: skip
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
            format_func=lambda i: f"{fmt_date(by_id[i].day)} · {by_id[i].description}",
        )  # fmt: skip
        if st.button("Remove planned item"):
            remove_planned(conn, account, gone)
            st.rerun()

with st.expander("Recurring payments placed in this period", expanded=False,
                 icon=":material/repeat:"):  # fmt: skip
    if forecast.items:
        show_table(
            forecast_table(forecast.items, by_key, hidden), money=["Amount ($)"], dates=["Date"]
        )
    else:
        st.caption("None: no active recurring series has a payment in this period.")

st.caption(DISCLAIMER)
