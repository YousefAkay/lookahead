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

Everything else (recurring payments and their amounts, big one-time rows left out) is as in the
point forecast. The result is shown with and without the irregular deposits.

The shortfall probability is the share of futures whose balance is below the safety buffer on
at least one day. It is what the futures say, not a tuned number.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from lookahead.forecast.schedule import OVERDUE_MARK, ScheduledItem
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


def sample_item_day(
    item: ScheduledItem, series: Stored | None, posted: Mapping[str, date], start: ForecastStart,
    rng: random.Random,
) -> date:  # fmt: skip
    """The day a placed payment falls on in one future."""
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


def _week_pool(
    spending: Mapping[date, int], deposits: Mapping[date, int], start: ForecastStart,
    history_start: date | None,
) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:  # fmt: skip
    """Every whole past week (7 days) that starts on the forecast's first weekday, within the
    last 13 weeks, as (spending by day, deposits by day)."""
    if history_start is None:
        return []
    as_of = start.data_last_updated
    day = max(history_start, as_of - timedelta(days=POOL_DAYS - 1))
    pool = []
    while day + timedelta(days=BLOCK - 1) <= as_of:
        if day.weekday() == start.first_day.weekday():
            days = [day + timedelta(days=k) for k in range(BLOCK)]
            pool.append((tuple(spending.get(d, 0) for d in days),
                         tuple(deposits.get(d, 0) for d in days)))  # fmt: skip
        day += timedelta(days=1)
    return pool


def _percentile(values: list[int], share: float) -> int:
    ordered = sorted(values)
    return ordered[round(share * (len(ordered) - 1))]


def simulate(
    start: ForecastStart, items: Sequence[ScheduledItem], series: Sequence[Stored],
    posted: Mapping[str, date], spending_daily: Mapping[date, int],
    deposits_daily: Mapping[date, int], history_start: date | None, buffer_cents: int,
    fees_per_day: float = 0.0, futures: int = N_FUTURES, seed: int = 0,
) -> Simulation:  # fmt: skip
    """`futures` simulated balance paths over the forecast days, with and without irregular
    deposits. The same seed gives the same answer."""
    rng = random.Random(seed)
    horizon = (start.last_day - start.first_day).days + 1
    days = tuple(start.first_day + timedelta(days=k) for k in range(horizon))
    by_key = {s.key: s for s in series}
    pool = _week_pool(spending_daily, deposits_daily, start, history_start)
    weeks = -(-horizon // BLOCK)
    fees = constant_path(fees_per_day, horizon)
    with_dep: list[list[int]] = []
    without: list[list[int]] = []
    for _ in range(futures):
        scheduled = [0] * horizon
        for item in items:
            day = sample_item_day(item, by_key.get(item.series_key), posted, start, rng)
            k = (day - start.first_day).days
            if 0 <= k < horizon:
                scheduled[k] += item.cents
        spend_daily: list[int] = []
        dep_daily: list[int] = []
        for _week in range(weeks):
            spend, dep = rng.choice(pool) if pool else ((0,) * BLOCK, (0,) * BLOCK)
            spend_daily += spend
            dep_daily += dep
        money = spent = money_in = 0
        a: list[int] = []
        b: list[int] = []
        for k in range(horizon):
            money += scheduled[k]
            spent += spend_daily[k]
            money_in += dep_daily[k]
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
        share(with_dep, buffer_cents), share(without, buffer_cents),
        share(with_dep, 0), share(without, 0),
        _percentile([min(p) for p in with_dep], 0.5) if with_dep else start.balance_cents,
        _percentile([min(p) for p in without], 0.5) if without else start.balance_cents,
        len(pool),
    )  # fmt: skip
