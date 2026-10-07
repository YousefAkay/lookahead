"""The everyday-spending stream D(t, h) and its two baselines (3b step 3).

D(t, h) is the cumulative spending from the day after t through h days later. What counts:
- card purchases, cash, bill payments and transfers out that belong to no recurring series
  (e-Transfers to people, money to savings, a one-off bill): they all lower the balance, so
  they stay in. One stream, not two:
  a separate transfer model would add a method before any evidence that one is needed;
- large one-offs stay in as well. They really happen, and leaving them out would make the
  forecast too optimistic, the risky direction for a shortfall warning;
- card returns and corrections that are not fully reversed lower the stream.
What does not count: payments of a recurring series (they are placed on their own dates),
fully reversed pairs, bank fees (modelled separately, because they follow events such as an
overdraft), income and incoming transfers (no income is assumed).

Both baselines use only rows posted on or before the forecast date `as_of` (rule zero).
Amounts are integer cents; an average is an estimate, so a path is rounded after summing.
"""

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from lookahead.flows import OWN_ACCOUNT, Flow, Txn

FEE_REASONS = frozenset({"bank fee"})
RETURN_REASONS = frozenset({"card return", "pre-auth correction", "returned e-Transfer"})
RECENT_DAYS = 28
WEEKDAY_WEEKS = 8


def _counted(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date
) -> list[tuple[Txn, Flow, int]]:
    """The rows that make up the stream, each with its cents spent (negative for a return)."""
    out: list[tuple[Txn, Flow, int]] = []
    for t in txns:
        flow = flows[t.row_id]
        if t.posted_date > as_of or t.row_id in series_rows or flow.netted:
            continue
        if flow.reason in FEE_REASONS:
            continue
        # A bill payment is typed "recurring outflow" because most are; one that is in no
        # series still lowers the balance, and nothing else would count it.
        spends = flow.flow_type in ("everyday", "transfer") or flow.reason == "bill payment"
        if t.amount_cents < 0 and spends:
            out.append((t, flow, -t.amount_cents))
        elif t.amount_cents > 0 and flow.reason in RETURN_REASONS:
            out.append((t, flow, -t.amount_cents))
    return out


def spending_by_day(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date
) -> dict[date, int]:
    """Spending in cents for each day (positive = spent; a day of returns can be negative)."""
    daily: dict[date, int] = defaultdict(int)
    for t, _, cents in _counted(txns, flows, series_rows, as_of):
        daily[t.posted_date] += cents
    return dict(daily)


def _cumulative(per_day: list[float]) -> list[int]:
    total, out = 0.0, []
    for amount in per_day:
        total += amount
        out.append(round(total))
    return out


def _days_in(as_of: date, history_start: date, length: int) -> list[date]:
    """The last `length` days up to `as_of`, but never before the history began."""
    first = max(as_of - timedelta(days=length - 1), history_start)
    return [first + timedelta(days=k) for k in range((as_of - first).days + 1)]


def recent_average_path(
    daily: Mapping[date, int], as_of: date, history_start: date | None, horizon: int,
    days: int = RECENT_DAYS,
) -> list[int]:  # fmt: skip
    """Baseline 1: the average of the last 28 days, times the number of days ahead.
    Days with no spending count as zero; a short history is averaged over the days it has."""
    if history_start is None or history_start > as_of:
        return [0] * horizon
    window = _days_in(as_of, history_start, days)
    average = max(sum(daily.get(d, 0) for d in window), 0) / len(window)
    return _cumulative([average] * horizon)


def weekday_average_path(
    daily: Mapping[date, int], as_of: date, history_start: date | None, first_day: date,
    horizon: int, weeks: int = WEEKDAY_WEEKS,
) -> list[int]:  # fmt: skip
    """Baseline 2: the average spending on each weekday over the last 8 weeks, added up over
    the weekdays of the forecast days. A weekday with no day in the history uses the overall
    average."""
    if history_start is None or history_start > as_of:
        return [0] * horizon
    window = _days_in(as_of, history_start, weeks * 7)
    overall = max(sum(daily.get(d, 0) for d in window), 0) / len(window)
    by_weekday: dict[int, float] = {}
    for weekday in range(7):
        seen = [daily.get(d, 0) for d in window if d.weekday() == weekday]
        by_weekday[weekday] = max(sum(seen), 0) / len(seen) if seen else overall
    return _cumulative(
        [by_weekday[(first_day + timedelta(days=k)).weekday()] for k in range(horizon)]
    )


# --- What is in everyday spending (the evidence behind the weekday baseline) ---

BREAKDOWN_TYPES = (
    "Card purchases",
    "Cash",
    "Transfers to people",
    "Transfers to my own accounts",
    "Bill payments (in no series)",
    "Other withdrawals",
    "Other",
    "Returns",
    "Large one-offs",
)
_TYPE_OF_REASON = {
    "card purchase": "Card purchases",
    "cash withdrawal": "Cash",
    "outgoing e-Transfer": "Transfers to people",
    "own-account transfer": "Transfers to my own accounts",
    OWN_ACCOUNT: "Transfers to my own accounts",
    "bill payment": "Bill payments (in no series)",
    "withdrawal": "Other withdrawals",
}
# A row is a "large one-off" if it is at least this much and at least 5 times the median row of
# the window. It is only a label for this table: large one-offs stay in the forecast.
LARGE_MIN_CENTS = 30000
LARGE_TIMES_MEDIAN = 5


@dataclass(frozen=True)
class BreakdownRow:
    type: str
    per_weekday: tuple[float, ...]  # average cents spent on each weekday, Monday first
    per_week: float  # the seven added up: what one week of this type adds to the forecast


def spending_breakdown(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    history_start: date | None, weeks: int = WEEKDAY_WEEKS,
) -> list[BreakdownRow]:  # fmt: skip
    """The weekday baseline's history, split by type: for each type, the average spent on each
    weekday over the last 8 weeks. Same rows as the baseline (series payments are not here).
    The rows add up to the baseline's weekday averages before it rounds or clamps them."""
    if history_start is None or history_start > as_of:
        return []
    window = _days_in(as_of, history_start, weeks * 7)
    in_window = set(window)
    rows = [r for r in _counted(txns, flows, series_rows, as_of) if r[0].posted_date in in_window]
    outgoing = [cents for _, _, cents in rows if cents > 0]
    large = max(LARGE_MIN_CENTS, LARGE_TIMES_MEDIAN * (median(outgoing) if outgoing else 0))
    sums: dict[str, list[float]] = {}
    for t, flow, cents in rows:
        kind = "Returns" if cents < 0 else "Large one-offs" if cents >= large else (
            _TYPE_OF_REASON.get(flow.reason, "Other")
        )  # fmt: skip
        sums.setdefault(kind, [0.0] * 7)[t.posted_date.weekday()] += cents
    days_per_weekday = [sum(d.weekday() == w for d in window) for w in range(7)]
    out = []
    for kind in BREAKDOWN_TYPES:
        if kind in sums:
            per = tuple(
                sums[kind][w] / days_per_weekday[w] if days_per_weekday[w] else 0.0
                for w in range(7)
            )
            out.append(BreakdownRow(kind, per, sum(per)))
    return out
