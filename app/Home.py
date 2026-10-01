"""Lookahead app entry point. Run from the project root: streamlit run app/Home.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `lookahead` importable

import streamlit as st  # noqa: E402

from lookahead.ui import DISCLAIMER  # noqa: E402

st.set_page_config(page_title="Lookahead", layout="wide")
st.title("Lookahead")
st.markdown(
    "A local-first cashflow planner. It reads bank CSV exports, and will forecast your "
    "chequing balance and warn you before a possible shortfall, with the evidence behind "
    "every warning."
)
st.markdown(
    "**Pages**\n\n"
    "- **Transactions**: what the importer stored, with search, filters and a balance chart.\n\n"
    "- **Label review**: confirm categories one merchant at a time (for the categorizer).\n\n"
    "Choose **Synthetic demo** or **My data** in the sidebar of each page. "
    "Everything runs on this computer."
)
st.caption(DISCLAIMER)
