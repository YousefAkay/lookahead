"""The "smarter categories" setup question on the page (categorizer v2, 4b). The logic and
the wording live in lookahead/smarter_categories.py; this file only draws them.

`ask_once` shows the question in the page the first time (until I answer); `settings_panel`
keeps the same choice in the settings panel, with the send button once it is on. Nothing is
ever sent without that button."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import streamlit as st

from lookahead import smarter_categories as smarter
from lookahead.ui import count


def ask_once(conn: sqlite3.Connection, has_key: bool, demo: bool) -> None:
    """Call it before `settings_panel`, so an answer here also sets the settings toggle."""
    current = smarter.state(conn, has_key, demo)
    if current == "needs key" and not st.session_state.get("smarter_needs_key_seen"):
        st.info(smarter.NEEDS_KEY)
        st.session_state["smarter_needs_key_seen"] = True
    if current != "ask":
        return
    with st.container(border=True, key="smarter_question"):
        st.markdown(smarter.QUESTION)
        yes, no = st.columns(2)
        for column, label, value in ((yes, "Turn on", True), (no, "Not now", False)):
            if column.button(label, key=f"smarter_{'yes' if value else 'no'}"):
                smarter.answer(conn, value)
                st.session_state["smarter_categories"] = value  # the settings toggle follows
                st.rerun()


def settings_panel(
    conn: sqlite3.Connection, account: str, has_key: bool, demo: bool, key: bytes | None,
    cache: Path, recategorize: Callable[[], object],
) -> None:  # fmt: skip
    """`recategorize` brings back the model's own answers when it is turned off."""
    current = smarter.state(conn, has_key, demo)
    if current == "hidden":  # the synthetic demo: nothing shown, nothing changes
        return
    st.sidebar.divider()
    wanted = st.sidebar.toggle(
        "Smarter categories for new merchants",
        value=current == "on",
        disabled=current == "needs key",
        help=smarter.QUESTION + " Off until you say yes. Nothing is sent until you click send.",
        key="smarter_categories",
    )
    if current == "needs key":
        st.sidebar.caption(smarter.NEEDS_KEY)
        return
    if wanted != (current == "on"):
        smarter.answer(conn, wanted)
        if not wanted:
            recategorize()
        st.rerun()
    if current != "on" or key is None:
        return
    if not has_key:  # on, but the key is gone: what is needed, and no send button
        st.sidebar.caption(smarter.NEEDS_KEY)
    from lookahead import rag_categorizer
    from lookahead.llm_fallback import candidates

    if cache.exists():  # saved answers only: nothing is sent here
        rag_categorizer.apply(conn, account, key, cache)

    names = len(candidates(conn, account, key))
    st.sidebar.caption(f"{count(names, 'new merchant name')} could be sent, with similar "
                       "labelled merchants and directory chains.")  # fmt: skip
    if names and has_key and st.sidebar.button(f"Send {count(names, 'name')} to Haiku 4.5"):
        try:
            done = rag_categorizer.apply(conn, account, key, cache, send=True)
        except Exception as e:  # the kind of error only: a message may quote a name
            st.session_state["smarter_note"] = f"Stopped: {type(e).__name__}. Nothing lost."
        else:
            note = (
                f"Sent {count(done.sent, 'name')}; {count(done.rows, 'row')} now with a "
                f"category from smarter categories; {done.unsure} unsure, "
                f"{done.not_taken} answers not usable (still to check)."
            )
            none_usable = (f"Smarter categories gave no usable answer for "
                           f"{count(done.with_answer, 'name')}. ")  # fmt: skip
            st.session_state["smarter_note"] = (none_usable if done.warning() else "") + note
        st.rerun()
    if "smarter_note" in st.session_state:
        st.sidebar.info(st.session_state.pop("smarter_note"))
