"""Tests for the arm runner of step 2 (DECISIONS.md, 2026-10-12). Synthetic personas only."""

from datetime import date

import pytest

from eval.arms import arm_dir, arm_options, window_kind
from lookahead.forecast.path import ForecastOptions


def test_the_arms_are_the_recorded_option_sets_with_keeps_coming_and_no_random_lumps() -> None:
    assert arm_options(0) == ForecastOptions()
    three = arm_options(3)
    assert three.outflow_placements and three.outflow_rule == "calendar"
    assert not any(arm_options(a).lump_arrivals for a in (0, 3))  # keeps-coming replaces them
    for retired in (1, 2, 4):  # a) lump proposals, retired on 2026-10-12
        with pytest.raises(ValueError, match="retired"):
            arm_options(retired)
    assert arm_options(5) == ForecastOptions() and not arm_options(5).renewal
    assert arm_options(6) == ForecastOptions(renewal=True) == arm_options(7) == arm_options(8)
    with pytest.raises(ValueError):
        arm_options(9)


def test_the_drop_rule_counts_archetypes_more_than_1_percent_worse() -> None:
    from eval.arms import worse_archetypes

    base = {"A": {"error30": 100.0, "bias": -50.0}, "B": {"error30": 100.0, "bias": 50.0}}
    arm = {"A": {"error30": 101.0, "bias": -50.6}, "B": {"error30": 101.1, "bias": 50.4}}
    assert worse_archetypes(arm, base) == (["B"], ["A"])


def test_a_forecast_that_changes_with_nothing_confirmed_or_placed_is_flagged() -> None:
    from eval.arms import FORECAST_FIELDS, changed_without_cause

    base = {"persona": "p", "origin": "2026-01-05", **{f: [1] for f in FORECAST_FIELDS}}
    same = {**base, "confirmed": 0, "placements": 0}
    moved = {**base, "confirmed": 0, "placements": 0, "p50": [2]}
    caused = {**moved, "placements": 1}
    assert changed_without_cause([same], [base]) == []
    assert changed_without_cause([moved], [base]) == ["p 2026-01-05"]
    assert changed_without_cause([caused], [base]) == []


def test_each_arm_and_set_has_its_own_folder_and_arm_0_is_the_backtest() -> None:
    folders = {arm_dir(a, long) for a in (0, 3, 5, 6, 7, 8) for long in (False, True)}
    assert len(folders) == 12
    assert arm_dir(0, False).parts[-2:] == ("backtest", "full")
    assert arm_dir(0, True).parts[-3:] == ("backtest", "long", "full")


def test_a_window_is_classed_by_tuition_first_then_family_money() -> None:
    record = {"origin": "2026-01-05"}
    tuition, family = {date(2026, 1, 20)}, {date(2026, 1, 10)}
    assert window_kind(record, tuition, family) == "with a tuition payment"
    assert window_kind(record, set(), family) == "family money, no tuition"
    assert window_kind(record, {date(2026, 2, 20)}, {date(2026, 1, 5)}) == "neither"  # outside
