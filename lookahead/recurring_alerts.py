"""Recurring alerts (week 5, item 3): what looks different about the payments I repeat.

Four kinds, always in forecast words, never "missed" or "fraud":
- not_seen: an expected payment not seen yet (the due date plus GRACE_DAYS business days passed);
  a series with a rent rule waits for its window to end plus the same grace;
- amount: the latest payment's amount looks different from what the series expects;
- stopped: a series that was active at the last look may have stopped (never for a series with a
  rent rule, and never one I ended);
- new: a detected series I have decided nothing about appeared since the last look.
The first three are raised only for series I confirmed or added by hand; "new" is the one kind
for the rest.

Episodes and dismissal follow the shortfall alert's shape (lookahead/alerts.py), in their own
tables, because that module imports the forecaster and this one must not. An episode is one alert
about one series (by stable id) and one kind. It closes when the condition goes away. A dismissed
episode speaks again only if it gets materially worse in kind: another due date has passed unpaid,
or the amount moved the worse way by MATERIAL_SHARE and MATERIAL_CENTS from where it was when last
told. "New" and "stopped" never speak again after a dismissal: confirming, rejecting, or a new
payment resolves them. The constants are provisional, picked to be noticeable and not measured.

Rule zero: a look at data date D uses rows posted on or before D and decisions as the series
passed in were built. What the previous look saw is stored as its data end date and a small map
from each series' stable id to whether it was active (the first plan recomputed the earlier series
with `series_as_of` instead, but that took 4.5 s on a 24-month made-up account: DECISIONS).
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from lookahead.recurring import LOOSE_TOLERANCE, TOLERANCE, business_days_apart, expected_dates
from lookahead.recurring_store import Decision, Stored, load_decisions
from lookahead.rent import RentRule, assign_cycles, window

PROVISIONAL = "provisional, not yet validated"
GRACE_DAYS = TOLERANCE  # business days after a due date before a payment is "not seen yet"
MATERIAL_SHARE = 0.10  # an amount differs / gets worse by at least this share ...
MATERIAL_CENTS = 1_000  # ... and by at least this much ($10)
KINDS = ("not_seen", "amount", "stopped", "new")
_DECIDED = ("confirmed", "user-added")


@dataclass(frozen=True)
class Condition:
    """Something true about a series on the data date."""

    series_id: str
    kind: str
    level: int  # not seen: due dates passed unpaid; otherwise 1
    amount_cents: int | None  # amount: the latest payment's size (positive)
    starts: bool  # may open an episode (stopped and new need a change since the last look)
    evidence: dict[str, Any]


@dataclass(frozen=True)
class LookResult:
    episode_id: int
    series_id: str
    kind: str
    notified: bool  # worth telling me about
    reason: str


@dataclass(frozen=True)
class AlertEpisode:
    episode_id: int
    series_id: str
    kind: str
    opened_on: date
    last_seen_on: date
    closed_on: date | None
    dismissed_on: date | None
    level: int  # as of the latest look
    amount_cents: int | None
    evidence: dict[str, Any]  # as of the latest look

    @property
    def status(self) -> str:
        if self.closed_on is not None:
            return "closed"
        return "dismissed" if self.dismissed_on is not None else "open"


# --- What is true on a data date ---


def material(delta_cents: int, reference_cents: int) -> bool:
    """A difference worth noticing: at least MATERIAL_SHARE of the reference and MATERIAL_CENTS."""
    return delta_cents >= MATERIAL_CENTS and delta_cents >= MATERIAL_SHARE * reference_cents


def rent_cycles_overdue(rule: RentRule, paid: list[date], data_date: date) -> list[date]:
    """Start dates of the rent cycles after the last paid one whose window has ended and whose
    grace period is over on `data_date`, with no payment. A window that wraps a month end or
    year end is handled by `window`. A late payment belongs to its own cycle (`assign_cycles`)."""
    if not paid:
        return []
    last = max(cycle for _, cycle in assign_cycles(rule, paid))
    n = last.year * 12 + last.month - 1
    out: list[date] = []
    while True:
        n += 1
        start, end = window(rule, n // 12, n % 12 + 1)
        if start > data_date:
            return out
        if end < data_date and business_days_apart(end, data_date) > GRACE_DAYS:
            out.append(start)


def conditions(
    conn: sqlite3.Connection, account: str, series: list[Stored], data_date: date,
    before: dict[str, bool] | None = None, before_date: date | None = None,
) -> list[Condition]:  # fmt: skip
    """Every alert condition true for these series on `data_date`. `before` says which series
    (by stable id) the last look saw and whether each was active, `before_date` is its data date.
    Stopped and new need a change since then, so with no earlier look they cannot start (the first
    look sets the baseline)."""
    decisions = load_decisions(conn, account)
    out: list[Condition] = []
    for s in series:
        if s.status == "rejected" or _ended_by_me(decisions, s):
            continue
        sid = s.id or s.key
        rent = s.window is not None and bool(s.allowed_cents)
        if s.status in _DECIDED:
            pays = _payments(conn, account, s, data_date)
            found = _not_seen(s, pays, data_date, rent) if s.active or rent else None
            if found is not None:
                out.append(Condition(sid, "not_seen", len(found["due"]), None, True, found))
            if s.active and not rent:
                changed = _amount(s, pays)
                if changed is not None:
                    out.append(Condition(sid, "amount", 1, changed["latest_cents"], True, changed))
            if not s.active and not rent:
                was_active = before is not None and before.get(sid) is True
                last = pays[-1][0].isoformat() if pays else None
                out.append(Condition(sid, "stopped", 1, None, was_active, _base(s, last_paid=last)))
        elif s.status == "detected" and s.active:
            fresh = (
                before is not None and before_date is not None and sid not in before
                and s.last_paid is not None and s.last_paid > before_date
            )  # fmt: skip
            out.append(Condition(sid, "new", 1, None, fresh, _base(s)))
    return out


def _base(s: Stored, **more: Any) -> dict[str, Any]:
    return {
        "direction": s.direction, "cadence": s.cadence, "expected_cents": s.expected_cents,
        "range_cents": list(s.range_cents),
        "last_paid": s.last_paid.isoformat() if s.last_paid else None, **more,
    }  # fmt: skip


def _ended_by_me(decisions: list[Decision], s: Stored) -> bool:
    latest = None
    for d in decisions:
        if d.key in (s.id, s.key) and d.action in ("confirm", "reject", "end"):
            latest = d.action
    return latest == "end"


def _payments(
    conn: sqlite3.Connection, account: str, s: Stored, data_date: date
) -> list[tuple[date, int, str]]:
    """(day, signed cents, fingerprint) of the series' payments posted by `data_date`, oldest
    first."""
    out: list[tuple[date, int, str]] = []
    for i in range(0, len(s.evidence), 500):
        chunk = s.evidence[i : i + 500]
        rows = conn.execute(
            "SELECT posted_date, amount_cents, fingerprint FROM transactions WHERE account = ?"
            " AND posted_date <= ? AND fingerprint IN (" + ",".join("?" * len(chunk)) + ")",
            (account, data_date.isoformat(), *chunk),
        )
        out += [(date.fromisoformat(d), int(c), fp) for d, c, fp in rows]
    return sorted(out, key=lambda p: (p[0], p[2]))


def _not_seen(
    s: Stored, pays: list[tuple[date, int, str]], data_date: date, rent: bool
) -> dict[str, Any] | None:
    if not pays:
        return None
    last = pays[-1][0]
    if rent:
        rule = RentRule(s.allowed_cents, *(s.window or (1, 1)))
        starts = rent_cycles_overdue(rule, [p[0] for p in pays], data_date)
        due = [window(rule, d.year, d.month)[1] for d in starts]  # the day each window ended
    else:
        tolerance = LOOSE_TOLERANCE if s.loose else TOLERANCE
        if s.edited:  # a date I set myself
            candidates = [s.next_expected]
        else:
            candidates = expected_dates(s.cadence, s.anchor, last, data_date, s.direction)
        due = [
            d for d in candidates
            if d > last and d < data_date and business_days_apart(last, d) > tolerance
            and business_days_apart(d, data_date) > tolerance
        ]  # fmt: skip
    if not due:
        return None
    return _base(s, due=[d.isoformat() for d in due], last_paid=last.isoformat(), rent=rent)


def _amount(s: Stored, pays: list[tuple[date, int, str]]) -> dict[str, Any] | None:
    """The latest payment's size against what the series expects. A rent rule is left out: it
    already says which amounts are normal, and a payment of another size is not counted at all."""
    if not pays:
        return None
    day, cents, _ = pays[-1]
    now, expected = abs(cents), abs(s.expected_cents)
    low, high = sorted(abs(c) for c in s.range_cents)
    outside = low - now if now < low else now - high if now > high else 0
    was: int | None = None
    if material(outside, expected):
        was = expected
    elif (
        s.previous_cents is not None
        and s.changed_fingerprint in {p[2] for p in pays[-2:]}
        and material(abs(expected - abs(s.previous_cents)), abs(s.previous_cents))
    ):  # a price change inside the last two payments
        was = abs(s.previous_cents)
    if was is None:
        return None
    return _base(s, latest_cents=now, latest_day=day.isoformat(), was_cents=was)


# --- The stored episodes ---


def record_look(conn: sqlite3.Connection, account: str, series: list[Stored]) -> list[LookResult]:
    """Look at these series (as of the account's last posting date) and keep the episode record
    up to date. Looking again at numbers already recorded adds nothing. Returns what changed.
    The stored baseline (which series the last look saw, and whether each was active) only moves
    forward when the data date does."""
    row = conn.execute(
        "SELECT MAX(posted_date) FROM transactions WHERE account = ?", (account,)
    ).fetchone()
    if row[0] is None:
        return []
    data_date = date.fromisoformat(row[0])
    saved = conn.execute(
        "SELECT last_look, series FROM recurring_alert_state WHERE account = ?", (account,)
    ).fetchone()
    before: dict[str, bool] | None = None
    before_date: date | None = None
    if saved is not None:
        before_date, before = date.fromisoformat(saved[0]), json.loads(saved[1])
    found = conditions(conn, account, series, data_date, before, before_date)
    results = _reconcile(conn, account, found, data_date)
    if saved is None or before_date != data_date:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO recurring_alert_state (account, last_look, series)"
                " VALUES (?, ?, ?)",
                (account, data_date.isoformat(),
                 json.dumps({s.id or s.key: s.active for s in series})),
            )  # fmt: skip
    return results


def _reconcile(
    conn: sqlite3.Connection, account: str, found: list[Condition], data_date: date
) -> list[LookResult]:
    day = data_date.isoformat()
    open_rows = conn.execute(
        "SELECT episode_id, series_id, kind, dismissed_on FROM recurring_alert_episodes"
        " WHERE account = ? AND closed_on IS NULL",
        (account,),
    ).fetchall()
    held = {(r[1], r[2]): (int(r[0]), r[3] is not None) for r in open_rows}
    wanted = {(c.series_id, c.kind) for c in found}
    results: list[LookResult] = []
    with conn:
        for k, (episode_id, _) in held.items():
            if k not in wanted:  # the condition went away
                conn.execute(
                    "UPDATE recurring_alert_episodes SET closed_on = ?, last_seen_on = ?"
                    " WHERE episode_id = ?",
                    (day, day, episode_id),
                )
        for c in found:
            hit = held.get((c.series_id, c.kind))
            if hit is None:
                if not c.starts:
                    continue
                cursor = conn.execute(
                    "INSERT INTO recurring_alert_episodes (account, series_id, kind, opened_on,"
                    " last_seen_on) VALUES (?, ?, ?, ?, ?)",
                    (account, c.series_id, c.kind, day, day),
                )
                episode_id = int(cursor.lastrowid or 0)
                _add_event(conn, episode_id, account, day, c, True, "new alert")
                results.append(LookResult(episode_id, c.series_id, c.kind, True, "new alert"))
                continue
            episode_id, dismissed = hit
            conn.execute(
                "UPDATE recurring_alert_episodes SET last_seen_on = ? WHERE episode_id = ?",
                (day, episode_id),
            )
            latest = _event(conn, episode_id, notified_only=False)
            if latest is not None and latest == (c.level, c.amount_cents):
                continue
            told = _event(conn, episode_id, notified_only=True)
            direction = str(c.evidence.get("direction", "out"))
            reason = _worse(c, told, direction) if dismissed else _changed(c, told, direction)
            if dismissed and reason is not None:  # it speaks again, so it is shown again
                conn.execute("UPDATE recurring_alert_episodes SET dismissed_on = NULL"
                             " WHERE episode_id = ?", (episode_id,))  # fmt: skip
            _add_event(conn, episode_id, account, day, c, reason is not None, reason or "")
            results.append(LookResult(episode_id, c.series_id, c.kind, reason is not None,
                                      reason or ""))  # fmt: skip
    return results


def _changed(c: Condition, told: tuple[int, int | None] | None, direction: str) -> str | None:
    """Why a look is worth telling me about after the last alert (the episode is not dismissed)."""
    if told is None:
        return "new alert"
    if c.kind == "not_seen" and c.level > told[0]:
        return f"another expected payment is not seen yet ({c.level} so far)"
    if c.kind == "amount" and told[1] is not None and c.amount_cents is not None:
        if material(abs(c.amount_cents - told[1]), told[1]):
            return "the latest amount moved again"
    return None


def _worse(c: Condition, told: tuple[int, int | None] | None, direction: str) -> str | None:
    """Why a dismissed alert speaks again: it got materially worse in kind. Only two things count:
    another expected payment not seen yet, or the amount moved the worse way (up for money out,
    down for money in) by the material share and amount. Better news and smaller moves stay
    quiet; stopped and new never speak again."""
    if told is None:
        return "new alert"
    if c.kind == "not_seen" and c.level > told[0]:
        return f"another expected payment is not seen yet ({c.level} so far)"
    if c.kind == "amount" and told[1] is not None and c.amount_cents is not None:
        worse = c.amount_cents - told[1] if direction == "out" else told[1] - c.amount_cents
        if worse > 0 and material(worse, told[1]):
            return "the amount moved the worse way again"
    return None


def _add_event(
    conn: sqlite3.Connection, episode_id: int, account: str, day: str, c: Condition,
    notified: bool, reason: str,
) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO recurring_alert_events (episode_id, account, data_date, level, amount_cents,"
        " notified, reason, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (episode_id, account, day, c.level, c.amount_cents, int(notified), reason,
         json.dumps(c.evidence)),
    )  # fmt: skip


def _event(
    conn: sqlite3.Connection, episode_id: int, notified_only: bool
) -> tuple[int, int | None] | None:
    row = conn.execute(
        "SELECT level, amount_cents FROM recurring_alert_events WHERE episode_id = ?"
        + (" AND notified = 1" if notified_only else "")
        + " ORDER BY event_id DESC LIMIT 1",
        (episode_id,),
    ).fetchone()
    return None if row is None else (int(row[0]), None if row[1] is None else int(row[1]))


def episodes(conn: sqlite3.Connection, account: str) -> list[AlertEpisode]:
    """Every episode of an account, newest first, each as of its latest look."""
    out = []
    rows = conn.execute(
        "SELECT episode_id, series_id, kind, opened_on, last_seen_on, closed_on, dismissed_on"
        " FROM recurring_alert_episodes WHERE account = ? ORDER BY episode_id DESC",
        (account,),
    ).fetchall()
    for r in rows:
        e = conn.execute(
            "SELECT level, amount_cents, evidence FROM recurring_alert_events"
            " WHERE episode_id = ? ORDER BY event_id DESC LIMIT 1",
            (r[0],),
        ).fetchone()
        out.append(AlertEpisode(
            int(r[0]), r[1], r[2], date.fromisoformat(r[3]), date.fromisoformat(r[4]),
            date.fromisoformat(r[5]) if r[5] else None, date.fromisoformat(r[6]) if r[6] else None,
            int(e[0]), None if e[1] is None else int(e[1]), json.loads(e[2]),
        ))  # fmt: skip
    return out


def open_alerts(conn: sqlite3.Connection, account: str) -> list[AlertEpisode]:
    """The open episodes I have not dismissed."""
    return [e for e in episodes(conn, account) if e.status == "open"]


def dismiss(
    conn: sqlite3.Connection, account: str, episode_id: int, when: date | None = None
) -> None:
    """Dismiss an episode: it stays quiet until it gets materially worse, or ends."""
    _set_dismissed(conn, account, episode_id, (when or date.today()).isoformat())


def restore(conn: sqlite3.Connection, account: str, episode_id: int) -> None:
    """Bring a dismissed episode back."""
    _set_dismissed(conn, account, episode_id, None)


def _set_dismissed(
    conn: sqlite3.Connection, account: str, episode_id: int, value: str | None
) -> None:
    with conn:
        cursor = conn.execute(
            "UPDATE recurring_alert_episodes SET dismissed_on = ?"
            " WHERE episode_id = ? AND account = ?",
            (value, episode_id, account),
        )
    if cursor.rowcount == 0:
        raise ValueError("no such alert episode")


# --- Words (no Streamlit here). The label is given, so a hidden name stays hidden. ---


def message(ep: AlertEpisode, label: str) -> str:
    """One plain sentence about an episode, in forecast words."""
    ev = ep.evidence
    if ep.kind == "not_seen":
        due = _day(ev["due"][0]) if ev.get("due") else "its due date"
        more = f" ({ep.level} expected payments so far)" if ep.level > 1 else ""
        return f"Expected payment not seen yet: {label}, expected around {due}{more}."
    if ep.kind == "amount":
        usual = _money(int(ev.get("was_cents", 0)))
        return (
            f"Amount looks different: {label}, latest payment {_money(ep.amount_cents or 0)} "
            f"against about {usual} before."
        )
    if ep.kind == "stopped":
        since = f" since {_day(ev['last_paid'])}" if ev.get("last_paid") else ""
        return f"{label} may have stopped: no payment seen{since}."
    size = _money(abs(int(ev.get("expected_cents", 0))))
    return (
        f"New recurring payment: {label}, about {size} {ev.get('cadence', '')}. "
        "Confirm it, or say it is not recurring."
    )


def evidence_lines(ep: AlertEpisode) -> list[str]:
    """What stands behind an alert, as plain lines: dates and amounts from the episode's latest
    look. No names (the label is the page's business)."""
    ev = ep.evidence
    last = f"Last payment seen: {_day(ev['last_paid'])}." if ev.get("last_paid") else ""
    lines = [f"Data ends {ep.last_seen_on:%b} {ep.last_seen_on.day}."]
    if ep.kind == "not_seen":
        due = ", ".join(_day(d) for d in ev.get("due", []))
        lines += [
            last,
            f"Expected by: {due}." if ev.get("rent") is False else f"Windows ended: {due}.",
            f"A payment counts as late after {GRACE_DAYS} business days past its date.",
        ]
    elif ep.kind == "amount":
        low, high = sorted(abs(int(c)) for c in ev.get("range_cents", [0, 0]))
        lines += [
            f"Latest payment: {_money(ep.amount_cents or 0)} on {_day(ev['latest_day'])}.",
            f"The series expects about {_money(abs(int(ev.get('expected_cents', 0))))}"
            f" (usually {_money(low)} to {_money(high)}); before it was {_money(ev['was_cents'])}.",
            f"Shown when it differs by at least {MATERIAL_SHARE:.0%} and {_money(MATERIAL_CENTS)}.",
        ]
    elif ep.kind == "stopped":
        lines += [last, "Two expected payments in a row have not been seen."]
    else:
        lines += [last, "Found by the detector; you have not decided about it yet."]
    return [line for line in lines if line]


def _money(cents: int) -> str:
    return f"${cents // 100:,}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


__all__ = [
    "GRACE_DAYS", "KINDS", "MATERIAL_CENTS", "MATERIAL_SHARE", "PROVISIONAL", "AlertEpisode",
    "Condition", "LookResult", "conditions", "dismiss", "episodes", "evidence_lines", "material",
    "message",
    "open_alerts", "record_look", "rent_cycles_overdue", "restore",
]  # fmt: skip
