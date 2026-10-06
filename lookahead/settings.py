"""App settings kept in the database (`app_settings`). A missing or damaged row means the
default. Money is integer cents."""

import sqlite3

DEFAULT_BUFFER_CENTS = 20000  # a $200 safety buffer
_BUFFER_KEY = "safety_buffer_cents"


def safety_buffer_cents(conn: sqlite3.Connection) -> int:
    """The balance a warning is measured against (editable on the Forecast page)."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (_BUFFER_KEY,)).fetchone()
    try:
        value = int(row[0]) if row else DEFAULT_BUFFER_CENTS
    except ValueError:
        return DEFAULT_BUFFER_CENTS
    return value if value >= 0 else DEFAULT_BUFFER_CENTS


def set_safety_buffer_cents(conn: sqlite3.Connection, cents: int) -> None:
    if cents < 0:
        raise ValueError("the safety buffer cannot be negative")
    with conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_BUFFER_KEY, str(int(cents))),
        )
