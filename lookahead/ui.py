"""Helpers for the Streamlit pages: data locations and display-ready tables.

Kept out of the page scripts so they can be tested without running Streamlit.
"""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from lookahead.pseudonymize import pseudonymize_sub_description

DISCLAIMER = "Estimates for personal budgeting. Not financial advice."
HIDDEN = "(hidden)"
FLAG = "to check"  # marks a low-confidence category; it never blocks anything
BLANK = "—"  # shown instead of the bank's single-space text


def count(n: int, word: str) -> str:
    """ "1 row", "2 rows": a number with its noun in the right form."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def recurring_count(series: Sequence[Any]) -> str:
    """Home's recurring fact: how many series were found and how many of those I confirmed (or
    added myself), e.g. "5 found · 0 confirmed". Rejected ones are not counted."""
    found = [s for s in series if s.status != "rejected"]
    if not found:
        return "None found"
    confirmed = sum(s.status in ("confirmed", "user-added") for s in found)
    return f"{len(found)} found · {confirmed} confirmed"


def synthetic_dir() -> Path:
    return Path(os.environ.get("LOOKAHEAD_SYNTHETIC_DIR", "data/synthetic"))


def demo_db_path() -> Path:
    return Path(os.environ.get("LOOKAHEAD_DEMO_DB", "data/synthetic/demo.db"))


def private_db_path() -> Path:
    from lookahead.demo_mode import is_demo

    if is_demo():
        raise PermissionError("demo mode has no private data")
    return Path(os.environ.get("LOOKAHEAD_PRIVATE_DB", "data/private/db/lookahead.db"))


def display_table(df: pd.DataFrame, key: bytes | None) -> pd.DataFrame:
    """Rows from `views.transactions` as shown on screen: ISO dates, dollars, and the
    merchant text pseudonymized when a key is given."""
    text = df["text"]
    if key is not None:
        text = pd.Series(
            [
                pseudonymize_sub_description(t, s, key).text
                for t, s in zip(df["type"], df["text"], strict=True)
            ],
            index=df.index,
            dtype="str",
        )
    hidden = text != df["text"]  # a name or a reference was replaced in this row
    text = text.where(text.str.strip() != "", BLANK)  # service charges, ABM withdrawals
    table = pd.DataFrame(
        {
            "Date": df["date"].dt.strftime("%Y-%m-%d"),
            "Type": df["type"],
            "Text": text,
            "Amount ($)": df["amount_cents"] / 100,  # display only; money stays in cents
            "Balance ($)": df["balance_cents"] / 100,
        }
    )
    if "source" not in df.columns or df["source"].isna().all():
        return table  # lookahead.categorize has not run for this account
    # The merchant of such a row could be a person's name: show it only when not hidden.
    merchant = df["merchant"].astype("object").where(~hidden, HIDDEN)
    table.insert(3, "Merchant", merchant.fillna(BLANK))
    table.insert(4, "Category", df["category"].astype("object").fillna(BLANK))
    table.insert(5, "Flow", df["flow_type"].astype("object").fillna(BLANK))
    table.insert(6, "Flag", df["flagged"].map({True: FLAG, False: ""}))
    table.insert(7, "From", df["source"].astype("object").fillna(BLANK))
    return table


def daily_balance(df: pd.DataFrame) -> pd.DataFrame:
    """End-of-day balance per date, oldest first, for the chart.

    Rows are newest first, so the first row of each date is that day's last posting.
    """
    last = df.drop_duplicates(subset="date", keep="first").sort_values("date")
    return pd.DataFrame(
        {"date": last["date"].to_numpy(), "balance ($)": (last["balance_cents"] / 100).to_numpy()}
    )


def balance_chart(daily: pd.DataFrame) -> go.Figure:
    """End-of-day balance as a step line: balances jump at postings and stay flat
    between them, so straight diagonal segments would suggest gradual change."""
    figure = px.line(daily, x="date", y="balance ($)", title="End-of-day balance", line_shape="hv")
    figure.update_traces(
        meta="recent", name="Balance", showlegend=False
    )  # the theme styles by role
    figure.add_hline(y=0, line_dash="dot", name="zero")
    return figure


SPLIT_NAMES = {"dev": "development", "test_a": "holdout (Test A)", "test_b": "holdout (Test B)"}


def review_table(rows: pd.DataFrame, labels: dict, key: bytes | None) -> pd.DataFrame:  # type: ignore[type-arg]
    """One merchant's rows on the label review screen: date, type, text (pseudonymized when
    a key is given), amount, split and current label."""
    texts = [
        pseudonymize_sub_description(t, s, key).text if key is not None else s
        for t, s in zip(rows["txn_type"], rows["text"], strict=True)
    ]
    current = []
    for f in rows["fingerprint"]:
        label = labels.get(f)
        current.append(BLANK if label is None else label.category or "skipped")
    return pd.DataFrame(
        {
            "Date": [d.isoformat() for d in rows["posted_date"]],
            "Type": list(rows["txn_type"]),
            "Text": [t if t.strip() else BLANK for t in texts],
            "Amount ($)": [c / 100 for c in rows["amount_cents"]],  # display only
            "Split": [SPLIT_NAMES[s] for s in rows["split"]],
            "Label": current,
        }
    )


# --- Recurring page ---

_KIND_NAMES = {
    "e-transfer": "e-Transfers of one amount",
    "own transfer": "Transfer to my own account",
}


def series_label(series: object, hide: bool = False) -> str:
    """What to call a series on screen: its merchant, your name for it, or what it is."""
    name = getattr(series, "name", None)
    if hide:
        return HIDDEN
    label = name or _KIND_NAMES.get(getattr(series, "kind", ""), BLANK)
    return label  # how it is paid goes in the grey line under the name, not after it


def recurring_table(  # type: ignore[type-arg]
    series: list, hidden: set[str],
    categories: dict[str, tuple[str | None, bool]] | None = None,
) -> pd.DataFrame:  # fmt: skip
    """Recurring series as shown on screen. Amounts in dollars (display only), as sizes:
    the direction column says whether money comes in or goes out. A category that is only
    an estimate from the series' payments says so."""
    shown = categories or {}

    def category(s: object) -> str:
        name, estimate = shown.get(s.key, (getattr(s, "category", None), False))  # type: ignore[attr-defined]
        return BLANK if not name else f"{name} (estimate)" if estimate else name

    return pd.DataFrame(
        {
            "Name": [series_label(s, s.key in hidden) for s in series],
            "Direction": ["money in" if s.direction == "in" else "money out" for s in series],
            "How often": [s.cadence for s in series],
            "Expected ($)": [abs(s.expected_cents) / 100 for s in series],
            "Usual low ($)": [min(abs(c) for c in s.range_cents) / 100 for s in series],
            "Usual high ($)": [max(abs(c) for c in s.range_cents) / 100 for s in series],
            "Amount": [s.amount_type for s in series],
            "Next expected": [s.next_expected.isoformat() for s in series],
            "Category": [category(s) for s in series],
            "Status": [s.status for s in series],
            "Active": ["yes" if s.active else "ended" for s in series],
            "Payments": [len(s.evidence) for s in series],
        }
    )


