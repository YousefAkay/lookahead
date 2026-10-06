"""Tests for 3b step 3: the everyday-spending stream and the two baselines.
Made-up rows and dates only."""

from datetime import date, timedelta

from lookahead.flows import Txn, flow_types
from lookahead.forecast.spending import (
    recent_average_path,
    spending_by_day,
    weekday_average_path,
)

CARD = "Opos {:<20}toron "


def txn(row_id: int, day: date, cents: int, kind: str, text: str) -> Txn:
    return Txn(row_id, day, cents, kind, text)


def stream(
    rows: list[Txn], series_rows: set[int] | None = None, as_of: date = date(2026, 9, 30)
) -> dict[date, int]:
    return spending_by_day(rows, flow_types(rows), series_rows or set(), as_of)


# --- What counts as spending ---


def test_card_purchases_cash_and_unattached_transfers_out_all_count() -> None:
    day = date(2026, 9, 10)
    rows = [
        txn(1, day, -2500, "pos purchase", CARD.format("Bookshop")),
        txn(2, day, -4000, "abm withdrawal", "Cash "),
        txn(3, day, -7500, "withdrawal", "Free Interac E-Transfer "),  # to a person
        txn(4, day, -10000, "customer transfer dr.", "To savings "),
    ]
    assert stream(rows) == {day: 2500 + 4000 + 7500 + 10000}


def test_large_one_offs_are_not_excluded() -> None:
    """They really happen; leaving them out would make the forecast too optimistic."""
    day = date(2026, 9, 10)
    rows = [txn(1, day, -900000, "pos purchase", CARD.format("Laptop Store"))]
    assert stream(rows) == {day: 900000}


def test_payments_of_a_recurring_series_are_not_everyday_spending() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, -6500, "bill payment", "Phone Co "),
            txn(2, day, -2500, "pos purchase", CARD.format("Bookshop"))]  # fmt: skip
    assert stream(rows, series_rows={1}) == {day: 2500}


def test_bank_fees_are_left_out_because_they_are_modelled_separately() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, -500, "service charge", "Monthly "),
            txn(2, day, -300, "overdraft interest", "Interest "),
            txn(3, day, -2500, "pos purchase", CARD.format("Bookshop"))]  # fmt: skip
    assert stream(rows) == {day: 2500}


def test_income_and_incoming_transfers_are_not_spending() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, 50000, "deposit", "Payroll "),
            txn(2, day, 20000, "customer transfer cr.", "From savings "),
            txn(3, day, 7000, "deposit", "Interac e-Transfer from a friend "),
            txn(4, day, -2500, "pos purchase", CARD.format("Bookshop"))]  # fmt: skip
    assert stream(rows) == {day: 2500}


def test_a_partial_card_return_lowers_that_days_spending() -> None:
    rows = [txn(1, date(2026, 9, 10), -5000, "pos purchase", CARD.format("Shoes")),
            txn(2, date(2026, 9, 12), 2000, "pos return", CARD.format("Shoes"))]  # fmt: skip
    assert stream(rows) == {date(2026, 9, 10): 5000, date(2026, 9, 12): -2000}


def test_a_fully_reversed_pair_is_not_spending_at_all() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, -5000, "pos purchase", CARD.format("Hold")),
            txn(2, day, 5000, "pos return", CARD.format("Hold"))]  # fmt: skip
    flows = flow_types(rows)
    assert flows[1].netted
    assert spending_by_day(rows, flows, set(), date(2026, 9, 30)) == {}


def test_rows_after_the_forecast_date_are_not_used() -> None:
    rows = [txn(1, date(2026, 9, 10), -2500, "pos purchase", CARD.format("Bookshop")),
            txn(2, date(2026, 10, 2), -9900, "pos purchase", CARD.format("Bookshop"))]  # fmt: skip
    assert stream(rows, as_of=date(2026, 9, 30)) == {date(2026, 9, 10): 2500}


