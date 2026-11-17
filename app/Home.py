"""Lookahead app entry point. Run from the project root: streamlit run app/Home.py

The router: every page is listed in app/navigation.py with its own address, so links to any page
keep working. The green top bar (app/theme.py) is the menu, so Streamlit's own is hidden. Home's
content is app/home_page.py.
"""

import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP.parent))  # make `lookahead` and `app` importable

import streamlit as st  # noqa: E402

from app.navigation import APP_PAGES  # noqa: E402

pages = [
    st.Page(str(APP / p.file), title=p.name, url_path=p.url_path or None, default=not p.url_path)
    for p in APP_PAGES
]
st.navigation(pages, position="hidden").run()
