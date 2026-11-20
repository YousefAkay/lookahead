"""Home's "Needs a look" card: the alerts already recorded, counted without running anything.

It reads the stored alert episodes only (lookahead/recurring_alerts.py for recurring payments,
lookahead/alerts.py for a possible shortfall, lookahead/budget_alerts.py for a possible
overspend). Nothing is detected again and no forecast is run, so Home stays quick; the
Recurring, Forecast and Budgets pages keep the episodes up to date.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from lookahead.alerts import Episode, active_alert
from lookahead.budget_alerts import BudgetEpisode, open_budget_alerts
from lookahead.budgets import month_key
from lookahead.recurring_alerts import open_alerts


@dataclass(frozen=True)
class NeedsALook:
    recurring: int  # recurring alerts open and not dismissed
    shortfall: Episode | None  # the open shortfall alert, if any
    budgets: tuple[BudgetEpisode, ...] = ()  # budget alerts open and not dismissed

    @property
    def anything(self) -> bool:
        return self.recurring > 0 or self.shortfall is not None or bool(self.budgets)


def needs_a_look(conn: sqlite3.Connection, account: str) -> NeedsALook:
    """What is open for an account. A database from before an alert's tables existed (opened
    read-only, as Home does) has nothing open of that kind."""
    try:
        recurring = len(open_alerts(conn, account))
    except sqlite3.OperationalError:
        recurring = 0
    try:
        shortfall = active_alert(conn, account)
    except sqlite3.OperationalError:
        shortfall = None
    return NeedsALook(recurring, shortfall, current_budget_alerts(conn, account))


def current_budget_alerts(conn: sqlite3.Connection, account: str) -> tuple[BudgetEpisode, ...]:
    """Open budget alerts of the current budget month only: the month of the day after the last
    posted row, as on the Budgets tab. An earlier month's alert ends only when the tab next looks,
    so after an import Home would otherwise still list it."""
    try:
        row = conn.execute("SELECT MAX(posted_date) FROM transactions WHERE account = ?",
                           (account,)).fetchone()  # fmt: skip
    except sqlite3.OperationalError:  # an old read-only database: nothing to list
        return ()
    if not row or not row[0]:
        return ()
    current = month_key(date.fromisoformat(row[0]) + timedelta(days=1))
    return tuple(e for e in open_budget_alerts(conn, account) if e.month >= current)