# --- Forecast page ---


def hidden_series(rows: pd.DataFrame, series: list, key: bytes | None) -> set[str]:  # type: ignore[type-arg]
    """Keys of the series whose own name must not show: pseudonymizing replaced a name in one
    of its payments (a person), and the name is not one I typed myself."""
    if key is None or rows.empty:
        return set()
    shown = display_table(rows, key)
    changed = set(rows.loc[(shown["Text"] != rows["text"]).to_numpy(), "fingerprint"])
    return {s.key for s in series if changed & set(s.evidence) and not s.named_by_me}


def forecast_table(items: list, by_key: dict, hidden: set[str]) -> pd.DataFrame:  # type: ignore[type-arg]
    """The recurring payments placed on the forecast days, one row each, with what each rests
    on. Amounts in dollars (display only): money in is positive, money out is negative."""
    return pd.DataFrame(
        {
            "Date": [i.day.isoformat() for i in items],
            "Name": [
                series_label(by_key[i.series_key], i.series_key in hidden)
                if i.series_key in by_key
                else (i.name or BLANK)  # a planned item: the words I typed
                for i in items
            ],
            "Amount ($)": [i.cents / 100 for i in items],
            "Based on": [i.basis for i in items],
            "Why": [i.note for i in items],
        }
    )


