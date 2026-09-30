"""Tests for calendar rules: month-end clamping, holidays and business-day shifts."""

from datetime import date

import pytest

from lookahead.calendar_rules import (
    holidays,
    is_business_day,
    is_card_posting_day,
    monthly_date,
    next_card_posting_day,
    shift_to_business_day,
)

# --- monthly_date: clamp the day to the month's length ---


@pytest.mark.parametrize(
    ("year", "month", "day", "expected"),
    [
        (2026, 1, 15, date(2026, 1, 15)),  # normal day, unchanged
        (2026, 6, 31, date(2026, 6, 30)),  # June has 30 days
        (2026, 2, 31, date(2026, 2, 28)),  # February, non-leap year
        (2028, 2, 30, date(2028, 2, 29)),  # February, leap year
        (2026, 12, 31, date(2026, 12, 31)),  # last day of a 31-day month
    ],
)
def test_monthly_date_clamps_to_month_length(
    year: int, month: int, day: int, expected: date
) -> None:
    assert monthly_date(year, month, day) == expected


@pytest.mark.parametrize("day", [0, 32])
def test_monthly_date_rejects_impossible_days(day: int) -> None:
    with pytest.raises(ValueError):
        monthly_date(2026, 1, day)


# --- holidays: moving dates computed correctly ---


@pytest.mark.parametrize(
    "good_friday",
    [date(2024, 3, 29), date(2025, 4, 18), date(2026, 4, 3)],
)
def test_good_friday(good_friday: date) -> None:
    assert good_friday in holidays(good_friday.year)


def test_monday_holidays_2026() -> None:
    h = holidays(2026)
    assert date(2026, 5, 18) in h  # Victoria Day: Monday before May 25
    assert date(2026, 9, 7) in h  # Labour Day: 1st Monday of September
    assert date(2026, 10, 12) in h  # Thanksgiving: 2nd Monday of October


def test_victoria_day_when_may_24_is_a_saturday() -> None:
    assert date(2025, 5, 19) in holidays(2025)


def test_easter_monday_is_not_a_holiday() -> None:
    assert date(2026, 4, 6) not in holidays(2026)


# --- holidays: fixed dates that fall on a weekend move to a weekday ---


def test_canada_day_on_sunday_is_observed_monday() -> None:
    h = holidays(2029)  # July 1, 2029 is a Sunday
    assert date(2029, 7, 2) in h


def test_christmas_on_saturday_pushes_both_christmas_and_boxing_day() -> None:
    h = holidays(2027)  # Dec 25, 2027 is a Saturday
    assert date(2027, 12, 27) in h  # Christmas observed Monday
    assert date(2027, 12, 28) in h  # Boxing Day observed Tuesday


def test_boxing_day_on_saturday_is_observed_monday() -> None:
    h = holidays(2026)  # Dec 25, 2026 is a Friday, Dec 26 a Saturday
    assert date(2026, 12, 25) in h
    assert date(2026, 12, 28) in h


# --- is_business_day ---


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (date(2026, 6, 10), True),  # ordinary Wednesday
        (date(2026, 6, 13), False),  # Saturday
        (date(2026, 6, 14), False),  # Sunday
        (date(2026, 7, 1), False),  # Canada Day, a Wednesday
        (date(2026, 4, 3), False),  # Good Friday
    ],
)
def test_is_business_day(d: date, expected: bool) -> None:
    assert is_business_day(d) is expected


# --- shift_to_business_day: paycheques move earlier, bills move later ---


def test_business_day_does_not_move() -> None:
    wednesday = date(2026, 6, 10)
    assert shift_to_business_day(wednesday, "earlier") == wednesday
    assert shift_to_business_day(wednesday, "later") == wednesday


def test_saturday_payday_moves_to_friday() -> None:
    assert shift_to_business_day(date(2026, 6, 13), "earlier") == date(2026, 6, 12)


def test_sunday_bill_moves_to_monday() -> None:
    assert shift_to_business_day(date(2026, 6, 14), "later") == date(2026, 6, 15)


def test_shift_skips_a_holiday_next_to_a_weekend() -> None:
    # Saturday Apr 4, 2026, paid earlier: skip Good Friday (Apr 3) to Thursday.
    assert shift_to_business_day(date(2026, 4, 4), "earlier") == date(2026, 4, 2)
    # Labour Day Monday Sep 7, 2026, bill later: Tuesday.
    assert shift_to_business_day(date(2026, 9, 7), "later") == date(2026, 9, 8)


def test_shift_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError):
        shift_to_business_day(date(2026, 6, 13), "sideways")  # type: ignore[arg-type]


# --- Card posting days: Monday to Saturday, not a holiday ---


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (date(2026, 6, 13), True),  # Saturday: card purchases post
        (date(2026, 6, 14), False),  # Sunday: never
        (date(2026, 7, 1), False),  # Canada Day
        (date(2026, 12, 26), True),  # Saturday Boxing Day; the holiday is observed Mon Dec 28
    ],
)
def test_is_card_posting_day(d: date, expected: bool) -> None:
    assert is_card_posting_day(d) is expected


@pytest.mark.parametrize(
    ("made", "posted"),
    [
        (date(2026, 6, 13), date(2026, 6, 13)),  # Saturday stays
        (date(2026, 6, 14), date(2026, 6, 15)),  # Sunday -> Monday
        (date(2026, 4, 5), date(2026, 4, 6)),  # Easter Sunday -> Easter Monday (not a holiday)
        (date(2026, 9, 7), date(2026, 9, 8)),  # Labour Day -> Tuesday
        (date(2026, 9, 6), date(2026, 9, 8)),  # Sunday before Labour Day skips both
        (date(2026, 12, 25), date(2026, 12, 26)),  # Christmas (Fri) -> Saturday
    ],
)
def test_next_card_posting_day(made: date, posted: date) -> None:
    assert next_card_posting_day(made) == posted
