"""Shortfall alerts (3c): a provisional rule, episodes, evidence and suppression.

The rule is provisional, not yet validated: alert when the chance of dipping below the safety
buffer is above 20 percent, or the median lowest balance is below the buffer. The week 4 backtest
replaces it with a threshold chosen on validation periods only.

An episode is a run of alerts about the same predicted low: while a new look at the forecast
still has its low within SAME_LOW_DAYS of the open episode's low, it belongs to that episode. A
look is only worth telling me about (`notified`) when something material changed since the last
alert: the low moved by MATERIAL_LOW_CENTS, the chance crossed a band, or the date shifted by
DATE_SHIFT_DAYS. Looks that change nothing material are recorded and stay quiet. I can dismiss an
episode; it then stays quiet for small moves and for getting better, and speaks again only if it
gets materially worse in kind: the chance enters a higher band, or the middle future's low drops
below $0 (and it ends when the condition goes away, or I can bring it back).

Rule zero: every number comes from the forecast handed in, which was built from rows posted on or
before its own data date. Nothing here reads anything else or looks at today's date, except to
stamp a dismissal. Runs must be recorded in order of their data dates (a replay does so).
"""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from lookahead.forecast.path import Forecast
from lookahead.forecast.simulate import Simulation

PROVISIONAL = "provisional, not yet validated"
PROBABILITY_THRESHOLD = 0.20  # alert when the chance of dipping below the buffer is above this
BANDS = (0.20, 0.50, 0.80)  # a probability crossing one of these is a material change
MATERIAL_LOW_CENTS = 10_000  # the low moving by this much (a $100 step) is a material change
DATE_SHIFT_DAYS = 3  # the low coming this many days earlier or later is a material change
SAME_LOW_DAYS = 10  # lows this close are "the same low": one episode
LARGEST_PAYMENTS = 5  # how many payments before the low the evidence names


@dataclass(frozen=True)
class Snapshot:
    """What an alert says about one look at the forecast."""

    data_date: date  # the last posting date the forecast used
    low_day: date  # the day the middle future bottoms out
    low_cents: int  # the middle future's lowest balance
    probability: float  # share of simulated futures below the buffer on some day
    buffer_cents: int
    median_lowest_cents: int  # the median over futures of each future's lowest balance

    @property
    def band(self) -> int:
        return band_of(self.probability)

    @property
    def days_of_warning(self) -> int:
        return (self.low_day - self.data_date).days


@dataclass(frozen=True)
class Episode:
    episode_id: int
    account: str
    opened_on: date
    last_seen_on: date
    closed_on: date | None
    dismissed_on: date | None
    low_day: date  # as of the latest look
    low_cents: int
    probability: float
    buffer_cents: int
    evidence: dict[str, Any]  # as of the latest look

    @property
    def status(self) -> str:
        if self.closed_on is not None:
            return "closed"
        return "dismissed" if self.dismissed_on is not None else "open"


@dataclass(frozen=True)
class RunResult:
    episode_id: int | None  # None: no alert condition, nothing is open
    notified: bool  # worth telling me about
    reason: str  # why: "new alert", "the low is lower", ... or "" when quiet


def band_of(probability: float) -> int:
    """0 up to and including 20 percent, 1 up to 50, 2 up to 80, 3 above."""
    return sum(probability > edge for edge in BANDS)


def should_alert(sim: Simulation, buffer_cents: int) -> bool:
    """The provisional rule."""
    return sim.shortfall_probability > PROBABILITY_THRESHOLD or (
        sim.median_lowest_cents < buffer_cents
    )


