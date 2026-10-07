"""Planned items (3d): money in or out that I enter myself, placed exactly on its day.

A term payment or an expected deposit is not a recurring series and is not guessed from history.
Each item says what it is, which day, and how much (money in is positive); the forecast labels
it "entered" and never moves it. Items are dated: one entered after a replay's date is ignored
by that replay (rule zero), and removing an item takes effect from the day it was removed.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

MAX_DESCRIPTION = 80


@dataclass(frozen=True)
class PlannedItem:
    item_id: int
    day: date
    cents: int  # signed: money in is positive
    description: str
    entered_at: date

    @property
    def is_deposit(self) -> bool:
        return self.cents > 0


def add_planned(
    conn: sqlite3.Connection, account: str, day: date, cents: int, description: str,
    when: date | None = None,
) -> int:  # fmt: skip
    """Enter an item. `cents` is signed (positive: money in). Returns its id."""
    text = " ".join(description.split())
    if cents == 0:
        raise ValueError("an amount other than zero is needed")
    if not text:
        raise ValueError("say what it is")
    if len(text) > MAX_DESCRIPTION:
        raise ValueError(f"at most {MAX_DESCRIPTION} characters")
    with conn:
        cursor = conn.execute(
            "INSERT INTO planned_items (account, day, amount_cents, description, entered_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account, day.isoformat(), int(cents), text, (when or date.today()).isoformat()),
        )
    return int(cursor.lastrowid or 0)


def remove_planned(
    conn: sqlite3.Connection, account: str, item_id: int, when: date | None = None
) -> None:
    """Remove an item from `when` on (it stays visible to replays before that day)."""
    with conn:
        cursor = conn.execute(
            "UPDATE planned_items SET removed_at = ? WHERE item_id = ? AND account = ?"
            " AND removed_at IS NULL",
            ((when or date.today()).isoformat(), item_id, account),
        )
    if cursor.rowcount == 0:
        raise ValueError("no such planned item, or it was already removed")


def planned_as_of(
    conn: sqlite3.Connection, account: str, as_of: date | None = None
) -> list[PlannedItem]:
    """The items that exist on `as_of` (entered by then, not yet removed), by day. `None` means
    now: every item that has not been removed."""
    limit = (as_of or date.max).isoformat()
    rows = conn.execute(
        "SELECT item_id, day, amount_cents, description, entered_at FROM planned_items"
        " WHERE account = ? AND entered_at <= ? AND (removed_at IS NULL OR removed_at > ?)"
        " ORDER BY day, item_id",
        (account, limit, limit),
    )
    return [
        PlannedItem(r[0], date.fromisoformat(r[1]), r[2], r[3], date.fromisoformat(r[4]))
        for r in rows
    ]


# --- Received: a real transaction has arrived for the item ---

MATCH_DAYS = 7  # a real transaction counts if it posted within this many days of the item's day
MATCH_PERCENT = 5  # ... and its amount is within this percent of the item's


def received_planned(
    conn: sqlite3.Connection, account: str, items: list[PlannedItem], as_of: date | None = None
) -> frozenset[int]:
    """The ids of the items that a real transaction has since matched, so the forecast stops
    placing them (they would be counted twice: once as the item, once in the balance).

    A transaction matches an item if it has the same direction, an amount within MATCH_PERCENT of
    the item's, a posting date within MATCH_DAYS of the item's day, and was imported on or after
    the day the item was entered (an older row cannot be the money I was waiting for). Rule zero:
    only rows posted on or before `as_of` count. One transaction matches one item: the closest
    pairs are settled first (by days apart, then by amount difference)."""
    if not items:
        return frozenset()
    limit = (as_of or date.max).isoformat()
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, imported_at FROM transactions"
        " WHERE account = ? AND posted_date <= ?",
        (account, limit),
    ).fetchall()
    pairs = []
    for item in items:
        for row_id, posted, cents, imported_at in rows:
            apart = abs((date.fromisoformat(posted) - item.day).days)
            same_way = (cents > 0) == (item.cents > 0) and cents != 0
            close = abs(cents - item.cents) * 100 <= MATCH_PERCENT * abs(item.cents)
            if (
                same_way
                and close
                and apart <= MATCH_DAYS
                and imported_at[:10] >= item.entered_at.isoformat()
            ):
                pairs.append((apart, abs(cents - item.cents), item.day, item.item_id, row_id))
    taken: set[int] = set()
    done: set[int] = set()
    for _, _, _, item_id, row_id in sorted(pairs):
        if item_id not in done and row_id not in taken:
            done.add(item_id)
            taken.add(row_id)
    return frozenset(done)
