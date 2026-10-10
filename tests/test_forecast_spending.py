"""Tests for 3b step 3: the everyday-spending stream and the two baselines.
Made-up rows and dates only."""

from datetime import date, timedelta

from lookahead.flows import Txn, flow_types
from lookahead.forecast.spending import (
    lump_rows,
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


def test_big_one_time_rows_are_not_everyday_spending() -> None:
    """A $6,000 move to savings and a $19,000 tuition payment are not everyday spending:
    averaged over weekdays they would be repeated every week."""
    day = date(2026, 9, 10)
    rows = [
        txn(1, day, -2500, "pos purchase", CARD.format("Bookshop")),
        txn(2, day, -3000, "pos purchase", CARD.format("Bookshop")),
        txn(3, day, -2000, "pos purchase", CARD.format("Bookshop")),
        txn(4, day, -600000, "customer transfer dr.", "To savings "),
        txn(5, day, -1900000, "bill payment", "City University "),
    ]
    assert stream(rows) == {day: 2500 + 3000 + 2000}


def test_a_row_must_be_both_above_the_floor_and_far_above_the_typical_row() -> None:
    day = date(2026, 9, 10)
    rows = [txn(i, day, -20000, "pos purchase", CARD.format("Shop")) for i in range(1, 6)]
    rows.append(txn(9, day, -60000, "withdrawal", "Free Interac E-Transfer "))
    # typical row 200 dollars: 5 times is 1000; 600 is above the 500 floor but not far above
    assert stream(rows) == {day: 5 * 20000 + 60000}
    rows.append(txn(10, day, -120000, "withdrawal", "Free Interac E-Transfer "))
    assert stream(rows) == {day: 5 * 20000 + 60000}  # 1,200 dollars is more than 5 x typical


def test_the_floor_applies_when_the_typical_row_is_tiny() -> None:
    day = date(2026, 9, 10)
    rows = [txn(i, day, -500, "pos purchase", CARD.format("Shop")) for i in range(1, 6)]
    rows += [
        txn(8, day, -49900, "withdrawal", "Free Interac E-Transfer "),  # under 500 dollars
        txn(9, day, -50000, "withdrawal", "Free Interac E-Transfer "),
    ]  # 300: big
    assert stream(rows) == {day: 5 * 500 + 49900}


def test_the_typical_row_comes_from_the_last_13_weeks_only() -> None:
    """Old history does not move the line."""
    day = date(2026, 9, 10)
    old = [
        txn(i, date(2025, 1, 1), -2000000, "pos purchase", CARD.format("Old")) for i in range(1, 9)
    ]
    rows = [*old, txn(20, day, -60000, "withdrawal", "Free Interac E-Transfer ")]
    rows += [txn(30 + i, day, -2000, "pos purchase", CARD.format("Shop")) for i in range(6)]
    # the typical row of the last 13 weeks is 20 dollars, so 600 dollars is a big one-time row
    assert stream(rows, as_of=date(2026, 9, 30))[day] == 6 * 2000


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

from lookahead.forecast.spending import (  # noqa: E402
    BREAKDOWN_TYPES,
    big_events,
    deposits_by_day,
    spending_breakdown,
)


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


def test_a_big_one_time_row_is_not_in_the_table_it_is_listed_as_an_event() -> None:
    wed = date(2026, 9, 30)
    rows = [
        txn(i, wed - timedelta(days=i), -2000, "pos purchase", CARD.format("Bookshop"))
        for i in range(1, 11)
    ]  # ten ordinary purchases
    rows.append(txn(99, wed, -400000, "withdrawal", "Free Interac E-Transfer "))
    table = {r.type: r for r in breakdown(rows)}
    assert set(table) == {"Card purchases"}  # nothing else, and no "large" row
    events = big_events(rows, flow_types(rows), set(), AS_OF)
    assert [(e.day, e.kind, e.cents) for e in events] == [(wed, "Transfers to people", -400000)]


def test_big_events_cover_both_directions_and_name_their_type() -> None:
    rows = [
        txn(i, date(2026, 9, i), -2000, "pos purchase", CARD.format("Shop")) for i in range(1, 8)
    ]
    rows += [
        txn(10 + i, date(2026, 9, 10 + i), 2500, "deposit", "Interac e-Transfer from a friend ")
        for i in range(5)
    ]
    rows += [txn(30, date(2026, 9, 3), -600000, "customer transfer dr.", "To savings "),
             txn(31, date(2026, 9, 5), 900000, "deposit", "Payroll "),
             txn(32, date(2026, 9, 6), 700000, "customer transfer cr.", "From savings "),
             ]  # fmt: skip
    events = big_events(rows, flow_types(rows), set(), AS_OF)
    assert [(e.day.day, e.kind, e.cents) for e in events] == [
        (3, "Transfers to my own accounts", -600000),
        (5, "Deposit", 900000),
        (6, "Transfer from my own accounts", 700000),
    ]  # oldest first


def test_series_payments_and_fees_are_never_big_events() -> None:
    rows = [txn(1, date(2026, 9, 3), -900000, "withdrawal", "Free Interac E-Transfer "),
            txn(2, date(2026, 9, 4), -900000, "service charge", "Big fee "),
            txn(3, date(2026, 9, 5), -2000, "pos purchase", CARD.format("Shop"))]  # fmt: skip
    assert big_events(rows, flow_types(rows), {1}, AS_OF) == []


# --- Irregular money in: deposits that are in no series ---


def deposits(rows: list[Txn], series_rows: set[int] | None = None):  # type: ignore[no-untyped-def]
    return deposits_by_day(rows, flow_types(rows), series_rows or set(), AS_OF)


def test_irregular_money_in_counts_whatever_its_source() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, 5000, "deposit", "Interac e-Transfer from a friend "),
            txn(2, day, 7000, "credit memo", "Refund of fees "),
            txn(3, day, 3000, "customer transfer cr.", "From savings ")]  # fmt: skip
    assert deposits(rows) == {day: 5000 + 7000 + 3000}


