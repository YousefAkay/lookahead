"""Budget alerts (spec docs/specs/2026-11-20-budgets-that-forecast.md): the shortfall alert's shape
(episodes, evidence, dismiss), for "you may go over this budget this month".

The rule: alert when the chance of ending the month over the limit is above 50 percent, from day
8 of the month onward (the forecast's first day is the 8th or later: the first week is too noisy).
No tuning; "more likely than not".

One episode per category per month. It ends when the chance is no longer above 50 percent, and
opens again (the same episode, still dismissed if I dismissed it) if the chance comes back that
month; a look in a later month ends it at its month's last day.

What is recorded (an event, with its evidence): the first alert, the chance changing band (20, 50,
80 percent), or the likely overrun moving by MATERIAL_CENTS ($25) since the last recorded event. A
band change is notified; a $25 move only refreshes the evidence. A dismissed episode comes back
only when the chance enters a higher band than when it last spoke; a $25 move never brings it
back.

Rule zero: every number comes from the budget month handed in (lookahead/budgets.py), built from
rows posted on or before its data date. Nothing here looks at today's date, except to stamp a
dismissal.
"""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from lookahead.alerts import band_of
from lookahead.budgets import BudgetMonth, Pace, month_end, month_key
from lookahead.chance import chance_text

PROBABILITY_THRESHOLD = 0.50  # alert when the chance of going over is above this
FIRST_DAY_OF_MONTH = 8  # ... and the forecast's first day is the 8th or later
MATERIAL_CENTS = 2_500  # the likely overrun moving by $25 refreshes the evidence


def should_alert(pace: Pace, first_day: date) -> bool:
    """The budget alert rule."""
    return first_day.day >= FIRST_DAY_OF_MONTH and pace.chance_over > PROBABILITY_THRESHOLD


@dataclass(frozen=True)
class BudgetEpisode:
    episode_id: int
    account: str
    category: str
    month: str  # "YYYY-MM"
    opened_on: date
    last_seen_on: date
    closed_on: date | None
    dismissed_on: date | None
    probability: float  # as of the latest recorded event
    limit_cents: int
    spent_cents: int
    likely_end_cents: int
    evidence: dict[str, Any]

    @property
    def status(self) -> str:
        if self.closed_on is not None:
            return "closed"
        return "dismissed" if self.dismissed_on is not None else "open"

    @property
    def likely_over_cents(self) -> int:
        return max(self.likely_end_cents - self.limit_cents, 0)


@dataclass(frozen=True)
class LookResult:
    category: str
    episode_id: int | None  # None: no alert for this category
    recorded: bool  # an event was written
    notified: bool  # worth telling me about
    reason: str


def evidence_of(pace: Pace) -> dict[str, Any]:
    """What stands behind the alert: the figures and the rows behind spent so far."""
    return {
        "spent_cents": pace.spent_cents,
        "limit_cents": pace.limit_cents,
        "likely_end_cents": pace.likely_end_cents,
        "chance_over": pace.chance_over,
        "futures": pace.futures,
        "big_one_off": pace.big_one_off,
        "rows": [
            {"date": r.day.isoformat(), "cents": r.cents, "fingerprint": r.fingerprint}
            for r in pace.rows
        ],  # fmt: skip
    }


def record_look(
    conn: sqlite3.Connection, account: str, month: BudgetMonth, first_day: date
) -> list[LookResult]:  # fmt: skip
    """Apply the rule to each budget of this look and keep the episodes up to date. Looking again
    at the same numbers adds nothing. Looks must come in data-date order (a replay does so)."""
    key = month_key(month.month)
    data_date = month.data_date
    with conn:
        for episode_id, ep_month in conn.execute(
            "SELECT episode_id, month FROM budget_alert_episodes WHERE account = ?"
            " AND closed_on IS NULL AND month < ?",
            (account, key),
        ).fetchall():  # an earlier month's episode ends at its month's last day
            last = month_end(date.fromisoformat(f"{ep_month}-01")).isoformat()
            conn.execute("UPDATE budget_alert_episodes SET closed_on = ? WHERE episode_id = ?",
                         (last, episode_id))  # fmt: skip
        budgeted = {p.category for p in month.paces}
        for episode_id, category in conn.execute(
            "SELECT episode_id, category FROM budget_alert_episodes WHERE account = ?"
            " AND closed_on IS NULL AND month = ?",
            (account, key),
        ).fetchall():  # a budget I turned off has nothing to alert about
            if category not in budgeted:
                _close(conn, episode_id, data_date)
    return [_look(conn, account, key, pace, data_date, first_day) for pace in month.paces]


