"""Recurring series as the app keeps them (step 3a-iii): detection plus your decisions.

Detection (lookahead.recurring) is recomputed from the transactions; what you decide is a
dated log, replayed on top, so a later Time Machine can use only the decisions made by a
date:
- confirm / reject ("not recurring"): the latest one wins. A confirmed series keeps a
  snapshot of its pattern, so it stays even if detection later loses it;
- end: no longer expected. It stays ended; a payment posting later is flagged, not used
  to quietly switch it back on;
- edit: your next date and/or amount, until a payment posts after that day (then the
  detected values take over again);
- add: a series you enter by hand (status "user-added"), e.g. irregular income.
Status: detected / confirmed / user-added / rejected. Evidence is stored as transaction
fingerprints, which survive re-imports. The result is saved in `recurring_series`, so the
pages and the forecast only read it.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta

from lookahead.flows import Txn
from lookahead.recurring import CADENCES, Series, detect, expected_dates
from lookahead.resolve import UserDecisions, load_user_decisions

ACTIONS = ("confirm", "reject", "end", "edit", "add")
STATUSES = ("detected", "confirmed", "user-added", "rejected")


@dataclass(frozen=True)
class Stored:
    key: str
    name: str | None  # the merchant, or the name you gave; None for transfers
    kind: str  # merchant / e-transfer / own transfer / user
    direction: str  # in / out
    cadence: str
    anchor: date
    anchor_day: int | None
    amount_type: str  # fixed / variable
    expected_cents: int
    range_cents: tuple[int, int]
    next_expected: date
    status: str  # one of STATUSES
    active: bool
    evidence: tuple[str, ...] = ()  # fingerprints of the payments found, oldest first
    previous_cents: int | None = None  # amount before a price change
    changed_fingerprint: str | None = None  # first payment at the new amount
    last_paid: date | None = None
    edited: bool = False  # your next date or amount is in use
    detected_now: bool = True  # False: not found by detection (added by hand, or lost)
    paid_after_end: bool = False  # you ended it, and a payment has posted since


@dataclass(frozen=True)
class Decision:
    key: str
    action: str
    details: dict[str, object]
    decided_at: date
    decision_id: int = 0


# --- The dated log ---


def decide(
    conn: sqlite3.Connection, account: str, key: str, action: str,
    details: dict[str, object] | None = None, when: date | None = None,
) -> int:  # fmt: skip
    """Append one decision to the log. Returns its id."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    with conn:
        cursor = conn.execute(
            "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account, key, action, json.dumps(details or {}), (when or date.today()).isoformat()),
        )
    return int(cursor.lastrowid or 0)


def confirm(
    conn: sqlite3.Connection, account: str, series: Stored, when: date | None = None
) -> None:
    """Confirm a series, keeping a snapshot of its pattern (no evidence, no last payment)."""
    snapshot = {
        k: v
        for k, v in _to_json(series).items()
        if k
        not in (
            "evidence",
            "status",
            "active",
            "edited",
            "last_paid",
            "changed_fingerprint",
            "detected_now",
            "paid_after_end",
        )  # fmt: skip
    }
    decide(conn, account, series.key, "confirm", snapshot, when)


def reject(conn: sqlite3.Connection, account: str, key: str, when: date | None = None) -> None:
    decide(conn, account, key, "reject", None, when)


def end(conn: sqlite3.Connection, account: str, key: str, when: date | None = None) -> None:
    decide(conn, account, key, "end", None, when)


def edit(
    conn: sqlite3.Connection, account: str, key: str, next_date: date | None = None,
    amount_cents: int | None = None, when: date | None = None,
) -> None:  # fmt: skip
    if next_date is None and amount_cents is None:
        raise ValueError("nothing to edit: give a next date or an amount")
    details: dict[str, object] = {}
    if next_date is not None:
        details["next_date"] = next_date.isoformat()
    if amount_cents is not None:
        details["amount_cents"] = int(amount_cents)
    decide(conn, account, key, "edit", details, when)