def test_refunds_and_netted_holds_are_not_money_in() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, -5000, "pos purchase", CARD.format("Hold")),
            txn(2, day, 5000, "pos return", CARD.format("Hold")),  # fully reversed: netted
            txn(3, day, -8000, "pos purchase", CARD.format("Shoes")),
            txn(4, date(2026, 9, 12), 2000, "pos return", CARD.format("Shoes")),  # a refund
            txn(5, day, 4000, "deposit", "Interac e-Transfer from a friend ")]  # fmt: skip
    assert deposits(rows) == {day: 4000}


def test_an_income_series_payment_is_not_an_irregular_deposit() -> None:
    day = date(2026, 9, 10)
    rows = [txn(1, day, 120000, "deposit", "Payroll "),
            txn(2, day, 4000, "deposit", "Interac e-Transfer from a friend ")]  # fmt: skip
    assert deposits(rows, series_rows={1}) == {day: 4000}


def test_a_big_deposit_leaves_the_irregular_stream_by_the_same_rule() -> None:
    day = date(2026, 9, 10)
    rows = [txn(i, day, 2500, "deposit", "Interac e-Transfer from a friend ") for i in range(1, 6)]
    rows.append(txn(9, day, 600000, "deposit", "Payroll "))
    assert deposits(rows) == {day: 5 * 2500}


def test_deposits_after_the_forecast_date_are_not_used() -> None:
    friend = "Interac e-Transfer from a friend "
    rows = [txn(1, date(2026, 9, 10), 4000, "deposit", friend),
            txn(2, date(2026, 10, 3), 9000, "deposit", friend)]  # fmt: skip
    assert deposits(rows) == {date(2026, 9, 10): 4000}


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
    assert "Transfers to my own accounts" in BREAKDOWN_TYPES
    assert "Large one-offs" not in BREAKDOWN_TYPES  # they are events, not spending


# --- Bank fees: their recent daily average, until they have their own model ---

from lookahead.forecast.spending import recent_fee_rate  # noqa: E402