def snapshot_of(forecast: Forecast) -> Snapshot | None:
    """The alert's snapshot for a forecast, or None if the rule does not alert (or there are no
    simulated futures to apply it to)."""
    sim = forecast.simulation
    if sim is None or not should_alert(sim, forecast.buffer_cents):
        return None
    lowest = min(range(len(sim.p50)), key=lambda k: (sim.p50[k], k))
    return Snapshot(
        data_date=forecast.start.data_last_updated,
        low_day=sim.days[lowest],
        low_cents=sim.p50[lowest],
        probability=sim.shortfall_probability,
        buffer_cents=forecast.buffer_cents,
        median_lowest_cents=sim.median_lowest_cents,
    )


def material_change(old: Snapshot, new: Snapshot) -> str | None:
    """Why `new` is worth a new alert after `old`, or None."""
    move = new.low_cents - old.low_cents
    if abs(move) >= MATERIAL_LOW_CENTS:
        return f"the low is {_dollars(abs(move))} {'higher' if move > 0 else 'lower'}"
    if new.band != old.band:
        return f"the chance changed band ({old.probability:.0%} to {new.probability:.0%})"
    shift = (new.low_day - old.low_day).days
    if abs(shift) >= DATE_SHIFT_DAYS:
        return f"the low is {abs(shift)} days {'later' if shift > 0 else 'earlier'}"
    return None


def worse_after_dismissal(old: Snapshot, new: Snapshot) -> str | None:
    """Why a dismissed episode should speak again: it got materially worse in kind. Only two
    things count: the chance entered a higher band, or the middle future's low dropped below
    $0 (it was not below before). Getting better, and any smaller move, stay quiet."""
    if new.band > old.band:
        return f"the chance entered a higher band ({old.probability:.0%} to {new.probability:.0%})"
    if new.low_cents < 0 <= old.low_cents:
        return "the middle future's low dropped below $0"
    return None


def alert_message(snap: Snapshot) -> str:
    """One plain sentence, in forecast words."""
    days = snap.days_of_warning
    return (
        f"Possible shortfall: {snap.probability:.0%} of simulated futures dip below your "
        f"{_dollars(snap.buffer_cents)} buffer, and the middle future's lowest balance is about "
        f"{_dollars(snap.low_cents)} around {_day(snap.low_day)}, {days} "
        f"{'day' if days == 1 else 'days'} after the data ends."
    )


def build_evidence(forecast: Forecast) -> dict[str, Any]:
    """What stands behind the alert: the low, the largest payments before it, how many of the
    assumptions are detected, confirmed or entered, the big events set aside, the data date."""
    sim = forecast.simulation
    assert sim is not None, "evidence needs simulated futures"
    snap = snapshot_of(forecast)
    lowest = min(range(len(sim.p50)), key=lambda k: (sim.p50[k], k))
    low_day = sim.days[lowest]
    before = sorted(
        (i for i in forecast.items if i.cents < 0 and i.day <= low_day),
        key=lambda i: (i.cents, i.day),
    )[:LARGEST_PAYMENTS]
    return {
        "account": forecast.start.account,
        "data_date": forecast.start.data_last_updated.isoformat(),
        "forecast_first_day": forecast.start.first_day.isoformat(),
        "buffer_cents": forecast.buffer_cents,
        "low_day": low_day.isoformat(),
        "low_cents": sim.p50[lowest],
        "median_lowest_cents": sim.median_lowest_cents,
        "probability": sim.shortfall_probability,
        "probability_without_deposits": sim.shortfall_probability_without,
        "futures": sim.futures,
        "method": forecast.method,
        "spending_per_day_cents": round(
            sum(d.spending_cents for d in forecast.days) / len(forecast.days)
        )
        if forecast.days
        else 0,  # the average, as the page's callout says it
        "largest_payments": [_item(i) for i in before],
        "assumptions": dict(Counter(i.basis for i in forecast.items)),
        "assumption_items": [_item(i) for i in forecast.items][:60],
        "big_events": [
            {"day": e.day.isoformat(), "kind": e.kind, "cents": e.cents, "group": e.group}
            for e in forecast.big_events
        ],
        "rule": f"{PROVISIONAL}: chance above {PROBABILITY_THRESHOLD:.0%}, or median lowest "
        "balance below the buffer",
        "triggered_by_median": snap is not None and snap.median_lowest_cents < snap.buffer_cents,
    }


