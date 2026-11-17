"""A counts-only check of the Scenario page's daily spend (and income), for my real database.

It sorts every row of the page's window exactly as `lookahead.scenario_prefill` does (the same
`window`, `spend_buckets` and `income_buckets`) and prints row counts and percent shares only:
no amounts, merchant text, dates or account names. Labels come from fixed lists in the code
(flow types, flow-typing reasons, prediction sources); anything else prints as "other".

The database is opened read-only (SQLite refuses writes). The stored series are the ones the
page uses; if they are out of date they are rebuilt in memory only, never saved.

    python -m lookahead.scenario_check --db PATH [--account N]
"""

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from lookahead.categorize import SOURCES
from lookahead.db import connect_readonly
from lookahead.flows import _RULES, OWN_ACCOUNT
from lookahead.recurring_store import Stored, cache_current, load_series, series_as_of
from lookahead.scenario_prefill import (
    COUNTED,
    _typed,
    income_buckets,
    prefill,
    spend_buckets,
    window,
)
from lookahead.views import transactions

FLOW_TYPES = ("everyday", "recurring_outflow", "transfer", "income", "refund")
REASONS = tuple(
    sorted(
        {reason for _, reason in _RULES.values()}
        | {"incoming e-Transfer", "outgoing e-Transfer", "returned e-Transfer"}
        | {"no rule (fallback)", OWN_ACCOUNT}
    )
)
OTHER = "other"
NONE = "(none stored)"
TOP = (1, 5, 10)  # how concentrated the counted total is: the share of the largest rows
# One row above this share of the counted total is not daily spending (my real-data check of
# 2026-11-17: one big bill was 72%); the check warns, and the tests hold the page to it.
DOMINANT_SHARE = 0.25


def largest_shares(cents: Sequence[int], top: Sequence[int] = TOP) -> dict[int, float]:
    """The share (0 to 1) of the total held by the largest 1, 5, 10 rows; 0 for no rows."""
    ordered = sorted((abs(int(c)) for c in cents), reverse=True)
    total = sum(ordered)
    return {n: (sum(ordered[:n]) / total if total else 0.0) for n in top}


def counted_spend(conn: sqlite3.Connection, account: str, series: Sequence[Stored]) -> list[int]:
    """The cents of each money-out row the daily spend counts (positive), as the page sorts them."""
    base = prefill(conn, account, series)
    if base.data_date is None:
        return []
    rows = transactions(conn, account)
    w = window(rows, _typed(conn, account, base.data_date), series, base.data_date)
    counted = w.money_out[spend_buckets(w) == COUNTED]
    return [-int(c) for c in counted["amount_cents"]]


