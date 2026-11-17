"""Prefill the scenario calculator from the database: a small adapter, no Streamlit.

- The balance: after the newest posting (rows within a day are stored newest first).
- Recurring flows: my confirmed and hand-added series. A series with a rent rule is rent: its
  latest allowed amount (my latest rent payment's amount if it is allowed, otherwise the larger
  one) on its usual day. A rent series that has ended is dropped when another rent series is still
  active (after a move, 2026-11-17).
- Income: the money in of the last WINDOW_DAYS, scaled to a month, less what is not income:
  transfers between my own accounts (the flow typing's "transfer", both directions), refunds,
  netted pairs, money already in a confirmed or hand-added series (ended ones too, 2026-11-17),
  and big one-off deposits (the
  shared one-time money rule, `lookahead.one_time`: the deposits the forecaster sets aside). Its
  usual day is kept for the "arrives on a set day" choice.
- The daily spend: the average day of the last WINDOW_DAYS (total over the days, days with no
  spending included) of all money out except what is counted elsewhere: rent (a series with a
  rent rule), confirmed or hand-added series, transfers between my own accounts, and netted pairs.
  Card payments, e-Transfers to other people and withdrawals count (2026-11-10).
- Own-account series: a confirmed or hand-added series whose payments are mostly transfers between
  my own accounts at the bank (the flow typing's "own-account transfer", unless I typed the row as
  something else; a payment to my own card or credit line is not one) is kept apart, so the
  combined chequing-and-savings view can leave it out: there such transfers cancel out (scenario
  v2, `starting_point`).
Each estimate comes with a plain-words basis for the page, never blank: when nothing could be
estimated it says why.
What cannot be estimated is left at zero and named in `missing`, so the page can say so. Like the
calculator, nothing here comes from the forecaster or the proposers.
"""

import sqlite3
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from lookahead.flows import Flow as Typed
from lookahead.flows import Txn, flow_types
from lookahead.one_time import big_deposits, big_payments
from lookahead.recurring_store import Stored, rent_rule_of
from lookahead.rent import RentRule
from lookahead.resolve import load_user_decisions
from lookahead.scenario import Flow
from lookahead.settings import safety_buffer_cents
from lookahead.views import transactions

WINDOW_DAYS = 90
TOP_LEFT = 10  # the spend note also gives the average without this many biggest payments
MIN_HISTORY_DAYS = 30  # less than this: the daily spend and income are not estimated
MONTH_DAYS = 365.25 / 12
ANCHOR_YEAR = 2024  # a monthly flow's anchor: only its day of the month is used
_DECIDED = ("confirmed", "user-added")
NOT_INCOME = ("transfer", "refund")  # flow types of money in that is not income
# The flow-typing reason of a transfer between my own accounts at the bank (chequing and savings).
# Not "payment to my own account": that is a bill payment to my own card or credit line, money that
# leaves chequing and savings for good, so it must not cancel out in the combined view.
OWN_REASONS = ("own-account transfer",)

# What the rules do with each row of the window (shown in the notes and by the counts-only check,
# lookahead/scenario_check.py). The first matching bucket wins, in this order.
COUNTED = "counted"
SPEND_LEFT_OUT = (
    "refunded pairs",
    "rent",
    "confirmed recurring payments",
    "transfers between your own accounts",
    "big one-off payments",
)
INCOME_LEFT_OUT = (
    "money already in a confirmed series",
    "transfers between your own accounts",
    "refunds",
    "refunded pairs",
    "one-off deposits (big lump sums)",
)


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
    income_basis: str = ""
    spend_basis: str = ""
    own_account_flows: tuple[Flow, ...] = ()  # confirmed series of transfers between my accounts
    # The counts behind the two estimates, for the page: (label, rows), in a fixed order
    window_days: int = 0
    income_counts: tuple[tuple[str, int], ...] = ()
    spend_counts: tuple[tuple[str, int], ...] = ()
    spend_without_largest_cents: int | None = None  # the same average without the TOP_LEFT biggest