def fee_rate(rows: list[Txn], history_start: date | None = LONG_AGO) -> float:
    return recent_fee_rate(rows, flow_types(rows), AS_OF, history_start)


def test_the_fee_rate_is_the_fees_of_the_last_28_days_spread_evenly() -> None:
    rows = [txn(1, date(2026, 9, 5), -500, "service charge", "Monthly "),
            txn(2, date(2026, 9, 20), -300, "overdraft interest", "Interest "),
            txn(3, date(2026, 7, 1), -90000, "service charge", "Old ")]  # fmt: skip
    assert fee_rate(rows) == 800 / 28  # the July one is older than 28 days


def test_a_fee_reversal_takes_the_fee_back() -> None:
    rows = [txn(1, date(2026, 9, 5), -500, "service charge", "Monthly "),
            txn(2, date(2026, 9, 6), 500, "service charge", "Reversal ")]  # fmt: skip
    assert fee_rate(rows) == 0.0


def test_fees_are_not_everyday_spending_and_everyday_spending_is_not_fees() -> None:
    rows = [txn(1, date(2026, 9, 5), -500, "service charge", "Monthly "),
            txn(2, date(2026, 9, 5), -2800, "pos purchase", CARD.format("Shop"))]  # fmt: skip
    assert fee_rate(rows) == 500 / 28
    assert stream(rows) == {date(2026, 9, 5): 2800}


def test_the_fee_rate_uses_only_rows_posted_by_the_forecast_date() -> None:
    rows = [txn(1, date(2026, 9, 5), -500, "service charge", "Monthly "),
            txn(2, date(2026, 10, 2), -9000, "service charge", "Later ")]  # fmt: skip
    assert fee_rate(rows) == 500 / 28


def test_a_short_history_averages_fees_over_the_days_it_has() -> None:
    start = AS_OF - timedelta(days=9)  # ten days of history
    rows = [txn(1, date(2026, 9, 25), -500, "service charge", "Monthly ")]
    assert fee_rate(rows, history_start=start) == 50.0
    assert fee_rate(rows, history_start=None) == 0.0


# --- Pass-through: a big deposit that goes straight back out ---

FRIEND = "Interac e-Transfer from a friend "
UNI = "City University "
ETX = "Free Interac E-Transfer "


def ordinary(start_id: int = 100) -> list[Txn]:
    """Ordinary small rows so that the typical row is small: purchases and small deposits."""
    rows = [txn(start_id + i, date(2026, 9, 1) + timedelta(days=i), -2000, "pos purchase",
                CARD.format("Shop")) for i in range(12)]  # fmt: skip
    rows += [txn(start_id + 20 + i, date(2026, 9, 2) + timedelta(days=3 * i), 3000, "deposit",
                 FRIEND)
             for i in range(4)]  # fmt: skip
    return rows


def paired(rows: list[Txn], series_rows: set[int] | None = None, as_of: date = AS_OF):  # type: ignore[no-untyped-def]
    return big_events(rows, flow_types(rows), series_rows or set(), as_of)


def groups(events) -> list[tuple[int, str, int | None]]:  # type: ignore[no-untyped-def]
    return [(e.cents, e.group, e.pair) for e in events]


def test_a_big_deposit_that_goes_straight_to_savings_is_a_pass_through() -> None:
    rows = ordinary() + [
        txn(1, date(2026, 9, 3), 600000, "deposit", "Payroll "),
        txn(2, date(2026, 9, 4), -300000, "customer transfer dr.", "To savings "),
        txn(3, date(2026, 9, 6), -295000, "customer transfer dr.", "To savings "),
    ]
    events = paired(rows)
    assert groups(events) == [(600000, "Pass-through", 1), (-300000, "Pass-through", 1),
                              (-295000, "Pass-through", 1)]  # fmt: skip
    daily = spending_by_day(rows, flow_types(rows), set(), AS_OF)
    assert sum(daily.values()) == 12 * 2000  # the transfers are not everyday spending
    assert sum(deposits_by_day(rows, flow_types(rows), set(), AS_OF).values()) == 4 * 3000


