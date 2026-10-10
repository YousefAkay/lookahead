"""Simulated futures from the person's own history (3b): 500 of them, a range and not one path.

Each future is built from three things, all using only rows posted on or before the forecast
date (rule zero):
- Weeks. For each week ahead it draws one whole past week (7 days, the same weekday to start as
  the forecast week) from the last 13 weeks, and takes both the everyday spending and the
  irregular deposits of that week. They come from the same week on purpose: a week that was
  heavy because money came in stays that way. Draws are not rescaled to the point forecast.
- Dates. A rent payment is drawn from inside its window, weighted by my own past rent dates. A
  payment of a series that is only detected moves by one of its own past offsets (days early or
  late). Confirmed and entered dates stay where they are.
- Fees at their recent daily average, the same in every future.
- Random lump arrivals, only when that option is on (off by default; DECISIONS.md 2026-10-11):
  each forecast day gets a lump with the observed daily rate of lumps, its size drawn from the
  observed lump sizes, from the future's own "lumps" stream. Lumps are irregular deposits.

Common random numbers: every week and every placed payment draws from its own stream, named by
the seed, the future and what is being drawn. Changing one payment, or the spending in a period,
never changes the draws of anything else, so a scenario and the original can be compared draw
for draw and the difference is the change itself, not luck.

Everything else (recurring payments and their amounts, big one-time rows left out) is as in the
point forecast. The result is shown with and without the irregular deposits.

The shortfall probability is the share of futures whose balance is below the safety buffer on
at least one day. It is what the futures say, not a tuned number.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from lookahead.forecast.schedule import MOVED_NOTE, OVERDUE_MARK, ScheduledItem
from lookahead.forecast.spending import constant_path
from lookahead.forecast.start import ForecastStart
from lookahead.recurring_store import Stored, rent_rule_of
from lookahead.rent import cycle_start, draw_rent_date

N_FUTURES = 500
POOL_DAYS = 91  # the last 13 weeks
BLOCK = 7


@dataclass(frozen=True)
class Simulation:
    days: tuple[date, ...]
    futures: int
    p10: tuple[int, ...]  # with irregular deposits: the balance 10 in 100 futures stay under
    p50: tuple[int, ...]  # the median balance
    p90: tuple[int, ...]
    median_without: tuple[int, ...]  # the median balance if no irregular deposit comes in
    shortfall_probability: float  # share of futures below the buffer on some day
    shortfall_probability_without: float
    below_zero_probability: float
    below_zero_probability_without: float
    median_lowest_cents: int  # the median over futures of each future's lowest balance
    median_lowest_without_cents: int
    weeks_drawn_from: int  # how many different past weeks the futures could draw
    lowest_cents: tuple[int, ...] = ()  # each future's lowest balance (with deposits), sorted
    paths: tuple[tuple[int, ...], ...] = ()  # each future's daily balance, only when asked for


def sample_item_day(
    item: ScheduledItem, series: Stored | None, posted: Mapping[str, date], start: ForecastStart,
    rng: random.Random,
) -> date:  # fmt: skip
    """The day a placed payment falls on in one future."""
    if item.note == MOVED_NOTE:
        return item.day  # a scenario put it here: it stays here
    as_of = start.data_last_updated
    rule = rent_rule_of(series) if series is not None else None
    if rule is not None and series is not None and series.direction == "out":
        if OVERDUE_MARK in item.note:
            return item.day  # expected on the first day: late, not scattered
        past = [posted[fp] for fp in series.evidence if fp in posted and posted[fp] <= as_of]
        cycle = cycle_start(rule, item.day)
        return draw_rent_date(
            rule, cycle.year, cycle.month, past, as_of, rng, not_before=start.first_day
        )
    if item.basis == "detected" and series is not None and series.offsets:
        moved = item.day + timedelta(days=rng.choice(series.offsets))
        return max(moved, start.first_day)
    return item.day


def pool_week_starts(start: ForecastStart, history_start: date | None) -> list[date]:
    """The first day of every whole past week (7 days) that starts on the forecast's first
    weekday, within the last 13 weeks: the weeks the futures can draw from, oldest first."""
    if history_start is None:
        return []
    as_of = start.data_last_updated
    day = max(history_start, as_of - timedelta(days=POOL_DAYS - 1))
    starts = []
    while day + timedelta(days=BLOCK - 1) <= as_of:
        if day.weekday() == start.first_day.weekday():
            starts.append(day)
        day += timedelta(days=1)
    return starts


def _week_pool(
    spending: Mapping[date, int], deposits: Mapping[date, int], start: ForecastStart,
    history_start: date | None,
) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:  # fmt: skip
    """Each of those weeks as (spending by day, deposits by day)."""
    pool = []
    for first in pool_week_starts(start, history_start):
        days = [first + timedelta(days=k) for k in range(BLOCK)]
        pool.append((tuple(spending.get(d, 0) for d in days),
                     tuple(deposits.get(d, 0) for d in days)))  # fmt: skip
    return pool


def _percentile(values: list[int], share: float) -> int:
    ordered = sorted(values)
    return ordered[round(share * (len(ordered) - 1))]


@dataclass(frozen=True)
class SpendingCut:
    """Spend less on the days from `first` to `last`: by a percent of each day's spending, or by
    a number of cents a day (never below zero). Money coming back (a return) is left alone."""

    first: date
    last: date
    percent: float | None = None
    cents_per_day: int | None = None

    def apply(self, day: date, spent: int) -> int:
        if spent <= 0 or not self.first <= day <= self.last:
            return spent
        if self.percent is not None:
            return round(spent * (1 - self.percent / 100))
        return max(spent - (self.cents_per_day or 0), 0)


def _stream(seed: int, future: int, *parts: object) -> random.Random:
    """A random stream named by what it is for. A string seed is hashed the same way every run."""
    return random.Random("|".join(str(p) for p in (seed, future, *parts)))


@dataclass(frozen=True)
class SimulationInputs:
    """Everything `simulate` needs, kept so a scenario can change a part and run it again."""

    start: ForecastStart
    items: tuple[ScheduledItem, ...]
    series: tuple[Stored, ...]
    posted: Mapping[str, date]
    spending_daily: Mapping[date, int]
    deposits_daily: Mapping[date, int]
    history_start: date | None
    buffer_cents: int
    fees_per_day: float = 0.0
    futures: int = N_FUTURES
    seed: int = 0
    # Big money that keeps coming (DECISIONS.md, 2026-10-11): random arrivals; off at rate 0
    arrivals_in_rate: float = 0.0
    arrivals_in_sizes: tuple[int, ...] = ()
    arrivals_out_rate: float = 0.0
    arrivals_out_sizes: tuple[int, ...] = ()
    # Random lump arrivals (an option, off by default): the chance of a lump on any forecast day
    # and the observed lump sizes in cents. A rate of 0 draws nothing.
    lump_rate: float = 0.0
    lump_sizes: tuple[int, ...] = ()
    # Proposals (an option, off by default): series key -> (days before, days after) its placed
    # day inside which the day is drawn uniformly in each future. Empty: nothing changes.
    item_windows: Mapping[str, tuple[int, int]] = field(default_factory=dict)


def simulate(
    start: ForecastStart, items: Sequence[ScheduledItem], series: Sequence[Stored],
    posted: Mapping[str, date], spending_daily: Mapping[date, int],
    deposits_daily: Mapping[date, int], history_start: date | None, buffer_cents: int,
    fees_per_day: float = 0.0, futures: int = N_FUTURES, seed: int = 0,
    spending_cut: SpendingCut | None = None,
) -> Simulation:  # fmt: skip
    """`futures` simulated balance paths over the forecast days, with and without irregular
    deposits. The same seed gives the same answer."""
    return run(
        SimulationInputs(start, tuple(items), tuple(series), posted, spending_daily,
                         deposits_daily, history_start, buffer_cents, fees_per_day, futures,
                         seed),
        spending_cut,
    )  # fmt: skip


def _arrivals(
    seed: int, future: int, name: str, rate: float, sizes: tuple[int, ...], horizon: int
) -> list[int]:  # fmt: skip
    """Each day gets one arrival with chance `rate`, its size drawn from the observed `sizes`.
    Its own random stream, named by what it is for, so no other draw moves."""
    out = [0] * horizon
    if rate > 0 and sizes:
        stream = _stream(seed, future, name)
        for k in range(horizon):
            if stream.random() < rate:
                out[k] = stream.choice(sizes)
    return out


def run(
    inputs: SimulationInputs, spending_cut: SpendingCut | None = None, keep_paths: bool = False
) -> Simulation:  # fmt: skip
    """Run the futures for these inputs, optionally with a cut in everyday spending.
    `keep_paths` keeps every future's daily balance (with deposits), for the calibration step;
    the app does not ask for them."""
    start, futures, seed = inputs.start, inputs.futures, inputs.seed
    horizon = (start.last_day - start.first_day).days + 1
    days = tuple(start.first_day + timedelta(days=k) for k in range(horizon))
    by_key = {s.key: s for s in inputs.series}
    pool = _week_pool(inputs.spending_daily, inputs.deposits_daily, start, inputs.history_start)
    weeks = -(-horizon // BLOCK)
    fees = constant_path(inputs.fees_per_day, horizon)
    with_dep: list[list[int]] = []
    without: list[list[int]] = []
    for future in range(futures):
        scheduled = [0] * horizon
        for item in inputs.items:
            stream = _stream(seed, future, "item", item.series_key, item.slot or item.day)
            window = inputs.item_windows.get(item.series_key)
            if window is not None and item.note != MOVED_NOTE:
                offset = stream.randint(-window[0], window[1])
                day = max(item.day + timedelta(days=offset), start.first_day)
            else:
                day = sample_item_day(
                    item, by_key.get(item.series_key), inputs.posted, start, stream
                )
            k = (day - start.first_day).days
            if 0 <= k < horizon:
                scheduled[k] += item.cents
        arrive_in = _arrivals(seed, future, "arrivals-in", inputs.arrivals_in_rate,
                              inputs.arrivals_in_sizes, horizon)  # fmt: skip
        arrive_out = _arrivals(seed, future, "arrivals-out", inputs.arrivals_out_rate,
                               inputs.arrivals_out_sizes, horizon)  # fmt: skip
        weeks_stream = _stream(seed, future, "weeks")
        spend_daily: list[int] = []
        dep_daily: list[int] = []
        for _week in range(weeks):
            spend, dep = weeks_stream.choice(pool) if pool else ((0,) * BLOCK, (0,) * BLOCK)
            spend_daily += spend
            dep_daily += dep
        if inputs.lump_rate > 0 and inputs.lump_sizes:
            lumps = _stream(seed, future, "lumps")  # its own stream: no other draw moves
            for k in range(horizon):
                if lumps.random() < inputs.lump_rate:  # at most one lump a day
                    dep_daily[k] += lumps.choice(inputs.lump_sizes)
        money = spent = money_in = 0
        a: list[int] = []
        b: list[int] = []
        for k in range(horizon):
            money += scheduled[k]
            spend_today = spend_daily[k]
            if spending_cut is not None:
                spend_today = spending_cut.apply(days[k], spend_today)
            spent += spend_today + arrive_out[k]  # a big payment is spending, never cut
            money_in += dep_daily[k] + arrive_in[k]
            base = start.balance_cents + money - spent - fees[k]
            a.append(base + money_in)
            b.append(base)
        with_dep.append(a)
        without.append(b)

    def share(paths: list[list[int]], limit: int) -> float:
        return sum(min(p) < limit for p in paths) / len(paths) if paths else 0.0

    def at(paths: list[list[int]], q: float) -> tuple[int, ...]:
        return tuple(_percentile([p[k] for p in paths], q) for k in range(horizon))

    return Simulation(
        days, futures, at(with_dep, 0.1), at(with_dep, 0.5), at(with_dep, 0.9), at(without, 0.5),
        share(with_dep, inputs.buffer_cents), share(without, inputs.buffer_cents),
        share(with_dep, 0), share(without, 0),
        _percentile([min(p) for p in with_dep], 0.5) if with_dep else start.balance_cents,
        _percentile([min(p) for p in without], 0.5) if without else start.balance_cents,
        len(pool), tuple(sorted(min(p) for p in with_dep)),
        tuple(tuple(p) for p in with_dep) if keep_paths else (),
    )  # fmt: skip
