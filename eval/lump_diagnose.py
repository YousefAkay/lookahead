"""Why does the lump detection find what it finds? Counts only.

For each account it prints the first and last posting date, then, for the 365-day window anchored
to today and for the window anchored to the last posting date (what the forecast does), how many
deposits each filter removes, in this order: a kind of money in the forecaster does not count
(refunds, netted holds, other flow types), in a recurring series, posted outside the window, under
the big-deposit cutoff (at least $500 and 5 times the typical row), paired as a pass-through. What
is left is the lumps. It opens the database read-only and prints no row, amount, text or name.

    python -m eval.lump_diagnose                 # your database (data/private)
    python -m eval.lump_diagnose --db some.db
"""

import argparse
import sys
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from pathlib import Path

from eval.lump_senders import load_inputs
from lookahead.db import connect_readonly
from lookahead.flows import Flow, Txn
from lookahead.forecast.spending import lump_stages
from lookahead.ui import private_db_path
from lookahead.views import accounts


def funnel(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], anchor: date
) -> dict[str, object]:
    """The counts at each filter for a window anchored at `anchor`."""
    stages = lump_stages(txns, flows, series_rows, anchor)
    deposits = [t for t in txns if t.amount_cents > 0 and t.posted_date <= anchor]
    wrong_type: Counter[str] = Counter()
    kept_type = []
    for t in deposits:
        flow = flows[t.row_id]
        if flow.netted:
            wrong_type["netted hold"] += 1
        elif flow.flow_type not in ("income", "transfer"):
            wrong_type[flow.flow_type] += 1
        else:
            kept_type.append(t)
    in_series = [t for t in kept_type if t.row_id in series_rows]
    after_series = [t for t in kept_type if t.row_id not in series_rows]
    ids = {r[0].row_id for r in stages.in_window}
    outside = [t for t in after_series if t.row_id not in ids]
    small = len(stages.in_window) - len(stages.big)
    paired = len(stages.big) - len(stages.lumps)
    big_any_date = [t for t in after_series if t.amount_cents >= stages.cutoff_cents]
    newest_big = max((t.posted_date for t in big_any_date), default=None)
    other = len(deposits) - sum(wrong_type.values()) - len(in_series) - len(outside) - (
        small + paired + len(stages.lumps))  # fmt: skip
    return {
        "window_first": stages.first_day, "deposits": len(deposits),
        "wrong_type": dict(wrong_type), "in_series": len(in_series),
        "outside_window": len(outside), "under_cutoff": small, "pass_through": paired,
        "lumps": len(stages.lumps), "other": other,
        "cutoff_is_floor": stages.cutoff_is_floor, "own_yardstick": stages.own_yardstick,
        "big_any_date": len(big_any_date),
        "newest_big_days_before_anchor": None if newest_big is None else (anchor - newest_big).days,
    }  # fmt: skip


def print_funnel(label: str, anchor: date, f: dict[str, object]) -> None:
    wrong = f["wrong_type"]
    assert isinstance(wrong, dict)
    print(f"  window anchored to {label} ({anchor}): {f['window_first']} to {anchor}")
    print(f"    deposits (amount above zero) posted by then: {f['deposits']}")
    print(f"    removed, a kind of money in the forecaster does not count: {sum(wrong.values())}")
    for kind, n in sorted(wrong.items()):
        print(f"        {kind}: {n}")
    print(f"    removed, in a recurring series: {f['in_series']}")
    print(f"    removed, posted outside the window: {f['outside_window']}")
    print(f"    removed, under the big-deposit cutoff: {f['under_cutoff']}")
    print(f"    removed, paired as a pass-through: {f['pass_through']}")
    print(f"    removed, anything else: {f['other']}")
    print(f"    lumps left: {f['lumps']}")
    cutoff = "the $500 floor" if f["cutoff_is_floor"] else "5 times the typical row"
    yardstick = "the deposits' own" if f["own_yardstick"] else "everything's"
    print(f"    the cutoff is {cutoff}; the typical row is {yardstick}")
    print(f"    big deposits of any date (after the type and series filters): {f['big_any_date']}")
    newest = f["newest_big_days_before_anchor"]
    print(f"    the newest of them is {'-' if newest is None else newest} days before the anchor")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.lump_diagnose", description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    conn = connect_readonly(db_path)
    try:
        for number, account in enumerate(accounts(conn), start=1):
            loaded = load_inputs(conn, account)
            print(f"account-{number}")  # the name stays out of the output
            if loaded is None:
                print("  no rows")
                continue
            txns, flows, in_series, last = loaded
            first = min(t.posted_date for t in txns)
            print(f"  rows: {len(txns)}; first posting {first}; last posting {last}")
            print(f"  rows in recurring series: {len(in_series)}")
            today = date.today()
            for label, anchor in (("today", today), ("the last posting date", last)):
                print_funnel(label, anchor, funnel(txns, flows, in_series, anchor))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
