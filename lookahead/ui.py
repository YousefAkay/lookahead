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
BLANK = "—"  # shown instead of the bank's single-space text


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
    text = text.where(text.str.strip() != "", BLANK)  # service charges, ABM withdrawals
    return pd.DataFrame(
        {
            "Date": df["date"].dt.strftime("%Y-%m-%d"),
            "Type": df["type"],
            "Text": text,
            "Amount ($)": df["amount_cents"] / 100,  # display only; money stays in cents
            "Balance ($)": df["balance_cents"] / 100,
        }
    )


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
