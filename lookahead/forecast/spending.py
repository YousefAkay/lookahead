"""The everyday-spending stream D(t, h) and its two baselines (3b step 3).

D(t, h) is the cumulative spending from the day after t through h days later. What counts:
- card purchases, cash, bill payments and transfers out that belong to no recurring series
  (e-Transfers to people, money to savings, a one-off bill): they all lower the balance, so
  they stay in. One stream, not two:
  a separate transfer model would add a method before any evidence that one is needed;
- big one-time rows do not: a row of at least $500 that is at least 5 times the typical row of
  the last 13 weeks (the same rule for money out and for money in) is left out of every
  everyday stream and listed on its own (`big_events`); a big deposit that is paid straight
  back out (85 to 115 percent within 14 days) is a pass-through, set aside together with the
  outflows that paid it out; Averaged over weekdays, a one-off
  would be repeated every week; and the deposit that funded it must leave the deposit stream
  too, or the futures would be too optimistic;
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
from itertools import combinations
from statistics import median

from lookahead.flows import OWN_ACCOUNT, Flow, Txn

FEE_REASONS = frozenset({"bank fee"})
FEE_REVERSAL = "fee reversal"
FEE_DAYS = 28
LARGE_MIN_CENTS = 50000
TYPICAL_MIN_ROWS = 5  # fewer rows than this in a direction: use the typical row of everything
LARGE_TIMES_MEDIAN = 5
LARGE_WINDOW_DAYS = 91  # the typical row is taken from the last 13 weeks
PASS_THROUGH_DAYS = 14  # outflows in the 14 days from the deposit can pay it back out
PASS_THROUGH_ROWS = 12  # at most the 12 largest candidate rows are tried together
PASS_THROUGH_BAND = (85, 115)  # outflows add up to 85 to 115 percent of the deposit
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

_DEPOSIT_KIND = {
    "credit memo": "Credit memo",
    "deposit": "Deposit",
    "incoming e-Transfer": "Incoming e-Transfer",
    "own-account transfer": "Transfer from my own accounts",
}


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


@dataclass(frozen=True)
class BigEvent:
    day: date
    kind: str  # what it was, e.g. "Transfers to my own accounts" or "Deposit"
    cents: int  # signed: money in is positive
    group: str = ""  # "Pass-through", "Large deposit" or "Large payment"
    pair: int | None = None  # the number of the pass-through it belongs to


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


@dataclass(frozen=True)
class _Streams:
    spending: list[Row]  # everyday spending rows (and returns)
    deposits: list[Row]  # irregular money in
    events: list[BigEvent]  # what was set aside: pass-throughs, large deposits, large payments
    paired_ids: frozenset[int] = frozenset()  # rows that are part of a pass-through


def _streams(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    exclude: Collection[int] = (),
) -> _Streams:  # fmt: skip
    """Sort every row into the everyday spending, the irregular deposits, or an event that is
    set aside. A big row is one of at least $500 and 5 times the typical row of its direction
    over the last 13 weeks. A big deposit that was paid straight back out is a pass-through:
    it and the outflows that paid it out are all set aside (the outflows can include small
    transfers and bill payments, never a small card purchase). Other big deposits and big
    payments are set aside and listed too. Rows in `exclude` (the keeps-coming arrivals, which
    are forecast) are not listed as set aside; nothing else about them changes here."""
    out_rows = _counted(txns, flows, series_rows, as_of)
    in_rows = _money_in(txns, flows, series_rows, as_of)
    first = as_of - timedelta(days=LARGE_WINDOW_DAYS - 1)
    everything = [r[2] for r in out_rows + in_rows if r[2] > 0 and r[0].posted_date >= first]
    kept_out, big_out = _split_large(out_rows, as_of, everything)
    kept_in, big_in = _split_large(in_rows, as_of, everything)
    big_ids = {r[0].row_id for r in big_out}

    def could_be_paid_out(r: Row) -> bool:
        """A transfer or bill payment of any size, or any big row; never a small purchase."""
        return (
            r[0].row_id in big_ids or r[1].flow_type == "transfer" or r[1].reason == "bill payment"
        )

    candidates = [r for r in out_rows if r[2] > 0 and could_be_paid_out(r)]
    pairs = _pass_through(big_in, candidates)
    paired = {r[0].row_id for _, combo in pairs for r in combo}
    paired |= {dep[0].row_id for dep, _ in pairs}
    events: list[BigEvent] = []

    def deposit_event(r: Row, group: str, pair: int | None) -> BigEvent:
        return BigEvent(
            r[0].posted_date, _DEPOSIT_KIND.get(r[1].reason, "Other money in"), r[2], group, pair
        )

    def payment_event(r: Row, group: str, pair: int | None) -> BigEvent:
        return BigEvent(
            r[0].posted_date, _TYPE_OF_REASON.get(r[1].reason, "Other"), -r[2], group, pair
        )

    for n, (dep, combo) in enumerate(pairs, start=1):
        events.append(deposit_event(dep, "Pass-through", n))
        events += [payment_event(r, "Pass-through", n) for r in combo]
    skip = paired | set(exclude)
    events += [deposit_event(r, "Large deposit", None) for r in big_in if r[0].row_id not in skip]
    events += [payment_event(r, "Large payment", None) for r in big_out if r[0].row_id not in skip]
    events.sort(key=lambda e: (e.day, -e.cents))
    return _Streams(
        [r for r in kept_out if r[0].row_id not in paired], kept_in, events, frozenset(paired)
    )


# --- Big money that keeps coming (DECISIONS.md, 2026-10-11) ---

KEEPS_EARLIER = 2  # earlier rows of similar size a row needs to be "recurring-size"
KEEPS_SIMILAR = (0.5, 2.0)  # similar: between half and double its size
KEEPS_DAYS = 365  # the earlier rows are looked for in the 365 days before, and the rate over a year
KEEPS_PAYMENTS_PER_YEAR = 6  # payments come back as arrivals only when there are this many
_OWN_ACCOUNT_IN = "own-account transfer"  # the reason of a transfer in from my own account


@dataclass(frozen=True)
class KeepsComing:
    """Switch on: big rows that keep arriving at a similar size are forecast as random arrivals
    at their own rate and sizes instead of being set aside. `own_account=False`: a transfer in
    from my own other account is neither such a row nor an earlier row of similar size."""

    own_account: bool = True


DEFAULT_KEEPS = KeepsComing()  # the forecaster's default (DECISIONS.md, 2026-10-11); None = off


@dataclass(frozen=True)
class Arrivals:
    in_rate: float = 0.0  # chance a day of a recurring-size big deposit
    in_sizes: tuple[int, ...] = ()  # cents, oldest first
    out_rate: float = 0.0
    out_sizes: tuple[int, ...] = ()
    members: frozenset[int] = frozenset()  # the rows that became arrivals (not set aside any more)
    payments_below_floor: int = 0  # recurring-size payments left set aside: under 6 a year


def _similar_earlier(row: Row, others: Sequence[tuple[date, int]]) -> int:
    day, cents = row[0].posted_date, row[2]
    low, high = KEEPS_SIMILAR[0] * cents, KEEPS_SIMILAR[1] * cents
    since = day - timedelta(days=KEEPS_DAYS)
    return sum(1 for d, c in others if since <= d < day and low <= c <= high)


def keeps_coming_arrivals(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    options: KeepsComing,
) -> Arrivals:  # fmt: skip
    """The big rows of the last 365 days that keep coming: at least the big cutoff of `as_of`, in
    no series, not part of a pass-through, with 2 or more earlier rows of similar size of the
    same direction. Their rate (rows over the days observed) and sizes are what the futures draw
    from. Payments only when there are at least 6 a year. Only rows posted by `as_of` are read."""
    if not txns:
        return Arrivals()
    out_rows = [r for r in _counted(txns, flows, series_rows, as_of) if r[2] > 0]
    in_all = _money_in(txns, flows, series_rows, as_of)
    cut_in, cut_out = big_cutoffs(txns, flows, series_rows, as_of)
    first = as_of - timedelta(days=KEEPS_DAYS - 1)
    big_in_all = [r for r in in_all if r[2] >= cut_in and r[0].posted_date >= first]
    big_out = [r for r in out_rows if r[2] >= cut_out and r[0].posted_date >= first]
    candidates = [
        r for r in out_rows if r[2] >= cut_out or r[1].flow_type == "transfer"
        or r[1].reason == "bill payment"
    ]  # fmt: skip
    paired = set(_streams(txns, flows, series_rows, as_of).paired_ids)
    for dep, combo in _pass_through(big_in_all, candidates):
        paired.add(dep[0].row_id)
        paired.update(r[0].row_id for r in combo)
    own_ok = options.own_account
    in_rows = in_all if own_ok else [r for r in in_all if r[1].reason != _OWN_ACCOUNT_IN]
    in_by_day = [(r[0].posted_date, r[2]) for r in in_rows]
    out_by_day = [(r[0].posted_date, r[2]) for r in out_rows]
    members_in = sorted(
        (r for r in big_in_all if r[0].row_id not in paired
         and (own_ok or r[1].reason != _OWN_ACCOUNT_IN)
         and _similar_earlier(r, in_by_day) >= KEEPS_EARLIER),
        key=lambda r: (r[0].posted_date, r[0].row_id),
    )  # fmt: skip
    members_out = sorted(
        (r for r in big_out if r[0].row_id not in paired
         and _similar_earlier(r, out_by_day) >= KEEPS_EARLIER),
        key=lambda r: (r[0].posted_date, r[0].row_id),
    )  # fmt: skip
    began = min(t.posted_date for t in txns)
    days = (as_of - max(first, began)).days + 1
    below = 0
    if len(members_out) < KEEPS_PAYMENTS_PER_YEAR:
        below, members_out = len(members_out), []
    return Arrivals(
        len(members_in) / days, tuple(r[2] for r in members_in),
        len(members_out) / days, tuple(r[2] for r in members_out),
        frozenset(r[0].row_id for r in members_in + members_out), below,
    )  # fmt: skip


LUMP_LOOKBACK_DAYS = 365  # lump deposits are looked for in the last year


@dataclass(frozen=True)
class LumpStages:
    """Every step of the lump detection, so that a count of what each filter removed can be
    printed and `lump_rows` cannot drift from it."""

    money_in: list[Row]  # money in of a kind the forecaster counts, in no series, by the date
    in_window: list[Row]  # of those, posted within the lookback
    big: list[Row]  # of those, at or above the big-deposit cutoff
    lumps: list[Row]  # of those, not paired as a pass-through: the lumps
    cutoff_is_floor: bool  # the cutoff is the $500 floor, not 5 times the typical row
    own_yardstick: bool  # the typical row is that of the deposits (5 or more in 13 weeks)
    first_day: date  # the first day of the lookback window
    cutoff_cents: int  # the size from which a deposit is big (never printed by the reports)


def lump_stages(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    lookback_days: int = LUMP_LOOKBACK_DAYS,
) -> LumpStages:  # fmt: skip
    """Run the lump detection and keep each stage: deposits the forecaster sets aside as large
    deposits (at least $500 and 5 times the typical row of the last 13 weeks, in no series, not
    paid straight back out within 14 days) posted in the last `lookback_days` up to `as_of`.
    Pass-throughs are not lumps, because the outflow that paid them out is set aside too. Only
    rows posted on or before `as_of` are read (rule zero)."""
    out_rows = _counted(txns, flows, series_rows, as_of)
    in_rows = _money_in(txns, flows, series_rows, as_of)
    recent = as_of - timedelta(days=LARGE_WINDOW_DAYS - 1)
    everything = [r[2] for r in out_rows + in_rows if r[2] > 0 and r[0].posted_date >= recent]
    own_in = [c for t, _, c in in_rows if c > 0 and t.posted_date >= recent]
    own_out = [c for t, _, c in out_rows if c > 0 and t.posted_date >= recent]
    own_yardstick = len(own_in) >= TYPICAL_MIN_ROWS
    cut_in = large_cutoff(own_in if own_yardstick else everything)
    cut_out = large_cutoff(own_out if len(own_out) >= TYPICAL_MIN_ROWS else everything)
    first = as_of - timedelta(days=lookback_days - 1)
    in_window = [r for r in in_rows if r[0].posted_date >= first]
    big_in = [r for r in in_window if r[2] >= cut_in]
    candidates = [
        r for r in out_rows if r[2] > 0 and (
            r[2] >= cut_out or r[1].flow_type == "transfer" or r[1].reason == "bill payment")
    ]  # fmt: skip
    paired = {dep[0].row_id for dep, _ in _pass_through(big_in, candidates)}
    lumps = sorted((r for r in big_in if r[0].row_id not in paired),
                   key=lambda r: (r[0].posted_date, r[0].row_id))  # fmt: skip
    return LumpStages(in_rows, in_window, big_in, lumps, cut_in == LARGE_MIN_CENTS,
                      own_yardstick, first, cut_in)  # fmt: skip


def lump_rows(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    lookback_days: int = LUMP_LOOKBACK_DAYS,
) -> list[Row]:  # fmt: skip
    """The lump deposits of the last `lookback_days` up to `as_of`, oldest first."""
    return lump_stages(txns, flows, series_rows, as_of, lookback_days).lumps


def lump_arrivals(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    history_start: date | None, lookback_days: int = LUMP_LOOKBACK_DAYS,
    exclude: Collection[int] = (),
) -> tuple[float, tuple[int, ...]]:  # fmt: skip
    """The random lump arrivals' inputs (an option, off by default): the chance of a lump on any
    day, the lumps of the window divided by its days (from the later of the first posted row and
    `lookback_days` back, to `as_of`, both included), and the observed lump sizes in cents,
    oldest first. No history: rate 0. Only rows posted on or before `as_of` (rule zero). Lumps in
    `exclude` (row ids) are left out of both: a sender whose deposit is confirmed or declined as a
    proposal is not drawn at random as well."""
    if history_start is None or history_start > as_of:
        return 0.0, ()
    lumps = [r for r in lump_rows(txns, flows, series_rows, as_of, lookback_days)
             if r[0].row_id not in exclude]  # fmt: skip
    first = max(history_start, as_of - timedelta(days=lookback_days - 1))
    days = (as_of - first).days + 1
    return len(lumps) / days, tuple(cents for _, _, cents in lumps)


def spending_by_day(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date
) -> dict[date, int]:
    """Everyday spending in cents for each day (positive = spent; a day of returns can be
    negative). Big one-time rows and pass-through outflows are not in it."""
    daily: dict[date, int] = defaultdict(int)
    for t, _, cents in _streams(txns, flows, series_rows, as_of).spending:
        daily[t.posted_date] += cents
    return dict(daily)


def deposits_by_day(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date
) -> dict[date, int]:
    """Irregular money in, in cents for each day: deposits, incoming e-Transfers and transfers
    from my own accounts that are in no series. Big deposits are not in it: when one comes
    cannot be predicted from history."""
    daily: dict[date, int] = defaultdict(int)
    for t, _, cents in _streams(txns, flows, series_rows, as_of).deposits:
        daily[t.posted_date] += cents
    return dict(daily)


def big_events(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    exclude: Collection[int] = (),
) -> list[BigEvent]:  # fmt: skip
    """What was set aside in the last 13 weeks, oldest first: pass-throughs (a big deposit and
    the outflows that paid it out, numbered), large deposits and large payments. None of it is
    forecast."""
    return _streams(txns, flows, series_rows, as_of, exclude).events


@dataclass(frozen=True)
class DepositWeek:
    first: date
    last: date
    in_stream: tuple[tuple[str, int], ...]  # (type, cents) of the deposits the futures can draw
    set_aside: tuple[BigEvent, ...]  # big deposits and pass-throughs of that week: never drawn
    total: int  # cents in the stream that week


def deposit_weeks(
    txns: Sequence[Txn], flows: Mapping[int, Flow], series_rows: Collection[int], as_of: date,
    week_starts: Sequence[date], exclude: Collection[int] = (),
) -> list[DepositWeek]:  # fmt: skip
    """For each week the futures can draw from, the irregular deposits in the stream (by type,
    largest first) and the big deposits and pass-throughs that were set aside that week. This is
    the evidence behind the deposit part of the simulated range."""
    streams = _streams(txns, flows, series_rows, as_of, exclude)
    out = []
    for first in week_starts:
        last = first + timedelta(days=6)
        kinds: dict[str, int] = defaultdict(int)
        for t, flow, cents in streams.deposits:
            if first <= t.posted_date <= last:
                kinds[_DEPOSIT_KIND.get(flow.reason, "Other money in")] += cents
        aside = tuple(e for e in streams.events if e.cents > 0 and first <= e.day <= last)
        in_stream = tuple(sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))
        out.append(DepositWeek(first, last, in_stream, aside, sum(kinds.values())))
    return out


def recent_fee_rate(
    txns: Sequence[Txn], flows: Mapping[int, Flow], as_of: date, history_start: date | None,
    days: int = FEE_DAYS,
) -> float:  # fmt: skip
    """Bank fees as cents per day: the fees of the last 28 days (less any reversed) spread over
    those days. A stand-in until fees have their own model: they follow events such as an
    overdraft, which an average cannot see."""
    if history_start is None or history_start > as_of:
        return 0.0
    window = set(_days_in(as_of, history_start, days))
    total = 0
    for t in txns:
        if t.posted_date not in window:
            continue
        reason = flows[t.row_id].reason
        if reason in FEE_REASONS and t.amount_cents < 0:
            total += -t.amount_cents
        elif reason == FEE_REVERSAL and t.amount_cents > 0:
            total -= t.amount_cents
    return max(total, 0) / len(window)


def constant_path(per_day: float, horizon: int) -> list[int]:
    """Cumulative cents for a steady amount a day, rounded after summing."""
    return _cumulative([per_day] * horizon)


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
    kept = _streams(txns, flows, series_rows, as_of).spending
    rows = [r for r in kept if r[0].posted_date in in_window]
    sums: dict[str, list[float]] = {}
    for t, flow, cents in rows:
        kind = "Returns" if cents < 0 else _TYPE_OF_REASON.get(flow.reason, "Other")
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
