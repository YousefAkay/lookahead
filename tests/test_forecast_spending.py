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


# --- Bill payments that belong to no series still lower the balance ---


def test_a_bill_payment_that_is_in_no_series_is_still_spending() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, -42000, "bill payment", "City Tuition "),
            txn(2, day, -6500, "bill payment", "Phone Co ")]  # fmt: skip
    assert stream(rows) == {day: 42000 + 6500}
    assert stream(rows, series_rows={2}) == {day: 42000}  # the series one is placed on its dates


# --- What is in everyday spending: the evidence table ---

from lookahead.forecast.spending import BREAKDOWN_TYPES, spending_breakdown  # noqa: E402


def breakdown(rows: list[Txn], series_rows: set[int] | None = None):  # type: ignore[no-untyped-def]
    return spending_breakdown(rows, flow_types(rows), series_rows or set(), AS_OF, LONG_AGO)


def test_each_type_gets_its_own_row_with_the_average_for_each_weekday() -> None:
    wed = date(2026, 9, 30)  # a Wednesday: weekday 2
    rows = [
        txn(1, wed, -8000, "pos purchase", CARD.format("Bookshop")),
        txn(2, wed, -16000, "withdrawal", "Free Interac E-Transfer "),
        txn(3, wed, -24000, "customer transfer dr.", "To savings "),
        txn(4, wed, -4000, "abm withdrawal", "Cash "),
    ]
    table = {r.type: r for r in breakdown(rows)}
    assert table["Card purchases"].per_weekday[2] == 8000 / 8  # one Wednesday of eight
    assert table["Transfers to people"].per_weekday[2] == 16000 / 8
    assert table["Transfers to my own accounts"].per_weekday[2] == 24000 / 8
    assert table["Cash"].per_weekday[2] == 4000 / 8
    assert table["Card purchases"].per_weekday[0] == 0  # nothing on Mondays
    assert table["Card purchases"].per_week == 1000.0


def test_a_large_one_off_gets_its_own_row_whatever_its_type() -> None:
    wed = date(2026, 9, 30)
    rows = [
        txn(i, wed - timedelta(days=i), -2000, "pos purchase", CARD.format("Bookshop"))
        for i in range(1, 11)
    ]  # ten ordinary purchases
    rows.append(txn(99, wed, -400000, "withdrawal", "Free Interac E-Transfer "))
    table = {r.type: r for r in breakdown(rows)}
    assert table["Large one-offs"].per_week == 400000 / 8 and "Transfers to people" not in table


def test_the_rows_of_the_table_add_up_to_the_weekday_average_the_forecast_uses() -> None:
    rows = [
        txn(1, date(2026, 9, 30), -8000, "pos purchase", CARD.format("Bookshop")),
        txn(2, date(2026, 9, 29), -16000, "withdrawal", "Free Interac E-Transfer "),
        txn(3, date(2026, 9, 23), -5000, "customer transfer dr.", "To savings "),
        txn(4, date(2026, 9, 22), -42000, "bill payment", "City Tuition "),
        txn(5, date(2026, 9, 23), 3000, "pos return", CARD.format("Bookshop")),
    ]
    flows = flow_types(rows)
    daily = spending_by_day(rows, flows, set(), AS_OF)
    path = weekday_average_path(daily, AS_OF, LONG_AGO, FIRST, horizon=7)
    from_table = [sum(r.per_weekday[(FIRST + timedelta(days=k)).weekday()] for r in breakdown(rows))
                  for k in range(7)]  # fmt: skip
    assert path[-1] == round(sum(from_table))  # a week of the table = a week of the forecast


def test_series_payments_are_left_out_of_the_table_too() -> None:
    rows = [txn(1, date(2026, 9, 30), -82000, "withdrawal", "Free Interac E-Transfer "),
            txn(2, date(2026, 9, 30), -8000, "pos purchase", CARD.format("Bookshop"))]  # fmt: skip
    table = {r.type: r for r in breakdown(rows, series_rows={1})}
    assert "Transfers to people" not in table and table["Card purchases"].per_week == 1000.0


def test_the_types_are_the_known_ones() -> None:
    assert "Large one-offs" in BREAKDOWN_TYPES and "Transfers to my own accounts" in BREAKDOWN_TYPES
