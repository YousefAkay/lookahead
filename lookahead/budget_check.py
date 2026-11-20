"""A counts-only check of the Budgets tab, for my real database (spec
docs/specs/2026-11-20-budgets-that-forecast.md, "My real-data checks").

It runs exactly what the tab runs (lookahead.budgets) and prints counts and yes/no only: no amounts,
merchant text, dates, chances or account names. Category names come from the fixed list in the
code. The database is opened read-only (SQLite refuses writes), so nothing is saved, and no budget
alert is recorded: the open-alert count is what the tab recorded the last time it was opened.

    python -m lookahead.budget_check --db PATH [--account N]
"""

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from lookahead.budget_alerts import budget_episodes, open_budget_alerts, should_alert
from lookahead.budgets import (
    BUDGETABLE,
    CategoryRow,
    budget_forecast,
    budget_month,
    category_rows,
    is_stale,
    load_limits,
    on_budget,
    proposals_for,
    shown_categories,
    waiting_proposals,
)
from lookahead.db import connect_readonly
from lookahead.views import accounts, has_categories


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def correction_lines(
    conn: sqlite3.Connection, account: str, rows: list[CategoryRow], month_first: date
) -> list[str]:  # fmt: skip
    """Counts that show whether a move made on the tab was saved and reached this month's rows."""
    try:
        stored = dict(conn.execute(
            "SELECT scope, COUNT(*) FROM category_corrections WHERE account = ? GROUP BY scope",
            (account,),
        ).fetchall())  # fmt: skip
        mine = {r[0] for r in conn.execute(
            "SELECT fingerprint FROM category_predictions WHERE account = ?"
            " AND source = 'your label'", (account,))}  # fmt: skip
    except sqlite3.OperationalError:  # a database from before corrections existed
        stored, mine = {}, set()
    this_month = [r for r in rows if r.day >= month_first and r.category in BUDGETABLE]
    return [
        f"Category corrections stored: {stored.get('row', 0)} one-row, "
        f"{stored.get('merchant', 0)} whole-merchant",
        f"Rows this month in a budget category, shown with your corrected category: "
        f"{sum(r.fingerprint in mine for r in this_month)} of {len(this_month)}",
    ]


def report(conn: sqlite3.Connection, account: str, today: date | None = None) -> list[str]:
    """The lines for one account (counts and yes/no only)."""
    if not has_categories(conn, account):
        return ["No categories stored for this account: run the categorizer first."]
    forecast = budget_forecast(conn, account)
    if forecast is None or forecast.inputs is None:
        return ["No transactions for this account."]
    data_date = forecast.start.data_last_updated
    rows = category_rows(conn, account, data_date)
    proposals = proposals_for(conn, account, rows, forecast)
    stored = load_limits(conn, account)
    shown = shown_categories(stored, proposals)
    saved = {s.category: s.limit_cents for s in shown if s.limit_cents is not None}
    lines = [
        f"Budget month is today's calendar month: "
        f"{yes_no(not is_stale(forecast.start.first_day, today or date.today()))}",
        "",
        f"{'Category':<24}{'proposal':>10}{'months':>8}{'saved':>7}{'rows this month':>17}",
    ]
    for category in BUDGETABLE:
        p = proposals.get(category)
        in_month = sum(1 for r in rows if r.category == category
                       and r.day >= forecast.start.first_day.replace(day=1))  # fmt: skip
        lines.append(
            f"{category:<24}{yes_no(p is not None and p.limit_cents is not None):>10}"
            f"{p.months if p else 0:>8}{yes_no(category in saved):>7}{in_month:>17}"
        )
    proposed = sum(1 for p in proposals.values() if p.limit_cents is not None)
    lines += ["", f"Categories with a proposal: {proposed} of {len(BUDGETABLE)}",
              f"Categories with a saved limit: {len(saved)}"]  # fmt: skip
    # The pace and the what-if on the saved limits, or, before Looks right, on the proposals.
    limits = saved or waiting_proposals(shown)
    basis = "saved limits" if saved else "the proposals (nothing saved yet)"
    month = budget_month(forecast, rows, limits)
    with_pace = sum(1 for p in month.paces if p.futures > 0)
    above_zero = sum(1 for p in month.paces if p.chance_over > 0)
    lines += [
        f"Pace computed on: {basis}",
        f"Categories with a pace computed from the futures: {with_pace} of {len(month.paces)} "
        f"({forecast.inputs.futures} futures)",
        f"Categories with a chance of going over above 0: {above_zero} of {len(month.paces)}",
        f"Categories that would alert on this look: "
        f"{sum(should_alert(p, forecast.start.first_day) for p in month.paces)}",
        f"Categories whose spent so far includes a big one-off: "
        f"{sum(p.big_one_off for p in month.paces)}",
        f"Open budget alerts (recorded by the tab): {len(open_budget_alerts(conn, account))} "
        f"of {len(budget_episodes(conn, account))} episodes",
        *correction_lines(conn, account, rows, forecast.start.first_day.replace(day=1)),
    ]
    if month.paces:
        result = on_budget(forecast, month)
        lines += [
            "On budget, the balance is at or above as-now in every future on every day: "
            f"{yes_no(result.never_lower)}",
            "On budget, the middle future's lowest balance is at or above as-now: "
            f"{yes_no(result.low_on_budget_cents >= result.low_now_cents)}",
            "On budget, the chance of dipping below the buffer is at or below as-now: "
            f"{yes_no(result.chance_on_budget <= result.chance_now)}",
        ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.budget_check", description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--account", type=int, default=1, help="account number (1 = the first)")
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"No database at {args.db}", file=sys.stderr)
        return 2
    conn = connect_readonly(args.db)
    try:
        names = accounts(conn)
        if not 1 <= args.account <= len(names):
            print(f"Account number 1 to {len(names)}", file=sys.stderr)
            return 2
        print(f"Account {args.account} of {len(names)}")
        print("\n".join(report(conn, names[args.account - 1])))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