def add_series(
    conn: sqlite3.Connection, account: str, name: str, direction: str, cadence: str,
    anchor: date, amount_cents: int, when: date | None = None,
) -> str:  # fmt: skip
    """A series you enter by hand. `amount_cents` is its size; the sign follows `direction`."""
    if direction not in ("in", "out"):
        raise ValueError("direction must be 'in' or 'out'")
    if cadence not in CADENCES:
        raise ValueError(f"unknown cadence {cadence!r}")
    if not name.strip() or amount_cents == 0:
        raise ValueError("a name and a non-zero amount are needed")
    signed = abs(int(amount_cents)) * (1 if direction == "in" else -1)
    details = {"name": name.strip(), "direction": direction, "cadence": cadence,
               "anchor": anchor.isoformat(), "amount_cents": signed}  # fmt: skip
    decision_id = decide(conn, account, "user|pending", "add", details, when)
    key = f"user|{decision_id}"
    with conn:
        conn.execute(
            "UPDATE recurring_decisions SET series_key = ? WHERE decision_id = ?",
            (key, decision_id),
        )
    return key


def load_decisions(
    conn: sqlite3.Connection, account: str, as_of: date | None = None
) -> list[Decision]:
    """The log in the order made; `as_of` keeps only decisions made by that date."""
    rows = conn.execute(
        "SELECT series_key, action, details, decided_at, decision_id FROM recurring_decisions"
        " WHERE account = ? AND decided_at <= ? ORDER BY decided_at, decision_id",
        (account, (as_of or date.max).isoformat()),
    )
    return [Decision(k, a, json.loads(d), date.fromisoformat(day), i) for k, a, d, day, i in rows]


# --- Detection plus decisions ---


def _next_from(cadence: str, anchor: date, direction: str, on_or_after: date) -> date:
    card = False
    dates = expected_dates(cadence, anchor, on_or_after - timedelta(days=10),
                           on_or_after + timedelta(days=400), direction, card)  # fmt: skip
    return next(d for d in dates if d >= on_or_after)


def _from_detected(s: Series, fingerprint: dict[int, str], posted: dict[int, date]) -> Stored:
    return Stored(
        key=s.key, name=s.merchant, kind=s.kind, direction=s.direction, cadence=s.cadence,
        anchor=s.anchor, anchor_day=s.anchor_day, amount_type=s.amount_type,
        expected_cents=s.expected_cents, range_cents=s.range_cents,
        next_expected=s.next_expected, status="detected", active=s.active,
        evidence=tuple(fingerprint[r] for r in s.rows), previous_cents=s.previous_cents,
        changed_fingerprint=None if s.changed_row is None else fingerprint[s.changed_row],
        last_paid=posted[s.rows[-1]],
    )  # fmt: skip


def current_series(
    txns: Sequence[Txn],
    fingerprint: dict[int, str],
    decisions: Sequence[Decision],
    as_of: date,
    user: UserDecisions | None = None,
    key: bytes | None = None,
) -> list[Stored]:
    """Every series on `as_of`: detected from rows posted by then, with the decisions given
    (already cut at a date by the caller) replayed on top. Sorted by key."""
    posted = {t.row_id: t.posted_date for t in txns}
    series = {
        s.key: _from_detected(s, fingerprint, posted)
        for s in detect(txns, user, as_of=as_of, key=key)
    }
    for d in decisions:
        if d.action == "add":
            cadence, direction = str(d.details["cadence"]), str(d.details["direction"])
            anchor = date.fromisoformat(str(d.details["anchor"]))
            amount = int(d.details["amount_cents"])  # type: ignore[call-overload]
            series[d.key] = Stored(
                key=d.key, name=str(d.details["name"]), kind="user", direction=direction,
                cadence=cadence, anchor=anchor,
                anchor_day=None if cadence in ("weekly", "biweekly") else anchor.day,
                amount_type="fixed", expected_cents=amount, range_cents=(amount, amount),
                next_expected=_next_from(cadence, anchor, direction, max(as_of, anchor)),
                status="user-added", active=True, detected_now=False,
            )  # fmt: skip
            continue
        if d.key not in series:
            if d.action != "confirm" or "cadence" not in d.details:
                continue  # a decision about a series that no longer exists
            series[d.key] = _from_snapshot(d, as_of)  # confirmed once: it stays
            continue
        s = series[d.key]
        paid_since = s.last_paid is not None and s.last_paid > d.decided_at
        if d.action == "confirm" and s.status != "user-added":
            series[d.key] = replace(s, status="confirmed")
        elif d.action == "reject":
            series[d.key] = replace(s, status="rejected")
        elif d.action == "end":  # stays ended; a later payment is flagged, for an alert
            series[d.key] = replace(s, active=False, paid_after_end=paid_since)
        elif d.action == "edit" and not paid_since:
            changes: dict[str, object] = {"edited": True}
            if "next_date" in d.details:
                changes["next_expected"] = date.fromisoformat(str(d.details["next_date"]))
            if "amount_cents" in d.details:
                amount = int(d.details["amount_cents"])  # type: ignore[call-overload]
                changes |= {"expected_cents": amount, "range_cents": (amount, amount)}
            series[d.key] = replace(s, **changes)  # type: ignore[arg-type]
    return sorted(series.values(), key=lambda s: s.key)


