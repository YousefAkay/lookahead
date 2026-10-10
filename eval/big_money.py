"""What the forecaster sets aside as big money, and how much of it keeps coming. Counts only.

Method in DECISIONS.md (2026-10-11, "Big money that keeps coming"). For each origin of the real
backtest it prints the big-deposit and big-payment cutoffs the forecaster used and how many
deposits and payments it set aside in the 13 weeks before; then, over the whole history, every
deposit and payment of at least $500 in no series, by whether it was big on the day it arrived, by
size band, and how many of those had earlier rows of similar size. The database is opened
read-only. No row, date, text or name is printed: cutoffs in dollars, counts and shares.

    python -m eval.big_money                 # your database (data/private)
    python -m eval.big_money --db some.db
"""

import argparse
import statistics
import sys
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest_real import real_origins
from eval.diagnose_real import cutoffs
from lookahead.db import connect_readonly
from lookahead.flows import Flow, Txn, flow_types
from lookahead.forecast.spending import (
    LARGE_MIN_CENTS,
    LARGE_WINDOW_DAYS,
    TYPICAL_MIN_ROWS,
    _money_in,
    _streams,
)
from lookahead.recurring_store import series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.ui import private_db_path
from lookahead.views import accounts

SIMILAR = (0.5, 2.0)  # a row is of similar size if it is between half and double
SIMILAR_DAYS = 365  # ... and was posted in the 365 days before
BAND_EDGES = (50000, 100000, 250000, 500000)  # $500, $1,000, $2,500, $5,000
BAND_NAMES = ("$500 to $1,000", "$1,000 to $2,500", "$2,500 to $5,000", "$5,000 and up")


def band_of(cents: int) -> int:
    """0 to 3: which size band a row of at least $500 is in."""
    return sum(cents >= edge for edge in BAND_EDGES[1:])


def similar_before(cents: int, day: date, others: Sequence[tuple[date, int]]) -> int:
    """How many rows of `others` are of similar size and were posted in the 365 days before."""
    low, high = SIMILAR[0] * cents, SIMILAR[1] * cents
    since = day - timedelta(days=SIMILAR_DAYS)
    return sum(1 for d, c in others if since <= d < day and low <= c <= high)


def origin_facts(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], origin: date
) -> dict[str, Any]:  # fmt: skip
    """What the forecaster did at one origin: its two cutoffs and what it set aside."""
    cut_in, cut_out = cutoffs(txns, flows, series_rows, origin)
    recent = origin - timedelta(days=LARGE_WINDOW_DAYS - 1)
    own_in = [
        c for t, _, c in _money_in(txns, flows, series_rows, origin) if t.posted_date >= recent
    ]
    events = _streams(txns, flows, series_rows, origin).events
    return {
        "cut_in": cut_in, "cut_out": cut_out, "own_yardstick": len(own_in) >= TYPICAL_MIN_ROWS,
        "deposits_large": sum(1 for e in events if e.cents > 0 and e.group == "Large deposit"),
        "deposits_pass": sum(1 for e in events if e.cents > 0 and e.group == "Pass-through"),
        "payments_large": sum(1 for e in events if e.cents < 0 and e.group == "Large payment"),
        "payments_pass": sum(1 for e in events if e.cents < 0 and e.group == "Pass-through"),
    }  # fmt: skip


def history_rows(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int]
) -> dict[str, list[dict[str, Any]]]:  # fmt: skip
    """Every deposit and payment of at least $500 in no series: its size band, whether it was big
    on the day it arrived (cutoff from rows posted by that day), and how many earlier rows of
    similar size there were. Flow types come from all the rows (a label for the history)."""
    deposits = [
        (t.posted_date, t.amount_cents, flows[t.row_id].reason)
        for t in txns
        if t.amount_cents > 0 and t.row_id not in series_rows and not flows[t.row_id].netted
        and flows[t.row_id].flow_type in ("income", "transfer")
    ]  # fmt: skip
    payments = [
        (t.posted_date, -t.amount_cents, flows[t.row_id].reason)
        for t in txns
        if t.amount_cents < 0 and t.row_id not in series_rows and not flows[t.row_id].netted
        and (flows[t.row_id].flow_type in ("everyday", "transfer")
             or flows[t.row_id].reason == "bill payment")
    ]  # fmt: skip
    out: dict[str, list[dict[str, Any]]] = {"deposits": [], "payments": []}
    for kind, rows in (("deposits", deposits), ("payments", payments)):
        sizes = [(d, c) for d, c, _ in rows]
        for day, cents, reason in rows:
            if cents < LARGE_MIN_CENTS:
                continue
            by_then = [t for t in txns if t.posted_date <= day]
            cut_in, cut_out = cutoffs(by_then, flows, series_rows, day)
            cut = cut_in if kind == "deposits" else cut_out
            out[kind].append({
                "cents": cents, "band": band_of(cents), "big": cents >= cut,
                "similar": similar_before(cents, day, sizes), "type": reason,
            })  # fmt: skip
    return out


def _d(cents: float) -> str:
    return f"${cents / 100:,.0f}"


def _pct(x: float) -> str:
    return f"{x:.0%}"


