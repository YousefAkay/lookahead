"""Tests for the arm runner of step 3 (DECISIONS.md, 2026-10-11). Synthetic personas only."""

from datetime import date

import pytest

from eval.arms import arm_dir, arm_options, window_kind
from lookahead.forecast.path import ForecastOptions


def test_the_arms_are_the_recorded_option_sets_and_4_and_5_are_not_run() -> None:
    assert arm_options(0) == ForecastOptions()
    assert arm_options(1) == ForecastOptions(lump_arrivals=True)
    two, three = arm_options(2), arm_options(3, frozenset({"k"}))
    assert two.lump_arrivals and two.lump_proposals and two.confirm_all_lumps
    assert three.lump_arrivals and three.lump_proposals and not three.confirm_all_lumps
    assert three.confirmed_keys == {"k"} and not two.outflow_placements
    for arm in (4, 5):
        with pytest.raises(ValueError, match="not run"):
            arm_options(arm)


def test_each_arm_and_set_has_its_own_folder_and_arm_0_is_the_backtest() -> None:
    folders = {arm_dir(a, long) for a in range(4) for long in (False, True)}
    assert len(folders) == 8
    assert arm_dir(0, False).parts[-2:] == ("backtest", "full")
    assert arm_dir(0, True).parts[-3:] == ("backtest", "long", "full")


def test_a_window_is_classed_by_tuition_first_then_family_money() -> None:
    record = {"origin": "2026-01-05"}
    tuition, family = {date(2026, 1, 20)}, {date(2026, 1, 10)}
    assert window_kind(record, tuition, family) == "with a tuition payment"
    assert window_kind(record, set(), family) == "family money, no tuition"
    assert window_kind(record, {date(2026, 2, 20)}, {date(2026, 1, 5)}) == "neither"  # outside
