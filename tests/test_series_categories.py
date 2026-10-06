"""A series' category: yours if you gave one, else the most common among its payments."""

from datetime import date

import pandas as pd

from lookahead.recurring_store import Stored
from lookahead.views import series_categories


def series(key: str, evidence: tuple[str, ...], category: str | None = None) -> Stored:
    return Stored(
        key=key, name=key, kind="merchant", direction="out", cadence="monthly",
        anchor=date(2026, 1, 1), anchor_day=1, amount_type="fixed", expected_cents=-100,
        range_cents=(-100, -100), next_expected=date(2026, 8, 1), status="detected",
        active=True, evidence=evidence, category=category,
    )  # fmt: skip


def rows(*cells: tuple[str, str | None, date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fingerprint": [c[0] for c in cells],
            "category": pd.Series([c[1] for c in cells], dtype="object"),
            "date": pd.to_datetime([c[2] for c in cells]),
        }
    )


def test_the_most_common_category_of_the_payments_is_an_estimate() -> None:
    table = rows(("a", "Subscriptions", date(2026, 1, 1)), ("b", "Subscriptions", date(2026, 2, 1)),
                 ("c", "Shopping", date(2026, 3, 1)))  # fmt: skip
    assert series_categories(table, [series("s", ("a", "b", "c"))]) == {
        "s": ("Subscriptions", True)
    }


def test_my_own_category_overrides_and_is_not_an_estimate() -> None:
    table = rows(("a", "Shopping", date(2026, 1, 1)), ("b", "Shopping", date(2026, 2, 1)))
    out = series_categories(table, [series("s", ("a", "b"), category="Subscriptions")])
    assert out == {"s": ("Subscriptions", False)}


def test_a_tie_goes_to_the_category_of_the_latest_payment() -> None:
    table = rows(("a", "Shopping", date(2026, 1, 1)), ("b", "Subscriptions", date(2026, 2, 1)))
    assert series_categories(table, [series("s", ("a", "b"))])["s"] == ("Subscriptions", True)


def test_payments_without_a_category_are_ignored_and_none_means_none() -> None:
    table = rows(("a", None, date(2026, 1, 1)), ("b", "Rent", date(2026, 2, 1)),
                 ("c", None, date(2026, 3, 1)))  # fmt: skip
    assert series_categories(table, [series("s", ("a", "b", "c"))])["s"] == ("Rent", True)
    assert series_categories(table, [series("t", ("a", "c"))])["t"] == (None, False)
    assert series_categories(table, [series("u", ())])["u"] == (None, False)  # no payments


def test_only_the_payments_of_that_series_count() -> None:
    table = rows(("a", "Rent", date(2026, 1, 1)), ("b", "Shopping", date(2026, 2, 1)),
                 ("c", "Shopping", date(2026, 3, 1)))  # fmt: skip
    out = series_categories(table, [series("r", ("a",)), series("s", ("b", "c"))])
    assert out["r"][0] == "Rent" and out["s"][0] == "Shopping"
