"""Tests for the Forecast page helpers: the table of placed payments and the hidden names."""

from datetime import date

import pandas as pd

from lookahead.forecast.path import Forecast, ForecastDay
from lookahead.forecast.schedule import ScheduledItem
from lookahead.forecast.simulate import Simulation
from lookahead.forecast.spending import BigEvent, BreakdownRow
from lookahead.forecast.start import ForecastStart
from lookahead.planned import PlannedItem
from lookahead.recurring_store import Stored
from lookahead.ui import (
    BLANK,
    HIDDEN,
    breakdown_table,
    events_table,
    forecast_chart,
    forecast_table,
    hidden_series,
    planned_table,
)


def stored(key: str, name: str | None, evidence: tuple[str, ...] = (), **fields: object) -> Stored:
    base: dict[str, object] = dict(
        key=key, name=name, kind="merchant", direction="out", cadence="monthly",
        anchor=date(2026, 1, 5), anchor_day=5, amount_type="fixed", expected_cents=-6500,
        range_cents=(-6500, -6500), next_expected=date(2026, 10, 5), status="detected",
        active=True, evidence=evidence,
    )  # fmt: skip
    return Stored(**{**base, **fields})  # type: ignore[arg-type]


def test_the_table_shows_dates_dollars_signs_and_why() -> None:
    items = [
        ScheduledItem(date(2026, 10, 5), -6500, "a", "Phone Co", "detected", "from its pattern"),
        ScheduledItem(date(2026, 10, 15), 120000, "b", "Bursary", "entered", "typed by me"),
    ]
    table = forecast_table(
        items, {"a": stored("a", "Phone Co"), "b": stored("b", "Bursary")}, set()
    )
    assert list(table["Date"]) == ["2026-10-05", "2026-10-15"]
    assert list(table["Name"]) == ["Phone Co", "Bursary"]
    assert list(table["Amount ($)"]) == [-65.0, 1200.0]  # money in is positive
    assert list(table["Based on"]) == ["detected", "entered"]
    assert list(table["Why"]) == ["from its pattern", "typed by me"]


def test_a_hidden_series_shows_no_name() -> None:
    items = [ScheduledItem(date(2026, 10, 5), -6500, "a", "Jane Doe", "detected", "")]
    table = forecast_table(items, {"a": stored("a", "Jane Doe")}, {"a"})
    assert list(table["Name"]) == [HIDDEN]


def test_a_series_without_a_name_is_described_by_what_it_is() -> None:
    items = [ScheduledItem(date(2026, 10, 5), -6500, "a", None, "detected", "")]
    table = forecast_table(items, {"a": stored("a", None, kind="own transfer")}, set())
    assert list(table["Name"]) == ["Transfer to my own account"]
    assert BLANK not in list(table["Name"])


def test_an_empty_forecast_table_has_its_columns() -> None:
    table = forecast_table([], {}, set())
    assert list(table.columns) == ["Date", "Name", "Amount ($)", "Based on", "Why"]
    assert table.empty


def rows_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
            "type": ["credit memo", "pos purchase"],
            "text": ["1/Jane Doe ", "Opos Bookshop         toron "],
            "amount_cents": [50000, -2500],
            "balance_cents": [50000, 47500],
            "fingerprint": ["f1", "f2"],
        }
    )


def test_a_series_whose_payments_show_a_persons_name_is_hidden_unless_it_is_named_by_me() -> None:
    series = [stored("a", "Jane Doe", ("f1",)), stored("b", "Bookshop", ("f2",)),
              stored("c", "Rent share", ("f1",), named_by_me=True)]  # fmt: skip
    assert hidden_series(rows_frame(), series, bytes(range(32))) == {"a"}
    assert hidden_series(rows_frame(), series, None) == set()  # not pseudonymizing