def test_a_big_deposit_that_pays_tuition_is_a_pass_through() -> None:
    rows = ordinary() + [
        txn(1, date(2026, 9, 22), 2000000, "credit memo", "Family "),
        txn(2, date(2026, 9, 22), -1865900, "bill payment", UNI),
    ]
    assert groups(paired(rows)) == [(2000000, "Pass-through", 1), (-1865900, "Pass-through", 1)]


def test_the_share_must_be_between_85_and_115_percent() -> None:
    def case(out: int) -> list[str]:
        rows = ordinary() + [
            txn(1, date(2026, 9, 3), 100000, "deposit", "Payroll "),
            txn(2, date(2026, 9, 5), -out, "bill payment", UNI),
        ]
        return [e.group for e in paired(rows)]

    assert case(84000) == ["Large deposit", "Large payment"]  # 84 percent: not a pass-through
    assert case(85000) == ["Pass-through", "Pass-through"]
    assert case(115000) == ["Pass-through", "Pass-through"]
    assert case(116000) == ["Large deposit", "Large payment"]


def test_a_payment_a_little_over_the_deposit_still_counts() -> None:
    """A deposit that covered most, not all, of a payment (here 113 percent)."""
    rows = ordinary() + [
        txn(1, date(2026, 9, 1), 640000, "credit memo", "Family "),
        txn(2, date(2026, 9, 4), -725000, "bill payment", UNI),
    ]
    assert [e.group for e in paired(rows)] == ["Pass-through", "Pass-through"]


def test_only_the_14_days_after_the_deposit_count() -> None:
    def case(day: int) -> list[str]:
        rows = ordinary() + [txn(1, date(2026, 9, 3), 100000, "deposit", "Payroll "),
                             txn(2, date(2026, 9, day), -100000, "bill payment", UNI)]  # fmt: skip
        return [e.group for e in paired(rows)]

    assert case(17) == ["Pass-through", "Pass-through"]  # day 14 after the 3rd
    assert case(18) == ["Large deposit", "Large payment"]  # day 15
    assert case(2) == ["Large payment", "Large deposit"]  # before the deposit: not its outflow


def test_each_outflow_is_used_once_and_deposits_pair_in_order() -> None:
    rows = ordinary() + [
        txn(1, date(2026, 9, 1), 500000, "deposit", "Payroll "),
        txn(2, date(2026, 9, 3), 500000, "deposit", "Payroll "),
        txn(3, date(2026, 9, 4), -500000, "bill payment", UNI),
        txn(4, date(2026, 9, 6), -500000, "customer transfer dr.", "To savings "),
    ]
    events = paired(rows)
    assert groups(events) == [(500000, "Pass-through", 1), (500000, "Pass-through", 2),
                              (-500000, "Pass-through", 1),
                              (-500000, "Pass-through", 2)]  # fmt: skip


def test_small_transfers_can_complete_a_pass_through_but_a_purchase_cannot() -> None:
    rows = ordinary() + [
        txn(1, date(2026, 9, 3), 600000, "deposit", "Payroll "),
        txn(2, date(2026, 9, 4), -500000, "customer transfer dr.", "To savings "),
        txn(3, date(2026, 9, 5), -25000, "withdrawal", ETX),
        txn(4, date(2026, 9, 6), -25000, "withdrawal", ETX),
        txn(5, date(2026, 9, 7), -20000, "pos purchase", CARD.format("Shoes")),
    ]
    flows = flow_types(rows)
    daily = spending_by_day(rows, flows, set(), AS_OF)
    # 5,000 + 250 + 250 = 5,500 of 6,000 = 91.7 percent: paired. A small purchase is not a
    # transfer, so it never completes a pass-through and stays everyday spending.
    assert sum(e.pair == 1 for e in paired(rows)) == 4
    assert daily[date(2026, 9, 6)] == 2000  # only the ordinary purchase: the transfers left
    assert daily[date(2026, 9, 7)] == 2000 + 20000  # ordinary + shoes