@dataclass(frozen=True)
class StartingPoint:
    """What the calculator starts from in one view."""

    balance_cents: int
    flows: tuple[Flow, ...]  # the recurring flows counted in this view, rent aside
    combined: bool  # chequing and savings together


def counts_top_up_twice(base: Prefill, top_ups_cents: int) -> bool:
    """Chequing-only view: a confirmed top-up from savings is already listed and the top-ups field
    adds more, so the same money may be counted twice (the page warns)."""
    return top_ups_cents > 0 and any(f.cents > 0 for f in base.own_account_flows)


def starting_point(base: Prefill, savings_cents: int | None) -> StartingPoint:
    """Chequing and savings together when a savings balance is given (transfers between my own
    accounts cancel out, so own-account series are left out), else chequing only."""
    if savings_cents is None:
        return StartingPoint(base.balance_cents, base.flows + base.own_account_flows, False)
    return StartingPoint(base.balance_cents + savings_cents, base.flows, True)


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
    # Rent comes from any series with a rent rule that I have not rejected, confirmed or not: its
    # payments are left out of the daily spend, so the rent field must carry them.
    rent_series = [s for s in series if s.status != "rejected" and s.direction == "out"
                   and rent_rule_of(s) is not None]  # fmt: skip
    rent_keys = {s.key for s in rent_series}
    rows = transactions(conn, account)
    typed = _typed(conn, account, last)
    w = window(rows, typed, series, last)
    own = w.own
    flows: list[Flow] = []
    own_flows: list[Flow] = []
    own_keys: set[str] = set()
    rents: list[tuple[date, Flow]] = []
    # After a move (2026-11-17, my decision): an ended rent series is dropped when another rent
    # series is still active; with none active, the most recently paid one is still the rent.
    if any(s.active for s in rent_series):
        rent_series = [s for s in rent_series if s.active]
    for s in rent_series:
        rule = rent_rule_of(s)
        assert rule is not None  # rent_series keeps only series with a rule
        paid = _payments(conn, account, s.evidence)
        rents.append((paid[-1][0] if paid else date.min, _rent_flow(conn, account, s, rule)))
    for s in decided:
        if s.key not in rent_keys:
            flow = Flow(s.name or s.kind, s.expected_cents, s.cadence, s.anchor,
                        card=s.kind == "e-transfer", key=s.key)  # fmt: skip
            mine = sum(fp in own for fp in s.evidence)
            if s.evidence and 2 * mine > len(s.evidence):
                own_flows.append(flow)
                own_keys.add(s.key)
            else:
                flows.append(flow)
    rent = None
    if rents:  # the rent paid most recently is the one the page edits; any other is a flow
        rents.sort(key=lambda r: r[0])
        rent = rents[-1][1]
        flows += [f for _, f in rents[:-1]]

    span = w.days
    too_short = (
        f"Only {_count(span, 'day')} of history (fewer than {MIN_HISTORY_DAYS}): not estimated."
    )

    income = None
    income_basis = ""
    in_bucket = income_buckets(w)
    pay = w.money_in[in_bucket == COUNTED]
    left_out = int((in_bucket == INCOME_LEFT_OUT[-1]).sum())
    # a confirmed top-up from savings is not an income series
    has_income_series = any(s.direction == "in" and s.key not in own_keys for s in decided)
    if span < MIN_HISTORY_DAYS:
        missing.append("income")
        income_basis = too_short
    elif not pay.empty:
        monthly = round(int(pay["amount_cents"].sum()) * MONTH_DAYS / span)
        day = usual_day(list(pay["date"].dt.date)) or 1
        income = Flow("Income", monthly, "monthly", date(ANCHOR_YEAR, 1, day))
        income_basis = (
            f"{_count(len(pay), 'deposit')} counted over {span} days, scaled to a month; "
            f"transfers between your own accounts, refunds, money already in a confirmed series "
            f"and {_count(left_out, 'one-off deposit')} left out."
        )
    elif not has_income_series:
        missing.append("income")
        income_basis = (
            f"No deposits counted as income in the last {span} days: transfers between your own "
            f"accounts, refunds, money already in a confirmed series and "
            f"{_count(left_out, 'one-off deposit')} left out."
        )
    else:
        income_basis = "Your income is in a confirmed recurring series, listed below."

    spend = 0
    spend_basis = ""
    without_largest = None
    out_bucket = spend_buckets(w)
    if span < MIN_HISTORY_DAYS:
        missing.append("daily spend")
        spend_basis = too_short
    else:
        # Left out only what is counted elsewhere (2026-11-10, my decision): rent, confirmed
        # recurring series and transfers between my own accounts; refunded pairs net to zero.
        # Card payments, e-Transfers to other people and withdrawals are spending.
        out = w.money_out[out_bucket == COUNTED]
        spend = round(-int(out["amount_cents"].sum()) / span)
        paid = sorted((-int(c) for c in out["amount_cents"]), reverse=True)
        if len(paid) > TOP_LEFT:  # with 10 or fewer, the line would say $0: left out
            without_largest = round(sum(paid[TOP_LEFT:]) / span)
        big = int((out_bucket == "big one-off payments").sum())
        spend_basis = (
            f"The average day over {span} days (days with no spending count): "
            f"{_count(len(out), 'payment')} counted, card payments, e-Transfers to other "
            "people and withdrawals included; rent, confirmed recurring payments, transfers "
            f"between your own accounts, refunded pairs and {_count(big, 'big one-off payment')}"
            " left out. Add a big payment back as a dated one-off money out."
        )
    income_counts = _tally(in_bucket, ("deposits counted", *INCOME_LEFT_OUT))
    spend_counts = _tally(out_bucket, ("payments counted", *SPEND_LEFT_OUT))
    return Prefill(last, balance, tuple(flows), rent, income, int(spend), buffer,
                   tuple(missing), income_basis, spend_basis, tuple(own_flows), span,
                   income_counts, spend_counts, without_largest)  # fmt: skip


