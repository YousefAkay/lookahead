"""Tests for 3b step 2: the recurring payments placed on the forecast days.
Made-up series and dates only."""

from datetime import date

from lookahead.forecast.schedule import ScheduledItem, scheduled_items
from lookahead.forecast.start import ForecastStart
from lookahead.recurring_store import Stored


def start_on(last_data_day: date, days: int = 30) -> ForecastStart:
    from datetime import timedelta

    first = last_data_day + timedelta(days=1)
    return ForecastStart("a", last_data_day, 100000, first, first + timedelta(days=days - 1))


def series(key: str = "s", **fields: object) -> Stored:
    base: dict[str, object] = dict(
        key=key, name=key, kind="merchant", direction="out", cadence="monthly",
        anchor=date(2026, 1, 5), anchor_day=5, amount_type="fixed", expected_cents=-6500,
        range_cents=(-6500, -6500), next_expected=date(2026, 10, 5), status="detected",
        active=True,
    )  # fmt: skip
    merged = {**base, **fields}
    if "anchor" not in fields:  # a monthly series falls on the day of its next expected date
        due = merged["next_expected"]
        merged["anchor"], merged["anchor_day"] = due, due.day  # type: ignore[attr-defined]
    return Stored(**merged)  # type: ignore[arg-type]


def days_of(items: list[ScheduledItem]) -> list[date]:
    return [i.day for i in items]


def test_a_monthly_payment_is_placed_on_its_next_expected_date() -> None:
    items = scheduled_items([series()], {}, start_on(date(2026, 9, 28)))
    assert [(i.day, i.cents, i.series_key) for i in items] == [(date(2026, 10, 5), -6500, "s")]


def test_a_series_with_a_pattern_is_placed_every_time_it_falls_in_the_horizon() -> None:
    twice = series(cadence="semimonthly", anchor=date(2024, 1, 1), anchor_day=1,
                   next_expected=date(2026, 10, 1))  # fmt: skip
    items = scheduled_items([twice], {}, start_on(date(2026, 9, 28)))
    assert days_of(items) == [date(2026, 10, 1), date(2026, 10, 15)]  # not Oct 29: past the end
    weekly = series("w", cadence="weekly", anchor=date(2026, 9, 29), anchor_day=None,
                    next_expected=date(2026, 10, 6))  # fmt: skip
    items = scheduled_items([weekly], {}, start_on(date(2026, 9, 28)))
    assert days_of(items) == [date(2026, 10, 6), date(2026, 10, 13), date(2026, 10, 20),
                              date(2026, 10, 27)]  # fmt: skip


def test_nothing_is_placed_outside_the_horizon() -> None:
    far = series(next_expected=date(2026, 12, 5))
    assert scheduled_items([far], {}, start_on(date(2026, 9, 28))) == []


def test_a_payment_due_on_or_before_the_last_data_day_is_expected_on_the_first_forecast_day() -> (
    None
):
    """It was due by the time the data ends and has not been seen: it may be late (or paid
    after the export), so the forecast expects it first thing."""
    due = series(next_expected=date(2026, 9, 28))
    item = scheduled_items([due], {}, start_on(date(2026, 9, 28)))[0]
    assert item.day == date(2026, 9, 29) and "not seen" in item.note
    edited = series(next_expected=date(2026, 9, 3), edited=True)  # my own date, already past
    assert scheduled_items([edited], {}, start_on(date(2026, 9, 28)))[0].day == date(2026, 9, 29)


def test_ended_and_rejected_series_are_left_out() -> None:
    items = scheduled_items(
        [series("a"), series("b", active=False), series("c", status="rejected")],
        {}, start_on(date(2026, 9, 28)),
    )  # fmt: skip
    assert [i.series_key for i in items] == ["a"]


def test_every_item_says_whether_it_is_detected_confirmed_or_entered() -> None:
    items = scheduled_items(
        [series("a"), series("b", status="confirmed"), series("c", status="user-added"),
         series("d", status="confirmed", edited=True)],
        {}, start_on(date(2026, 9, 28)),
    )  # fmt: skip
    assert {i.series_key: i.basis for i in items} == {
        "a": "detected", "b": "confirmed", "c": "entered", "d": "entered",
    }  # fmt: skip