def test_payments_of_a_series_never_complete_a_pass_through() -> None:
    rows = ordinary() + [txn(1, date(2026, 9, 3), 100000, "deposit", "Payroll "),
                         txn(2, date(2026, 9, 4), -100000, "withdrawal", ETX)]  # fmt: skip
    assert [e.group for e in paired(rows, series_rows={2})] == ["Large deposit"]


def test_nothing_after_the_forecast_date_is_used_for_pairing() -> None:
    rows = ordinary() + [txn(1, date(2026, 9, 28), 100000, "deposit", "Payroll "),
                         txn(2, date(2026, 10, 3), -100000, "bill payment", UNI)]  # fmt: skip
    assert [e.group for e in paired(rows, as_of=date(2026, 9, 30))] == ["Large deposit"]


def test_a_big_deposit_with_no_matching_outflow_is_a_large_deposit_not_forecast() -> None:
    rows = ordinary() + [txn(1, date(2026, 9, 3), 1500000, "credit memo", "Family ")]
    assert groups(paired(rows)) == [(1500000, "Large deposit", None)]
    assert sum(deposits_by_day(rows, flow_types(rows), set(), AS_OF).values()) == 4 * 3000


# --- What "typical row" means when there are few deposits ---


def test_one_deposit_in_the_window_is_judged_against_the_typical_row_of_everything() -> None:
    """Its own size cannot be its yardstick: a single deposit would never be 5 times itself."""
    rows = ordinary()[:12] + [txn(1, date(2026, 9, 5), 1500000, "credit memo", "Family ")]
    assert paired(rows)[0].group == "Large deposit"
    assert sum(deposits_by_day(rows, flow_types(rows), set(), AS_OF).values()) == 0


def test_with_enough_deposits_their_own_typical_size_is_the_yardstick() -> None:
    """A person who regularly receives a few hundred dollars: 2,500 is not unusual for them."""
    rows = ordinary()[:12]
    rows += [txn(30 + i, date(2026, 9, 1 + 3 * i), 60000, "deposit", FRIEND) for i in range(6)]
    rows.append(txn(40, date(2026, 9, 20), 250000, "deposit", FRIEND))
    assert paired(rows) == []  # 2,500 is under 5 x the typical 600
    assert sum(deposits_by_day(rows, flow_types(rows), set(), AS_OF).values()) == 6 * 60000 + 250000


def test_a_few_small_deposits_are_not_large_just_because_there_are_few() -> None:
    rows = ordinary()[:12]
    rows += [txn(30, date(2026, 9, 4), 40000, "deposit", FRIEND),
             txn(31, date(2026, 9, 9), 45000, "deposit", FRIEND)]  # fmt: skip
    assert paired(rows) == []  # 400 and 450 dollars: under the floor of 500 dollars


# --- What's in irregular deposits: the weeks the futures draw from ---

from lookahead.forecast.spending import DepositWeek, deposit_weeks  # noqa: E402

WEEK_STARTS = [date(2026, 7, 2) + timedelta(days=7 * k) for k in range(13)]  # Thursdays


def weeks_for(rows: list[Txn], series_rows: set[int] | None = None) -> list[DepositWeek]:
    return deposit_weeks(rows, flow_types(rows), series_rows or set(), AS_OF, WEEK_STARTS)


def test_each_week_shows_its_deposits_by_type_and_what_was_set_aside() -> None:
    rows = ordinary() + [txn(1, date(2026, 9, 12), 1500000, "credit memo", "Family ")]
    weeks = {w.first: w for w in weeks_for(rows)}
    assert len(weeks) == 13 and all((w.last - w.first).days == 6 for w in weeks.values())
    early = weeks[date(2026, 9, 3)]  # 3 to 9 September: two small deposits (the 5th and the 8th)
    assert early.in_stream == (("Incoming e-Transfer", 6000),) and early.total == 6000
    big = weeks[date(2026, 9, 10)]  # the 11th is in the stream; the 12th was set aside
    assert big.total == 3000
    assert [(e.cents, e.group) for e in big.set_aside] == [(1500000, "Large deposit")]
    assert weeks[date(2026, 7, 2)].total == 0 and weeks[date(2026, 7, 2)].set_aside == ()


