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