def _close(conn: sqlite3.Connection, episode_id: int, data_date: date) -> None:
    day = data_date.isoformat()
    conn.execute(
        "UPDATE budget_alert_episodes SET closed_on = ?, last_seen_on = ? WHERE episode_id = ?",
        (day, day, episode_id),
    )


def _look(
    conn: sqlite3.Connection, account: str, key: str, pace: Pace, data_date: date,
    first_day: date,
) -> LookResult:  # fmt: skip
    row = conn.execute(
        "SELECT episode_id, closed_on, dismissed_on FROM budget_alert_episodes"
        " WHERE account = ? AND category = ? AND month = ?",
        (account, pace.category, key),
    ).fetchone()
    with conn:
        if not should_alert(pace, first_day):
            if row is not None and row[1] is None:
                _close(conn, int(row[0]), data_date)
            return LookResult(pace.category, None, False, False, "")
        if row is None:
            cursor = conn.execute(
                "INSERT INTO budget_alert_episodes (account, category, month, opened_on,"
                " last_seen_on) VALUES (?, ?, ?, ?, ?)",
                (account, pace.category, key, data_date.isoformat(), data_date.isoformat()),
            )
            episode_id = int(cursor.lastrowid or 0)
            _event(conn, episode_id, data_date, pace, notified=True, reason="new alert")
            return LookResult(pace.category, episode_id, True, True, "new alert")
        episode_id, dismissed = int(row[0]), row[2] is not None
        conn.execute(
            "UPDATE budget_alert_episodes SET closed_on = NULL, last_seen_on = ?"
            " WHERE episode_id = ?",
            (data_date.isoformat(), episode_id),
        )  # the chance came back this month: the same episode, still dismissed if it was
        latest = _latest(conn, episode_id)
        told = _latest(conn, episode_id, notified_only=True)
        band = band_of(pace.chance_over)
        over = pace.likely_over_cents
        notified, reason, record = False, "", False
        if dismissed:
            if told is not None and band > band_of(told[0]):
                notified, record = True, True
                was, now = chance_text(told[0]), chance_text(pace.chance_over)
                reason = f"the chance entered a higher band ({was} to {now})"
                conn.execute("UPDATE budget_alert_episodes SET dismissed_on = NULL"
                             " WHERE episode_id = ?", (episode_id,))  # fmt: skip
        elif latest is not None and band != band_of(latest[0]):
            notified, record = True, True
            was, now = chance_text(latest[0]), chance_text(pace.chance_over)
            reason = f"the chance changed band ({was} to {now})"
        if not record and latest is not None and abs(over - latest[1]) >= MATERIAL_CENTS:
            record = True  # evidence refreshed; never notified, never brings a dismissed one back
            reason = f"the likely overrun moved by {_dollars(abs(over - latest[1]))}"
        if record:
            _event(conn, episode_id, data_date, pace, notified=notified, reason=reason)
    return LookResult(pace.category, episode_id, record, notified, reason)


def _event(
    conn: sqlite3.Connection, episode_id: int, data_date: date, pace: Pace, *, notified: bool,
    reason: str,
) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO budget_alert_events (episode_id, data_date, probability, limit_cents,"
        " spent_cents, likely_end_cents, notified, reason, evidence)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (episode_id, data_date.isoformat(), pace.chance_over, pace.limit_cents,
         pace.spent_cents, pace.likely_end_cents, int(notified), reason,
         json.dumps(evidence_of(pace))),
    )  # fmt: skip


def _latest(
    conn: sqlite3.Connection, episode_id: int, notified_only: bool = False
) -> tuple[float, int] | None:  # fmt: skip
    """(chance, likely overrun) of the latest recorded (or notified) event."""
    extra = " AND notified = 1" if notified_only else ""
    row = conn.execute(
        "SELECT probability, likely_end_cents - limit_cents FROM budget_alert_events"
        f" WHERE episode_id = ?{extra} ORDER BY event_id DESC LIMIT 1",
        (episode_id,),
    ).fetchone()
    return None if row is None else (float(row[0]), max(int(row[1]), 0))


