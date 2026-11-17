"""App settings kept in the database (`app_settings`). A missing or damaged row means the
default. Money is integer cents."""

import sqlite3
from datetime import date

DEFAULT_BUFFER_CENTS = 20000  # a $200 safety buffer
_BUFFER_KEY = "safety_buffer_cents"
_SAVINGS_KEY = "scenario_savings"  # "<cents>|<ISO date entered>"


def savings_balance(conn: sqlite3.Connection) -> tuple[int, date] | None:
    """The savings balance typed on the Scenario page and the day it was entered, or None (not
    entered, or a damaged row): then the calculator shows chequing only."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (_SAVINGS_KEY,)).fetchone()
    if row is None:
        return None
    try:
        cents, entered = str(row[0]).split("|")
        value = int(cents)
        return (value, date.fromisoformat(entered)) if value >= 0 else None
    except ValueError:
        return None


def set_savings_balance(conn: sqlite3.Connection, cents: int | None, entered: date) -> None:
    """Remember the savings balance with the day it was entered; None forgets it."""
    with conn:
        if cents is None:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (_SAVINGS_KEY,))
            return
        if cents < 0:
            raise ValueError("a savings balance cannot be negative")
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SAVINGS_KEY, f"{int(cents)}|{entered.isoformat()}"),
        )


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
