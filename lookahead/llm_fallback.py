"""The opt-in LLM fallback in the app (step 8d): OFF by default.

When you turn it on, rows of NEW merchants whose category the trained model flagged can get
Claude's answer instead. Everything that may leave the machine is decided by
`lookahead.llm_categorizer`: the extracted name of a card purchase and nothing else, one
cached call per name. A name is only ever sent when you click the send button; retraining
reuses answers already saved and never sends anything.
A row answered this way keeps its flag (nobody has checked it) and shows "LLM" as the source.
"""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import lookahead.llm_categorizer as llm
from lookahead.config import load_env

SETTING = "llm_fallback"
DETAIL = "Claude, from the merchant name only"


def is_on(conn: sqlite3.Connection) -> bool:
    """The stored setting; off when it was never set (or in an older database)."""
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING,)).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row) and row[0] == "on"


def set_on(conn: sqlite3.Connection, on: bool) -> None:
    with conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (SETTING, "on" if on else "off"),
        )


def has_api_key(env_path: Path = Path(".env")) -> bool:
    return bool(os.environ.get(llm.KEY_NAME) or load_env(env_path).get(llm.KEY_NAME))


def candidates(conn: sqlite3.Connection, account: str, key: bytes) -> dict[str, list[str]]:
    """name -> fingerprints: flagged rows of new merchants whose name may be sent."""
    out: dict[str, list[str]] = {}
    for fingerprint, txn_type, text in conn.execute(
        "SELECT p.fingerprint, t.description, t.sub_description FROM category_predictions p"
        " JOIN transactions t ON t.fingerprint = p.fingerprint"
        " WHERE p.account = ? AND p.flagged = 1 AND p.source IN ('model', 'LLM')",
        (account,),
    ):
        name = llm.sendable_name(txn_type, text, key)
        if name:
            out.setdefault(name, []).append(fingerprint)
    return out


@dataclass(frozen=True)
class Applied:
    names: int  # distinct names among the candidates
    sent: int  # calls made now
    rows: int  # rows that now show the LLM's category


def not_asked_yet(conn: sqlite3.Connection, account: str, key: bytes, cache: Path) -> list[str]:
    """Candidate names with no saved answer: what the send button would send."""
    return sorted(set(candidates(conn, account, key)) - set(llm.load_cache(cache)))


def apply(
    conn: sqlite3.Connection, account: str, key: bytes, cache: Path = llm.DEFAULT_CACHE,
    send: bool = False, client: object | None = None,
) -> Applied:  # fmt: skip
    """Show the LLM's category on the candidate rows. `send=False` uses saved answers only
    (nothing leaves the machine); `send=True` first asks for the names not asked yet."""
    if not is_on(conn):
        raise RuntimeError("the LLM fallback is off")
    rows = candidates(conn, account, key)
    sent = 0
    if send:
        answers, sent = llm.classify(set(rows), cache, client)
    else:
        saved = llm.load_cache(cache)
        answers = {n: saved[n] for n in rows if n in saved}
    updates = [
        (answers[name].category, DETAIL, fingerprint)
        for name, fingerprints in rows.items()
        if name in answers and answers[name].category
        for fingerprint in fingerprints
    ]
    with conn:
        conn.executemany(
            "UPDATE category_predictions SET category = ?, source = 'LLM', detail = ?"
            " WHERE fingerprint = ?",
            updates,
        )
    return Applied(len(rows), sent, len(updates))