def known(value: object, allowed: Sequence[str]) -> str:
    """A label from a fixed list, never free text from the data."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return NONE
    return str(value) if str(value) in allowed else OTHER


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "   -  "


def table(title: str, groups: pd.Series, money: pd.Series, of: str) -> list[str]:
    """Rows and share of the money per group, largest share first."""
    total = int(money.sum())
    lines = [f"{title:<52}{'rows':>6}  share of {of}"]
    frame = pd.DataFrame({"g": groups.to_numpy(), "m": money.to_numpy()})
    summary = frame.groupby("g", sort=False)["m"].agg(["size", "sum"])
    for name, row in summary.sort_values(["sum", "size"], ascending=False).iterrows():
        lines.append(f"  {name:<50}{int(row['size']):>6}  {pct(int(row['sum']), total)}")
    if summary.empty:
        lines.append("  (no rows)")
    return lines


def report(conn: sqlite3.Connection, account: str, series: Sequence[Stored]) -> list[str]:
    """The lines to print: counts and shares only."""
    base = prefill(conn, account, series)
    if base.data_date is None:
        return ["This account has no transactions."]
    rows = transactions(conn, account)
    typed = _typed(conn, account, base.data_date)
    w = window(rows, typed, series, base.data_date)
    reason_of = {typed.fingerprint[i]: f.reason for i, f in typed.flows.items()}

    out = w.money_out.assign(bucket=spend_buckets(w))
    out_money = -out["amount_cents"]
    lines = [
        f"Window: {w.days} days. Money-out rows in the window: {len(out)}.",
        "",
        *table("What the spend rule did with them", out["bucket"], out_money, "money out"),
    ]
    counted = out[out["bucket"] == COUNTED]
    counted_money = -counted["amount_cents"]
    reasons = counted["fingerprint"].map(reason_of).map(lambda r: known(r, REASONS))
    flow = counted["flow_type"].map(lambda v: known(v, FLOW_TYPES))
    source = counted["source"].map(lambda v: known(v, SOURCES))
    lines += [
        "",
        *table(
            "Counted rows, by flow-typing reason (bank's type)", reasons, counted_money, "counted"
        ),  # fmt: skip
        "",
        *table(
            "Counted rows, by stored flow type (its source)",
            flow + " (" + source + ")",
            counted_money,
            "counted",
        ),  # fmt: skip
    ]
    total = int(counted_money.sum())
    top = largest_shares(list(counted_money))
    shares = ", ".join(f"largest {n}: {100 * s:.1f}%" for n, s in top.items())
    lines += ["", f"How concentrated the counted total is ({len(counted)} rows): {shares}."]
    if top[1] > DOMINANT_SHARE:
        lines.append(
            f"WARNING: one counted row is more than {DOMINANT_SHARE:.0%} of the counted total:"
            " it is probably not daily spending."
        )
    detected = {
        fp for s in series if s.status == "detected" for fp in s.evidence
    }  # shown on the Recurring page, not on Scenario
    in_detected = counted["fingerprint"].isin(detected)
    lines.append(
        f"Counted rows that belong to a detected (not confirmed) series: {int(in_detected.sum())}"
        f" rows, {pct(int(counted_money[in_detected].sum()), total).strip()} of counted."
    )

    money_in = w.money_in.assign(bucket=income_buckets(w))
    lines += [
        "",
        f"Money-in rows in the window: {len(money_in)}.",
        *table(
            "What the income rule did with them",
            money_in["bucket"],
            money_in["amount_cents"],
            "money in",
        ),  # fmt: skip
        "",
        "Series listed on the page:",
        f"  listed in both views: {len(base.flows)}"
        f" ({sum(f.cents > 0 for f in base.flows)} money in,"
        f" {sum(f.cents < 0 for f in base.flows)} money out)",
        f"  own-account series, chequing-only view only: {len(base.own_account_flows)}"
        f" ({sum(f.cents > 0 for f in base.own_account_flows)} money in,"
        f" {sum(f.cents < 0 for f in base.own_account_flows)} money out)",
        f"  rent field filled: {'yes' if base.rent else 'no'}",
        f"Estimates missing: {', '.join(base.missing) or 'none'}.",
    ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True, help="the database (read-only)")
    parser.add_argument(
        "--account", type=int, default=None, help="which account (1, 2, ...) if more than one"
    )
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"No database at {args.db}.", file=sys.stderr)
        return 2
    conn = connect_readonly(args.db)
    try:
        accounts = [r[0] for r in conn.execute(
            "SELECT DISTINCT account FROM transactions ORDER BY account")]  # fmt: skip
        print("Scenario check: counts and shares only (no amounts, names or dates).")
        print(f"Accounts in the database: {len(accounts)}.")
        if not accounts:
            return 0
        if args.account is None and len(accounts) > 1:
            for k, name in enumerate(accounts, start=1):
                n_rows = conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE account = ?", (name,)
                ).fetchone()[0]
                print(f"  --account {k}: {n_rows} rows")
            print("More than one account: run again with one of the --account numbers above.")
            return 2
        n = args.account or 1
        if not 1 <= n <= len(accounts):
            print(f"--account must be between 1 and {len(accounts)}.", file=sys.stderr)
            return 2
        account = accounts[n - 1]
        if cache_current(conn, account):
            series = load_series(conn, account)
            print("Series: the page's saved list.")
        else:
            series, _ = series_as_of(conn, account, None, None)
            print("Series: rebuilt in memory (the saved list is out of date; nothing written).")
        print()
        print("\n".join(report(conn, account, series)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
