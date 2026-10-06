"""Tests for the rent rule: allowed amounts and a window of days that can wrap a month end.
Made-up dates only."""

import random
from collections import Counter
from datetime import date

import pytest

from lookahead.rent import (
    RentRule,
    assign_cycles,
    cycle_start,
    draw_rent_date,
    earliest_day,
    next_window_day,
    parse_dollars,
    rent_cents,
    window,
    window_containing,
    window_days,
)

RULE = RentRule((79500, 82000), from_day=30, to_day=7)  # the 30th to the 7th, wrapping


def test_a_window_starting_on_the_30th_wraps_into_the_next_month() -> None:
    assert window(RULE, 2026, 11) == (date(2026, 11, 30), date(2026, 12, 7))
    assert window(RULE, 2026, 1) == (date(2026, 1, 30), date(2026, 2, 7))


def test_the_window_wraps_across_a_year_end() -> None:
    assert window(RULE, 2026, 12) == (date(2026, 12, 30), date(2027, 1, 7))
    days = window_days(RULE, 2026, 12)
    assert len(days) == 9 and days[0] == date(2026, 12, 30) and days[-1] == date(2027, 1, 7)


def test_a_from_day_of_30_in_february_is_clamped_to_the_last_day_of_the_month() -> None:
    assert window(RULE, 2027, 2) == (date(2027, 2, 28), date(2027, 3, 7))  # 28 days
    assert window(RULE, 2028, 2) == (date(2028, 2, 29), date(2028, 3, 7))  # leap year
    assert earliest_day(RULE, 2027, 2) == date(2027, 2, 28)


def test_a_from_day_of_31_is_clamped_in_a_30_day_month() -> None:
    assert window(RentRule((1,), 31, 7), 2026, 4)[0] == date(2026, 4, 30)


def test_a_to_day_is_clamped_in_the_month_it_falls_in() -> None:
    # 31st to the 30th: the window ends on the 30th, or on the last day of a short February
    assert window(RentRule((1,), 31, 30), 2027, 1)[1] == date(2027, 2, 28)


def test_a_window_inside_one_month_does_not_wrap() -> None:
    inside = RentRule((1,), 25, 31)
    assert window(inside, 2027, 2) == (date(2027, 2, 25), date(2027, 2, 28))
    assert window(RentRule((1,), 3, 9), 2026, 5) == (date(2026, 5, 3), date(2026, 5, 9))


def test_which_window_a_date_falls_in() -> None:
    assert window_containing(RULE, date(2027, 3, 3)) == (date(2027, 2, 28), date(2027, 3, 7))
    assert window_containing(RULE, date(2027, 1, 1)) == (date(2026, 12, 30), date(2027, 1, 7))
    assert window_containing(RULE, date(2026, 5, 30)) == (date(2026, 5, 30), date(2026, 6, 7))
    assert window_containing(RULE, date(2027, 1, 15)) is None
    assert window_containing(RULE, date(2026, 6, 8)) is None  # the day after the window


def test_the_larger_allowed_amount_is_the_one_used() -> None:
    assert rent_cents(RULE) == 82000
    assert RentRule((82000, 79500, 82000), 30, 7).amounts_cents == (79500, 82000)


@pytest.mark.parametrize(
    ("amounts", "from_day", "to_day"),
    [((), 30, 7), ((0,), 30, 7), ((-5,), 30, 7), ((1,), 0, 7), ((1,), 30, 32), ((1,), 31, 0)],
)
def test_a_bad_rule_is_refused(amounts: tuple[int, ...], from_day: int, to_day: int) -> None:
    with pytest.raises(ValueError):
        RentRule(amounts, from_day, to_day)


def test_dollars_typed_in_the_app_become_cents() -> None:
    assert parse_dollars("820, 795.50") == (82000, 79550)


def test_dollars_may_be_separated_by_commas_or_semicolons_and_bad_text_is_refused() -> None:
    assert parse_dollars("820;795") == (82000, 79500)
    for bad in ("", "abc", "-5", "0", "820, ", "8.205"):
        with pytest.raises(ValueError):
            parse_dollars(bad)


def test_the_single_date_is_the_earliest_unpaid_day_still_ahead() -> None:
    # inside a window, not yet paid: today (the earliest day still to come)
    assert next_window_day(RULE, date(2027, 3, 3), {date(2027, 1, 30)}) == date(2027, 3, 3)
    # before the window starts: the first day of the window
    assert next_window_day(RULE, date(2027, 2, 10), {date(2027, 1, 30)}) == date(2027, 2, 28)
    # this cycle is already paid: the next window
    assert next_window_day(RULE, date(2027, 3, 3), {date(2027, 2, 28)}) == date(2027, 3, 30)
    # across the year end
    assert next_window_day(RULE, date(2026, 12, 31), {date(2026, 11, 30)}) == date(2026, 12, 31)
    assert next_window_day(RULE, date(2027, 1, 2), {date(2026, 12, 30)}) == date(2027, 1, 30)
    assert next_window_day(RULE, date(2026, 6, 1), set()) == date(2026, 6, 1)  # window 5/30-6/7


def test_a_cycle_that_ended_unpaid_is_not_the_next_date() -> None:
    """Late rent: the window ended unpaid, so the single date moves on (the missing payment
    is a matter for an alert, not for a date in the past)."""
    assert next_window_day(RULE, date(2027, 1, 12), {date(2026, 11, 30)}) == date(2027, 1, 30)


# --- Rent cycles: which payment belongs to which month's rent, whatever day it was sent ---

LATE = RentRule((82000,), from_day=27, to_day=7)  # a window that is for prediction only


