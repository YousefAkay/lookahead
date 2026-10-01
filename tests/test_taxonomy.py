"""Tests for the fixed label sets."""

import pytest

from lookahead.categories import conflicts
from lookahead.taxonomy import CATEGORIES, check_labels


def test_seventeen_categories() -> None:
    assert len(CATEGORIES) == 17 == len(set(CATEGORIES))
    assert "Gifts & donations" in CATEGORIES
    assert "Personal care" in CATEGORIES
    # Added next to its neighbour, so the label screen lists them together.
    assert CATEGORIES.index("Personal care") == CATEGORIES.index("Health & pharmacy") + 1


def test_personal_care_is_a_valid_label() -> None:
    check_labels("Personal care", "everyday")
    with pytest.raises(ValueError):
        check_labels("Haircuts", "everyday")


@pytest.mark.parametrize(
    ("flow", "bad"), [("everyday", False), ("recurring_outflow", False), ("refund", False),
                      ("income", True), ("transfer", True)],
)  # fmt: skip
def test_personal_care_follows_the_usual_conflict_rules(flow: str, bad: bool) -> None:
    assert conflicts(flow, "Personal care") is bad


def test_labels_and_preferences_accept_personal_care(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from lookahead.categories import set_category_preference
    from lookahead.db import connect

    conn = connect(tmp_path / "t.db")
    set_category_preference(conn, "Barbier Lune", "Personal care")


# --- Gifts & donations (17th, added during labelling) ---


def test_gifts_and_donations_is_a_valid_label() -> None:
    check_labels("Gifts & donations", "everyday")


@pytest.mark.parametrize(
    ("flow", "bad"),
    [("everyday", False), ("recurring_outflow", False), ("refund", False),
     ("transfer", False),  # an e-Transfer you override as a gift
     ("income", True)],
)  # fmt: skip
def test_gifts_conflict_rules(flow: str, bad: bool) -> None:
    assert conflicts(flow, "Gifts & donations") is bad


def test_other_spending_categories_still_conflict_with_a_transfer() -> None:
    assert conflicts("transfer", "Groceries") and conflicts("transfer", "Personal care")


def test_e_transfers_stay_transfers_by_default() -> None:
    from datetime import date

    from lookahead.categories import category_by_rules
    from lookahead.flows import Txn

    row = Txn(1, date(2026, 9, 1), -5_000, "withdrawal", "Free Interac E-Transfer ")
    assert category_by_rules([row])[1].category == "Transfers"
