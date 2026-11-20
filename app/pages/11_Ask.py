"""Ask: a question about my money, answered on this computer (week 7, spec
docs/specs/2026-11-20-ask.md).

The question becomes a plan of building blocks (the offline parser for now), the plan is checked,
then run with the code the other pages use. The page shows the answer, "How I worked it out" (the
plan in plain words, each step's result and the merchant matches made), the rows behind it and
the date the data runs to. Nothing is written to the database and nothing is sent anywhere.

"Today" is the real date with my data; with the synthetic demo data it is the day after the data
ends, so "last month" means something for made-up data.
"""

import html
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # make `lookahead` and `app` importable

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.theme import account_setting, apply, data_settings, fmt_date, footer, top_bar  # noqa: E402
from lookahead import demo_mode  # noqa: E402
from lookahead.ask.answer import (  # noqa: E402
    EXAMPLES,
    Asked,
    ask_offline,
    headline,
    result_table,
    step_value,
)
from lookahead.ask.run import AskBack, Context, Listing, RowSet  # noqa: E402
from lookahead.budgets import load_limits  # noqa: E402
from lookahead.db import connect_readonly  # noqa: E402
from lookahead.demo import build_demo_db  # noqa: E402
from lookahead.demo import has_categories as demo_has_categories  # noqa: E402
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key  # noqa: E402
from lookahead.ui import (  # noqa: E402
    DISCLAIMER,
    HIDDEN,
    demo_db_path,
    display_table,
    private_db_path,
    synthetic_dir,
)
from lookahead.views import accounts, has_categories, last_posted, transactions  # noqa: E402

TITLE = "Ask"
QUESTION = "ask_question"
CHOICES = "ask_choices"  # {"question": text, "picks": {phrase: merchant}}
DEMO_LIMITS = "bu_demo_limits"  # the Budgets tab's session limits in demo mode
BUDGET_EXAMPLE = "How am I doing on my Shopping budget?"
MAX_ROWS_SHOWN = 200

apply(TITLE)
bar = st.container(key="topbar")

# --- Which database ---
source, pseudonymize = data_settings()
if source == "Synthetic demo":
    db_path = demo_db_path()
    if not db_path.exists() or not demo_has_categories(db_path):
        with st.spinner("Building the synthetic demo database..."):
            build_demo_db(db_path, synthetic_dir())
else:
    db_path = private_db_path()
    if not db_path.exists():
        top_bar(bar, TITLE, None, source)
        st.info(f"No database found at `{db_path}`. Import your exports first.")
        st.stop()

try:
    stored_key: bytes | None = pseudonym_key()
except PseudonymKeyError:
    stored_key = None
display_key = stored_key if pseudonymize else None
if pseudonymize and stored_key is None:
    st.warning("No PSEUDONYM_KEY: showing raw text.")

conn = connect_readonly(db_path)  # Ask only reads
names = accounts(conn)
if not names:
    top_bar(bar, TITLE, None, source)
    st.info("This database has no transactions yet.")
    st.stop()
account = account_setting(names)
last = last_posted(conn, account)
top_bar(bar, TITLE, last, source)
assert last is not None

st.markdown(f"<h1>{TITLE}</h1>", unsafe_allow_html=True)
st.markdown(
    '<p class="la-sub">Ask about your spending, your forecast, a what-if, your budgets or safe to '
    "spend. It is worked out on this computer from your data, and nothing is sent anywhere.</p>",
    unsafe_allow_html=True,
)
if not has_categories(conn, account):
    st.info("Ask needs categories first: run the categorizer (Transactions page).")
    footer(DISCLAIMER)
    st.stop()

today = last + timedelta(days=1) if source == "Synthetic demo" else demo_mode.today(last)
limits = None
if demo_mode.is_demo():  # the Budgets tab keeps demo limits in the session only
    limits = dict(st.session_state.get(DEMO_LIMITS, {}).get(account, {}))
has_budgets = any(v is not None for v in (limits or load_limits(conn, account)).values())
examples = [e for e in EXAMPLES if has_budgets or "budget" not in e.casefold()]


def use_example(text: str) -> None:
    st.session_state[QUESTION] = text


def pick(question: str, phrase: str, name: str) -> None:
    picks = dict(st.session_state.get(CHOICES, {}).get("picks", {}))
    picks[phrase] = name
    st.session_state[CHOICES] = {"question": question, "picks": picks}