def test_a_payment_belongs_to_the_latest_window_that_started_by_its_date() -> None:
    assert cycle_start(LATE, date(2027, 1, 3)) == date(2026, 12, 27)  # inside the window
    assert cycle_start(LATE, date(2027, 1, 12)) == date(2026, 12, 27)  # late: after it ended
    assert cycle_start(LATE, date(2027, 1, 26)) == date(2026, 12, 27)  # the day before the next
    assert cycle_start(LATE, date(2027, 1, 27)) == date(2027, 1, 27)
    assert cycle_start(RULE, date(2027, 3, 1)) == date(2027, 2, 28)  # clamped February start
    assert cycle_start(RULE, date(2027, 1, 3)) == date(2026, 12, 30)  # across the year end


def test_a_late_payment_in_mid_month_counts_for_its_own_cycle() -> None:
    sent = [date(2026, 12, 12), date(2027, 1, 3), date(2027, 2, 2), date(2027, 3, 12)]
    assert [c for _, c in assign_cycles(LATE, sent)] == [
        date(2026, 11, 27), date(2026, 12, 27), date(2027, 1, 27), date(2027, 2, 27),
    ]  # fmt: skip


def test_two_payments_in_one_cycle_put_the_extra_one_in_the_nearest_cycle_with_none() -> None:
    sent = [date(2026, 11, 29), date(2027, 1, 3), date(2027, 1, 12)]
    cycles = [c for _, c in assign_cycles(LATE, sent)]
    assert cycles == [date(2026, 11, 27), date(2026, 12, 27), date(2027, 1, 27)]
    assert len(set(cycles)) == 3  # never two in one cycle


def test_when_two_cycles_are_equally_near_the_earlier_one_is_chosen() -> None:
    """Two payments in January with nothing before: more likely a missed earlier month
    paid late than a month paid in advance."""
    cycles = [c for _, c in assign_cycles(LATE, [date(2027, 1, 3), date(2027, 1, 12)])]
    assert cycles == [date(2026, 12, 27), date(2026, 11, 27)]  # the 3rd keeps its own


def test_a_month_with_no_payment_stays_empty() -> None:
    cycles = {c for _, c in assign_cycles(LATE, [date(2026, 11, 29), date(2027, 1, 28)])}
    assert cycles == {date(2026, 11, 27), date(2027, 1, 27)}  # December has none
    assert next_window_day(LATE, date(2027, 2, 1), cycles) == date(2027, 2, 27)


def test_a_cycle_assignment_never_depends_on_later_payments() -> None:
    """Rule zero: the cycles found by a date are the first ones found with every payment."""
    sent = [date(2026, 11, 29), date(2026, 12, 29), date(2027, 1, 3), date(2027, 1, 12),
            date(2027, 2, 3), date(2027, 2, 12), date(2027, 3, 29)]  # fmt: skip
    everything = assign_cycles(LATE, sent)
    for k in range(1, len(sent) + 1):
        assert assign_cycles(LATE, sent[:k]) == everything[:k]


def test_cycles_wrap_across_month_and_year_ends() -> None:
    sent = [date(2026, 12, 31), date(2027, 1, 30), date(2027, 3, 1), date(2027, 3, 31)]
    assert [c for _, c in assign_cycles(RULE, sent)] == [
        date(2026, 12, 30), date(2027, 1, 30), date(2027, 2, 28), date(2027, 3, 30),
    ]  # fmt: skip


def test_a_drawn_date_is_always_inside_the_window() -> None:
    rng = random.Random(1)
    for year, month in ((2026, 12), (2027, 2), (2028, 2), (2026, 4)):
        days = set(window_days(RULE, year, month))
        assert {
            draw_rent_date(RULE, year, month, [], date(2026, 1, 1), rng) for _ in range(300)
        } <= days


def test_with_no_past_rent_every_day_of_the_window_is_possible() -> None:
    rng = random.Random(2)
    seen = {draw_rent_date(RULE, 2026, 12, [], date(2026, 1, 1), rng) for _ in range(500)}
    assert seen == set(window_days(RULE, 2026, 12))


def test_a_drawn_date_follows_my_own_past_rent_dates() -> None:
    rng = random.Random(3)
    past = [date(2026, m, 1) for m in range(1, 9)] * 3  # always paid on the 1st
    draws = Counter(
        draw_rent_date(RULE, 2026, 12, past, date(2026, 9, 1), rng) for _ in range(2000)
    )
    assert draws[date(2027, 1, 1)] / 2000 > 0.6  # the 1st dominates, other days stay possible
    assert len(draws) > 1


def test_past_rent_dates_after_the_forecast_date_are_not_used() -> None:
    """Rule zero: only dates posted on or before the date of the forecast count."""
    rng = random.Random(4)
    future = [date(2026, 10, 5)] * 30  # posted after the forecast date below
    draws = Counter(
        draw_rent_date(RULE, 2026, 12, future, date(2026, 9, 1), rng) for _ in range(2000)
    )
    assert draws[date(2027, 1, 5)] / 2000 < 0.25  # about 1 in 9 when nothing is known


def test_a_payment_in_a_clamped_february_counts_as_a_30th() -> None:
    rng = random.Random(5)
    past = [date(2027, 2, 28)] * 20 + [date(2026, 1, 30)] * 20  # both stand for the 30th
    draws = Counter(
        draw_rent_date(RULE, 2026, 12, past, date(2027, 3, 1), rng) for _ in range(2000)
    )
    assert draws[date(2026, 12, 30)] / 2000 > 0.6