def _from_snapshot(d: Decision, as_of: date) -> Stored:
    saved = d.details
    cadence, direction = str(saved["cadence"]), str(saved["direction"])
    anchor = date.fromisoformat(str(saved["anchor"]))
    low, high = saved["range_cents"]  # type: ignore[misc]
    return Stored(
        key=d.key, name=saved.get("name"), kind=str(saved["kind"]),  # type: ignore[arg-type]
        direction=direction, cadence=cadence, anchor=anchor,
        anchor_day=saved.get("anchor_day"),  # type: ignore[arg-type]
        amount_type=str(saved["amount_type"]),
        expected_cents=int(saved["expected_cents"]),  # type: ignore[call-overload]
        range_cents=(int(low), int(high)),
        next_expected=_next_from(cadence, anchor, direction, as_of),
        status="confirmed", active=True, detected_now=False,
        previous_cents=saved.get("previous_cents"),  # type: ignore[arg-type]
    )  # fmt: skip


# --- Storage ---


def _to_json(s: Stored) -> dict[str, object]:
    data = asdict(s)
    for name in ("anchor", "next_expected", "last_paid"):
        data[name] = None if data[name] is None else data[name].isoformat()
    data["range_cents"], data["evidence"] = list(s.range_cents), list(s.evidence)
    return data


def series_as_of(
    conn: sqlite3.Connection, account: str, as_of: date | None = None, key: bytes | None = None
) -> tuple[list[Stored], date | None]:
    """The series as they stood on `as_of`, from the source: rows posted by then, merchant
    decisions and recurring decisions made by then. This is what the forecast and the Time
    Machine read (rule zero); the `recurring_series` table is only a cache for the pages.
    `as_of=None` means the account's last posting date. Returns (series, the date used)."""
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, (as_of or date.max).isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    if not txns:
        return [], as_of
    day = as_of or max(t.posted_date for t in txns)
    series = current_series(
        txns, {r[0]: r[5] for r in raw}, load_decisions(conn, account, as_of), day,
        load_user_decisions(conn, as_of), key,
    )  # fmt: skip
    return series, day


def refresh(conn: sqlite3.Connection, account: str, key: bytes | None = None) -> list[Stored]:
    """Detect again as of the account's last posting date, replay every decision, and
    replace the stored series (the pages' cache), in one database transaction."""
    series, as_of = series_as_of(conn, account, None, key)
    if as_of is None:
        return []
    with conn:
        conn.execute("DELETE FROM recurring_series WHERE account = ?", (account,))
        conn.executemany(
            "INSERT INTO recurring_series (account, series_key, data, as_of) VALUES (?, ?, ?, ?)",
            [(account, s.key, json.dumps(_to_json(s)), as_of.isoformat()) for s in series],
        )
    return series


def load_series(conn: sqlite3.Connection, account: str) -> list[Stored]:
    """The stored series (empty before the first refresh, or in an older database)."""
    try:
        rows = conn.execute(
            "SELECT data FROM recurring_series WHERE account = ? ORDER BY series_key", (account,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for (text,) in rows:
        d = json.loads(text)
        for name in ("anchor", "next_expected", "last_paid"):
            d[name] = None if d[name] is None else date.fromisoformat(d[name])
        d["range_cents"], d["evidence"] = tuple(d["range_cents"]), tuple(d["evidence"])
        out.append(Stored(**d))
    return out