def test_the_spending_table_has_a_row_per_type_and_a_column_per_weekday_in_dollars() -> None:
    rows = [
        BreakdownRow("Card purchases", (0, 0, 1000.0, 0, 0, 0, 0), 1000.0),
        BreakdownRow("Cash", (0, 0, 0, 0, 0, 0, 5000.0), 5000.0),
    ]
    table = breakdown_table(rows)
    assert list(table.columns) == ["Type", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
                                   "Per week"]  # fmt: skip
    assert list(table["Type"]) == ["Card purchases", "Cash", "All types"]
    assert table.loc[0, "Wed"] == 10.0 and table.loc[1, "Sun"] == 50.0  # cents -> dollars
    assert table.loc[2, "Per week"] == 60.0 and table.loc[2, "Wed"] == 10.0  # the total row


def test_an_empty_spending_table_is_empty() -> None:
    assert breakdown_table([]).empty


def test_the_big_events_table_shows_dates_types_signed_dollars_and_what_each_is() -> None:
    events = [
        BigEvent(date(2026, 9, 3), "Deposit", 640000, "Pass-through", 1),
        BigEvent(date(2026, 9, 4), "Bill payments (in no series)", -725000, "Pass-through", 1),
        BigEvent(date(2026, 9, 8), "Credit memo", 400000, "Large deposit"),
        BigEvent(date(2026, 9, 9), "Transfers to people", -60000, "Large payment"),
    ]
    table = events_table(events)
    assert list(table.columns) == ["Date", "Type", "Amount ($)", "What"]
    assert list(table["Date"]) == ["2026-09-03", "2026-09-04", "2026-09-08", "2026-09-09"]
    assert list(table["Amount ($)"]) == [6400.0, -7250.0, 4000.0, -600.0]  # money in is positive
    assert list(table["What"]) == [
        "Pass-through 1", "Pass-through 1", "Large deposit (not forecast)",
        "Large payment (not forecast)",
    ]  # fmt: skip
    assert events_table([]).empty


def make_forecast(simulated: bool) -> Forecast:
    first = date(2026, 10, 1)
    start = ForecastStart("a", date(2026, 9, 30), 100000, first, date(2026, 10, 3))
    days = [ForecastDay(first.replace(day=1 + k), 100000 - 100 * k, 0, 100, 0) for k in range(3)]
    sim = None
    if simulated:
        sim = Simulation(
            tuple(d.day for d in days), 500, (90000, 89000, 88000), (99000, 98500, 98000),
            (101000, 100500, 100000), (98000, 97000, 96000), 0.12, 0.31, 0.01, 0.02, 95000, 90000,
            12,
        )  # fmt: skip
    return Forecast(start, "test", days, [], False, simulation=sim)


def test_the_chart_shows_the_range_both_medians_and_the_point_forecast() -> None:
    figure = forecast_chart(pd.DataFrame(), make_forecast(True), 20000)
    names = [t.name for t in figure.data]
    assert "Range of 500 simulated futures (10th to 90th percentile)" in names
    assert "Middle future (median), with irregular deposits" in names
    assert "Middle future (median), without irregular deposits" in names
    assert "Forecast with no randomness (an estimate)" in names


def test_the_chart_still_works_without_a_simulation() -> None:
    names = [t.name for t in forecast_chart(pd.DataFrame(), make_forecast(False), 20000).data]
    assert names == ["Forecast with no randomness (an estimate)"]


def test_a_planned_item_shows_in_the_placed_payments_with_its_own_description() -> None:
    items = [ScheduledItem(date(2026, 10, 10), -50000, "planned|3", "Concert tickets",
                           "entered", "planned by me: placed exactly on its day")]  # fmt: skip
    table = forecast_table(items, {}, set())  # no series behind it
    assert list(table["Name"]) == ["Concert tickets"]
    assert list(table["Based on"]) == ["entered"] and list(table["Amount ($)"]) == [-500.0]


