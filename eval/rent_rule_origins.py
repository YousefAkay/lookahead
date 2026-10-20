"""At how many past origins could the rent fix (6475a7b) change my real forecast? Counts only.

    python -m eval.rent_rule_origins --db data/private/db/lookahead.db

The fix: a series with a rent rule is placed even if detection calls it inactive, unless I ended
it. So it can change a forecast only at an origin where a series with a rent rule is, by
detection alone, inactive, and I had not ended it. This opens the database read-only (an
in-memory copy), takes the same kind of weekly origins as the real backtest (every Monday with
eight weeks of history before it and thirty observed days after it) and prints only:
  origins checked, origins with a rent-rule series, origins where the fix could change the forecast.
It prints no name, date, amount or key. If the last number is 0 the baseline cannot have changed.
"""

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from lookahead import recurring_store as rs
from lookahead.flows import Txn
from lookahead.identity import _accounts, _in_memory_copy
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.resolve import load_user_decisions

HISTORY_DAYS, OBSERVED_DAYS = 56, 30


def origin_days(first: date, last: date) -> list[date]:
    """Every Monday with eight weeks of history behind it and thirty observed days after it."""
    day = first + timedelta(days=HISTORY_DAYS)
    day += timedelta(days=(7 - day.weekday()) % 7)
    out = []
    while day <= last - timedelta(days=OBSERVED_DAYS):
        out.append(day)
        day += timedelta(days=7)
    return out


def detection_only(
    conn: sqlite3.Connection, account: str, day: date, key: bytes | None
) -> list[rs.Stored]:
    """The series as detection alone finds them on `day`: no decision of mine replayed."""
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, day.isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    if not txns:
        return []
    fingerprints = {r[0]: r[5] for r in raw}
    return rs.current_series(txns, fingerprints, [], day, load_user_decisions(conn, day), key)


def affected_origins(
    conn: sqlite3.Connection, account: str, key: bytes | None = None
) -> tuple[int, int, int]:
    """(origins checked, origins with a rent-rule series, origins the fix could change)."""
    bounds = conn.execute(
        "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?", (account,)
    ).fetchone()
    if bounds[0] is None:
        return 0, 0, 0
    days = origin_days(date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1]))
    with_rule = changed = 0
    for day in days:
        decided, _ = rs.series_as_of(conn, account, day, key)
        ruled = {s.key: s for s in decided if rs.rent_rule_of(s) is not None}
        if not ruled:
            continue
        with_rule += 1
        raw = {s.key: s for s in detection_only(conn, account, day, key)}
        ended_ids = {d.key for d in rs.load_decisions(conn, account, day) if d.action == "end"}
        if any(
            (k in raw and not raw[k].active) and s.id not in ended_ids and s.key not in ended_ids
            for k, s in ruled.items()
        ):
            changed += 1
    return len(days), with_rule, changed


def rule_timing(conn: sqlite3.Connection, account: str) -> tuple[int, int, int]:
    """(rent rules I have now, of which made after the last origin, of which made after the data
    ends). A decision counts at an origin only if it was made on or before that day, so a rule made
    after the last origin was never in force in the backtest."""
    bounds = conn.execute(
        "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?", (account,)
    ).fetchone()
    if bounds[0] is None:
        return 0, 0, 0
    first, last = date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1])
    days = origin_days(first, last)
    latest: dict[str, rs.Decision | None] = {}
    for d in rs.load_decisions(conn, account):
        if d.action == "rent":
            latest[d.key] = d if d.details else None  # a later empty rule takes it away
    rules = [d for d in latest.values() if d is not None]
    after_origin = sum(not days or d.decided_at > days[-1] for d in rules)
    return len(rules), after_origin, sum(d.decided_at > last for d in rules)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.rent_rule_origins")
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    try:
        key: bytes | None = pseudonym_key()
    except PseudonymKeyError:
        key = None
        print("note: no PSEUDONYM_KEY, e-Transfers that name a recipient are not detected")
    copy = _in_memory_copy(args.db)
    try:
        total = [0, 0, 0]
        timing = [0, 0, 0]
        for account in _accounts(copy):  # a rent rule is a decision: only these can matter
            for i, n in enumerate(affected_origins(copy, account, key)):
                total[i] += n
            for i, n in enumerate(rule_timing(copy, account)):
                timing[i] += n
    finally:
        copy.close()
    print("origins checked (weekly, like the real backtest):", total[0])
    print("origins with a series that has a rent rule:", total[1])
    print("origins where the rent fix could change the forecast:", total[2])
    print("rent rules I have now:", timing[0])
    print("  of which made after the last origin (never in force in the backtest):", timing[1])
    print("  of which made after the data ends:", timing[2])
    print("-> the baseline cannot have changed" if total[2] == 0 else "-> rerun the real backtest")
    if timing[0] and timing[1] == timing[0]:
        print("-> every rent rule is newer than the last origin: the backtest never used one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