with st.form("ask", border=False):
    st.text_input("Your question", key=QUESTION, placeholder=examples[0])
    st.form_submit_button("Ask", type="primary")

question = str(st.session_state.get(QUESTION, "")).strip()
if not question:
    st.markdown('<p class="la-note">Try one of these:</p>', unsafe_allow_html=True)
    for i, example in enumerate(examples):
        st.button(example, key=f"example_{i}", on_click=use_example, args=(example,))
    footer(DISCLAIMER)
    st.stop()

# --- The answer ---
saved = st.session_state.get(CHOICES, {})
picks = saved.get("picks", {}) if saved.get("question") == question else {}
ctx = Context(conn, account, today, stored_key, choices=picks, limits=limits)
with st.spinner("Working it out..."):
    asked: Asked = ask_offline(question, ctx)

hidden_names: set[str] = set()
if display_key is not None:
    everything = transactions(conn, account)
    shown = display_table(everything, display_key)
    if "Merchant" in shown.columns:
        hidden_names = {m for m, s in zip(everything["merchant"], shown["Merchant"], strict=True)
                        if isinstance(m, str) and s == HIDDEN}  # fmt: skip


def hide(text: str) -> str:
    for name in hidden_names:
        text = text.replace(name, HIDDEN)
    return text


def hide_table(df: pd.DataFrame) -> pd.DataFrame:
    for column in ("Merchant", "Name"):
        if column in df.columns:
            df = df.assign(**{column: df[column].map(lambda v: HIDDEN if v in hidden_names
                                                     else v)})  # fmt: skip
    return df


out = asked.outcome
with st.container(border=True, key="ask-answer"):
    st.markdown(f"<h2>{html.escape(hide(headline(asked)))}</h2>", unsafe_allow_html=True)
    if out is not None and out.kind == "answer":
        table = result_table(out.result)
        if table is not None:
            st.dataframe(hide_table(table), hide_index=True)
        if isinstance(out.result, Listing):
            st.dataframe(display_table(out.result.rows, display_key), hide_index=True)
        for note in getattr(out.result, "notes", ()):
            st.caption(note)
    if out is not None and isinstance(out.result, AskBack) and out.result.candidates:
        for i, name in enumerate(out.result.candidates):
            st.button(hide(name), key=f"pick_{i}", on_click=pick,
                      args=(question, out.result.phrase, name))  # fmt: skip
    if out is not None:
        for note in out.notes:
            st.caption(note)
    if asked.show_examples:
        st.markdown('<p class="la-note">Here is what I can answer, for example:</p>',
                    unsafe_allow_html=True)  # fmt: skip
        for i, example in enumerate(examples):
            st.button(example, key=f"example_{i}", on_click=use_example, args=(example,))

if asked.plan is not None and out is not None:
    st.markdown("<h3>How I worked it out</h3>", unsafe_allow_html=True)
    lines = []
    for line, step in zip(out.words, asked.plan.steps, strict=True):
        value = step_value(out.values[step.name]) if step.name in out.values else ""
        lines.append(f"{line} → {value}" if value else line)
    for m in out.matches:
        if m.status == "one":
            lines.append(f"“{m.phrase}” matched {m.names[0]} ({m.how}).")
        elif m.status == "many":
            lines.append(f"“{m.phrase}” matches {len(m.names)} names.")
    st.markdown(
        "".join(f'<p class="la-note">{html.escape(hide(line))}</p>' for line in lines),
        unsafe_allow_html=True,
    )
    behind = [v for v in out.values.values() if isinstance(v, RowSet)]
    if behind and out.kind == "answer":
        st.markdown("<h3>Rows behind this</h3>", unsafe_allow_html=True)
        for rows in behind:
            n = len(rows.rows)
            more = f" (the first {MAX_ROWS_SHOWN})" if n > MAX_ROWS_SHOWN else ""
            st.caption(f"“{rows.name}”: {n} {'row' if n == 1 else 'rows'}{more}")
            if n:
                st.dataframe(display_table(rows.rows.head(MAX_ROWS_SHOWN), display_key),
                             hide_index=True)  # fmt: skip

st.caption(f"Data through {fmt_date(last)}. Forecast figures are estimates.")
footer(DISCLAIMER)
