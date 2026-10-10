"""Tests for the error diagnosis (eval/diagnose.py) and the persona database helper.

Synthetic development personas only, in temporary folders.
"""

from datetime import date
from pathlib import Path

import pytest

from eval.diagnose import expected_from_pool, origin_parts, part_of
from eval.personas_db import persona_db
from lookahead.forecast.path import build_forecast
from lookahead.generator.schedules import GeneratedTxn


def _txn(series_id: str | None = None, one_off: bool = False, category: str = "Groceries"
         ) -> GeneratedTxn:  # fmt: skip
    flow = "income" if category == "Income" else "everyday"
    return GeneratedTxn(date(2026, 1, 1), -100, "pos purchase", "M", category, flow,
                        series_id=series_id, one_off=one_off)  # fmt: skip


def test_parts_follow_the_answer_key() -> None:
    assert part_of(_txn("v2-ITIGHT-01-tuition")) == "tuition"
    assert part_of(_txn("v2-ITIGHT-01-tuition-funding", category="Income")) == "family"
    assert part_of(_txn("v2-ITIGHT-01-tuition-topup", category="Income")) == "family"
    assert part_of(_txn("v2-ITIGHT-01-family", category="Income")) == "family"
    assert part_of(_txn("v2-FREE-01-gigs", category="Income")) == "other_irregular"
    assert part_of(_txn("v2-SAL-01-pay", category="Income")) == "regular_income"
    assert part_of(_txn("v2-IFAM-01-allowance", category="Income")) == "regular_income"
    assert part_of(_txn("v2-SAL-01-rent")) == "bills"
    assert part_of(_txn(one_off=True)) == "one_time"
    assert part_of(_txn(category="Fees & interest")) == "fees"
    assert part_of(_txn()) == "everyday"


def test_expected_from_pool_averages_each_day_of_the_week() -> None:
    weeks = [date(2026, 1, 5), date(2026, 1, 12)]
    values = {date(2026, 1, 5): 100.0, date(2026, 1, 13): 50.0}  # day 0 of week 1, day 1 of 2
    # 30 days: days 0 and 1 of the week occur 5 times each, the others 4 times
    assert expected_from_pool(values, weeks, 30) == pytest.approx(5 * 50 + 5 * 25)
    assert expected_from_pool(values, [], 30) == 0.0


@pytest.mark.slow
def test_parts_add_up_to_the_mean_of_the_futures(tmp_path: Path) -> None:
    """The expected parts plus the start balance are the mean future on day 30 (SAL has no date
    jitter: every payment offset is 0), and the happened parts add up to what was observed."""
    with persona_db("v2-SAL-01", tmp_path) as db:
        origin = date(2026, 2, 2)
        parts = origin_parts(db, origin, 100)
        forecast = build_forecast(db.conn, "v2-SAL-01", "weekday", origin, None, 30, 4000,
                                  keep_paths=True)  # fmt: skip
        assert forecast is not None and forecast.simulation is not None
        mean_30 = sum(p[-1] for p in forecast.simulation.paths) / 4000
    expected_30 = parts["start_cents"] + sum(parts["expected"].values())
    assert abs(expected_30 - mean_30) < 0.02 * abs(mean_30)
    assert parts["start_cents"] + sum(parts["happened"].values()) == parts["realized_30"]


def test_persona_db_refuses_held_out(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="held-out"), persona_db("v2-SAL-04", tmp_path):
        pass
