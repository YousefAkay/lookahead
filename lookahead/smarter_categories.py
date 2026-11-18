"""The setup question for "smarter categories" (categorizer v2, 4b): one clear question
instead of a hidden toggle. It turns the RAG categorizer on or off (`categorizer_v2_rag`).

- Asked the first time categories are shown for my data, and kept in the settings panel.
- The answer is remembered in `app_settings`. Not answered means off.
- With no API key there is no question: the app says what is needed instead.
- Never offered for the synthetic demo.
"""

import sqlite3
from typing import Literal

from lookahead import categorizer_v2

QUESTION = (
    "Smarter categories for new merchants: sends merchant names and your category labels "
    "(never amounts, dates or descriptions) to Haiku 4.5. Turn on?"
)
NEEDS_KEY = (
    "Smarter categories for new merchants need an API key: add ANTHROPIC_API_KEY to your "
    ".env file (and PSEUDONYM_KEY, which decides which names may be sent). Until then they "
    "stay off."
)

State = Literal["hidden", "needs key", "ask", "on", "off"]


def state(conn: sqlite3.Connection, has_key: bool, demo: bool) -> State:
    """What to show: nothing (demo), what is needed (no key), the question (never answered),
    or the remembered answer."""
    if demo:
        return "hidden"
    answer = categorizer_v2.stored_answer(conn, categorizer_v2.RAG)
    if not has_key:
        return "needs key" if answer is not True else "on"
    if answer is None:
        return "ask"
    return "on" if answer else "off"


def answer(conn: sqlite3.Connection, yes: bool) -> None:
    """Remember the answer (the settings panel can change it any time)."""
    categorizer_v2.set_option(conn, categorizer_v2.RAG, yes)
