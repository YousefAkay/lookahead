"""Prefill the scenario calculator from the database: a small adapter, no Streamlit.

- The balance: after the newest posting (rows within a day are stored newest first).
- Recurring flows: my confirmed and hand-added series. A series with a rent rule is rent: its
  latest allowed amount (my latest rent payment's amount if it is allowed, otherwise the larger
  one) on its usual day.
- Income: income rows of the last WINDOW_DAYS that are not already in a confirmed or hand-added
  series, as an average per month, arriving on their usual day.
- The daily spend: the median day of the last WINDOW_DAYS (days with no spending count) of money
  out that is not in a recurring series, not rent, not a transfer and not a netted pair.
What cannot be estimated is left at zero and named in `missing`, so the page can say so. Like the
calculator, nothing here comes from the forecaster or the proposers.
"""

import sqlite3
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from lookahead.recurring_store import Stored, rent_rule_of
from lookahead.rent import RentRule
from lookahead.scenario import Flow
from lookahead.settings import safety_buffer_cents
from lookahead.views import transactions

WINDOW_DAYS = 90
MIN_HISTORY_DAYS = 30  # less than this: the daily spend and income are not estimated
MONTH_DAYS = 365.25 / 12
ANCHOR_YEAR = 2024  # a monthly flow's anchor: only its day of the month is used
_DECIDED = ("confirmed", "user-added")


@dataclass(frozen=True)
class Prefill:
    data_date: date | None  # the balance is at the end of this day
    balance_cents: int
    flows: tuple[Flow, ...]  # confirmed and hand-added series, rent aside
    rent: Flow | None
    income: Flow | None  # income not already in a series
    daily_spend_cents: int
    buffer_cents: int
    missing: tuple[str, ...]  # what could not be estimated (left at zero)


def usual_day(days: Sequence[date]) -> int | None:
    """The most common day of the month; a tie goes to the earlier day."""
    if not days:
        return None
    counts = Counter(d.day for d in days)
    return min(counts, key=lambda day: (-counts[day], day))


def rent_amount(rule: RentRule, paid: Sequence[tuple[date, int]]) -> int:
    """The latest allowed amount: my latest rent payment's size if the rule allows it, otherwise
    the larger allowed amount."""
    if paid:
        latest = abs(max(paid)[1])
        if latest in rule.amounts_cents:
            return latest
    return rule.amounts_cents[-1]


def _payments(conn: sqlite3.Connection, account: str, fps: Sequence[str]) -> list[tuple[date, int]]:
    if not fps:
        return []
    rows = conn.execute(
        f"SELECT posted_date, amount_cents FROM transactions WHERE account = ? AND fingerprint"
        f" IN ({', '.join('?' for _ in fps)})",
        (account, *fps),
    ).fetchall()
    return sorted((date.fromisoformat(d), int(c)) for d, c in rows)


def _rent_flow(conn: sqlite3.Connection, account: str, s: Stored, rule: RentRule) -> Flow:
    paid = _payments(conn, account, s.evidence)
    day = usual_day([d for d, _ in paid]) or rule.from_day
    return Flow(s.name or "Rent", -rent_amount(rule, paid), "monthly", date(ANCHOR_YEAR, 1, day),
                card=True, key=s.key)  # fmt: skip


def prefill(conn: sqlite3.Connection, account: str, series: Sequence[Stored]) -> Prefill:
    """The calculator's starting values for an account, from its stored rows and series."""
    buffer = safety_buffer_cents(conn)
    newest = conn.execute(
        "SELECT posted_date, balance_after_cents FROM transactions WHERE account = ?"
        " ORDER BY posted_date DESC, id ASC LIMIT 1",
        (account,),
    ).fetchone()
    if newest is None:
        return Prefill(None, 0, (), None, None, 0, buffer, ("balance", "daily spend", "income"))
    last, balance = date.fromisoformat(newest[0]), int(newest[1])
    missing: list[str] = []

    decided = [s for s in series if s.status in _DECIDED and (s.active or rent_rule_of(s))]
    flows: list[Flow] = []
    rents: list[tuple[date, Flow]] = []
    for s in decided:
        rule = rent_rule_of(s)
        if rule is not None and s.direction == "out":
            paid = _payments(conn, account, s.evidence)
            rents.append((paid[-1][0] if paid else date.min, _rent_flow(conn, account, s, rule)))
        else:
            flows.append(Flow(s.name or s.kind, s.expected_cents, s.cadence, s.anchor,
                              card=s.kind == "e-transfer", key=s.key))  # fmt: skip
    rent = None
    if rents:  # the rent paid most recently is the one the page edits; any other is a flow
        rents.sort(key=lambda r: r[0])
        rent = rents[-1][1]
        flows += [f for _, f in rents[:-1]]

    rows = transactions(conn, account)
    first = rows["date"].min().date()
    since = max(first, last - timedelta(days=WINDOW_DAYS - 1))
    span = (last - since).days + 1
    recent = rows[rows["date"].dt.date >= since]
    in_decided = {fp for s in decided for fp in s.evidence}
    in_any = {fp for s in series if s.status != "rejected" for fp in s.evidence}

    income = None
    pay = recent[(recent["flow_type"] == "income") & (recent["amount_cents"] > 0)
                 & ~recent["fingerprint"].isin(in_decided)]  # fmt: skip
    has_income_series = any(s.direction == "in" for s in decided)
    if span < MIN_HISTORY_DAYS:
        missing.append("income")
    elif not pay.empty:
        monthly = round(int(pay["amount_cents"].sum()) * MONTH_DAYS / span)
        day = usual_day(list(pay["date"].dt.date)) or 1
        income = Flow("Income", monthly, "monthly", date(ANCHOR_YEAR, 1, day))
    elif not has_income_series:
        missing.append("income")

    spend = 0
    if span < MIN_HISTORY_DAYS:
        missing.append("daily spend")
    else:
        out = recent[(recent["amount_cents"] < 0) & ~recent["fingerprint"].isin(in_any)
                     & (recent["flow_type"] != "transfer")
                     & (recent["flow_type"] != "recurring_outflow")
                     & (recent["source"] != "netted")]  # fmt: skip
        by_day = (-out.groupby(out["date"].dt.date)["amount_cents"].sum()).to_dict()
        every_day = [by_day.get(since + timedelta(days=k), 0) for k in range(span)]
        spend = round(statistics.median(every_day))
    return Prefill(last, balance, tuple(flows), rent, income, int(spend), buffer, tuple(missing))
