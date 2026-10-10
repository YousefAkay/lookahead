"""Model report: how well the categorizer and the forecast did in the backtests, with n.

Synthetic results come from saved files in docs/results (made by `python -m eval.report_data`).
Real results are read from data/private/backtest at runtime when they exist; nothing real is
written anywhere. Held-out results do not exist yet: the final test has not been run.
"""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead`, `app` and `eval` importable

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app.theme import COLORS, apply, footer, show_table, tag_html, top_bar  # noqa: E402
from eval.report_data import (  # noqa: E402
    ALERT_LABELS,
    CATEGORIZER,
    FORECAST_LABELS,
    HISTORY_ORDER,
    SYNTHETIC_OUT,
    alert_values,
    breakdown_rows,
    forecast_values,
    load_json,
    real_summary,
    reliability_values,
)
from lookahead.ui import DISCLAIMER  # noqa: E402

apply("Model report")
bar = st.container(key="topbar")
top_bar(bar, "Model report", None, "Results", until="Saved test results")

ROOT = Path(__file__).resolve().parents[2]
synthetic = load_json(ROOT / SYNTHETIC_OUT)
categorizer = load_json(ROOT / CATEGORIZER)
try:
    real = real_summary()
    real_problem = None
except ValueError as problem:  # an older saved format, for example
    real, real_problem = None, str(problem)

RUN_LOCALLY = "run locally"
NOT_RUN = "not run yet"

st.markdown(
    "<h1>Model report</h1>"
    '<p class="la-sub">How the categorizer and the forecast did when tested on data they had not '
    "seen.</p>"
    '<p class="la-note">Every number has its n. Synthetic and real results are never mixed. '
    "Synthetic numbers come from made-up personas: 20 for development, 10 held out for one final "
    "test that has not been run. Real numbers are one person's account (a case study) and are "
    "read from this computer only when they exist.</p>",
    unsafe_allow_html=True,
)


def table(rows: list[str], columns: dict[str, list[str]]) -> pd.DataFrame:
    return pd.DataFrame({"": rows, **columns})


# --- How it is tested ---
with st.container(border=True, key="card-how"):
    st.markdown(
        "<h2>How it is tested</h2>"
        "<p>The forecast is replayed every Monday of a persona's year (40 dates each): it may use "
        "only the rows posted by that date, and is then compared with the 30 days that really "
        "followed. That rule (only what was known at the time) is checked by tests that rewrite "
        "the future and expect the same forecast. Settings are chosen on the 20 development "
        "personas only; the 10 held-out personas are kept for a single final test.</p>",
        unsafe_allow_html=True,
    )

# --- Categorizer ---
with st.container(border=True, key="card-categorizer"):
    st.markdown("<h2>Categorizer</h2>", unsafe_allow_html=True)
    if categorizer:
        frame = pd.DataFrame([
            {"Test set": r["set"], "Rows": r["rows"], "Merchants": r["groups"],
             "Accuracy": f"{r['hybrid_accuracy']:.1%}", "Macro-F1": f"{r['hybrid_macro_f1']:.1%}",
             "With LLM fallback": f"{r['llm_accuracy']:.1%} / {r['llm_macro_f1']:.1%}"}
            for r in categorizer["rows"]
        ])  # fmt: skip
        show_table(frame, whole=True)
        st.markdown(
            f'<p class="la-note">{categorizer["source"]} Trained on {categorizer["trained_on"]}. '
            + " ".join(categorizer["notes"])
            + "</p>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("No categorizer results file.")

# --- Forecast ---
real_note = (
    f"Could not read the real results: {real_problem}" if real_problem
    else "Real: no results on this computer. Run <code>python -m eval.backtest_real</code> locally."
    if real is None else
    f"Real: {real['source']['origins']} origins, {real['source']['accounts']} account(s), "
    f"{real['source']['futures']} futures, read from data/private at runtime."
)  # fmt: skip


def column(summary: dict | None, key: str, values) -> list[str]:  # type: ignore[no-untyped-def,type-arg]
    if summary is None:
        return [RUN_LOCALLY] * len(values(None))
    return values(summary[key])


SHELVED = tag_html("shelved: not used by the app", "neutral")


with st.container(border=True, key="card-forecast"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
        f"<h2>Forecast, 30 days ahead</h2>{tag_html('development results', 'neutral')}</div>"
        '<p class="la-note">Origins not already below the buffer. Middle future = the median of '
        "500 simulated futures; the band is its 10th to 90th percentile, so it should cover the "
        "real balance 80% of the time.</p>",
        unsafe_allow_html=True,
    )
    if synthetic is None:
        st.caption("No synthetic results file: run `python -m eval.report_data`.")
    else:
        show_table(table(list(FORECAST_LABELS), {
            "Synthetic, development": forecast_values(synthetic["ordinary"]),
            "Synthetic, held out": [NOT_RUN] * len(FORECAST_LABELS),
            "Real": column(real and real["own_buffer"], "ordinary", forecast_values),
            "Real, buffer $0": column(real and real["zero_buffer"], "ordinary", forecast_values),
        }), whole=True)  # fmt: skip
        st.markdown(f'<p class="la-note">{real_note}</p>'.replace("$", "&#36;"),
                    unsafe_allow_html=True)  # fmt: skip

# --- Alerts ---
with st.container(border=True, key="card-alerts"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
        f"<h2>Shortfall alerts</h2>{tag_html('provisional rule', 'neutral')}</div>"
        '<p class="la-note">The rule the app uses today: alert when more than 20% of futures dip '
        "below the buffer, or the middle future's lowest balance does. A dip counts as caught if "
        "an alert opened in the 30 days before it.</p>",
        unsafe_allow_html=True,
    )
    if synthetic is not None:
        show_table(table(list(ALERT_LABELS), {
            "Synthetic, development": alert_values(synthetic["alerts"]),
            "Synthetic, held out": [NOT_RUN] * len(ALERT_LABELS),
            "Real": column(real and real["own_buffer"], "alerts", alert_values),
            "Real, buffer $0": column(real and real["zero_buffer"], "alerts", alert_values),
        }), whole=True)  # fmt: skip

# --- Reliability ---
with st.container(border=True, key="card-reliability"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
        f"<h2>Is the shortfall chance honest?</h2>{SHELVED}</div>"
        '<p class="la-note">Origins grouped by the chance the forecast gave. If the chance is '
        "honest, what happened matches what was said.</p>",
        unsafe_allow_html=True,
    )
    if synthetic is not None:
        bins = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
        show_table(table(bins, {
            "Synthetic, development": reliability_values(synthetic["ordinary"]),
            "Synthetic, calibrated (shelved)": reliability_values(
                synthetic["calibrated"]["ordinary"]
            ),
            "Real": column(real and real["own_buffer"], "ordinary", reliability_values),
        }), whole=True)  # fmt: skip
        st.markdown(
            '<p class="la-note">Calibrated: futures widened by a multiplier per day ahead and the '
            f"chance mapped ({synthetic['calibrated']['variant']}), fitted on 19 personas and "
            "scored on the 20th, in turn. Shelved: on the real account it made the bands "
            "unusably wide, so the app does not use it.</p>",
            unsafe_allow_html=True,
        )

# --- Threshold curve ---
with st.container(border=True, key="card-threshold"):
    st.markdown(
        '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
        f"<h2>Alert threshold on the calibrated chance</h2>{SHELVED}</div>"
        '<p class="la-note">Synthetic development personas, calibrated chances (each persona '
        "scored with a calibration fitted without it). The aim: the most dips caught with at "
        "least 70% of alerts right and at most 0.1 false alerts per 30 days.</p>",
        unsafe_allow_html=True,
    )
    if synthetic is not None:
        pts = synthetic["threshold_curve"]
        figure = go.Figure()
        xs = [p["threshold"] * 100 for p in pts]
        figure.add_scatter(
            x=xs,
            y=[(p["recall"] or 0) * 100 for p in pts],
            name="Caught (recall)",
            mode="lines+markers",
            line={"color": COLORS.green, "width": 3},
        )
        figure.add_scatter(x=xs, y=[(p["precision"] or 0) * 100 for p in pts],
                           name="Alerts that were right (precision)", mode="lines+markers",
                           line={"color": COLORS.blue, "width": 2, "dash": "dash"})  # fmt: skip
        figure.add_hline(y=70, line={"color": COLORS.amber_line, "dash": "dot", "width": 1.4})
        figure.update_layout(height=300, xaxis_title="threshold (%)", yaxis_title="",
                             yaxis_range=[0, 105], margin={"t": 10})  # fmt: skip
        figure.update_yaxes(tickprefix="", ticksuffix="%", tickformat="d")  # not dollars
        figure.update_traces(hovertemplate="%{y:.0f}% at %{x:.0f}%<extra>%{fullData.name}</extra>")
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        frame = pd.DataFrame([
            {"Threshold": f"{p['threshold']:.0%}",
             "Caught": f"{p['caught']} of {p['shortfall_episodes']}",
             "Right": f"{p['alert_episodes'] - p['false_alerts']} of {p['alert_episodes']}",
             "False per 30 days": f"{p['false_per_30_days']:.3f}",
             "Lead time (days)": "-" if not p["lead_times"] else
             f"{sum(p['lead_times']) / len(p['lead_times']):.1f}"}
            for p in pts
        ])  # fmt: skip
        show_table(frame)
        chosen = synthetic["threshold_chosen"]
        st.markdown(
            '<p class="la-note">'
            + (f"The aim is met best at {chosen:.0%}. " if chosen is not None else
               "No threshold meets the aim. ")
            + "Caught goes up with the threshold because at low thresholds one alert stays on "
            "across several dips, and a dip only counts if an alert opened before it. The app "
            "keeps its provisional rule: this curve rests on the shelved calibration, so the "
            "threshold will be chosen again on raw chances. Real: run "
            "<code>python -m eval.calibrate apply</code> "
            "locally.</p>",
            unsafe_allow_html=True,
        )  # fmt: skip

# --- Breakdowns ---
COLUMNS = ["Group", "Origins (personas)", "30-day error", "Band coverage", "Lowest-balance bias",
           "Brier"]  # fmt: skip
with st.container(border=True, key="card-breakdowns"):
    st.markdown("<h2>By kind of persona and by length of history</h2>", unsafe_allow_html=True)
    if synthetic is not None:
        st.markdown("<h3>Synthetic, by archetype</h3>", unsafe_allow_html=True)
        show_table(pd.DataFrame(breakdown_rows(synthetic["by_archetype"]), columns=COLUMNS),
                   whole=True)  # fmt: skip
        st.markdown(
            '<p class="la-note">IFAM and ITIGHT are international students with tuition and '
            "family money; DHOME and DRENT domestic students; SAL and SEM salaried; TIGHT a tight "
            "budget; FREE a freelancer.</p>",
            unsafe_allow_html=True,
        )
        st.markdown("<h3>Synthetic, by history before the date</h3>", unsafe_allow_html=True)
        show_table(pd.DataFrame(breakdown_rows(synthetic["by_history"], HISTORY_ORDER),
                                columns=COLUMNS), whole=True)  # fmt: skip
    st.markdown("<h3>Real, by history before the date</h3>", unsafe_allow_html=True)
    if real is not None:
        show_table(pd.DataFrame(breakdown_rows(real["own_buffer"]["by_history"], HISTORY_ORDER),
                                columns=COLUMNS), whole=True)  # fmt: skip
    else:
        st.caption(f"{RUN_LOCALLY}: python -m eval.backtest_real")

# --- Known limitations ---
with st.container(border=True, key="card-limits"):
    st.markdown(
        "<h2>Known limitations</h2><ul>"
        "<li>The forecast does not expect tuition or other big one-time payments, and does not "
        "expect big deposits either. For students with tuition and family money the 30-day error "
        "is in the thousands of dollars.</li>"
        "<li>On synthetic data the forecast is too optimistic; on my own account it is too "
        "pessimistic (it predicts dips that do not happen). A fix that helps one can hurt the "
        "other.</li>"
        "<li>The 80% band covers the real balance about half the time. Widening it fixes that on "
        "average but makes it several times wider, and very wide for calm accounts.</li>"
        "<li>The alert rule is provisional. The threshold study uses 52 shortfall episodes from 8 "
        "personas; that is a small n.</li>"
        "<li>Origins within a persona overlap (30 days ahead, every 7 days), so the number of "
        "independent cases is smaller than the number of origins.</li>"
        "<li>Synthetic personas were made by me, with rules I also wrote the forecaster for. "
        "Real results are one person's account.</li>"
        "<li>The held-out test has not been run. Nothing here is a final number.</li></ul>",
        unsafe_allow_html=True,
    )

footer(DISCLAIMER)