def _item(i: Any) -> dict[str, Any]:
    return {"name": i.name or "", "day": i.day.isoformat(), "cents": i.cents, "basis": i.basis,
            "series_key": i.series_key}  # fmt: skip


# --- The stored episodes ---


def record_run(conn: sqlite3.Connection, account: str, forecast: Forecast) -> RunResult:
    """Apply the rule to this forecast and keep the episode record up to date. Looking again at
    numbers already recorded adds nothing."""
    snap = snapshot_of(forecast)
    open_row = conn.execute(
        "SELECT episode_id, dismissed_on FROM alert_episodes WHERE account = ?"
        " AND closed_on IS NULL ORDER BY episode_id DESC LIMIT 1",
        (account,),
    ).fetchone()
    if snap is None:
        if open_row is not None:
            with conn:
                day = forecast.start.data_last_updated.isoformat()
                conn.execute(
                    "UPDATE alert_episodes SET closed_on = ?, last_seen_on = ?"
                    " WHERE episode_id = ?",
                    (day, day, open_row[0]),
                )
        return RunResult(None, False, "")

    same = False
    if open_row is not None:
        latest = _latest_event(conn, open_row[0])
        same = latest is not None and abs((snap.low_day - latest[0]).days) <= SAME_LOW_DAYS
    with conn:
        if open_row is not None and not same:  # a different low: the old episode is over
            conn.execute(
                "UPDATE alert_episodes SET closed_on = ? WHERE episode_id = ?",
                (snap.data_date.isoformat(), open_row[0]),
            )
        if same:
            episode_id, dismissed = int(open_row[0]), open_row[1] is not None
            if _same_numbers(conn, episode_id, snap):
                return RunResult(episode_id, False, "")
            told = _last_notified(conn, episode_id)
            if told is None:
                reason = "new alert"
            elif dismissed:  # quiet unless it got materially worse in kind
                reason = worse_after_dismissal(told, snap)
            else:
                reason = material_change(told, snap)
            notified = reason is not None
            if notified and dismissed:  # it speaks again, so it is shown again
                conn.execute("UPDATE alert_episodes SET dismissed_on = NULL WHERE episode_id = ?",
                             (episode_id,))  # fmt: skip
            conn.execute("UPDATE alert_episodes SET last_seen_on = ? WHERE episode_id = ?",
                         (snap.data_date.isoformat(), episode_id))  # fmt: skip
        else:
            cursor = conn.execute(
                "INSERT INTO alert_episodes (account, opened_on, last_seen_on) VALUES (?, ?, ?)",
                (account, snap.data_date.isoformat(), snap.data_date.isoformat()),
            )
            episode_id, reason, notified = int(cursor.lastrowid or 0), "new alert", True
        conn.execute(
            "INSERT INTO alert_events (episode_id, account, data_date, low_day, low_cents,"
            " median_lowest_cents, probability, buffer_cents, notified, reason, evidence)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (episode_id, account, snap.data_date.isoformat(), snap.low_day.isoformat(),
             snap.low_cents, snap.median_lowest_cents, snap.probability, snap.buffer_cents,
             int(notified), reason or "", json.dumps(build_evidence(forecast))),
        )  # fmt: skip
    return RunResult(episode_id, notified, reason or "")


def _latest_event(conn: sqlite3.Connection, episode_id: int) -> tuple[date, int] | None:
    row = conn.execute(
        "SELECT low_day, low_cents FROM alert_events WHERE episode_id = ?"
        " ORDER BY event_id DESC LIMIT 1",
        (episode_id,),
    ).fetchone()
    return None if row is None else (date.fromisoformat(row[0]), int(row[1]))


