"""Forecast: the balance for the next 30 days, from the data's last date, with every assumption.

A range from simulated futures built from your own past weeks. It needs no confirmations:
recurring payments are used as the app found them unless you changed them on the Recurring page.
The look (the target design) lives in app/theme.py.
"""

import html
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    COLORS,
    account_setting,
    alert_episodes_html,
    apply,
    cached_safe_to_spend,
    callout_html,
    chart_legend_html,
    choice_query,
    coming_up_html,
    data_sources,
    demo_database,
    fit_y_range,
    fmt_date,
    fmt_day,
    footer,
    humanize_dates,
    money,
    money_short,
    result_panel_html,
    safe_to_spend_card,
    scenario_legend_html,
    seed_choice,
    show_table,
    side_label,
    style_forecast_chart,
    style_scenario_chart,
    tag_html,
    top_bar,
)
from lookahead import demo_mode  # noqa: E402
from lookahead.alerts import (  # noqa: E402
    RULE_LABEL,
    alert_message,
    dismiss,
    episodes,
    record_run,
    restore,
    snapshot_of,
)
from lookahead.chance import chance_text  # noqa: E402
from lookahead.db import connect  # noqa: E402
from lookahead.forecast.path import BASELINES, build_forecast  # noqa: E402
from lookahead.forecast.scenario import (  # noqa: E402
    ExtraItem,
    MovedBill,
    Scenario,
    SpendingChange,
    run_scenario,
)
from lookahead.forecast.simulate import N_FUTURES  # noqa: E402
from lookahead.planned import add_planned, remove_planned  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.recurring_store import rent_rule_of  # noqa: E402
from lookahead.rent import rent_cents  # noqa: E402
from lookahead.settings import safety_buffer_cents, set_safety_buffer_cents  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    breakdown_table,
    daily_balance,
    deposit_weeks_table,
    events_table,
    forecast_chart,
    forecast_table,
    hidden_series,
    planned_table,
    private_db_path,
    scenario_chart,
    series_label,
)
from lookahead.views import accounts, import_status, transactions  # noqa: E402


def link_query(page: str) -> str:
    """A link to another page that keeps the data, account and demo visit choice."""
    query = choice_query()
    return page + (f"?{query}" if query else "")


WAITING = f"Simulating {N_FUTURES} futures… This takes a few seconds."

apply("Forecast")
bar = st.container(key="topbar")  # the green bar: drawn into once the data date is known

# --- Settings (the sidebar): data, account, forecast settings ---
side_label("Data")
SOURCES = data_sources()  # synthetic only in demo mode
seed_choice("data_source", SOURCES)
source = st.sidebar.radio("Data", SOURCES, label_visibility="collapsed", key="data_source")
if source == "Synthetic demo":
    db_path = demo_database()
else:
    db_path = private_db_path()
    if not db_path.exists():
        top_bar(bar, "Forecast", None, source)
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

