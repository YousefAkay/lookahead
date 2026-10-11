"""The variants of the recurring-placement fix, by name (DECISIONS.md, 2026-10-12).

off: the forecaster without the fix; b: possibly ended series are not placed; a: weekly series of
card purchases are not placed and their rows go back to the everyday stream; both: a and b.
"""

from lookahead.forecast.path import ForecastOptions
from lookahead.forecast.recurring_filter import RecurringFix

FIXES: dict[str, RecurringFix | None] = {
    "off": None,
    "b": RecurringFix(possibly_ended=True, weekly_card=False),
    "a": RecurringFix(possibly_ended=False, weekly_card=True),
    "both": RecurringFix(possibly_ended=True, weekly_card=True),
}


def fix_options(name: str) -> ForecastOptions:
    return ForecastOptions(recurring_fix=FIXES[name])