def _same_numbers(conn: sqlite3.Connection, episode_id: int, snap: Snapshot) -> bool:
    row = conn.execute(
        "SELECT data_date, low_day, low_cents, probability, buffer_cents, median_lowest_cents"
        " FROM alert_events WHERE episode_id = ? ORDER BY event_id DESC LIMIT 1",
        (episode_id,),
    ).fetchone()
    return row is not None and _snapshot(row) == snap


def _last_notified(conn: sqlite3.Connection, episode_id: int) -> Snapshot | None:
    row = conn.execute(
        "SELECT data_date, low_day, low_cents, probability, buffer_cents, median_lowest_cents"
        " FROM alert_events WHERE episode_id = ? AND notified = 1 ORDER BY event_id DESC LIMIT 1",
        (episode_id,),
    ).fetchone()
    return None if row is None else _snapshot(row)


def _snapshot(row: Any) -> Snapshot:
    return Snapshot(date.fromisoformat(row[0]), date.fromisoformat(row[1]), int(row[2]),
                    float(row[3]), int(row[4]), int(row[5]))  # fmt: skip


def episodes(conn: sqlite3.Connection, account: str) -> list[Episode]:
    """Every episode of an account, newest first, each as of its latest look."""
    out = []
    rows = conn.execute(
        "SELECT episode_id, opened_on, last_seen_on, closed_on, dismissed_on"
        " FROM alert_episodes WHERE account = ? ORDER BY episode_id DESC",
        (account,),
    ).fetchall()
    for r in rows:
        e = conn.execute(
            "SELECT low_day, low_cents, probability, buffer_cents, evidence FROM alert_events"
            " WHERE episode_id = ? ORDER BY event_id DESC LIMIT 1",
            (r[0],),
        ).fetchone()
        out.append(Episode(
            int(r[0]), account, date.fromisoformat(r[1]), date.fromisoformat(r[2]),
            date.fromisoformat(r[3]) if r[3] else None, date.fromisoformat(r[4]) if r[4] else None,
            date.fromisoformat(e[0]), int(e[1]), float(e[2]), int(e[3]), json.loads(e[4]),
        ))  # fmt: skip
    return out


def active_alert(conn: sqlite3.Connection, account: str) -> Episode | None:
    """The open episode I have not dismissed, if any."""
    for episode in episodes(conn, account):
        if episode.status == "open":
            return episode
    return None


def dismiss(
    conn: sqlite3.Connection, account: str, episode_id: int, when: date | None = None
) -> None:
    """Dismiss an episode: it stays quiet until it ends."""
    _set_dismissed(conn, account, episode_id, (when or date.today()).isoformat())


def restore(conn: sqlite3.Connection, account: str, episode_id: int) -> None:
    """Bring a dismissed episode back."""
    _set_dismissed(conn, account, episode_id, None)


def _set_dismissed(
    conn: sqlite3.Connection, account: str, episode_id: int, value: str | None
) -> None:
    with conn:
        cursor = conn.execute(
            "UPDATE alert_episodes SET dismissed_on = ? WHERE episode_id = ? AND account = ?",
            (value, episode_id, account),
        )
    if cursor.rowcount == 0:
        raise ValueError("no such alert episode")


# --- Small formatting (no Streamlit here) ---


def _dollars(cents: int) -> str:
    if cents % 100 == 0 and cents >= 0:
        return f"${cents // 100:,}"
    return f"{'−' if cents < 0 else ''}${abs(cents) / 100:,.2f}"


def _day(day: date) -> str:
    return f"{day:%b} {day.day}"


__all__ = [
    "BANDS", "DATE_SHIFT_DAYS", "MATERIAL_LOW_CENTS", "PROVISIONAL", "SAME_LOW_DAYS", "Episode",
    "RunResult", "Snapshot", "active_alert", "alert_message", "band_of", "build_evidence",
    "dismiss", "episodes", "material_change", "record_run", "restore", "should_alert",
    "snapshot_of", "worse_after_dismissal",
]  # fmt: skip
