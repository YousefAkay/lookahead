"""Helpers for the Streamlit pages: data locations and display-ready tables.

Kept out of the page scripts so they can be tested without running Streamlit.
"""

import os
from pathlib import Path

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


def synthetic_dir() -> Path:
    return Path(os.environ.get("LOOKAHEAD_SYNTHETIC_DIR", "data/synthetic"))


def demo_db_path() -> Path:
    return Path(os.environ.get("LOOKAHEAD_DEMO_DB", "data/synthetic/demo.db"))


def private_db_path() -> Path:
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
    figure.add_hline(y=0, line_dash="dot")
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
    return f"{label} (sent by hand, the day varies)" if getattr(series, "loose", False) else label


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
            "Name": [series_label(by_key[i.series_key], i.series_key in hidden) for i in items],
            "Amount ($)": [i.cents / 100 for i in items],
            "Based on": [i.basis for i in items],
            "Why": [i.note for i in items],
        }
    )


def forecast_chart(actual: pd.DataFrame, forecast: object, buffer_cents: int) -> go.Figure:
    """Recent end-of-day balances, then the projected ones (dashed), with zero and the buffer."""
    days = forecast.days  # type: ignore[attr-defined]
    figure = go.Figure()
    if not actual.empty:
        figure.add_scatter(x=actual["date"], y=actual["balance ($)"], mode="lines",
                           line_shape="hv", name="Recent balance")  # fmt: skip
    figure.add_scatter(
        x=[d.day for d in days], y=[d.balance_cents / 100 for d in days], mode="lines",
        line={"dash": "dash"}, line_shape="hv", name="Forecast (an estimate)",
    )  # fmt: skip
    figure.add_hline(y=0, line_dash="dot")
    figure.add_hline(y=buffer_cents / 100, line_dash="dash", annotation_text="safety buffer")
    figure.update_layout(title="Projected end-of-day balance", yaxis_title="balance ($)")
    return figure
