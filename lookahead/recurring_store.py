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
- add: a series you enter by hand (status "user-added"), e.g. irregular income;
- category: the category of every payment in the series (a subscription billed by a store
  where you also shop: the series is Subscriptions, your other purchases there are not);
- continue: this series carries on an ended one (the same subscription under a new merchant
  name). The two are shown as one series with one history; no merchant is merged;
- rent: for a series paid by hand (rent by e-Transfer), the allowed amounts and the window of
  days of the month it is sent in (lookahead.rent). The latest one wins; None removes it.
  Any outgoing e-Transfer of an allowed amount counts as rent, whatever the day;
- rename: your own name for a series, shown instead of the detected one (label only; the
  matching of payments never uses it). The latest one wins; None gives the detected name back.
A series added by hand finds its own past payments by its name (a whole-word match on the
transaction text, the amount not needed).
Status: detected / confirmed / user-added / rejected. Evidence is stored as transaction
fingerprints, which survive re-imports. The result is saved in `recurring_series`, so the
pages and the forecast only read it.
"""

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta

from lookahead.flows import Flow, Txn, flow_types
from lookahead.recurring import CADENCES, Series, detect, expected_dates
from lookahead.rent import RentRule, assign_cycles, next_window_day, rent_cents
from lookahead.resolve import UserDecisions, load_user_decisions
from lookahead.taxonomy import CATEGORIES

# Bump when detection or the replay of decisions changes what a stored series holds; the
# pages then rebuild the saved copy by themselves instead of showing an older one.
LOGIC_VERSION = 2  # 2: a series with a rent rule is called Rent
ACTIONS = ("confirm", "reject", "end", "edit", "add", "category", "continue", "rent", "rename")
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
    offsets: tuple[int, ...] = ()  # days each payment was from its expected date (- early)
    loose: bool = False  # e-Transfers sent by hand: about monthly, the day varies
    category: str | None = None  # your category for every payment of this series
    continues: tuple[str, ...] = ()  # keys of the ended series this one carries on
    allowed_cents: tuple[int, ...] = ()  # rent rule: the amounts it may be paid in (sizes)
    window: tuple[int, int] | None = None  # rent rule: from-day and to-day of the month
    named_by_me: bool = False  # the name is my own text (hand-added or renamed), safe to show


@dataclass(frozen=True)
class RowOverride:
    """What a series changes for one of its payments, on screen and in the forecast."""

    series_key: str
    flow_type: str | None  # "recurring_outflow", applied only if the row's own flow type is ...
    only_if_flow: str | None  # ... this one ("everyday" card purchase, confirmed "transfer")
    category: str | None


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
            "category",
            "continues",
            "allowed_cents",
            "window",
            "named_by_me",
        )  # fmt: skip
    }
    decide(conn, account, series.key, "confirm", snapshot, when)


def confirm_all(
    conn: sqlite3.Connection, account: str, series: Sequence[Stored], when: date | None = None
) -> int:
    """Confirm every series still marked "detected" (not ended, not rejected), one decision
    each, written exactly as a single confirm would. Returns how many were confirmed."""
    waiting = [s for s in series if s.status == "detected" and s.active]
    for s in waiting:
        confirm(conn, account, s, when)
    return len(waiting)


def set_category(
    conn: sqlite3.Connection, account: str, key: str, category: str | None,
    when: date | None = None,
) -> None:  # fmt: skip
    """Give every payment of a series one category (None: back to each row's own)."""
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    decide(conn, account, key, "category", {"category": category}, when)


def link_continuation(
    conn: sqlite3.Connection, account: str, key: str, previous_key: str,
    when: date | None = None,
) -> None:  # fmt: skip
    """Say that series `key` carries on the ended series `previous_key`."""
    if key == previous_key:
        raise ValueError("a series cannot continue itself")
    decide(conn, account, key, "continue", {"previous": previous_key}, when)


def set_rent_rule(
    conn: sqlite3.Connection, account: str, key: str, rule: RentRule | None,
    when: date | None = None,
) -> None:  # fmt: skip
    """Say which amounts and which days of the month a series is paid in (None: remove it)."""
    details: dict[str, object] = {} if rule is None else {
        "amounts_cents": list(rule.amounts_cents), "from_day": rule.from_day,
        "to_day": rule.to_day,
    }  # fmt: skip
    decide(conn, account, key, "rent", details, when)


def set_name(
    conn: sqlite3.Connection, account: str, key: str, name: str | None,
    when: date | None = None,
) -> None:  # fmt: skip
    """Give a series a name of your own, shown instead of the detected one (None: take it
    back). This changes the label only."""
    clean = " ".join(name.split()) if name else ""
    if len(clean) > 60:
        raise ValueError("a name of at most 60 characters")
    decide(conn, account, key, "rename", {"name": clean or None}, when)


def rent_rule_of(s: Stored) -> RentRule | None:
    """The rent rule a stored series carries, if any."""
    if not s.allowed_cents or s.window is None:
        return None
    return RentRule(s.allowed_cents, s.window[0], s.window[1])


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
        last_paid=posted[s.rows[-1]], offsets=s.offsets, loose=s.loose,
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
    links: list[tuple[str, str]] = []  # (series, the ended one it continues), in log order
    rules: dict[str, RentRule | None] = {}  # the latest rent rule of each series
    names: dict[str, str | None] = {}  # the latest name of my own for each series
    for d in decisions:
        if d.action == "rent":
            rules[d.key] = _rule_from(d.details)
            continue
        if d.action == "rename":
            names[d.key] = d.details.get("name")  # type: ignore[assignment]
            continue
        if d.action == "continue":
            links.append((d.key, str(d.details["previous"])))
            continue
        if d.action == "add":
            cadence, direction = str(d.details["cadence"]), str(d.details["direction"])
            anchor = date.fromisoformat(str(d.details["anchor"]))
            amount = int(d.details["amount_cents"])  # type: ignore[call-overload]
            found = _charges_named(str(d.details["name"]), direction, txns)
            series[d.key] = Stored(
                key=d.key, name=str(d.details["name"]), kind="user", direction=direction,
                cadence=cadence, anchor=anchor,
                anchor_day=None if cadence in ("weekly", "biweekly") else anchor.day,
                amount_type="fixed", expected_cents=amount, range_cents=(amount, amount),
                next_expected=_next_from(cadence, anchor, direction, max(as_of, anchor)),
                status="user-added", active=True, detected_now=False, named_by_me=True,
                evidence=tuple(fingerprint[t.row_id] for t in found),
                last_paid=found[-1].posted_date if found else None,
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
        elif d.action == "category":
            series[d.key] = replace(s, category=d.details.get("category"))  # type: ignore[arg-type]
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
    for k, name in names.items():  # after the loop: the latest wins, and None gives it back
        if name and k in series:
            series[k] = replace(series[k], name=name, named_by_me=True)
    rented = [(k, r) for k, r in rules.items() if r is not None and k in series]
    if rented:  # after every edit, so the rule's amount and window win
        flows = flow_types(txns, user)
        for k, rule in rented:
            if series[k].direction == "out" and series[k].status != "rejected":
                series[k] = _with_rent_rule(series[k], rule, txns, fingerprint, flows, as_of)
                if not series[k].named_by_me:  # a name I set myself still wins
                    series[k] = replace(series[k], name="Rent", named_by_me=True)
    for new_key, old_key in links:  # after everything else, so both sides are up to date
        if new_key in series and old_key in series:
            series[new_key] = _joined(series[new_key], series.pop(old_key))
    return sorted(series.values(), key=lambda s: s.key)


def _rule_from(details: dict[str, object]) -> RentRule | None:
    if not details:
        return None
    amounts = details["amounts_cents"]
    return RentRule(
        tuple(int(a) for a in amounts),  # type: ignore[attr-defined]
        int(details["from_day"]), int(details["to_day"]),  # type: ignore[call-overload]
    )  # fmt: skip


def _charges_named(name: str, direction: str, txns: Sequence[Txn]) -> list[Txn]:
    """Payments whose text contains `name` as whole words (any case), in the series'
    direction, oldest first. The amount is not needed: a price can change."""
    words = " ".join(name.casefold().split())
    if not words:
        return []
    pattern = re.compile(r"(?<!\w)" + re.escape(words) + r"(?!\w)")
    right_way = (
        (lambda t: t.amount_cents < 0) if direction == "out" else (lambda t: t.amount_cents > 0)
    )
    found = [
        t for t in txns if right_way(t) and pattern.search(" ".join(t.text.casefold().split()))
    ]
    return sorted(found, key=lambda t: (t.posted_date, t.row_id))


def _with_rent_rule(
    s: Stored, rule: RentRule, txns: Sequence[Txn], fingerprint: dict[int, str],
    flows: dict[int, Flow], as_of: date,
) -> Stored:  # fmt: skip
    """Apply a rent rule: the larger amount, and every outgoing e-Transfer of an allowed
    amount posted by `as_of` counts as a payment of this series, whatever the day (the window
    only places the next payment). Each payment is given its own rent cycle."""
    posted = {fingerprint[t.row_id]: t.posted_date for t in txns}
    paid = {fp: posted[fp] for fp in s.evidence if fp in posted}
    for t in txns:
        if (
            t.posted_date <= as_of
            and -t.amount_cents in rule.amounts_cents
            and flows[t.row_id].reason == "outgoing e-Transfer"
            and not flows[t.row_id].netted
        ):  # fmt: skip
            paid[fingerprint[t.row_id]] = t.posted_date
    last_paid = max(paid.values(), default=s.last_paid)
    larger, smaller = rent_cents(rule), rule.amounts_cents[0]
    changes: dict[str, object] = {
        "allowed_cents": rule.amounts_cents, "window": (rule.from_day, rule.to_day),
        "expected_cents": -larger, "range_cents": (-larger, -smaller), "amount_type": "fixed",
        "evidence": tuple(sorted(paid, key=lambda fp: (paid[fp], fp))), "last_paid": last_paid,
    }  # fmt: skip
    if not s.edited:  # your own date, if you set one, stays
        cycles = {cycle for _, cycle in assign_cycles(rule, list(paid.values()))}
        changes["next_expected"] = next_window_day(rule, as_of, cycles)
    return replace(s, **changes)  # type: ignore[arg-type]


def _joined(new: Stored, old: Stored) -> Stored:
    """`new` carrying on `old`: one history, the newer pattern, either one's category."""
    changed = old.expected_cents != new.expected_cents
    return replace(
        new,
        evidence=old.evidence + new.evidence,
        offsets=old.offsets + new.offsets,
        previous_cents=new.previous_cents
        if new.previous_cents is not None
        else (old.expected_cents if changed else None),
        changed_fingerprint=new.changed_fingerprint
        or (new.evidence[0] if changed and new.evidence else None),
        category=new.category or old.category,
        status="confirmed" if "confirmed" in (new.status, old.status) else new.status,
        continues=(*old.continues, old.key, *new.continues),
    )


def row_overrides(series: Sequence[Stored]) -> dict[str, RowOverride]:
    """fingerprint -> what its series changes for that payment. Rejected series change
    nothing. A card purchase in a series becomes a recurring outflow; an e-Transfer only
    once you confirmed its series; a series category applies to its payments only."""
    out: dict[str, RowOverride] = {}
    for s in series:
        if s.status == "rejected":
            continue
        flow = only_if = None
        if s.direction == "out" and s.kind == "merchant":
            flow, only_if = "recurring_outflow", "everyday"
        elif s.direction == "out" and s.kind == "e-transfer" and s.status == "confirmed":
            flow, only_if = "recurring_outflow", "transfer"
        for fingerprint in s.evidence:
            out[fingerprint] = RowOverride(s.key, flow, only_if, s.category)
    return out


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
        offsets=tuple(saved.get("offsets", ())),  # type: ignore[arg-type]
        loose=bool(saved.get("loose", False)),
    )  # fmt: skip


# --- Storage ---


def _to_json(s: Stored) -> dict[str, object]:
    data = asdict(s)
    for name in ("anchor", "next_expected", "last_paid"):
        data[name] = None if data[name] is None else data[name].isoformat()
    data["range_cents"], data["evidence"] = list(s.range_cents), list(s.evidence)
    data["offsets"], data["continues"] = list(s.offsets), list(s.continues)
    data["allowed_cents"] = list(s.allowed_cents)
    data["window"] = None if s.window is None else list(s.window)
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
        with conn:
            conn.execute("DELETE FROM recurring_series WHERE account = ?", (account,))
            conn.execute(
                "INSERT OR REPLACE INTO recurring_cache_info (account, version, as_of)"
                " VALUES (?, ?, '')", (account, LOGIC_VERSION),
            )  # fmt: skip
        return []
    with conn:
        conn.execute("DELETE FROM recurring_series WHERE account = ?", (account,))
        conn.execute(
            "INSERT OR REPLACE INTO recurring_cache_info (account, version, as_of)"
            " VALUES (?, ?, ?)", (account, LOGIC_VERSION, as_of.isoformat()),
        )  # fmt: skip
        conn.executemany(
            "INSERT INTO recurring_series (account, series_key, data, as_of) VALUES (?, ?, ?, ?)",
            [(account, s.key, json.dumps(_to_json(s)), as_of.isoformat()) for s in series],
        )
    return series


def cache_current(conn: sqlite3.Connection, account: str) -> bool:
    """True if the saved series were made by this version of the code from all the rows now
    stored (the same last posting date)."""
    try:
        saved = conn.execute(
            "SELECT version, as_of FROM recurring_cache_info WHERE account = ?", (account,)
        ).fetchone()
        last = conn.execute(
            "SELECT MAX(posted_date) FROM transactions WHERE account = ?", (account,)
        ).fetchone()[0]
    except sqlite3.OperationalError:  # an older database without these tables
        return False
    return saved is not None and saved[0] == LOGIC_VERSION and saved[1] == (last or "")


def ensure_current(
    conn: sqlite3.Connection, account: str, key: bytes | None = None
) -> list[Stored]:
    """The saved series, rebuilt first if they are from older code or older data."""
    if cache_current(conn, account):
        return load_series(conn, account)
    return refresh(conn, account, key)


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
        d["offsets"], d["continues"] = tuple(d.get("offsets", ())), tuple(d.get("continues", ()))
        d["allowed_cents"] = tuple(d.get("allowed_cents", ()))
        d["window"] = None if d.get("window") is None else tuple(d["window"])
        out.append(Stored(**d))
    return out
