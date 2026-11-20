"""The projected balance (3b step 4): where it starts, the recurring payments placed on their
days, and everyday spending from a baseline.

    balance(day) = balance at the start + scheduled money through that day
                   - cumulative everyday spending through that day

This is a point forecast (an estimate, one path). Ranges from simulated spending paths come
later. Everything uses only rows posted by the forecast date and the decisions made by then.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from typing import Any

from lookahead.flows import Flow, Txn, flow_types
from lookahead.forecast.recurring_filter import (
    DEFAULT_FIX,
    RecurringFix,
    possibly_ended_keys,
    weekly_card_keys,
)
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
    SpendingRows,
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
    spending_rows,
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
    OutflowPlacement,
    decisions_as_of,
    repeated_outflows,
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
    """The optional parts of the forecaster; the app uses the defaults. `lump_arrivals` and
    `outflow_placements` are off by default; `recurring_fix` is on by default (None is off).

    `lump_arrivals`: deposits set aside as large deposits (lumps) come back in the futures as
    random arrivals, at their observed rate over the last 365 days and with their observed sizes
    (DECISIONS.md, 2026-10-11, step 0)."""

    lump_arrivals: bool = False
    # `outflow_placements`: b) repeated large payments placed by default, `outflow_rule` choosing
    # the next date ("earliest", or "calendar": a year after the same-term payment, 2026-10-12).
    # `proposal_key` is the pseudonym key for senders and recipients (the forecast's own `key`
    # when None).
    outflow_placements: bool = False
    outflow_rule: str = "earliest"
    proposal_key: bytes | None = None
    # The recurring-placement fix (DECISIONS.md, 2026-10-12): (b) a series whose last payment is
    # more than 1.5 periods back is "possibly ended" and not placed (the default); (a) a weekly
    # series of card purchases is not placed and its rows go back to the everyday stream.
    # None: the whole fix is off.
    recurring_fix: RecurringFix | None = DEFAULT_FIX
    # The renewal draw (DECISIONS.md, 2026-10-12): the keeps-coming deposits arrive after gaps
    # drawn from the account's own past gaps, given the days since the last one, instead of at a
    # constant daily chance. Off by default: False is the old forecaster exactly.
    renewal: bool = False


DEFAULT_OPTIONS = ForecastOptions()


def settings_differences(
    saved: dict[str, Any], settings: dict[str, Any], prefix: str = ""
) -> list[str]:
    """What differs between the saved settings and the new ones, one line each, by name."""
    out = []
    for key in sorted({*saved, *settings}):
        a, b = saved.get(key, "(not recorded)"), settings.get(key, "(not recorded)")
        if isinstance(a, dict) and isinstance(b, dict):
            out += settings_differences(a, b, f"{prefix}{key}.")
        elif a != b:
            out.append(f"{prefix}{key}: saved {a}, now {b}")
    return out


def forecaster_settings(
    options: ForecastOptions | None = None, keeps: KeepsComing | None = DEFAULT_KEEPS
) -> dict[str, Any]:  # fmt: skip
    """The settings that decide what a forecast is, as plain values, for the meta files of saved
    results (backtests, arms, Time Machine snapshots): the recurring fix, keeps-coming and the
    options. A saved run is continued only if these are the same, so changing a default can never
    mix old and new forecasts. `options=None` is the current default, read when this is called."""
    chosen = DEFAULT_OPTIONS if options is None else options
    return {
        "keeps": None if keeps is None else asdict(keeps),
        "recurring_fix": None if chosen.recurring_fix is None else asdict(chosen.recurring_fix),
        "lump_arrivals": chosen.lump_arrivals,
        "outflow_placements": chosen.outflow_placements,
        "outflow_rule": chosen.outflow_rule,
    }


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
    has_income: bool  # a placed income series exists (if not, no income is assumed)
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
    outflow_placements: tuple[OutflowPlacement, ...] = ()
    first_time_offers: tuple[FirstTimeOffer, ...] = ()
    # Series (a) or (b) chose not to place: (key, "weekly card purchases" or "possibly ended").
    not_placed: tuple[tuple[str, str], ...] = ()
    # The rows behind the everyday spending (for the budgets); nothing above depends on them.
    spending_rows: SpendingRows = SpendingRows((), frozenset())

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
    fix = options.recurring_fix
    card_keys: frozenset[str] = frozenset()
    if fix is not None and fix.weekly_card:  # (a): these rows are everyday spending again
        card_keys = weekly_card_keys(
            series,
            {fp: flows[row_of[fp]].reason for s in series for fp in s.evidence if fp in row_of},
        )
        in_series = in_series - {
            row_of[fp] for s in series if s.key in card_keys for fp in s.evidence if fp in row_of
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
    not_placed = set(card_keys)
    if fix is not None and fix.possibly_ended:  # (b): their rows stay in `in_series`
        not_placed |= possibly_ended_keys(series, posted, start.data_last_updated)
    placeable = [s for s in series if s.key not in not_placed]
    items = scheduled_items(
        placeable, posted, start, [p for p in planned if p.item_id not in received]
    )  # a received item is in the balance already: placing it again would count it twice
    proposed = _proposals(conn, account, as_of, txns, flows, in_series, start, rows, options, key)
    items = sorted([*items, *proposed.items], key=lambda i: (i.day, i.series_key))
    fee_rate = recent_fee_rate(txns, flows, start.data_last_updated, first_posted)
    fees = constant_path(fee_rate, horizon)
    arrivals = Arrivals()
    if keeps is not None:  # on by default; `keeps=None` is the old forecaster exactly
        placed = {row_of[fp] for p in proposed.placements for fp in p.evidence if fp in row_of}
        arrivals = keeps_coming_arrivals(
            txns, flows, in_series, start.data_last_updated, keeps,
            exclude=placed, renewal=options.renewal,
        )  # a placed payment is never drawn too  # fmt: skip
    simulation = inputs = None
    if futures > 0:
        deposits = deposits_by_day(txns, flows, in_series, start.data_last_updated)
        rate, sizes = (
            lump_arrivals(txns, flows, in_series, start.data_last_updated, first_posted,
                          exclude=arrivals.members)  # drawn once
            if options.lump_arrivals else (0.0, ())
        )  # fmt: skip
        inputs = SimulationInputs(
            start, tuple(items), tuple(series), posted, daily, deposits, first_posted,
            safety_buffer_cents(conn), fee_rate, futures, 0,
            arrivals_in_rate=arrivals.in_rate, arrivals_in_sizes=arrivals.in_sizes,
            arrivals_out_rate=arrivals.out_rate, arrivals_out_sizes=arrivals.out_sizes,
            arrivals_in_gaps=arrivals.in_gaps, arrivals_in_last=arrivals.in_last,
            lump_rate=rate, lump_sizes=sizes, item_windows=proposed.windows or {},
        )  # fmt: skip
        simulation = run(inputs, keep_paths=keep_paths)
    return Forecast(
        start, BASELINES[method], project_balance(start, items, spending, fees), items,
        any(s.direction == "in" and s.active and s.status != "rejected" for s in placeable),
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
        proposed.placements,
        proposed.offers,
        tuple((s.key, "weekly card purchases" if s.key in card_keys else "possibly ended")
              for s in series if s.key in not_placed and s.active and s.status != "rejected"),
        spending_rows(txns, flows, in_series, start.data_last_updated),
    )  # fmt: skip


@dataclass(frozen=True)
class _Proposed:
    items: tuple[ScheduledItem, ...] = ()  # placed payments
    windows: dict[str, tuple[int, int]] | None = None  # series key -> days before, after
    placements: tuple[OutflowPlacement, ...] = ()
    offers: tuple[FirstTimeOffer, ...] = ()


def _proposals(
    conn: sqlite3.Connection, account: str, as_of: date | None, txns: list[Txn],
    flows: dict[int, Flow], in_series: set[int], start: ForecastStart, rows: list[Any],
    options: ForecastOptions, key: bytes | None,
) -> _Proposed:  # fmt: skip
    """b) as of the forecast date, with my decisions as of that date. Nothing when it is off.
    (a), expected lump income by sender, was retired on 2026-10-12: it failed on my account.)"""
    if not options.outflow_placements:
        return _Proposed(windows={})
    pkey = options.proposal_key if options.proposal_key is not None else key
    decided = decisions_as_of(conn, account, as_of)
    fingerprints = {r[0]: r[5] for r in rows}
    as_of_day = start.data_last_updated
    items: list[ScheduledItem] = []
    windows: dict[str, tuple[int, int]] = {}
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
    return _Proposed(tuple(items), windows, tuple(placed), tuple(offers))
