"""The one-time money rule: which rows are big one-time money, and which big deposits are paid
straight back out (pass-throughs).

Moved here unchanged from lookahead/forecast/spending.py (2026-11-02) so that code outside the
forecaster can use the same rule without importing the forecaster: the forecaster imports it
from here, and so does the scenario calculator's prefill. Nothing here imports the forecaster.

- A big one-time row is one of at least $500 that is at least 5 times the typical (median) row
  of its direction over the last 13 weeks; with fewer than 5 rows in a direction, the typical
  row of everything (money in and out) is used, because one deposit cannot be judged against
  itself.
- A big deposit that is paid straight back out (85 to 115 percent within 14 days) is a
  pass-through.
Only rows posted on or before `as_of` are read (rule zero). Amounts are integer cents.
"""

from collections.abc import Collection, Mapping, Sequence
from datetime import date, timedelta
from itertools import combinations
from statistics import median

from lookahead.flows import Flow, Txn

FEE_REASONS = frozenset({"bank fee"})
LARGE_MIN_CENTS = 50000
TYPICAL_MIN_ROWS = 5  # fewer rows than this in a direction: use the typical row of everything
LARGE_TIMES_MEDIAN = 5
LARGE_WINDOW_DAYS = 91  # the typical row is taken from the last 13 weeks
PASS_THROUGH_DAYS = 14  # outflows in the 14 days from the deposit can pay it back out
PASS_THROUGH_ROWS = 12  # at most the 12 largest candidate rows are tried together
PASS_THROUGH_BAND = (85, 115)  # outflows add up to 85 to 115 percent of the deposit
RETURN_REASONS = frozenset({"card return", "pre-auth correction", "returned e-Transfer"})


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


def large_cutoff(amounts: Sequence[int]) -> int:
    """The size from which a row is a big one-time row: at least $500 and at least 5 times the
    typical (median) row. Used for money out and money in alike."""
    return max(LARGE_MIN_CENTS, LARGE_TIMES_MEDIAN * (int(median(amounts)) if amounts else 0))


def window_cutoff(
    rows: Sequence[tuple[Txn, Flow, int]], as_of: date, reference: Sequence[int] | None = None
) -> int:
    """The big-row cutoff of one direction at `as_of`: the typical row is that of these rows over
    the last 13 weeks when there are at least 5 of them, else the typical row of `reference`
    (everything money in and out), because one deposit cannot be judged against itself."""
    first = as_of - timedelta(days=LARGE_WINDOW_DAYS - 1)
    own = [c for t, _, c in rows if c > 0 and t.posted_date >= first]
    typical = own if len(own) >= TYPICAL_MIN_ROWS or reference is None else reference
    return large_cutoff(typical)


def big_cutoffs(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date
) -> tuple[int, int]:
    """(big-deposit cutoff, big-payment cutoff) in cents at `as_of`: the one rule the forecaster,
    the keeps-coming detection and the reports all use."""
    out_rows = _counted(txns, flows, series_rows, as_of)
    in_rows = _money_in(txns, flows, series_rows, as_of)
    first = as_of - timedelta(days=LARGE_WINDOW_DAYS - 1)
    everything = [r[2] for r in out_rows + in_rows if r[2] > 0 and r[0].posted_date >= first]
    return window_cutoff(in_rows, as_of, everything), window_cutoff(out_rows, as_of, everything)


def _split_large(
    rows: list[tuple[Txn, Flow, int]], as_of: date, reference: Sequence[int] | None = None
) -> tuple[list[tuple[Txn, Flow, int]], list[tuple[Txn, Flow, int]]]:
    """(kept, big): rows whose size is at least the cutoff of the last 13 weeks go to `big`
    (see `window_cutoff`). Rows older than the window are kept as they are."""
    first = as_of - timedelta(days=LARGE_WINDOW_DAYS - 1)
    cutoff = window_cutoff(rows, as_of, reference)
    big = [r for r in rows if r[2] >= cutoff and r[0].posted_date >= first]
    kept = [r for r in rows if r[2] < cutoff or r[0].posted_date < first]
    return kept, big


Row = tuple[Txn, Flow, int]


def _money_in(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date
) -> list[Row]:
    """Money in that is not a refund, not a netted hold and not a payment of a series (an
    income series is placed on its own dates). Includes transfers in from my own accounts."""
    out: list[Row] = []
    for t in txns:
        flow = flows[t.row_id]
        if t.posted_date > as_of or t.row_id in series_rows or flow.netted or t.amount_cents <= 0:
            continue
        if flow.flow_type in ("income", "transfer"):
            out.append((t, flow, t.amount_cents))
    return out


def _pass_through(
    big_in: list[Row], candidates: list[Row], band: tuple[int, int] = PASS_THROUGH_BAND
) -> list[tuple[Row, tuple[Row, ...]]]:
    """Pair each big deposit, oldest first, with outflows that took the money straight back
    out: some of the candidate rows from the deposit's day to 14 days later that add up to 85 to
    115 percent of it (the sum closest to 100 percent, then the fewest rows). A row is used for
    one deposit only. Each deposit looks only at rows up to its own 14 days, so nothing later
    changes a pairing that is already complete."""
    used: set[int] = set()
    pairs: list[tuple[Row, tuple[Row, ...]]] = []
    for dep in sorted(big_in, key=lambda r: (r[0].posted_date, r[0].row_id)):
        day, amount = dep[0].posted_date, dep[2]
        last = day + timedelta(days=PASS_THROUGH_DAYS)
        pool = [
            r for r in candidates if r[0].row_id not in used and day <= r[0].posted_date <= last
        ]
        pool = sorted(pool, key=lambda r: (-r[2], r[0].posted_date, r[0].row_id))[
            :PASS_THROUGH_ROWS
        ]
        best: tuple[tuple[int, int, tuple[tuple[date, int], ...]], tuple[Row, ...]] | None = None
        for size in range(1, len(pool) + 1):
            for combo in combinations(pool, size):
                total = sum(r[2] for r in combo)
                if 100 * total < band[0] * amount or 100 * total > band[1] * amount:
                    continue
                order = tuple(sorted((r[0].posted_date, r[0].row_id) for r in combo))
                key = (abs(total - amount), size, order)
                if best is None or key < best[0]:
                    best = (key, combo)
        if best is not None:
            used.update(r[0].row_id for r in best[1])
            pairs.append((dep, best[1]))
    return pairs