@dataclass(frozen=True)
class Window:
    """The last WINDOW_DAYS of an account (fewer if the data is shorter) and what is needed to
    sort its rows: the sets of fingerprints counted elsewhere."""

    days: int
    money_out: pd.DataFrame  # rows of the window with money out
    money_in: pd.DataFrame  # rows of the window with money in
    in_rent: set[str]  # paid by a series with a rent rule I have not rejected, confirmed or not
    in_confirmed: set[str]  # in a confirmed or hand-added series, ended ones too
    own: set[str]  # transfers between my own accounts
    one_off: set[str]  # big one-time deposits, by the shared rule
    one_off_out: set[str]  # big one-off payments, by the same rule


def window(rows: pd.DataFrame, typed: "_Typed", series: Sequence[Stored], last: date) -> Window:
    """The window ending on `last` and the sets the two estimates leave out."""
    first = rows["date"].min().date()
    since = max(first, last - timedelta(days=WINDOW_DAYS - 1))
    recent = rows[rows["date"].dt.date >= since]
    own = _own_account(typed, rows)
    return Window(
        days=(last - since).days + 1,
        money_out=recent[recent["amount_cents"] < 0],
        money_in=recent[recent["amount_cents"] > 0],
        in_rent={
            fp for s in series if s.status != "rejected" and rent_rule_of(s) for fp in s.evidence
        },  # fmt: skip
        # ended ones too: an ended one is not carried on, and its money is neither income nor
        # daily spend (2026-11-17, my decision, same as spending)
        in_confirmed={fp for s in series if s.status in _DECIDED for fp in s.evidence},
        own=own,
        one_off=_one_off_deposits(typed, series, last, own),
        one_off_out=_one_off_payments(typed, series, last),
    )


def spend_buckets(w: Window) -> pd.Series:
    """For each money-out row of the window: COUNTED, or the first SPEND_LEFT_OUT reason."""
    fp = w.money_out["fingerprint"]
    reasons = [
        w.money_out["source"] == "netted",
        fp.isin(w.in_rent),
        fp.isin(w.in_confirmed),
        fp.isin(w.own),
        fp.isin(w.one_off_out),
    ]
    return _first(w.money_out.index, zip(SPEND_LEFT_OUT, reasons, strict=True))


