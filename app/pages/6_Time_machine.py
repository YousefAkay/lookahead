"""Time machine: step through the dates of a synthetic persona and see what the app would have
shown on each, with only the data available then.

The steps are precomputed (`python -m lookahead.timemachine_snapshots`); this page only reads
them, so stepping is fast. The what-if at the current date is computed here, from the persona's
own database. Synthetic personas only: never my data.
"""

import html
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import streamlit as st  # noqa: E402

from app.theme import (  # noqa: E402
    apply,
    callout_html,
    chart_legend_html,
    fit_y_range,
    fmt_date,
    fmt_day,
    footer,
    humanize_dates,
    money,
    money_short,
    no_settings,
    result_panel_html,
    style_forecast_chart,
    tag_html,
    top_bar,
)
from lookahead.chance import chance_text  # noqa: E402
from lookahead.forecast.scenario import Scenario, SpendingChange  # noqa: E402
from lookahead.timemachine_snapshots import (  # noqa: E402
    alert_view,
    available_personas,
    evidence_lines,
    history_frame,
    load_balances,
    load_steps,
    moved,
    persona_label,
    realized_frame,
    scenario_at,
    snapshot_dir,
    step_chart,
    step_low,
    whatif_extra,
)
from lookahead.ui import DISCLAIMER  # noqa: E402

SOURCE = "Synthetic data"
apply("Time machine")
no_settings()  # the gear still opens: it says there is nothing to set here
bar = st.container(key="topbar")

directory = snapshot_dir()
personas = available_personas(directory)
if not personas:
    top_bar(bar, "Time machine", None, SOURCE)
    st.markdown("<h1>Time machine</h1>", unsafe_allow_html=True)
    st.info("No saved dates yet. Make them once with `python -m lookahead.timemachine_snapshots` "
            "(synthetic personas only; a few minutes).")  # fmt: skip
    st.stop()


def safe(text: str) -> str:
    return html.escape(text).replace("$", "&#36;")  # a dollar sign would start maths in markdown


# --- Which persona, which date ---
title_col, pick_col = st.columns([3, 2], vertical_alignment="bottom")
with pick_col:
    pid = st.selectbox("Persona", personas, format_func=persona_label, key="tm_persona")
steps = load_steps(directory, pid)
balances = load_balances(directory, pid)
dates = [date.fromisoformat(s["as_of"]) for s in steps]
if st.session_state.get("tm_for") != pid or st.session_state.get("tm_date") not in dates:
    # a new persona (or newly made dates) starts at the first date
    st.session_state["tm_for"] = pid
    st.session_state["tm_date"] = dates[0]


def shift(days: int) -> None:
    st.session_state["tm_date"] = moved(dates, st.session_state["tm_date"], days)


index = dates.index(st.session_state["tm_date"])
step = steps[index]
as_of = dates[index]
top_bar(bar, "Time machine", as_of, SOURCE)
with title_col:
    st.markdown(
        f'<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
        f"<h1>As of {fmt_date(as_of)}</h1>{tag_html('Synthetic data', 'neutral')}</div>"
        f'<p class="la-sub">{html.escape(persona_label(pid))} · buffer '
        f"{html.escape(money_short(step['buffer_cents']))} · date {index + 1} of {len(dates)}</p>"
        '<p class="la-note">Made-up data, not anyone\'s account. Each date shows what the app '
        "would have shown that day, from the rows posted by then.</p>",
        unsafe_allow_html=True,
    )

nav = st.columns([1, 1, 6, 1, 1], vertical_alignment="bottom")
nav[0].button("◀ Week", on_click=shift, args=(-7,), use_container_width=True, key="tm_back7")
nav[1].button("◀ Day", on_click=shift, args=(-1,), use_container_width=True, key="tm_back1")
with nav[2]:
    st.select_slider("Date", options=dates, key="tm_date", format_func=fmt_date,
                     label_visibility="collapsed")  # fmt: skip
nav[3].button("Day ▶", on_click=shift, args=(1,), use_container_width=True, key="tm_next1")
nav[4].button("Week ▶", on_click=shift, args=(7,), use_container_width=True, key="tm_next7")

# --- The alert state, then four number cards ---
view = alert_view(step)
st.markdown(callout_html(view.kind, view.tag, safe(humanize_dates(view.sentence))),
            unsafe_allow_html=True)  # fmt: skip
low_day, low_cents = step_low(step)
cards = st.columns(4)
with cards[0]:
    st.metric(f"Balance on {fmt_day(as_of)}", money(step["start_balance_cents"]),
              f"{step['history_days']} days of history", delta_color="off",
              delta_arrow="off")  # fmt: skip