def origin_lines(facts: list[dict[str, Any]], last_cutoffs: tuple[int, int]) -> list[str]:
    n = len(facts)
    lines = [f"  The forecaster at each of {n} origins:"]
    for name, key in (("big-deposit cutoff", "cut_in"), ("big-payment cutoff", "cut_out")):
        v = [f[key] for f in facts]
        floor = sum(x == LARGE_MIN_CENTS for x in v)
        lines.append(
            f"    {name}: median {_d(statistics.median(v))}, smallest {_d(min(v))}, "
            f"largest {_d(max(v))}; at the $500 floor at {floor} of {n} origins ({_pct(floor / n)})"
        )  # fmt: skip
    own = sum(f["own_yardstick"] for f in facts)
    lines.append(f"    the deposits' own typical row was used at {own} of {n} origins "
                 f"(5 or more deposits in 13 weeks)")  # fmt: skip
    lines.append(f"    the cutoffs at the last posting date (the one the lump diagnostic used): "
                 f"deposits {_d(last_cutoffs[0])}, payments {_d(last_cutoffs[1])}")  # fmt: skip
    kinds = (
        ("large deposits", "deposits_large"), ("deposits in a pass-through", "deposits_pass"),
        ("large payments", "payments_large"), ("payments in a pass-through", "payments_pass"),
    )  # fmt: skip
    for label, key in kinds:
        v = [f[key] for f in facts]
        shape = Counter(min(x, 3) for x in v)
        median = f"{statistics.median(v):.0f}"
        lines.append(
            f"    {label}, set aside in the 13 weeks before an origin: median {median}, "
            f"largest {max(v)}; none at {shape[0]} origins, 1 at {shape[1]}, 2 at {shape[2]}, "
            f"3 or more at {shape[3]}"
        )
    return lines


def history_lines(rows: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines = ["  Rows of at least $500 in no series, over the whole history:"]
    for kind in ("deposits", "payments"):
        items = rows[kind]
        total = sum(r["cents"] for r in items) or 1
        big = [r for r in items if r["big"]]
        lines.append(
            f"    {kind}: {len(items)} rows; {len(big)} were big on the day they arrived "
            f"({_pct(sum(r['cents'] for r in big) / total)} of the money)"
        )  # fmt: skip
        for b, name in enumerate(BAND_NAMES):
            mine = [r for r in items if r["band"] == b]
            mine_big = [r for r in mine if r["big"]]
            lines.append(f"      {name}: {len(mine)} rows, {len(mine_big)} big on arrival")
        if big:
            money = sum(r["cents"] for r in big)
            for label, test in (("no earlier row of similar size", lambda r: r["similar"] == 0),
                                ("1 earlier row of similar size", lambda r: r["similar"] == 1),
                                ("2 or more earlier rows of similar size",
                                 lambda r: r["similar"] >= 2)):  # fmt: skip
                hit = [r for r in big if test(r)]
                lines.append(
                    f"      of the big ones, {label}: {len(hit)} rows, "
                    f"{_pct(sum(r['cents'] for r in hit) / money)} of their money"
                )  # fmt: skip
    lines += type_lines(rows["deposits"])
    return lines


def type_lines(deposits: list[dict[str, Any]]) -> list[str]:
    """The deposits by the bank's transaction type: how many were big on arrival, and how much
    of the recurring-size money (big on arrival with 2 or more earlier rows of similar size) each
    type makes up. Own-account transfers in are moves of my own money, not income."""
    big = [r for r in deposits if r["big"]]
    keeps = [r for r in big if r["similar"] >= 2]
    lines = ["    deposits by type (the bank's name for the row):"]
    if not keeps:
        return [*lines, "      no big deposit has 2 or more earlier rows of similar size"]
    money = sum(r["cents"] for r in keeps)
    for kind in sorted({r["type"] for r in deposits}):
        mine = [r for r in deposits if r["type"] == kind]
        mine_big = [r for r in big if r["type"] == kind]
        mine_keeps = [r for r in keeps if r["type"] == kind]
        lines.append(
            f"      {kind}: {len(mine)} rows of $500 or more, {len(mine_big)} big on arrival, "
            f"{len(mine_keeps)} of those with 2 or more earlier rows of similar size = "
            f"{_pct(sum(r['cents'] for r in mine_keeps) / money)} of the recurring-size money"
        )  # fmt: skip
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.big_money", description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    conn = connect_readonly(db_path)
    try:
        for number, account in enumerate(accounts(conn), start=1):
            raw = conn.execute(
                "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
                " FROM transactions WHERE account = ?", (account,),
            ).fetchall()  # fmt: skip
            if not raw:
                continue
            txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
            row_of = {r[5]: r[0] for r in raw}
            first, last = min(t.posted_date for t in txns), max(t.posted_date for t in txns)
            print(f"account-{number}")  # the name stays out of the output
            facts = []
            for origin in real_origins(first, last):
                by_then = [t for t in txns if t.posted_date <= origin]
                series, _ = series_as_of(conn, account, origin, None)
                in_series = {
                    row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence
                    if fp in row_of
                }  # fmt: skip
                flows = flow_types(by_then, load_user_decisions(conn, origin))
                facts.append(origin_facts(by_then, flows, in_series, origin))
            series_last, _ = series_as_of(conn, account, last, None)
            in_series_last = {
                row_of[fp] for s in series_last if s.status != "rejected" for fp in s.evidence
                if fp in row_of
            }  # fmt: skip
            flows_all = flow_types(txns, load_user_decisions(conn, last))
            last_cut = cutoffs(txns, flows_all, in_series_last, last)
            if facts:
                for line in origin_lines(facts, last_cut):
                    print(line)
            for line in history_lines(history_rows(txns, flows_all, in_series_last)):
                print(line)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
