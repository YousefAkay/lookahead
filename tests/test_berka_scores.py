"""Berka scores: the shared metric summary (hand-made records) and the held-out refusal."""

import pandas as pd
import pytest

from eval import berka_scores as b
from eval.berka_split import load_split


def _record(persona: str, realized: list[int], p10: list[int], p90: list[int], *,
            dip_chance: float, dipped: bool, median_low: int, real_low: int) -> dict:  # fmt: skip
    days = len(realized)
    return {
        "persona": persona, "origin": "2020-01-06", "in_shortfall_at_origin": False,
        "realized": realized, "p10": p10, "p90": p90, "start_balance_cents": 1000,
        "crps_forecast": [100.0] * days, "crps_stays": [200.0] * days, "crps_walk": [400.0] * days,
        "shortfall_probability": dip_chance,
        "realized_first_below_buffer": "2020-01-10" if dipped else None,
        "median_lowest_cents": median_low, "realized_lowest_cents": real_low,
        "walk": {"shortfall_probability": 0.5},
    }  # fmt: skip


def test_summary_arithmetic() -> None:
    a = _record("1", [10, 20], [0, 0], [30, 30], dip_chance=1.0, dipped=True,
                median_low=500, real_low=300)  # fmt: skip
    c = _record("2", [10, 99], [0, 0], [30, 30], dip_chance=0.0, dipped=False,
                median_low=300, real_low=500)  # fmt: skip
    out = b.summary_core([a, c])  # the alert part is the engine's own, tested elsewhere
    assert out["n"] == 2 and out["units"] == 2 and out["dips"] == 1
    assert out["skill_n"] == pytest.approx(0.5) and out["skill_r"] == pytest.approx(0.75)
    assert out["coverage30"] == pytest.approx(0.5)  # day 30: 20 inside, 99 outside
    assert out["coverage_all"] == pytest.approx(0.75)
    assert out["brier_f"] == pytest.approx(0.0)  # both dip chances exactly right
    assert out["brier_n"] == pytest.approx(0.5) and out["brier_r"] == pytest.approx(0.25)
    assert out["bias"] == pytest.approx(0.0) and out["abs_bias"] == pytest.approx(2.0)


def test_held_out_accounts_are_refused() -> None:
    account = int(load_split()["primary_held_out"][0])  # type: ignore[index]
    with pytest.raises(ValueError):
        b.account_records(account, pd.DataFrame(), b.OUT / "never")