def budget_episodes(conn: sqlite3.Connection, account: str) -> list[BudgetEpisode]:
    """Every budget alert episode of an account, newest first, each as of its latest event."""
    try:
        rows = conn.execute(
            "SELECT episode_id, category, month, opened_on, last_seen_on, closed_on, dismissed_on"
            " FROM budget_alert_episodes WHERE account = ? ORDER BY episode_id DESC",
            (account,),
        ).fetchall()
    except sqlite3.OperationalError:  # a read-only database from before the table existed
        return []
    out = []
    for r in rows:
        e = conn.execute(
            "SELECT probability, limit_cents, spent_cents, likely_end_cents, evidence"
            " FROM budget_alert_events WHERE episode_id = ? ORDER BY event_id DESC LIMIT 1",
            (r[0],),
        ).fetchone()
        out.append(BudgetEpisode(
            int(r[0]), account, r[1], r[2], date.fromisoformat(r[3]), date.fromisoformat(r[4]),
            date.fromisoformat(r[5]) if r[5] else None, date.fromisoformat(r[6]) if r[6] else None,
            float(e[0]), int(e[1]), int(e[2]), int(e[3]), json.loads(e[4]),
        ))  # fmt: skip
    return out


def open_budget_alerts(conn: sqlite3.Connection, account: str) -> list[BudgetEpisode]:
    """The open episodes I have not dismissed."""
    return [e for e in budget_episodes(conn, account) if e.status == "open"]


def dismiss(
    conn: sqlite3.Connection, account: str, episode_id: int, when: date | None = None
) -> None:
    """Dismiss: quiet until the chance enters a higher band, or the episode ends."""
    _set_dismissed(conn, account, episode_id, (when or date.today()).isoformat())


def restore(conn: sqlite3.Connection, account: str, episode_id: int) -> None:
    """Bring a dismissed episode back."""
    _set_dismissed(conn, account, episode_id, None)


def _set_dismissed(
    conn: sqlite3.Connection, account: str, episode_id: int, value: str | None
) -> None:
    with conn:
        cursor = conn.execute(
            "UPDATE budget_alert_episodes SET dismissed_on = ? WHERE episode_id = ?"
            " AND account = ?",
            (value, episode_id, account),
        )
    if cursor.rowcount == 0:
        raise ValueError("no such budget alert")


def alert_message(e: BudgetEpisode) -> str:
    """One plain sentence, in forecast words. Already over: says so, with no chance quoted."""
    if e.spent_cents > e.limit_cents:
        return (
            f"You're already over this budget: {e.category}, spent so far "
            f"{_dollars(e.spent_cents)} against a {_dollars(e.limit_cents)} limit."
        )
    return (
        f"Possible overspend in {e.category}: in {chance_text(e.probability)} of simulated futures "
        f"you end the month above your {_dollars(e.limit_cents)} budget. Spent so far "
        f"{_dollars(e.spent_cents)}; likely end of month about {_dollars(e.likely_end_cents)}."
    )


def with_live_pace(e: BudgetEpisode, pace: Pace | None) -> BudgetEpisode:
    """The episode with this look's figures (reviewer NB5): its recorded event is refreshed only
    on a band change or a $25 move, so the tab shows the live pace beside the card instead."""
    if pace is None or pace.category != e.category:
        return e
    return replace(
        e, probability=pace.chance_over, limit_cents=pace.limit_cents,
        spent_cents=pace.spent_cents, likely_end_cents=pace.likely_end_cents,
    )  # fmt: skip


def home_line(e: BudgetEpisode) -> str:
    """Home's "Needs a look" sentence for a budget alert (plain dollar signs; Home escapes them).
    Already over: the tab's own words, with no chance quoted."""
    if e.spent_cents > e.limit_cents:
        return alert_message(e)
    return (
        f"Possible overspend in {e.category}: {chance_text(e.probability)} of simulated futures "
        f"end the month above your {_dollars(e.limit_cents)} budget (an estimate)."
    )


def _dollars(cents: int) -> str:
    """$1,234, or −$1,234 (a real minus sign in front of the dollar sign, as on the pages)."""
    sign = "\u2212" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.0f}"