def income_buckets(w: Window) -> pd.Series:
    """For each money-in row of the window: COUNTED, or the first INCOME_LEFT_OUT reason."""
    fp = w.money_in["fingerprint"]
    reasons = [
        fp.isin(w.in_confirmed),
        w.money_in["flow_type"] == "transfer",
        w.money_in["flow_type"] == "refund",
        w.money_in["source"] == "netted",
        fp.isin(w.one_off),
    ]
    return _first(w.money_in.index, zip(INCOME_LEFT_OUT, reasons, strict=True))


def _first(index: pd.Index, reasons: "Iterable[tuple[str, pd.Series]]") -> pd.Series:
    bucket = pd.Series(COUNTED, index=index, dtype="str")  # pandas 3 string dtype
    for name, hit in reversed(list(reasons)):  # the first reason in the list wins
        bucket[hit.fillna(False).astype(bool)] = name
    return bucket


def _tally(bucket: pd.Series, labels: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """(label, rows) for each bucket in order; the first label is the COUNTED bucket."""
    counts = bucket.value_counts()
    keys = (COUNTED, *labels[1:])
    return tuple((label, int(counts.get(k, 0))) for label, k in zip(labels, keys, strict=True))


@dataclass(frozen=True)
class _Typed:
    """An account's rows posted on or before a day, with their flow typing and reasons."""

    txns: list[Txn]
    flows: dict[int, Typed]  # row id -> flow type and reason
    fingerprint: dict[int, str]  # row id -> fingerprint


def _typed(conn: sqlite3.Connection, account: str, as_of: date) -> _Typed:
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, as_of.isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]
    return _Typed(txns, flow_types(txns, load_user_decisions(conn)), {r[0]: r[5] for r in rows})


def _own_account(typed: _Typed, rows: pd.DataFrame) -> set[str]:
    """Fingerprints of transfers between my own accounts: an own-account reason in the flow
    typing, unless the stored flow type (a correction of mine, or the model's) says otherwise."""
    other = set(rows.loc[rows["flow_type"].notna() & (rows["flow_type"] != "transfer"),
                         "fingerprint"])  # fmt: skip
    return {typed.fingerprint[i] for i, f in typed.flows.items()
            if f.reason in OWN_REASONS} - other  # fmt: skip


def _one_off_deposits(
    typed: "_Typed", series: Sequence[Stored], as_of: date, own: Collection[str] = ()
) -> set[str]:
    """Fingerprints of the big one-time deposits at `as_of`, by the shared rule. Transfers in from
    my own accounts are not given to the rule (2026-11-17): they are never income, and irregular
    top-ups from savings raised its typical deposit until a large one-off deposit no longer
    stood out.
    Transfers out stay in its reference for money in and out, as before.
    The rule itself and the forecaster's use of it are unchanged."""
    row_of = {fp: i for i, fp in typed.fingerprint.items()}
    in_series = {row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence
                 if fp in row_of}  # fmt: skip
    amount = {t.row_id: t.amount_cents for t in typed.txns}
    in_series |= {row_of[fp] for fp in own if fp in row_of and amount[row_of[fp]] > 0}
    found = big_deposits(typed.txns, typed.flows, in_series, as_of)
    return {typed.fingerprint[r[0].row_id] for r in found}


def _one_off_payments(typed: "_Typed", series: Sequence[Stored], as_of: date) -> set[str]:
    """Fingerprints of the big one-off payments at `as_of`, by the shared rule (2026-11-17): the
    money out the forecaster sets aside. A payment of a series I have not rejected is not one."""
    row_of = {fp: i for i, fp in typed.fingerprint.items()}
    in_series = {row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence
                 if fp in row_of}  # fmt: skip
    found = big_payments(typed.txns, typed.flows, in_series, as_of)
    return {typed.fingerprint[r[0].row_id] for r in found}


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