def test_a_pass_through_is_shown_as_set_aside_in_its_week() -> None:
    rows = ordinary() + [
        txn(1, date(2026, 9, 12), 600000, "deposit", "Payroll "),
        txn(2, date(2026, 9, 14), -590000, "customer transfer dr.", "To savings "),
    ]
    week = next(w for w in weeks_for(rows) if w.first == date(2026, 9, 10))
    assert [(e.cents, e.group) for e in week.set_aside] == [(600000, "Pass-through")]
    assert week.total == 3000  # only the small deposit is in the stream


def test_the_weeks_add_up_to_the_deposit_stream() -> None:
    rows = ordinary() + [txn(1, date(2026, 9, 12), 1500000, "credit memo", "Family ")]
    stream_total = sum(deposits_by_day(rows, flow_types(rows), set(), AS_OF).values())
    assert sum(w.total for w in weeks_for(rows)) == stream_total == 4 * 3000


def test_several_types_in_one_week_are_listed_largest_first() -> None:
    rows = ordinary()[:12] + [
        txn(1, date(2026, 9, 25), 4000, "deposit", FRIEND),
        txn(2, date(2026, 9, 26), 9000, "credit memo", "Family "),
        txn(3, date(2026, 9, 27), 2000, "customer transfer cr.", "From savings "),
        txn(4, date(2026, 9, 28), 3000, "deposit", FRIEND),
        txn(5, date(2026, 9, 29), 3500, "deposit", FRIEND),
    ]
    week = next(w for w in weeks_for(rows) if w.first == date(2026, 9, 24))
    assert week.in_stream == (
        ("Incoming e-Transfer", 10500),
        ("Credit memo", 9000),
        ("Transfer from my own accounts", 2000),
    )
    amounts = [c for _, c in week.in_stream]
    assert amounts == sorted(amounts, reverse=True)


def test_deposits_of_an_income_series_are_not_in_the_weeks() -> None:
    rows = ordinary()[:12] + [txn(1, date(2026, 9, 25), 120000, "deposit", "Payroll ")]
    assert sum(w.total for w in weeks_for(rows, series_rows={1})) == 0


# --- Lump deposits: the large deposits of the last year, with the rows themselves ---


def lumps_of(rows: list[Txn], series_rows: set[int] | None = None, as_of: date = AS_OF):  # type: ignore[no-untyped-def]
    return lump_rows(rows, flow_types(rows), series_rows or set(), as_of)


def test_a_large_deposit_older_than_13_weeks_is_still_a_lump_within_the_year() -> None:
    old = txn(1, date(2026, 2, 3), 1500000, "credit memo", "Family ")
    recent = txn(2, date(2026, 9, 5), 1200000, "credit memo", "Family ")
    too_old = txn(3, date(2025, 8, 1), 1500000, "credit memo", "Family ")  # over 365 days back
    assert [t.row_id for t, _, _ in lumps_of(ordinary() + [old, recent, too_old])] == [1, 2]


def test_small_deposits_series_rows_and_pass_throughs_are_not_lumps() -> None:
    small = txn(1, date(2026, 8, 1), 40000, "deposit", FRIEND)  # under the floor
    in_series = txn(2, date(2026, 8, 3), 1500000, "credit memo", "Family ")
    paid_out = txn(3, date(2026, 9, 2), 1500000, "credit memo", "Family ")
    tuition = txn(4, date(2026, 9, 4), -1500000, "bill payment", UNI)  # pays it straight out
    rows = ordinary() + [small, in_series, paid_out, tuition]
    assert lumps_of(rows, series_rows={2}) == []


def test_rows_posted_after_the_forecast_date_are_never_lumps() -> None:
    later = txn(1, date(2026, 10, 5), 1500000, "credit memo", "Family ")
    assert lumps_of(ordinary() + [later]) == []