seed_choice("pseudonymize")
pseudonymize = st.sidebar.toggle(
    "Pseudonymize text", key="pseudonymize",
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
    top_bar(bar, "Forecast", None, source)
    st.info("This database has no transactions yet.")
    st.stop()
account = account_setting(names)

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

wait = st.empty()  # the futures take several seconds: say so at once, never a blank page
wait.info(WAITING)
forecast = build_forecast(conn, account, method, None, stored_key)
wait.empty()
if forecast is None:
    top_bar(bar, "Forecast", None, source)
    st.info("This account has no transactions yet.")
    st.stop()

start = forecast.start
sim = forecast.simulation
top_bar(bar, "Forecast", start.data_last_updated, source)
DEFAULT_METHOD = "weekday"
snapshot = snapshot_of(forecast)  # the alert rule, applied to this forecast
if method == DEFAULT_METHOD:  # alerts follow the default estimate, not every one I try out
    record_run(conn, account, forecast)
status = import_status(conn, account)
rows = transactions(conn, account)
hidden = hidden_series(rows, list(forecast.series), display_key)
by_key = {s.key: s for s in forecast.series}
names_in_order = list(forecast.items and forecast_table(forecast.items, by_key, hidden)["Name"])

# --- Title row ---
today = demo_mode.today(start.first_day - timedelta(days=1))
title_col, action_col = st.columns([4, 1.3], vertical_alignment="bottom")
with title_col:
    st.markdown(
        f"<h1>Next {len(forecast.days)} days</h1>"
        f'<p class="la-sub">{fmt_day(start.first_day)} – {fmt_date(start.last_day)} · '
        f"{html.escape(account)} · buffer {html.escape(money_short(forecast.buffer_cents))}</p>"
        f'<p class="la-note">The forecast starts on {fmt_date(start.first_day)}, the day after '
        "the data ends."
        + (
            f" Today is {fmt_date(today)}: the days up to today are forecast too, not observed."
            if today > start.first_day
            else ""
        )
        + "</p>",
        unsafe_allow_html=True,
    )
with action_col:
    if st.button("New planned item", type="primary", use_container_width=True):
        st.session_state["open_planned"] = True
if not status.reconciled:
    st.warning("Balances do not add up for this account (see the Transactions page). "
               "The starting balance may be wrong.")  # fmt: skip

# --- The one-sentence callout, then four number cards ---
low_day, low_cents = forecast.lowest  # the lowest of the no-randomness estimate
middle_note = "in the estimate"
if sim is not None:  # the design's "lowest expected balance" is the middle future's lowest point
    k_low = min(range(len(sim.p50)), key=lambda k: (sim.p50[k], k))
    low_day, low_cents = sim.days[k_low], sim.p50[k_low]
    middle_note = "in the middle future"
spending_per_day = round(sum(d.spending_cents for d in forecast.days) / len(forecast.days))
buffer_text = html.escape(money_short(forecast.buffer_cents))
if sim is not None:
    below = round(sim.shortfall_probability * sim.futures)
    below_zero = round(sim.below_zero_probability * sim.futures)
    spent = f"Everyday spending is about {html.escape(money(spending_per_day))} a day."
    chance = html.escape(chance_text(sim.shortfall_probability))  # "<1%" inside HTML
    without_chance = html.escape(chance_text(sim.shortfall_probability_without))
    without = f"(without irregular deposits: {without_chance})"
    if snapshot is not None:  # the rule alerts
        sentence = (
            f"<strong>{chance} chance</strong> your balance dips below your "
            f"{buffer_text} buffer around <strong>{fmt_day(low_day)}</strong> {without}. {spent}"
        )
        callout = callout_html("warning", "Possible shortfall", sentence, "See why →", "#alerts")
    elif below:
        sentence = (
            f"<strong>Only {chance}</strong> of simulated futures dip below "
            f"your {buffer_text} buffer, around {fmt_day(low_day)} {without}: not enough to "
            f"alert. {spent}"
        )
        callout = callout_html("ok", "On track", sentence, "See why →", "#evidence")
    else:
        sentence = (
            f"<strong>No simulated future</strong> dips below your {buffer_text} buffer in the "
            f"next {len(forecast.days)} days {without}. {spent}"
        )
        callout = callout_html("ok", "On track", sentence, "See why →", "#evidence")
    st.markdown(callout, unsafe_allow_html=True)
elif forecast.first_below_buffer is not None:
    st.warning(
        "Possible shortfall: this estimate puts the balance below your "
        f"{money(forecast.buffer_cents, markdown=True)} buffer from "
        f"{fmt_date(forecast.first_below_buffer)}."
    )

cards = st.columns(4 if sim is not None else 2)
with cards[0]:
    st.metric(f"Balance on {fmt_day(start.data_last_updated)}", money(start.balance_cents),
              "Last imported posting", delta_color="off", delta_arrow="off")  # fmt: skip
with cards[1]:
    st.metric(
        "Lowest expected balance", money(low_cents), f"{fmt_day(low_day)}, {middle_note}",
        delta_color="off", delta_arrow="off",
    )  # fmt: skip
if sim is not None:
    with cards[2], st.container(key="tone-warn" if below else "tone-ok"):
        st.metric("Chance below buffer", f"{chance_text(sim.shortfall_probability)}",
                  f"{below} of {sim.futures} simulated futures", delta_color="off",
                  delta_arrow="off")  # fmt: skip
    with cards[3]:
        st.metric("Chance below $0", f"{chance_text(sim.below_zero_probability)}",
                  f"{below_zero} of {sim.futures} simulated futures", delta_color="off",
                  delta_arrow="off")  # fmt: skip

# --- Safe to spend: the everyday spending a day that keeps 80% of futures above the buffer ---
if sim is not None:
    version = (start, forecast.buffer_cents,
               tuple((i.day, i.cents, i.series_key) for i in forecast.items))  # fmt: skip
    safe_to_spend_card(cached_safe_to_spend(str(db_path), version, account, stored_key))

# --- Shortfall alerts: the current one with its evidence, then the earlier ones ---


series_by_key = {s.key: s for s in forecast.series}


def shown_name(entry: dict) -> str:  # type: ignore[type-arg]
    """A payment's name as shown: hidden when its series shows a person's name, and a plain
    description (such as "e-Transfers of one amount") when the series has no name."""
    series = series_by_key.get(entry["series_key"])
    if series is not None:
        return series_label(series, series.key in hidden)
    return entry["name"] or "Payment"  # a planned item: the words I typed


def safe(text: str) -> str:
    return html.escape(text).replace("$", "&#36;")  # a dollar sign would start maths in markdown


mine = episodes(conn, account)
current = next((e for e in mine if e.closed_on is None), None)  # the open one, dismissed or not
st.markdown('<div id="alerts"></div>', unsafe_allow_html=True)
with st.container(border=True, key="card-alerts"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
        f"<h2>Shortfall alerts</h2>{tag_html(RULE_LABEL, 'neutral')}</div>"
        '<p class="la-note">The rule, chosen on made-up development accounts: alert when the '
        "chance of dipping below your buffer is above 35%, or the middle future's lowest balance "
        "is below it. Alerts follow the weekday average.</p>",
        unsafe_allow_html=True,
    )
    if current is not None and current.status == "open" and snapshot is not None:
        st.markdown(f'<p style="font-size: 16px; margin: 0">{safe(alert_message(snapshot))}</p>',
                    unsafe_allow_html=True)  # fmt: skip
        ev = current.evidence
        with st.expander("Evidence for this alert"):
            low_text = (
                f"**Lowest balance:** {safe(money(ev['low_cents']))} in the middle future on "
                f"{fmt_date(date.fromisoformat(ev['low_day']))}. Chance below your "
                f"{safe(money_short(ev['buffer_cents']))} buffer: "
                f"{safe(chance_text(ev['probability']))} "
                f"(without irregular deposits: "
                f"{safe(chance_text(ev['probability_without_deposits']))}); median "
                f"lowest balance across the {ev['futures']} futures: "
                f"{safe(money(ev['median_lowest_cents']))}."
            )
            st.markdown(low_text, unsafe_allow_html=True)
            st.markdown("**Largest payments before the low**")
            if ev["largest_payments"]:
                for entry in ev["largest_payments"]:
                    when = fmt_date(date.fromisoformat(entry["day"]))
                    st.markdown(
                        f"- {safe(shown_name(entry))} · {when} · {safe(money(entry['cents']))} "
                        f"· {entry['basis']}",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No payments are placed before the low.")
            counts = ev["assumptions"]
            wording = ", ".join(f"{counts[k]} {k}" for k in ("detected", "confirmed", "entered")
                                if k in counts)  # fmt: skip
            st.markdown(
                f"**What the forecast assumes:** {wording or 'no placed payments'}. Detected ones "
                "are used as found; confirm or fix them on the Recurring page.",
            )
            st.markdown("**Set aside, not forecast**")
            if ev["big_events"]:
                for big in ev["big_events"]:
                    st.markdown(
                        f"- {safe(big['group'] or 'Big event')}: {safe(big['kind'])} · "
                        f"{fmt_date(date.fromisoformat(big['day']))} · "
                        f"{safe(money(big['cents']))}",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No big one-time rows in the last 13 weeks.")
            st.markdown(
                f"**Data to** {fmt_date(date.fromisoformat(ev['data_date']))}; forecast from "
                f"{fmt_date(date.fromisoformat(ev['forecast_first_day']))}. Everyday spending is "
                f"about {safe(money(ev['spending_per_day_cents']))} a day "
                f"({safe(ev['method'])}).",
                unsafe_allow_html=True,
            )
            st.caption(f"Rule: {ev['rule']}.")
        if st.button("Dismiss this alert", key="dismiss_alert"):
            dismiss(conn, account, current.episode_id)
            st.rerun()
    elif current is not None and current.status == "dismissed":
        st.markdown(
            f'<p style="margin: 0">You dismissed this alert (the low about '
            f"{fmt_day(current.low_day)}). It stays quiet for small moves and for good news, and "
            "speaks again only if the chance enters a higher band or the middle future's low "
            "drops below $0.</p>".replace("$", "&#36;"),
            unsafe_allow_html=True,
        )
        if st.button("Bring it back", key="restore_alert"):
            restore(conn, account, current.episode_id)
            st.rerun()
    else:
        st.markdown('<p style="margin: 0">No shortfall alert now.</p>', unsafe_allow_html=True)
    older = [e for e in mine if e is not current or e.status == "dismissed"]
    if older:
        st.markdown("<h3>Earlier and dismissed alerts</h3>", unsafe_allow_html=True)
        st.markdown(
            alert_episodes_html(
                [
                    (
                        f"{fmt_day(e.opened_on)} – {fmt_day(e.closed_on or e.last_seen_on)}",
                        f"Low about {money(e.low_cents)} around {fmt_day(e.low_day)} · "
                        f"{chance_text(e.probability)} chance",
                        e.status.capitalize(),
                    )
                    for e in older
                ]
            ).replace("$", "&#36;"),
            unsafe_allow_html=True,
        )

# --- The chart card ---
latest = rows["date"].max()
recent = rows[rows["date"] >= latest - pd.Timedelta(days=30)]
with st.container(border=True, key="card-chart"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; '
        'align-items: center"><h2 style="margin: 0">Projected balance</h2>'
        f"{chart_legend_html(forecast.buffer_cents)}</div>",
        unsafe_allow_html=True,
    )
    chart = style_forecast_chart(
        forecast_chart(daily_balance(recent), forecast, forecast.buffer_cents),
        start.data_last_updated,
        start.last_day,
        (low_day, low_cents),
    )
    last_days = daily_balance(recent).tail(10)["balance ($)"].tolist()  # a few days of history
    shown_values = [d.balance_cents / 100 for d in forecast.days] + [
        start.balance_cents / 100, forecast.buffer_cents / 100, low_cents / 100, *last_days
    ]  # fmt: skip
    if sim is not None:
        shown_values += [c / 100 for c in (*sim.p10, *sim.p90)]
    fit_y_range(chart, shown_values)
    chart.update_layout(height=340)
    st.plotly_chart(chart, width="stretch", theme=None, config={"displayModeBar": False})
    st.markdown(
        '<p class="la-note">Estimates from your own past weeks, not a guarantee. The band covers '
        f"80% of {sim.futures if sim is not None else 'the simulated'} futures.</p>",
        unsafe_allow_html=True,
    )

# --- What if... and Coming up, side by side ---
scenario_result = None  # set below when a scenario runs; its chart is drawn full width after
what_if, coming_up = st.columns(2, gap="medium")
with what_if, st.container(border=True, key="card-whatif"):
    st.markdown("<h2>What if…</h2>", unsafe_allow_html=True)
    st.markdown('<p class="la-note">Try a change. Nothing is saved.</p>', unsafe_allow_html=True)
    if sim is None:
        st.caption("No simulated futures for this account yet.")
    else:
        chosen = st.pills(
            "What if", ["Spend less", "Add a purchase", "Money arrives late", "Move a bill",
                        "No other money coming"],
            selection_mode="multi", key="wi_pills", label_visibility="collapsed",
        )  # fmt: skip
        chosen = chosen or []
        mode, amount, spend_from, spend_to = (
            "By dollars a week",
            0.0,
            start.first_day,
            start.last_day,
        )
        extra_amount, extra_day, extra_way, extra_what = 0.0, start.first_day, "Money out", ""
        delay, pick, move_to = 0, 0, start.first_day + timedelta(days=7)
        if "Spend less" in chosen:
            mode = st.radio("Everyday spending is lower", ["By dollars a week", "By a percent"],
                            key="wi_spend_mode", horizontal=True)  # fmt: skip
            amount = st.number_input("By (dollars a week, or percent)", min_value=0.0,
                                     value=70.0, step=5.0, key="wi_spend_value")  # fmt: skip
            period = st.columns(2)
            spend_from = period[0].date_input("From", value=start.first_day,
                                              key="wi_spend_from", format="YYYY/MM/DD")  # fmt: skip
            spend_to = period[1].date_input("To", value=start.last_day, key="wi_spend_to",
                                            format="YYYY/MM/DD")  # fmt: skip
        if "Add a purchase" in chosen:
            extra_day = st.date_input("Day", value=start.first_day, key="wi_extra_date",
                                      format="YYYY/MM/DD")  # fmt: skip
            extra_way = st.radio("Direction", ["Money out", "Money in"], horizontal=True,
                                 key="wi_extra_way")  # fmt: skip
            extra_amount = st.number_input("Amount ($)", min_value=0.0, value=0.0, step=50.0,
                                           key="wi_extra_amount")  # fmt: skip
            extra_what = st.text_input("What it is", key="wi_extra_what")
        if "Money arrives late" in chosen:
            delay = st.number_input("Money in arrives this many days later", 0, 60, 7, step=1,
                                    key="wi_delay")  # fmt: skip
        if "Move a bill" in chosen:
            pick = st.selectbox(
                "Move a payment", list(range(len(forecast.items) + 1)), key="wi_move_pick",
                format_func=lambda i: "Choose a payment" if i == 0 else (
                    f"{fmt_date(forecast.items[i - 1].day)} · {names_in_order[i - 1]} · "
                    f"{money(forecast.items[i - 1].cents)}"
                ),
            )  # fmt: skip
            move_to = st.date_input("…to this day", value=start.first_day + timedelta(days=7),
                                    key="wi_move_to", format="YYYY/MM/DD")  # fmt: skip
        no_other = "No other money coming" in chosen
        changed = bool("Spend less" in chosen or no_other or delay or extra_amount > 0 or pick > 0)
        if not chosen:
            st.caption("Choose something above to see what would happen.")
        elif not changed:
            st.caption("Fill in the details to see what would happen.")
        else:
            try:
                result = run_scenario(
                    forecast,
                    Scenario(
                        spending=None
                        if "Spend less" not in chosen
                        else SpendingChange(
                            spend_from,
                            spend_to,
                            percent=amount if mode == "By a percent" else None,
                            per_week_cents=round(amount * 100)
                            if mode == "By dollars a week"
                            else None,
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
                o, m = result.original, result.modified

                def verdict(before: float, after: float, lower_is_better: bool) -> str:
                    if round(before, 6) == round(after, 6):
                        return "same"
                    return "better" if (after < before) == lower_is_better else "worse"

                st.markdown(
                    result_panel_html([
                        ("Chance below buffer", f"{chance_text(o.shortfall_probability)}",
                         f"{chance_text(m.shortfall_probability)}",
                         verdict(o.shortfall_probability, m.shortfall_probability, True)),
                        ("Lowest expected balance", money(min(o.p50)), money(min(m.p50)),
                         verdict(min(o.p50), min(m.p50), False)),
                        ("Chance below $0", f"{chance_text(o.below_zero_probability)}",
                         f"{chance_text(m.below_zero_probability)}",
                         verdict(o.below_zero_probability, m.below_zero_probability, True)),
                        (f"Balance on {fmt_day(o.days[-1])}", money(o.p50[-1]), money(m.p50[-1]),
                         verdict(o.p50[-1], m.p50[-1], False)),
                    ]),
                    unsafe_allow_html=True,
                )  # fmt: skip
                st.markdown(
                    '<p class="la-note">Conditional scenario: '
                    + html.escape(humanize_dates(" ".join(result.changes))).replace("$", "&#36;")
                    + " This is not a forecast of what will happen.</p>",
                    unsafe_allow_html=True,
                )
                scenario_result = result

with coming_up, st.container(border=True, key="card-coming"):
    head = st.columns([2, 1.2], vertical_alignment="bottom")
    head[0].markdown("<h2>Coming up</h2>", unsafe_allow_html=True)
    head[1].markdown(
        f'<p style="margin: 0; text-align: right"><a href="{html.escape(link_query("Recurring"))}" '
        f'target="_self" style="font-size: 14px; font-weight: 700; color: {COLORS.green_dark}">'
        "All recurring →</a></p>",
        unsafe_allow_html=True,
    )
    if forecast.items:
        kinds = {"merchant": "Payment", "e-transfer": "e-Transfer", "user": "Added by me",
                 "own transfer": "Transfer to my own account"}  # fmt: skip
        upcoming = []
        for item, name in list(zip(forecast.items, names_in_order, strict=True))[:6]:
            series = by_key.get(item.series_key)
            if series is None:
                what = "Planned item" if item.series_key.startswith("planned|") else "Payment"
            elif series.direction == "in":
                what = "Income"
            elif rent_rule_of(series) is not None:
                what = "Rent"
            else:
                what = kinds.get(series.kind, "Payment")
            upcoming.append((fmt_day(item.day), name, f"{what} · {item.basis}",
                             money(item.cents, signed=item.cents > 0), item.cents > 0))  # fmt: skip
        st.markdown(coming_up_html(upcoming), unsafe_allow_html=True)
    else:
        st.caption("Nothing is expected in this period.")

# --- The comparison, full width, when a scenario is active ---
if scenario_result is not None:
    o, m = scenario_result.original, scenario_result.modified
    with st.container(border=True, key="card-compare"):
        st.markdown(
            '<div style="display: flex; flex-wrap: wrap; justify-content: space-between; '
            'gap: 12px; align-items: center"><h2 style="margin: 0">'
            f"Original and with your change</h2>{scenario_legend_html(forecast.buffer_cents)}"
            "</div>",
            unsafe_allow_html=True,
        )
        compare = style_scenario_chart(scenario_chart(forecast, o, m, forecast.buffer_cents))
        compare.update_layout(title={"text": ""})
        fit_y_range(compare, [
            start.balance_cents / 100, forecast.buffer_cents / 100,
            *(c / 100 for c in (*o.p10, *o.p90, *m.p10, *m.p90)),
        ])  # fmt: skip
        compare.update_layout(height=340)  # the same height as the main chart
        st.plotly_chart(compare, width="stretch", theme=None, config={"displayModeBar": False})
        st.markdown(
            '<p class="la-note">Conditional scenario, not a forecast of what will happen. '
            "Both start from the last observed balance.</p>",
            unsafe_allow_html=True,
        )

# --- The explanations, folded ---
st.markdown('<div id="evidence"></div>', unsafe_allow_html=True)
with st.container(key="evidence"):
    with st.expander("Assumptions behind this forecast", expanded=False):
        counts = {
            b: sum(i.basis == b for i in forecast.items)
            for b in ("detected", "confirmed", "entered")
        }
        st.markdown(
            f"- **Starting point:** the balance after the last posting on "
            f"{fmt_date(start.data_last_updated)}. Only data posted on or before that date is "
            f"used.\n"
            f"- **Recurring payments:** {len(forecast.items)} placed in the period "
            f"({counts['detected']} detected, {counts['confirmed']} confirmed, "
            f"{counts['entered']} entered by me). Detected ones are used as found; you can "
            "confirm or change them on the Recurring page.\n"
            f"- **Everyday spending:** the {forecast.method}. It includes transfers to people and "
            "to savings that are not part of a recurring series. Big one-time rows (below) are "
            "left out."
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
                f"- **Planned items:** {len(forecast.planned)} entered by me, {inside} inside "
                "this period, placed exactly on their days."
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
        arrivals = forecast.arrivals
        n_in, n_out = len(arrivals.in_sizes), len(arrivals.out_sizes)
        if n_in or n_out:
            st.markdown(
                f"- **Big money that keeps coming:** {n_in} recurring-size deposit"
                f"{'' if n_in == 1 else 's'} and {n_out} recurring-size payment"
                f"{'' if n_out == 1 else 's'} of the last 365 days, in no series, each with 2 or "
                "more earlier ones of similar size. The simulated futures draw them as random "
                "arrivals, at their observed rate and with their observed sizes, so the range, "
                "the middle future and the chances include them."
            )
        else:
            st.markdown(
                "- **Big money that keeps coming:** none. A big row of the last 365 days with 2 "
                "or more earlier ones of similar size would be drawn as random arrivals in the "
                "simulated futures."
            )
        if arrivals.payments_below_floor:
            st.markdown(
                f"- **Recurring-size big payments set aside:** {arrivals.payments_below_floor}, "
                "because there are fewer than 6 a year. They are not drawn."
            )
        if forecast.not_placed:
            why = {
                "weekly card purchases": "weekly card purchases, counted as everyday spending",
                "possibly ended": "possibly ended: no payment for over 1.5 times its usual gap",
            }
            by_key = {s.key: s for s in forecast.series}
            listed = "; ".join(f"{series_label(by_key[k], k in hidden)} ({why[r]})"
                               for k, r in forecast.not_placed)  # fmt: skip
            st.markdown(
                f"- **Recurring series not placed:** {listed}. Confirm one on the Recurring page "
                "to have it placed."
            )
        if sim is not None:
            st.markdown(
                f"- **Simulated futures:** {sim.futures}. Each draws, for every week ahead, one "
                f"whole past week from the last 13 weeks ({sim.weeks_drawn_from} different weeks "
                "here) and takes that week's everyday spending and irregular deposits together. "
                "Irregular deposits are money in that is in no series, not a refund and not a big "
                "one-time row. Rent falls inside its window, weighted by your own past rent "
                "dates; a detected payment's date moves by its own past offsets; confirmed and "
                "entered dates stay. The draws are not rescaled to the point forecast. Middle "
                f"future on the last day: {money(sim.p50[-1], markdown=True)} with irregular "
                f"deposits, {money(sim.median_without[-1], markdown=True)} without."
            )
        for s in forecast.series:
            rule = rent_rule_of(s)
            if rule is not None and s.active and s.status != "rejected":
                st.markdown(
                    f"- **{series_label(s, s.key in hidden)}:** assumed at the larger allowed "
                    f"amount ({money(rent_cents(rule), markdown=True)}), somewhere in its window "
                    f"(day {rule.from_day} to day {rule.to_day}). The forecast places it on the "
                    "earliest day of the window still ahead; a window that ended with no payment "
                    "is expected on the first forecast day."
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
            show_table(spending, money=list(spending.columns[1:]))

    with st.expander("What's in irregular deposits", expanded=False):
        st.caption(
            "The simulated futures draw whole past weeks. For each week ahead a future picks one "
            "of the weeks below at random and takes that week's irregular deposits (and its "
            "spending). A week with a large deposit in the stream therefore adds it to about one "
            f"in {max(len(forecast.deposit_weeks), 1)} of the weeks ahead. The big deposits and "
            "pass-throughs set aside (last column) are not drawn. Big deposits that keep coming "
            "at a similar size are not in this table: they are drawn as random arrivals (see the "
            "assumptions). Deposits of recurring income series are not here: they are placed on "
            "their own dates."
        )
        if forecast.deposit_weeks:
            weeks = deposit_weeks_table(list(forecast.deposit_weeks))
            weeks["Week"] = [f"{fmt_day(w.first)} to {fmt_day(w.last)}"
                             for w in forecast.deposit_weeks]  # fmt: skip
            weeks["Set aside"] = weeks["Set aside"].map(humanize_dates)
            show_table(weeks, money=["In the stream ($)"], whole=True,
                       widths={"Week": 125, "In the stream ($)": 105, "What": 230,
                               "Set aside": 300})  # fmt: skip
        else:
            st.caption("Not enough history for a whole past week.")

    with st.expander("Big one-time events (not forecast)", expanded=bool(forecast.big_events)):
        st.caption(
            "Big one-time rows of the last 13 weeks: a row of at least $500 that is at least 5 "
            "times the typical row, money out or money in. They are not everyday spending or "
            "irregular deposits, and the rows listed here are not drawn in the forecast. Big rows "
            "that keep coming at a similar size are not listed: they are drawn as random arrivals "
            "(see the assumptions). A big deposit that "
            "went straight back out (outflows adding up to 85 to 115 percent of it within 14 "
            "days) is shown with those outflows as a numbered pass-through. A big deposit with "
            "no such outflows is a large deposit: when one arrives cannot be predicted from "
            "history."
        )
        if forecast.big_events:
            show_table(events_table(list(forecast.big_events)), money=["Amount ($)"],
                       dates=["Date"], money_in=["Amount ($)"])  # fmt: skip
        else:
            st.caption("No big one-time rows in the last 13 weeks.")
        if forecast.large_deposits:
            st.info(
                "You sometimes receive large deposits. If you expect one, add it as a planned "
                'deposit. Use "Planned items" below.'
            )

    with st.expander(
        "Planned items (money in or out that I enter)",
        expanded=bool(forecast.planned) or st.session_state.get("open_planned", False),
    ):
        st.caption(
            "A payment or deposit you know is coming: a term payment, an expected deposit, a "
            'purchase. It is placed exactly on its day, labelled "entered", and never guessed '
            "or moved. Only days inside the forecast period change the forecast. When a real "
            "transaction of about the same amount posts within 7 days of it, it says received and "
            "is no longer placed."
        )
        if forecast.planned:
            show_table(planned_table(list(forecast.planned), start.first_day, start.last_day,
                                     forecast.received),
                       money=["Amount ($)"], dates=["Date", "Entered on"],
                       money_in=["Amount ($)"])  # fmt: skip
        else:
            st.caption("No planned items.")
        with st.form("planned_form"):
            planned_day = st.date_input("Day", value=start.first_day, key="planned_date",
                                        format="YYYY/MM/DD")  # fmt: skip
            planned_way = st.radio("Direction", ["Money out", "Money in"], horizontal=True,
                                   key="planned_direction")  # fmt: skip
            planned_size = st.number_input("Amount ($)", min_value=0.0, step=50.0,
                                           key="planned_amount")  # fmt: skip
            planned_what = st.text_input("What it is", key="planned_description")
            add_one = st.form_submit_button("Add planned item")
        if add_one and isinstance(planned_day, date):
            sign = 1 if planned_way == "Money in" else -1
            try:  # dollars only on screen; cents are stored
                add_planned(conn, account, planned_day, sign * round(planned_size * 100),
                            planned_what)  # fmt: skip
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

    with st.expander("Recurring payments placed in this period", expanded=False):
        if forecast.items:
            show_table(forecast_table(forecast.items, by_key, hidden), money=["Amount ($)"],
                       dates=["Date"], money_in=["Amount ($)"])  # fmt: skip
        else:
            st.caption("None: no active recurring series has a payment in this period.")

footer(DISCLAIMER)