def forecast_chart(actual: pd.DataFrame, forecast: object, buffer_cents: int) -> go.Figure:
    """Recent end-of-day balances, then the simulated range (10th to 90th percentile) with the
    middle future with and without irregular deposits, then the forecast with no randomness,
    with zero and the buffer. Every forecast line and the band start at the last day of the
    data, at that day's closing balance, so the chart has no gap."""
    days = forecast.days  # type: ignore[attr-defined]
    sim = getattr(forecast, "simulation", None)
    start = forecast.start  # type: ignore[attr-defined]
    anchor_day, anchor = start.data_last_updated, start.balance_cents / 100

    def from_anchor(xs: list, cents: list) -> dict:  # type: ignore[type-arg]
        return {"x": [anchor_day, *xs], "y": [anchor, *(c / 100 for c in cents)]}

    figure = go.Figure()
    if not actual.empty:
        figure.add_scatter(x=actual["date"], y=actual["balance ($)"], mode="lines",
                           line_shape="hv", name="Recent balance", meta="recent")  # fmt: skip
    if sim is not None:
        x = list(sim.days)
        figure.add_scatter(**from_anchor(x, sim.p90), mode="lines", line_shape="hv",
                           showlegend=False, hoverinfo="skip", meta="band_edge")  # fmt: skip
        figure.add_scatter(
            **from_anchor(x, sim.p10), mode="lines", line_shape="hv", fill="tonexty",
            name=f"Range of {sim.futures} simulated futures (10th to 90th percentile)",
            meta="band",
        )  # fmt: skip
        figure.add_scatter(
            **from_anchor(x, sim.p50), mode="lines", line_shape="hv", meta="median",
            name="Middle future (median), with irregular deposits",
        )  # fmt: skip
        figure.add_scatter(
            **from_anchor(x, sim.median_without), mode="lines", line_shape="hv",
            name="Middle future (median), without irregular deposits", meta="median_without",
        )  # fmt: skip
    figure.add_scatter(
        **from_anchor([d.day for d in days], [d.balance_cents for d in days]), mode="lines",
        line_shape="hv", name="Forecast with no randomness (an estimate)", meta="estimate",
    )  # fmt: skip
    figure.add_hline(y=0, line_dash="dot", name="zero")
    figure.add_hline(y=buffer_cents / 100, line_dash="dash", annotation_text="safety buffer",
                     name="buffer")  # fmt: skip
    figure.update_layout(title="Projected end-of-day balance", yaxis_title="balance ($)")
    return figure


def calculator_chart(
    start_day: object, start_cents: int, now: object, mine: object, buffer_cents: int | None,
    title: str = "End-of-day balance",
) -> go.Figure:  # fmt: skip
    """The scenario calculator's two lines, "as things are now" and "my scenario" (both
    lookahead.scenario results), from the starting balance, with the buffer and the first day
    "my scenario" goes below it. Not the forecast's what-if, which is `scenario_chart`."""
    figure = go.Figure()
    for result, name, meta in ((now, "As things are now", "now"), (mine, "My scenario", "mine")):
        days, balances = result.days, result.balances  # type: ignore[attr-defined]
        figure.add_scatter(
            x=[start_day, *days], y=[start_cents / 100, *(c / 100 for c in balances)],
            mode="lines", line_shape="hv", name=name, meta=meta,
        )  # fmt: skip
    figure.add_hline(y=0, line_dash="dot", name="zero")
    if buffer_cents is not None:
        figure.add_hline(y=buffer_cents / 100, line_dash="dash", annotation_text="safety buffer",
                         name="buffer")  # fmt: skip
    below = getattr(mine, "first_below", None)
    if below is not None:
        by_day = dict(zip(mine.days, mine.balances, strict=True))  # type: ignore[attr-defined]
        figure.add_scatter(x=[below], y=[by_day[below] / 100], mode="markers", meta="first_below",
                           name="First day below the buffer", marker={"size": 10})  # fmt: skip
    # The app's chart template has a 16 px top margin and no legend: that clips the title and
    # hides which line is which. Room on top for the title, then the line names in a row under it.
    figure.update_layout(
        title={"text": title, "x": 0, "xanchor": "left", "y": 0.98, "yanchor": "top"},
        yaxis_title="balance ($)", margin={"t": 72}, showlegend=True,
        legend={"orientation": "h", "x": 0, "xanchor": "left", "y": 1.02, "yanchor": "bottom"},
    )  # fmt: skip
    return figure


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def breakdown_table(rows: list) -> pd.DataFrame:  # type: ignore[type-arg]
    """What is in everyday spending: a row per type, the average for each weekday over the
    last 8 weeks, in dollars (display only), and a total row."""
    if not rows:
        return pd.DataFrame()
    table = pd.DataFrame(
        [[r.type, *(c / 100 for c in r.per_weekday), r.per_week / 100] for r in rows],
        columns=["Type", *_WEEKDAYS, "Per week"],
    )
    total = ["All types", *(float(table[c].sum()) for c in (*_WEEKDAYS, "Per week"))]
    return pd.concat([table, pd.DataFrame([total], columns=table.columns)], ignore_index=True)


