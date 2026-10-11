"""a) lump proposals (retired on 2026-10-12; kept as the record) on the account holder's own
data: counts and summary numbers only. Run by the
account holder only.

The real-account version of the step 2 checks of the 2026-10-11 plan (a) passed its gate on the
synthetic answer key; b) did not, so it has no real-account check). At every weekly origin with
eight weeks of history before it and thirty observed days after it, the lump proposals are made
exactly as the forecaster makes them (rows posted by the origin, recurring series as of the
origin, the pseudonym key for senders), then compared with what happened next: was a deposit from
the same sender (same keyed token) posted inside the proposal's window, and how far were its day
and size from the proposal's. It opens the database read-only and prints counts, medians of day
misses and percent size misses only: never a row, a date, an amount, a name or a token.

These numbers are development, not evaluation: the design was made while looking at the same
account.

    python -m eval.proposals_real                 # your database (data/private)
    python -m eval.proposals_real --db some.db
"""

import argparse
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest_real import real_origins
from eval.small_n import MIN_GROUP, MIN_STATISTIC, TOO_FEW
from lookahead.db import connect_readonly
from lookahead.flows import Txn, flow_types
from lookahead.forecast.start import forecast_start
from lookahead.proposals import lump_proposals, sender_of
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key, token
from lookahead.recurring_store import series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.ui import private_db_path
from lookahead.views import accounts


def check_account(conn: Any, account: str, key: bytes) -> dict[str, Any]:
    """Counts for one account: proposals at each origin and what followed them."""
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? ORDER BY posted_date, id",
        (account,),
    ).fetchall()
    every = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]
    fps = {r[0]: r[5] for r in rows}
    days = real_origins(every[0].posted_date, every[-1].posted_date) if every else []
    out: dict[str, Any] = {"origins": len(days), "with_proposal": 0, "proposals": 0,
                           "distinct": set(), "followed": 0, "date_miss": [], "size_miss": [],
                           "likely": 0, "likely_followed": 0}  # fmt: skip
    for origin in days:
        start = forecast_start(conn, account, origin)
        if start is None:
            continue
        txns = [t for t in every if t.posted_date <= start.data_last_updated]
        row_of = {fps[t.row_id]: t.row_id for t in txns}
        series, _ = series_as_of(conn, account, origin, None)  # as the real backtest does
        in_series = {row_of[fp] for s in series if s.status != "rejected"
                     for fp in s.evidence if fp in row_of}  # fmt: skip
        flows = flow_types(txns, load_user_decisions(conn, origin))
        found, _ = lump_proposals(txns, flows, in_series, start.data_last_updated,
                                  start.first_day, start.last_day, key, fps)  # fmt: skip
        out["with_proposal"] += bool(found)
        for p in found:
            out["proposals"] += 1
            out["distinct"].add(p.key)
            out["likely"] += p.confidence == "likely"
            later = [t for t in every if origin < t.posted_date <= origin + timedelta(days=60)
                     and t.amount_cents > 0 and (name := sender_of(t.text))
                     and token("PERSON", name, key) == p.sender]  # fmt: skip
            inside = [t for t in later if p.first <= t.posted_date <= p.last]
            if inside:
                hit = min(inside, key=lambda t: abs((t.posted_date - p.expected).days))
                out["followed"] += 1
                out["likely_followed"] += p.confidence == "likely"
                out["date_miss"].append(abs((hit.posted_date - p.expected).days))
                out["size_miss"].append(abs(hit.amount_cents - p.typical_cents)
                                        / hit.amount_cents)  # fmt: skip
    out["distinct"] = len(out["distinct"])
    return out


def _median(values: list[float]) -> str:
    """A median only from 10 or more values (a median of fewer is one of the rows)."""
    if not values:
        return "-"
    return f"{statistics.median(values):.1f}" if len(values) >= MIN_STATISTIC else TOO_FEW


def summary_lines(c: dict[str, Any]) -> list[str]:
    """What one account prints: counts, with a group under 5 hidden and a median under 10 values
    hidden (eval/small_n.py)."""
    lines = [f"  origins: {c['origins']}; with at least one proposal: {c['with_proposal']}"]
    if c["proposals"] < MIN_GROUP:
        return [*lines, f"  proposals: {TOO_FEW}"]
    lines.append(f"  proposals (origin, proposal): {c['proposals']} ({c['distinct']} distinct)")
    if c["likely"] < MIN_GROUP:
        lines.append(f"  'likely' (3+ lumps): {TOO_FEW}")
        likely = f"'likely': {TOO_FEW}"
    else:
        lines.append(f"  'likely' (3+ lumps): {c['likely']}")
        likely = f"'likely': {c['likely_followed']} of {c['likely']}"
    lines.append(f"  followed by a deposit from the same sender inside the window: "
                 f"{c['followed']} of {c['proposals']} ({likely})")  # fmt: skip
    lines.append(f"  when followed: day miss, median days {_median(c['date_miss'])}; size miss, "
                 f"median percent {_median([100 * x for x in c['size_miss']])}")  # fmt: skip
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.proposals_real", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    try:
        key = pseudonym_key()
    except PseudonymKeyError as error:
        print(f"No pseudonym key, so no sender can be grouped and no proposal is made: {error}")
        return 1
    conn = connect_readonly(db_path)
    try:
        for number, account in enumerate(accounts(conn), start=1):
            c = check_account(conn, account, key)
            print(f"account-{number}")  # the name stays out of the output
            for line in summary_lines(c):
                print(line)
    finally:
        conn.close()
    print("Development numbers: the design was made while looking at this account.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