def test_the_planned_table_says_whether_each_item_is_in_this_forecast() -> None:
    first, last = date(2026, 10, 1), date(2026, 10, 30)
    planned = [
        PlannedItem(1, date(2026, 9, 20), -7000, "Already happened", date(2026, 9, 29)),
        PlannedItem(2, date(2026, 10, 10), -50000, "Concert", date(2026, 9, 29)),
        PlannedItem(3, date(2027, 1, 2), 2000000, "Family, for the term", date(2026, 9, 29)),
    ]
    table = planned_table(planned, first, last)
    assert list(table.columns) == ["Date", "What", "Amount ($)", "Entered on", "In this forecast"]
    assert list(table["Amount ($)"]) == [-70.0, -500.0, 20000.0]  # money in is positive
    assert list(table["In this forecast"]) == [
        "no: the day has passed", "yes", "no: after the last forecast day",
    ]  # fmt: skip
    assert planned_table([], first, last).empty


def test_the_deposit_weeks_table_shows_each_week_with_its_types_and_what_was_set_aside() -> None:
    from lookahead.forecast.spending import DepositWeek
    from lookahead.ui import deposit_weeks_table

    weeks = [
        DepositWeek(date(2026, 9, 3), date(2026, 9, 9), (("Incoming e-Transfer", 6000),), (), 6000),
        DepositWeek(
            date(2026, 9, 10), date(2026, 9, 16), (("Incoming e-Transfer", 3000),),
            (BigEvent(date(2026, 9, 12), "Credit memo", 1500000, "Large deposit"),), 3000,
        ),
        DepositWeek(date(2026, 9, 17), date(2026, 9, 23), (), (), 0),
    ]  # fmt: skip
    table = deposit_weeks_table(weeks)
    assert list(table.columns) == ["Week", "In the stream ($)", "What", "Set aside"]
    assert list(table["Week"]) == ["2026-09-03 to 2026-09-09", "2026-09-10 to 2026-09-16",
                                   "2026-09-17 to 2026-09-23"]  # fmt: skip
    assert list(table["In the stream ($)"]) == [60.0, 30.0, 0.0]
    assert table.loc[0, "What"] == "Incoming e-Transfer 60.00"
    assert table.loc[1, "Set aside"] == "Large deposit 15,000.00 (2026-09-12, not forecast)"
    assert table.loc[2, "What"] == "nothing" and table.loc[2, "Set aside"] == ""
    assert deposit_weeks_table([]).empty


def test_every_forecast_line_and_the_band_start_at_the_last_observed_balance() -> None:
    """No gap between the recent balance and the forecast: each forecast trace starts on the
    last day of the data, at that day's closing balance."""
    forecast = make_forecast(True)
    figure = forecast_chart(pd.DataFrame(), forecast, 20000)
    anchor_day, anchor = forecast.start.data_last_updated, forecast.start.balance_cents / 100
    assert len(figure.data) == 5  # upper edge, lower edge (the band), two medians, the estimate
    for trace in figure.data:
        assert trace.x[0] == anchor_day and trace.y[0] == anchor
        assert len(trace.x) == len(forecast.days) + 1  # the anchor, then every forecast day


def test_the_estimate_line_also_starts_at_the_anchor_without_a_simulation() -> None:
    forecast = make_forecast(False)
    (trace,) = forecast_chart(pd.DataFrame(), forecast, 20000).data
    assert trace.x[0] == forecast.start.data_last_updated
    assert trace.y[0] == forecast.start.balance_cents / 100


def test_the_scenario_chart_shows_both_ranges_and_both_middles_from_the_same_start() -> None:
    from lookahead.ui import scenario_chart

    forecast = make_forecast(True)
    sim = forecast.simulation
    assert sim is not None
    from dataclasses import replace

    higher = {name: tuple(c + 500 for c in getattr(sim, name)) for name in ("p10", "p50", "p90")}
    modified = replace(sim, **higher)
    figure = scenario_chart(forecast, sim, modified, 20000)
    names = [t.name for t in figure.data]
    assert "Original: range (10th to 90th percentile)" in names
    assert "Original: middle future" in names
    assert "With the changes: range (10th to 90th percentile)" in names
    assert "With the changes: middle future" in names
    anchor_day, anchor = forecast.start.data_last_updated, forecast.start.balance_cents / 100
    for trace in figure.data:
        assert trace.x[0] == anchor_day and trace.y[0] == anchor  # no gap, nothing starts apart
