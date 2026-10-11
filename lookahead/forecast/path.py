"""The projected balance (3b step 4): where it starts, the recurring payments placed on their
days, and everyday spending from a baseline.

    balance(day) = balance at the start + scheduled money through that day
                   - cumulative everyday spending through that day

This is a point forecast (an estimate, one path). Ranges from simulated spending paths come
later. Everything uses only rows posted by the forecast date and the decisions made by then.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from lookahead.flows import Flow, Txn, flow_types
from lookahead.forecast.schedule import ScheduledItem, scheduled_items
from lookahead.forecast.simulate import (
    N_FUTURES,
    Simulation,
    SimulationInputs,
    pool_week_starts,
    run,
)
from lookahead.forecast.spending import (
    DEFAULT_KEEPS,
    Arrivals,
    BigEvent,
    BreakdownRow,
    DepositWeek,
    KeepsComing,
    big_events,
    constant_path,
    deposit_weeks,
    deposits_by_day,
    keeps_coming_arrivals,
    lump_arrivals,
    recent_average_path,
    recent_fee_rate,
    spending_breakdown,
    spending_by_day,
    weekday_average_path,
)
from lookahead.forecast.start import HORIZON_DAYS, ForecastStart, forecast_start
from lookahead.planned import PlannedItem, planned_as_of, received_planned
from lookahead.proposals import (
    CONFIRMED,
    JITTER_DAYS,
    LOOKS_RIGHT,
    NOT_THIS_TIME,
    FirstTimeOffer,
    LumpProposal,
    OutflowPlacement,
    decisions_as_of,
    lump_proposals,
    repeated_outflows,
    sender_rows,
)
from lookahead.recurring_store import Stored, series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.settings import DEFAULT_BUFFER_CENTS, safety_buffer_cents

BASELINES = {
    "recent": "average of the last 28 days",
    "weekday": "average for each weekday over the last 8 weeks",
}


@dataclass(frozen=True)
class ForecastOptions:
    """Experimental parts of the forecaster, all off by default; the app uses the defaults.

    `lump_arrivals`: deposits set aside as large deposits (lumps) come back in the futures as
    random arrivals, at their observed rate over the last 365 days and with their observed sizes
    (DECISIONS.md, 2026-10-11, step 0)."""

    lump_arrivals: bool = False
    # Proposals (DECISIONS.md, 2026-10-11, step 2). `lump_proposals`: a) expected lump income by
    # sender, placed only once confirmed ("looks right" in the decision log, or every one with
    # `confirm_all_lumps`, or the keys in `confirmed_keys`, used by the evaluation).
    # `outflow_placements`: b) repeated large payments placed by default, `outflow_rule` choosing
    # the next date ("earliest", or "calendar": a year after the same-term payment, 2026-10-12).
    # `proposal_key` is the pseudonym key for senders and recipients (the forecast's own `key`
    # when None).
    lump_proposals: bool = False
    confirm_all_lumps: bool = False
    confirmed_keys: frozenset[str] = frozenset()
    outflow_placements: bool = False
    outflow_rule: str = "earliest"
    proposal_key: bytes | None = None


DEFAULT_OPTIONS = ForecastOptions()


@dataclass(frozen=True)
class ForecastDay:
    day: date
    balance_cents: int  # projected end-of-day balance
    scheduled_cents: int  # recurring money in (+) or out (-) that day
    spending_cents: int  # everyday spending that day
    fees_cents: int = 0  # bank fees that day (their recent daily average)


@dataclass(frozen=True)
class Forecast:
    start: ForecastStart
    method: str  # how everyday spending was estimated
    days: list[ForecastDay]
    items: list[ScheduledItem]
    has_income: bool  # an income series exists (if not, no income is assumed)
    buffer_cents: int = DEFAULT_BUFFER_CENTS
    series: tuple[Stored, ...] = ()
    breakdown: tuple[BreakdownRow, ...] = ()  # what the weekday baseline's history is made of
    big_events: tuple[BigEvent, ...] = ()  # big one-time rows of the last 13 weeks: not forecast
    fees_per_day: float = 0.0  # cents a day, from the last 28 days
    simulation: Simulation | None = None  # the simulated futures (a range, not one path)
    planned: tuple[PlannedItem, ...] = ()  # every planned item I have entered (by the date)
    deposit_weeks: tuple[DepositWeek, ...] = ()  # the weeks the futures draw deposits from
    inputs: SimulationInputs | None = None  # what the futures were made from (for scenarios)
    received: frozenset[int] = frozenset()  # planned items a real transaction has matched
    arrivals: Arrivals = Arrivals()  # big money that keeps coming (None: off)
    # Proposals, only when their options are on (step 2): what was detected at this date.
    lump_proposals: tuple[LumpProposal, ...] = ()
    outflow_placements: tuple[OutflowPlacement, ...] = ()
    first_time_offers: tuple[FirstTimeOffer, ...] = ()

    @property
    def large_deposits(self) -> tuple[BigEvent, ...]:
        """Big deposits that were not paid straight back out: set aside, not forecast."""
        return tuple(e for e in self.big_events if e.group == "Large deposit")

    @property
    def lowest(self) -> tuple[date, int]:
        """The day with the lowest projected balance, and that balance."""
        low = min(self.days, key=lambda d: (d.balance_cents, d.day))
        return low.day, low.balance_cents

    @property
    def first_below_buffer(self) -> date | None:
        """The first day the projected balance is below the safety buffer, if any."""
        return next((d.day for d in self.days if d.balance_cents < self.buffer_cents), None)


def project_balance(
    start: ForecastStart, items: Sequence[ScheduledItem], spending_cumulative: Sequence[int],
    fees_cumulative: Sequence[int] | None = None,
) -> list[ForecastDay]:  # fmt: skip
    """One entry per forecast day. `spending_cumulative[k]` is the everyday spending, and
    `fees_cumulative[k]` the bank fees, from the first forecast day through day k."""
    count = len(spending_cumulative)
    fees = list(fees_cumulative) if fees_cumulative is not None else [0] * count
    scheduled = [0] * count
    for it in items:
        k = (it.day - start.first_day).days
        if 0 <= k < count:
            scheduled[k] += it.cents
    out, money, spent_before, fees_before = [], 0, 0, 0
    for k, spent_through in enumerate(spending_cumulative):
        money += scheduled[k]
        out.append(
            ForecastDay(
                start.first_day + timedelta(days=k),
                start.balance_cents + money - spent_through - fees[k],
                scheduled[k],
                spent_through - spent_before,
                fees[k] - fees_before,
            )
        )
        spent_before, fees_before = spent_through, fees[k]
    return out


def build_forecast(
    conn: sqlite3.Connection, account: str, method: str = "weekday", as_of: date | None = None,
    key: bytes | None = None, horizon_days: int = HORIZON_DAYS, futures: int = N_FUTURES,
    keep_paths: bool = False, options: ForecastOptions = DEFAULT_OPTIONS,
    keeps: KeepsComing | None = DEFAULT_KEEPS,
) -> Forecast | None:  # fmt: skip
    """A forecast from what is stored, with no confirmations needed: every active series is used
    as detected unless I changed it. `as_of=None` is a live forecast (all rows, all decisions);
    a date replays it as it would have looked then. None if there are no rows by then."""
    if method not in BASELINES:
        raise ValueError(f"unknown baseline {method!r}; choose from {sorted(BASELINES)}")
    start = forecast_start(conn, account, as_of, horizon_days)
    if start is None:
        return None
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, start.data_last_updated.isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]
    posted = {r[5]: date.fromisoformat(r[1]) for r in rows}
    row_of = {r[5]: r[0] for r in rows}
    series, _ = series_as_of(conn, account, as_of, key)
    flows = flow_types(txns, load_user_decisions(conn, as_of))
    in_series = {
        row_of[fp] for s in series if s.status != "rejected" for fp in s.evidence if fp in row_of
    }
    daily = spending_by_day(txns, flows, in_series, start.data_last_updated)
    first_posted = min(t.posted_date for t in txns)
    horizon = (start.last_day - start.first_day).days + 1
    if method == "recent":
        spending = recent_average_path(daily, start.data_last_updated, first_posted, horizon)
    else:
        spending = weekday_average_path(
            daily, start.data_last_updated, first_posted, start.first_day, horizon
        )
    planned = planned_as_of(conn, account, as_of)
    received = received_planned(conn, account, planned, start.data_last_updated)
    items = scheduled_items(
        series, posted, start, [p for p in planned if p.item_id not in received]
    )  # a received item is in the balance already: placing it again would count it twice
    proposed = _proposals(conn, account, as_of, txns, flows, in_series, start, rows, options, key)
    items = sorted([*items, *proposed.items], key=lambda i: (i.day, i.series_key))
    fee_rate = recent_fee_rate(txns, flows, start.data_last_updated, first_posted)
    fees = constant_path(fee_rate, horizon)
    arrivals = Arrivals()
    if keeps is not None:  # on by default; `keeps=None` is the old forecaster exactly
        placed = {row_of[fp] for p in proposed.placements for fp in p.evidence if fp in row_of}
        arrivals = keeps_coming_arrivals(
            txns, flows, in_series, start.data_last_updated, keeps, exclude=placed
        )  # a placed payment is never drawn too
    simulation = inputs = None
    if futures > 0:
        deposits = deposits_by_day(txns, flows, in_series, start.data_last_updated)
        rate, sizes = (
            lump_arrivals(txns, flows, in_series, start.data_last_updated, first_posted,
                          exclude=proposed.out_of_pool | arrivals.members)  # drawn once
            if options.lump_arrivals else (0.0, ())
        )  # fmt: skip
        inputs = SimulationInputs(
            start, tuple(items), tuple(series), posted, daily, deposits, first_posted,
            safety_buffer_cents(conn), fee_rate, futures, 0,
            arrivals_in_rate=arrivals.in_rate, arrivals_in_sizes=arrivals.in_sizes,
            arrivals_out_rate=arrivals.out_rate, arrivals_out_sizes=arrivals.out_sizes,
            lump_rate=rate, lump_sizes=sizes, item_windows=proposed.windows or {},
        )  # fmt: skip
        simulation = run(inputs, keep_paths=keep_paths)
    return Forecast(
        start, BASELINES[method], project_balance(start, items, spending, fees), items,
        any(s.direction == "in" and s.active and s.status != "rejected" for s in series),
        safety_buffer_cents(conn), tuple(series),
        tuple(spending_breakdown(txns, flows, in_series, start.data_last_updated, first_posted)),
        tuple(big_events(txns, flows, in_series, start.data_last_updated, arrivals.members)),
        fee_rate,
        simulation,
        tuple(planned),
        tuple(deposit_weeks(txns, flows, in_series, start.data_last_updated,
                            pool_week_starts(start, first_posted), arrivals.members)),
        inputs,
        received,
        arrivals,
        proposed.lumps,
        proposed.placements,
        proposed.offers,
    )  # fmt: skip


@dataclass(frozen=True)
class _Proposed:
    items: tuple[ScheduledItem, ...] = ()  # confirmed lumps and placed payments
    windows: dict[str, tuple[int, int]] | None = None  # series key -> days before, after
    out_of_pool: frozenset[int] = frozenset()  # lumps that are not drawn at random
    lumps: tuple[LumpProposal, ...] = ()
    placements: tuple[OutflowPlacement, ...] = ()
    offers: tuple[FirstTimeOffer, ...] = ()


def _proposals(
    conn: sqlite3.Connection, account: str, as_of: date | None, txns: list[Txn],
    flows: dict[int, Flow], in_series: set[int], start: ForecastStart, rows: list[Any],
    options: ForecastOptions, key: bytes | None,
) -> _Proposed:  # fmt: skip
    """a) and b) as of the forecast date, with my decisions as of that date (step 2). Nothing
    when both options are off."""
    if not (options.lump_proposals or options.outflow_placements):
        return _Proposed(windows={})
    pkey = options.proposal_key if options.proposal_key is not None else key
    decided = decisions_as_of(conn, account, as_of)
    fingerprints = {r[0]: r[5] for r in rows}
    as_of_day = start.data_last_updated
    items: list[ScheduledItem] = []
    windows: dict[str, tuple[int, int]] = {}
    out_of_pool: set[int] = set()
    found: list[LumpProposal] = []
    if options.lump_proposals:
        found, lumps = lump_proposals(txns, flows, in_series, as_of_day, start.first_day,
                                      start.last_day, pkey, fingerprints)  # fmt: skip
        for p in found:
            action = decided.get(p.key)
            wanted = (
                action == LOOKS_RIGHT or options.confirm_all_lumps
                or p.key in options.confirmed_keys
            )  # fmt: skip
            confirmed = wanted and action != NOT_THIS_TIME
            if confirmed or action == NOT_THIS_TIME:
                assert pkey is not None  # a proposal exists only with a key
                out_of_pool |= sender_rows(lumps, p.sender, pkey)
            if confirmed:
                items.append(ScheduledItem(
                    p.expected, p.typical_cents, p.key, "Expected deposit", "confirmed",
                    "expected lump income, confirmed: a day inside its window",
                ))  # fmt: skip
                windows[p.key] = ((p.expected - p.first).days, (p.last - p.expected).days)
        found = [replace(p, status=CONFIRMED) if p.key in {i.series_key for i in items} else p
                 for p in found]  # fmt: skip
    placed: list[OutflowPlacement] = []
    offers: list[FirstTimeOffer] = []
    if options.outflow_placements:
        placed, offers = repeated_outflows(
            txns, flows, in_series, as_of_day, start.first_day, start.last_day, pkey,
            fingerprints, options.outflow_rule,  # type: ignore[arg-type]
            load_user_decisions(conn, as_of),
        )  # fmt: skip
        kept = []
        for p in placed:
            action = decided.get(p.key)
            if action == NOT_THIS_TIME:
                continue  # that occurrence is skipped
            if action == LOOKS_RIGHT:
                p = replace(p, status=CONFIRMED)
            else:
                windows[p.key] = (JITTER_DAYS, JITTER_DAYS)
            items.append(ScheduledItem(
                p.day, p.cents, p.key, "Repeated large payment",
                "confirmed" if p.status == CONFIRMED else "detected",
                f"repeated large payment, {p.status}: {p.rule}",
            ))  # fmt: skip
            kept.append(p)
        placed = kept
    return _Proposed(tuple(items), windows, frozenset(out_of_pool), tuple(found), tuple(placed),
                     tuple(offers))  # fmt: skip