# --- Baseline 1: the average of the last 28 days, times the horizon ---

AS_OF = date(2026, 9, 30)
LONG_AGO = date(2026, 1, 1)


def test_the_recent_average_is_the_last_28_days_spread_evenly() -> None:
    daily = {AS_OF - timedelta(days=i): 100 for i in range(28)}  # 100 cents a day for 28 days
    daily[AS_OF - timedelta(days=40)] = 999999  # older than the window: ignored
    path = recent_average_path(daily, AS_OF, LONG_AGO, horizon=5)
    assert path == [100, 200, 300, 400, 500]  # D(t, h) for h = 1..5


def test_the_recent_average_counts_days_with_no_spending() -> None:
    daily = {AS_OF: 2800}  # one big day in the last 28
    assert recent_average_path(daily, AS_OF, LONG_AGO, horizon=2) == [100, 200]


def test_a_short_history_is_averaged_over_the_days_that_exist() -> None:
    history_start = AS_OF - timedelta(days=9)  # only 10 days of history
    daily = {history_start: 1000}
    assert recent_average_path(daily, AS_OF, history_start, horizon=3) == [100, 200, 300]


def test_no_history_means_no_spending_estimate() -> None:
    assert recent_average_path({}, AS_OF, None, horizon=3) == [0, 0, 0]


def test_refunds_cannot_make_the_forecast_spend_less_than_nothing() -> None:
    daily = {AS_OF: -5000}
    assert recent_average_path(daily, AS_OF, LONG_AGO, horizon=2) == [0, 0]


def test_the_recent_average_ignores_days_after_the_forecast_date() -> None:
    daily = {AS_OF: 2800, AS_OF + timedelta(days=3): 900000}
    assert recent_average_path(daily, AS_OF, LONG_AGO, horizon=1) == [100]


# --- Baseline 2: the average for each weekday over the last 8 weeks ---

FIRST = AS_OF + timedelta(days=1)  # Thursday 1 October 2026


def test_the_weekday_average_puts_spending_on_the_weekdays_it_happens() -> None:
    saturdays = [
        AS_OF - timedelta(days=d) for d in range(56) if (AS_OF - timedelta(days=d)).weekday() == 5
    ]
    assert len(saturdays) == 8
    daily = {d: 8000 for d in saturdays}  # 80 dollars every Saturday
    daily[AS_OF - timedelta(days=70)] = 999999  # older than 8 weeks: ignored
    path = weekday_average_path(daily, AS_OF, LONG_AGO, FIRST, horizon=10)
    steps = [b - a for a, b in zip([0, *path], path, strict=False)]
    expected = [8000 if (FIRST + timedelta(days=k)).weekday() == 5 else 0 for k in range(10)]
    assert steps == expected and sum(steps) == 16000  # two Saturdays in ten days


def test_the_weekday_average_is_the_mean_over_all_eight_of_that_weekday() -> None:
    mondays = [
        AS_OF - timedelta(days=d) for d in range(56) if (AS_OF - timedelta(days=d)).weekday() == 0
    ]
    daily = {mondays[0]: 8000}  # one busy Monday out of eight
    path = weekday_average_path(daily, AS_OF, LONG_AGO, date(2026, 10, 5), horizon=1)  # a Monday
    assert path == [1000]


def test_a_weekday_never_seen_in_a_short_history_uses_the_overall_average() -> None:
    history_start = AS_OF - timedelta(days=2)  # only Mon-Wed in the history
    daily = {history_start: 300, history_start + timedelta(days=1): 300,
             AS_OF: 300}  # fmt: skip
    path = weekday_average_path(daily, AS_OF, history_start, FIRST, horizon=7)
    assert path[-1] == 7 * 300 and path[0] == 300  # 300 a day, whichever weekday


def test_no_history_means_no_weekday_estimate() -> None:
    assert weekday_average_path({}, AS_OF, None, FIRST, horizon=3) == [0, 0, 0]
