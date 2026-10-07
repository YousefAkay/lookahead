"""Tests for the Forecast page helpers: the table of placed payments and the hidden names."""

from datetime import date

import pandas as pd

from lookahead.forecast.schedule import ScheduledItem
from lookahead.forecast.spending import BreakdownRow
from lookahead.recurring_store import Stored
from lookahead.ui import BLANK, HIDDEN, breakdown_table, forecast_table, hidden_series


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
        BreakdownRow("Large one-offs", (0, 0, 0, 0, 0, 0, 5000.0), 5000.0),
    ]
    table = breakdown_table(rows)
    assert list(table.columns) == ["Type", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
                                   "Per week"]  # fmt: skip
    assert list(table["Type"]) == ["Card purchases", "Large one-offs", "All types"]
    assert table.loc[0, "Wed"] == 10.0 and table.loc[1, "Sun"] == 50.0  # cents -> dollars
    assert table.loc[2, "Per week"] == 60.0 and table.loc[2, "Wed"] == 10.0  # the total row


def test_an_empty_spending_table_is_empty() -> None:
    assert breakdown_table([]).empty
