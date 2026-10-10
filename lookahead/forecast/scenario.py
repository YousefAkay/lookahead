"""What-if scenarios (3d): change one thing, run the same futures again, compare.

The original and the scenario use the same random draws (every week and every payment has its
own stream, see `simulate`), so each difference between them is the change itself and not luck.
A scenario is conditional: it says what would happen if the change were made, not what will
happen. Nothing here is saved; the controls are:

- spending less (a percent, or dollars a week) for a period;
- a planned purchase or deposit for this scenario only (a saved planned item is a different
  thing, entered on the Forecast page);
- money in arriving N days later (income series and planned deposits);
- moving a bill or planned payment to another day;
- no other money expected: irregular deposits off (random lump arrivals too, when that option is
  on), so only income series, planned deposits and deposits added here count as money in.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta

from lookahead.forecast.path import Forecast
from lookahead.forecast.schedule import MOVED_NOTE, ScheduledItem
from lookahead.forecast.simulate import Simulation, SpendingCut, run

MAX_DELAY_DAYS = 60
WEEK = 7


@dataclass(frozen=True)
class SpendingChange:
    """Spend less from `first` to `last`: by a percent, or by cents a week."""

    first: date
    last: date
    percent: float | None = None
    per_week_cents: int | None = None

    def __post_init__(self) -> None:
        if (self.percent is None) == (self.per_week_cents is None):
            raise ValueError("give a percent or an amount a week, not both and not neither")
        if self.percent is not None and not 0 < self.percent <= 100:
            raise ValueError("a percent above 0 and at most 100")
        if self.per_week_cents is not None and self.per_week_cents <= 0:
            raise ValueError("an amount above zero")
        if self.last < self.first:
            raise ValueError("the period must not end before it starts")

    def cut(self) -> SpendingCut:
        per_day = None if self.per_week_cents is None else round(self.per_week_cents / WEEK)
        return SpendingCut(self.first, self.last, self.percent, per_day)


@dataclass(frozen=True)
class ExtraItem:
    """A purchase or a deposit added for this scenario only (money in is positive)."""

    day: date
    cents: int
    description: str

    def __post_init__(self) -> None:
        if self.cents == 0:
            raise ValueError("an amount other than zero is needed")
        if not self.description.strip():
            raise ValueError("say what it is")


@dataclass(frozen=True)
class MovedBill:
    """Move the payment of `series_key` that is placed on `from_day` to `to_day`."""

    series_key: str
    from_day: date
    to_day: date


@dataclass(frozen=True)
class Scenario:
    spending: SpendingChange | None = None
    extras: tuple[ExtraItem, ...] = ()
    delay_money_in_days: int = 0
    moved: tuple[MovedBill, ...] = ()
    no_other_money: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.delay_money_in_days <= MAX_DELAY_DAYS:
            raise ValueError(f"delay money in by 0 to {MAX_DELAY_DAYS} days")

    def describe(self) -> tuple[str, ...]:
        """The changes in plain words, one line each."""
        lines: list[str] = []
        if self.spending is not None:
            s = self.spending
            how = (
                f"{s.percent:g} percent"
                if s.percent is not None
                else f"${(s.per_week_cents or 0) / 100:,.2f} a week"
            )
            lines.append(f"Everyday spending lower by {how} from {s.first} to {s.last}.")
        for e in self.extras:
            way = "money in" if e.cents > 0 else "money out"
            lines.append(
                f"Added for this scenario only: {e.description}, {way} "
                f"${abs(e.cents) / 100:,.2f} on {e.day}."
            )
        if self.delay_money_in_days:
            lines.append(
                f"Money in arrives {self.delay_money_in_days} days later "
                "(income series and planned deposits)."
            )
        for m in self.moved:
            lines.append(f"A payment moved from {m.from_day} to {m.to_day}.")
        if self.no_other_money:
            lines.append(
                "No other money expected: irregular deposits are off; only income series, "
                "planned deposits and deposits added here count as money in."
            )
        return tuple(lines)


@dataclass(frozen=True)
class ScenarioResult:
    original: Simulation
    modified: Simulation
    changes: tuple[str, ...]

    @property
    def shortfall_change(self) -> float:
        """The change in the chance of dipping below the buffer (a share, not points)."""
        return self.modified.shortfall_probability - self.original.shortfall_probability

    @property
    def below_zero_change(self) -> float:
        return self.modified.below_zero_probability - self.original.below_zero_probability

    @property
    def lowest_change(self) -> int:
        """The change in the median lowest balance, in cents."""
        return self.modified.median_lowest_cents - self.original.median_lowest_cents

    @property
    def final_change(self) -> int:
        """The change in the middle future's balance on the last day, in cents."""
        return self.modified.p50[-1] - self.original.p50[-1]


def run_scenario(forecast: Forecast, scenario: Scenario) -> ScenarioResult:
    """The original futures and the futures with the scenario's changes, from the same draws."""
    inputs = forecast.inputs
    if inputs is None:
        raise ValueError("this forecast has no simulated futures to change")
    first_day = inputs.start.first_day
    items = list(inputs.items)
    moved_at: set[int] = set()
    for move in scenario.moved:
        found = next(
            (i for i, it in enumerate(items)
             if it.series_key == move.series_key and it.day == move.from_day and i not in moved_at),
            None,
        )  # fmt: skip
        if found is None:
            raise ValueError("that payment is not placed on that day")
        if move.to_day < first_day:
            raise ValueError("a payment cannot be moved to a day that has already passed")
        it = items[found]
        items[found] = replace(it, day=move.to_day, basis="entered", note=MOVED_NOTE,
                               slot=it.slot or it.day)  # fmt: skip
        moved_at.add(found)
    if scenario.delay_money_in_days:
        later = timedelta(days=scenario.delay_money_in_days)
        for i, it in enumerate(items):
            if it.cents > 0 and i not in moved_at:  # money in; a payment is never delayed
                items[i] = replace(it, day=it.day + later, slot=it.slot or it.day)
    for n, extra in enumerate(scenario.extras):
        items.append(ScheduledItem(extra.day, extra.cents, f"scenario|{n}", extra.description,
                                   "entered", "added in this scenario only"))  # fmt: skip
    modified_inputs = replace(
        inputs, items=tuple(items),
        deposits_daily={} if scenario.no_other_money else inputs.deposits_daily,
        lump_rate=0.0 if scenario.no_other_money else inputs.lump_rate,
    )  # fmt: skip
    cut = scenario.spending.cut() if scenario.spending is not None else None
    return ScenarioResult(run(inputs), run(modified_inputs, cut), scenario.describe())