def events_table(events: list) -> pd.DataFrame:  # type: ignore[type-arg]
    """What was set aside from the forecast: date, what it was, the amount in dollars (display
    only; money in is positive, money out is negative), and which group it belongs to: a
    numbered pass-through, a large deposit or a large payment."""

    def what(e: object) -> str:
        group = getattr(e, "group", "")
        if group == "Pass-through":
            return f"Pass-through {e.pair}"  # type: ignore[attr-defined]
        return f"{group} (not forecast)" if group else ""

    return pd.DataFrame(
        {
            "Date": [e.day.isoformat() for e in events],
            "Type": [e.kind for e in events],
            "Amount ($)": [e.cents / 100 for e in events],
            "What": [what(e) for e in events],
        }
    )


def planned_table(  # type: ignore[type-arg]
    planned: list, first_day: object, last_day: object, received: frozenset[int] = frozenset()
) -> pd.DataFrame:
    """The planned items I entered: day, what, the amount in dollars (display only; money in is
    positive), when I entered it, and whether this forecast uses it. An item a real transaction
    has matched says "received": it is in the balance already, so it is not placed again."""

    def used(p: object) -> str:
        if p.item_id in received:  # type: ignore[attr-defined]
            return "received"
        if p.day < first_day:  # type: ignore[attr-defined, operator]
            return "no: the day has passed"
        return "yes" if p.day <= last_day else "no: after the last forecast day"  # type: ignore[attr-defined, operator]

    return pd.DataFrame(
        {
            "Date": [p.day.isoformat() for p in planned],
            "What": [p.description for p in planned],
            "Amount ($)": [p.cents / 100 for p in planned],
            "Entered on": [p.entered_at.isoformat() for p in planned],
            "In this forecast": [used(p) for p in planned],
        }
    )


def deposit_weeks_table(weeks: list) -> pd.DataFrame:  # type: ignore[type-arg]
    """What is in irregular deposits: one row for each week the futures can draw from, with the
    deposits in the stream (dollars, display only), their types, and what was set aside that
    week (big deposits and pass-throughs, which the futures never draw)."""

    def kinds(w: object) -> str:
        parts = [f"{kind} {cents / 100:.2f}" for kind, cents in w.in_stream]  # type: ignore[attr-defined]
        return "; ".join(parts) if parts else "nothing"

    def aside(w: object) -> str:
        return "; ".join(
            f"{e.group} {abs(e.cents) / 100:,.2f} ({e.day}, not forecast)"
            for e in w.set_aside  # type: ignore[attr-defined]
        )

    return pd.DataFrame(
        {
            "Week": [f"{w.first} to {w.last}" for w in weeks],
            "In the stream ($)": [w.total / 100 for w in weeks],
            "What": [kinds(w) for w in weeks],
            "Set aside": [aside(w) for w in weeks],
        }
    )


def scenario_chart(
    forecast: object, original: object, modified: object, buffer_cents: int
) -> go.Figure:
    """The original range and middle future, and the same with the scenario's changes, both from
    the last observed balance so they start together, with zero and the buffer."""
    start = forecast.start  # type: ignore[attr-defined]
    anchor_day, anchor = start.data_last_updated, start.balance_cents / 100

    def line(sim: object, cents: tuple[int, ...]) -> dict:  # type: ignore[type-arg]
        return {
            "x": [anchor_day, *sim.days],  # type: ignore[attr-defined]
            "y": [anchor, *(c / 100 for c in cents)],
        }

    figure = go.Figure()
    for sim, label, role in (
        (original, "Original", "original"),
        (modified, "With the changes", "modified"),
    ):
        figure.add_scatter(**line(sim, sim.p90), mode="lines", line_shape="hv",  # type: ignore[attr-defined]
                           showlegend=False, hoverinfo="skip", meta="band_edge")  # fmt: skip
        figure.add_scatter(
            **line(sim, sim.p10), mode="lines", line_shape="hv", fill="tonexty",  # type: ignore[attr-defined]
            name=f"{label}: range (10th to 90th percentile)", meta=f"{role}_band",
        )  # fmt: skip
        figure.add_scatter(
            **line(sim, sim.p50), mode="lines", line_shape="hv", meta=f"{role}_median",  # type: ignore[attr-defined]
            name=f"{label}: middle future",
        )  # fmt: skip
    figure.add_hline(y=0, line_dash="dot", name="zero")
    figure.add_hline(y=buffer_cents / 100, line_dash="dash", annotation_text="safety buffer",
                     name="buffer")  # fmt: skip
    figure.update_layout(title="Conditional scenario: original and with the changes",
                         yaxis_title="balance ($)")  # fmt: skip
    return figure