def test_income_comes_in_with_a_positive_sign() -> None:
    pay = series("pay", direction="in", expected_cents=120000, range_cents=(120000, 120000))
    (item,) = scheduled_items([pay], {}, start_on(date(2026, 9, 28)))
    assert item.cents == 120000


def test_items_are_sorted_by_day_then_series() -> None:
    items = scheduled_items(
        [series("b", next_expected=date(2026, 10, 9)), series("a", next_expected=date(2026, 10, 9)),
         series("c", next_expected=date(2026, 10, 2))],
        {}, start_on(date(2026, 9, 28)),
    )  # fmt: skip
    assert [(i.day.day, i.series_key) for i in items] == [(2, "c"), (9, "a"), (9, "b")]


# --- Rent: the larger amount, a window, one payment per cycle ---

ALLOWED = (79500, 82000)


def rent(evidence: tuple[str, ...] = (), **fields: object) -> Stored:
    return series(
        "rent", name="Rent", kind="e-transfer", expected_cents=-82000,
        range_cents=(-82000, -79500), allowed_cents=ALLOWED, window=(27, 7), evidence=evidence,
        next_expected=date(2026, 10, 27), status="confirmed", **fields,
    )  # fmt: skip


def test_rent_is_the_larger_amount_on_the_first_day_of_the_next_unpaid_window() -> None:
    posted = {"p": date(2026, 9, 29)}  # the September cycle (27 Sep - 7 Oct) is paid
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 10, 1)))
    assert [(i.day, i.cents) for i in items] == [(date(2026, 10, 27), -82000)]
    assert "larger" in items[0].note and "window" in items[0].note


def test_an_open_unpaid_window_is_expected_from_the_first_forecast_day() -> None:
    posted = {"p": date(2026, 8, 29)}  # August paid, September's window still open and unpaid
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 10, 1)))
    assert days_of(items) == [date(2026, 10, 2), date(2026, 10, 27)]


def test_an_ended_unpaid_window_with_stale_data_is_expected_on_the_first_forecast_day() -> None:
    posted = {"p": date(2026, 8, 29)}  # September's window ended on 7 Oct with no payment
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 10, 15)))
    assert days_of(items) == [date(2026, 10, 16), date(2026, 10, 27)]
    assert "ended" in items[0].note and "not seen" in items[0].note


def test_only_the_latest_ended_window_counts_as_overdue() -> None:
    posted = {"p": date(2026, 7, 29)}  # August and September both ended unpaid
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 10, 15)))
    assert days_of(items) == [date(2026, 10, 16), date(2026, 10, 27)]  # one overdue, not two


def test_a_skipped_month_in_the_past_is_not_expected_again() -> None:
    # August was never paid, but September and October were: a gap, not a late payment
    posted = {"a": date(2026, 7, 29), "b": date(2026, 9, 29), "c": date(2026, 10, 29)}
    # (the latest ended window, September's, is paid, so August is not overdue)
    items = scheduled_items([rent(("a", "b", "c"))], posted, start_on(date(2026, 11, 3)))
    assert days_of(items) == [date(2026, 11, 27)]


def test_a_late_payment_in_mid_month_pays_its_own_cycle() -> None:
    posted = {"p": date(2026, 10, 12)}  # rent for the September cycle, sent late
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 10, 15)))
    assert days_of(items) == [date(2026, 10, 27)]  # nothing overdue


def test_rent_rows_posted_after_the_forecast_start_are_not_used() -> None:
    posted = {"p": date(2026, 10, 20)}  # after the data ended: must not mark a cycle paid
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 10, 15)))
    assert days_of(items) == [date(2026, 10, 16), date(2026, 10, 27)]


def test_rent_wraps_across_a_year_end() -> None:
    posted = {"p": date(2026, 11, 28)}  # the November cycle (27 Nov - 7 Dec) is paid
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 12, 10)))
    assert days_of(items) == [date(2026, 12, 27)]
    items = scheduled_items([rent(("p",))], posted, start_on(date(2026, 12, 28)))
    # the December window is open and unpaid; January's starts on the last forecast day
    assert days_of(items) == [date(2026, 12, 29), date(2027, 1, 27)]