with cards[1]:
    st.metric("Lowest expected balance", money(low_cents), f"{fmt_day(low_day)}, middle future",
              delta_color="off", delta_arrow="off")  # fmt: skip
with cards[2], st.container(key="tone-warn" if view.kind == "warning" else "tone-ok"):
    st.metric("Chance below buffer", f"{chance_text(step['shortfall_probability'])}",
              f"of {step['futures']} simulated futures", delta_color="off",
              delta_arrow="off")  # fmt: skip
with cards[3]:
    st.metric("Recurring series found", str(step["n_series"]),
              f"{step['n_items']} payments placed", delta_color="off",
              delta_arrow="off")  # fmt: skip

# --- The chart card ---
with st.container(border=True, key="card-chart"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; '
        'align-items: center"><h2 style="margin: 0">Projected balance on this date</h2>'
        f"{chart_legend_html(step['buffer_cents'])}</div>",
        unsafe_allow_html=True,
    )
    show_real = st.toggle("Show what really happened next", key="tm_show_real",
                          help="The forecast never saw these days.")  # fmt: skip
    history = history_frame(balances, as_of)
    realized = realized_frame(balances, as_of) if show_real else None
    first_day = date.fromisoformat(step["days"][0])
    last_day = date.fromisoformat(step["days"][-1])
    chart = style_forecast_chart(step_chart(step, history, realized), first_day, last_day,
                                 (low_day, low_cents))  # fmt: skip
    shown = [c / 100 for c in (*step["p10"], *step["p90"])]
    shown += [step["buffer_cents"] / 100, *history["balance ($)"].tail(10).tolist()]
    if realized is not None:
        shown += realized["balance ($)"].tolist()
    fit_y_range(chart, shown)
    chart.update_layout(height=340)
    st.plotly_chart(chart, width="stretch", theme=None, config={"displayModeBar": False})
    st.markdown(
        f'<p class="la-note">The band covers 80% of {step["futures"]} simulated futures. '
        + ("The dotted line is what really happened; the forecast did not know it."
           if show_real else "") + "</p>",
        unsafe_allow_html=True,
    )  # fmt: skip

# --- The alert and its evidence, and a what-if at this date ---
alert_col, what_if = st.columns(2, gap="medium")
with alert_col, st.container(border=True, key="card-alerts"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
        f"<h2>Alert on this date</h2>{tag_html('rule chosen 2026-11-19', 'neutral')}</div>"
        f'<p style="margin: 0">{safe(view.status)}</p>',
        unsafe_allow_html=True,
    )
    lines = evidence_lines(step)
    if lines:
        with st.expander("Evidence for this alert", expanded=True):
            for line in lines:
                st.markdown(f"- {safe(humanize_dates(line))}", unsafe_allow_html=True)
    else:
        st.caption("No alert, so no evidence to show.")

with what_if, st.container(border=True, key="card-whatif"):
    st.markdown("<h2>What if… on this date</h2>", unsafe_allow_html=True)
    st.markdown('<p class="la-note">Try a change at this date. Nothing is saved.</p>',
                unsafe_allow_html=True)  # fmt: skip
    with st.form("tm_whatif"):
        cut = st.number_input("Spend less on everyday things (percent, next 30 days)", 0, 100, 0,
                              step=5, key="tm_cut")  # fmt: skip
        extra = st.number_input(
            "One deposit (+) or purchase (−) in a week ($)",
            value=0.0,
            step=50.0,
            key="tm_extra",
            help="A positive amount is money in, a negative one money out.",
        )
        late = st.number_input("Money in arrives this many days later", 0, 60, 0, key="tm_late")
        no_other = st.checkbox("No other money coming", key="tm_no_other")
        run_it = st.form_submit_button("See what would happen")
    if run_it:
        try:
            scenario = Scenario(
                spending=None if cut == 0 else SpendingChange(first_day, last_day,
                                                              percent=float(cut)),
                extras=whatif_extra(extra, as_of + timedelta(days=7)),
                delay_money_in_days=int(late), no_other_money=bool(no_other),
            )  # fmt: skip
            with st.spinner("Running the futures again..."):
                result = scenario_at(directory / f"{pid}.db", pid, as_of, scenario,
                                     step["futures"])  # fmt: skip
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
                + safe(humanize_dates(" ".join(result.changes)))
                + " This is not a forecast of what will happen.</p>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("Change something and press the button.")

footer("Synthetic data only. " + DISCLAIMER)
