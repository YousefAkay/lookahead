"""A chance read from the simulated futures says 100% only when every future does it, and 0% only
when none does; anything that would round to them reads ">99%" or "<1%"."""

import re
from datetime import date
from pathlib import Path

import pytest

from lookahead.ask.answer import _forecast, _whatif
from lookahead.ask.engine import ForecastAnswer, WhatIfAnswer
from lookahead.chance import chance_text

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("dipped", "shown"),
    [(0, "0%"), (1, "<1%"), (2, "<1%"), (3, "1%"), (210, "42%"), (497, "99%"), (498, ">99%"),
     (499, ">99%"), (500, "100%")],
)  # fmt: skip
def test_a_count_out_of_500_futures(dipped: int, shown: str) -> None:
    assert chance_text(dipped / 500) == shown


def test_ask_never_says_100_percent_when_one_future_stays_above() -> None:
    day = date(2026, 10, 31)
    one_stays = ForecastAnswer("chance_below", day, date(2026, 10, 2), chance=499 / 500,
                               below_cents=50_000, futures=500, below_is_buffer=True)  # fmt: skip
    assert _forecast(one_stays).startswith("A >99% chance")
    assert "(499 of 500 simulated futures)" in _forecast(one_stays)
    every = ForecastAnswer("chance_below", day, date(2026, 10, 2), chance=1.0,
                           below_cents=50_000, futures=500)  # fmt: skip
    assert _forecast(every).startswith("A 100% chance")
    whatif = WhatIfAnswer(-1000, 5000, 499 / 500, 1 / 500, 0, 0, date(2026, 10, 2), day,
                          50_000, 500)  # fmt: skip
    assert "at <1% instead of >99%" in _whatif(whatif)


WHOLE_PERCENT = re.compile(r"\{([^{}]+):\.0%\}")
NOT_FROM_FUTURES = {  # whole percents that are not a share of simulated futures
    "chosen.confidence",  # Label review: the categorizer's confidence
    "p['threshold']", "chosen",  # Model report: alert thresholds
    "CHANGE_SHARE", "PROBABILITY_THRESHOLD", "MATERIAL_SHARE", "DOMINANT_SHARE",  # fixed rules
    "share",  # Ask: a what-if's share of usual spending; labels: a share of rows; chance.py itself
}


def _sources() -> list[tuple[str, int, str]]:
    return [
        (str(path.relative_to(ROOT)), i, line)
        for folder in ("app", "lookahead")
        for path in sorted((ROOT / folder).rglob("*.py"))
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
    ]


def test_no_page_formats_a_chance_from_the_futures_with_plain_rounding() -> None:
    """Every whole percent in the app is either on the list of fixed rules and other shares, or
    goes through chance_text (a chance from the simulated futures)."""
    found = [
        f"{where}:{i} {m.group(1)}"
        for where, i, line in _sources()
        for m in WHOLE_PERCENT.finditer(line)
        if m.group(1) not in NOT_FROM_FUTURES
    ]
    assert found == []


def test_every_in_app_link_opens_in_the_whole_window() -> None:
    """On Community Cloud the app runs in a frame: a link opening in the frame loads Cloud's page a
    second time, inside the first."""
    assert [f"{w}:{i}" for w, i, line in _sources() if 'target="_self"' in line] == []


def test_a_time_machine_step_saved_with_the_old_wording_is_worded_again() -> None:
    from lookahead.timemachine_snapshots import alert_view

    step = {"buffer_cents": 20_000, "shortfall_probability": 499 / 500, "alert": {
        "condition": True, "notified": True, "reason": None, "active": True,
        "message": "Possible shortfall: 100% of simulated futures dip below your $200.00 buffer",
        "snapshot": {"data_date": "2026-10-01", "low_day": "2026-10-31", "low_cents": -25_140,
                     "probability": 499 / 500, "buffer_cents": 20_000,
                     "median_lowest_cents": -25_140}}}  # fmt: skip
    sentence = alert_view(step).sentence
    assert sentence.startswith("Possible shortfall: >99% of simulated futures")
    assert "100%" not in sentence
