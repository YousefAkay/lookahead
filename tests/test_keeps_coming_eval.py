"""Tests for the keeps-coming comparison helpers. Made-up records only."""

from eval.keeps_coming_eval import identical_personas, verdict


def rec(
    persona: str,
    origin: str,
    arch: str = "SAL",
    p50: int = 100,
    arrivals: int = 0,
    bias: int = 0,
    err: int = 0,
) -> dict:  # type: ignore[type-arg]
    """A record with the fields the helpers and the metrics read."""
    realized = [1000 + err] * 30
    return {
        "persona": persona, "origin": origin, "archetype": arch, "held_out": False,
        "dataset": "synthetic", "in_shortfall_at_origin": False,
        "p10": [p50 - 1] * 30, "p50": [1000] * 30, "p90": [p50 + 1] * 30, "point": [1000] * 30,
        "median_lowest_cents": 1000 + bias, "shortfall_probability": 0.0, "lowest_cents": [1000],
        "realized": realized, "realized_lowest_cents": 1000, "start_balance_cents": 1000,
        "realized_first_below_buffer": None, "arrivals_in": arrivals, "arrivals_out": 0,
        "buffer_cents": 100, "history_days": 100, "format": 2, "logic": 0,
        "realized_below_zero": False,
    }  # fmt: skip


def test_a_persona_without_arrivals_must_equal_the_current_forecaster_field_for_field() -> None:
    off = [rec("a", "2026-01-05"), rec("b", "2026-01-05"), rec("c", "2026-01-05")]
    on = [
        rec("a", "2026-01-05"),
        rec("b", "2026-01-05", arrivals=2),
        rec("c", "2026-01-05", p50=99),
    ]
    same, differ = identical_personas(off, on)
    assert same == ["a"]  # b has an arrival: neither list; c has none but differs: a bug
    assert differ == ["c"]


def test_the_drop_rule_needs_more_than_half_of_the_archetypes_worse() -> None:
    archs = ["A", "B", "C", "D"]
    off = [rec(f"p{a}", "2026-01-05", a, err=100) for a in archs]

    def worse(n: int) -> list[dict]:  # type: ignore[type-arg]
        return [rec(f"p{a}", "2026-01-05", a, err=300 if i < n else 100)
                for i, a in enumerate(archs)]  # fmt: skip

    assert verdict(off, worse(2))["drop"] is False  # exactly half is not more than half
    assert verdict(off, worse(3))["drop"] is True
    assert verdict(off, worse(0))["worse"]["mae30"] == []
    small = [rec(f"p{a}", "2026-01-05", a, err=100 + 0) for a in archs]
    assert verdict(off, small)["drop"] is False  # identical: nothing is worse


def test_a_run_with_the_switch_on_never_mixes_with_one_with_it_off(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json
    from datetime import date

    import pytest

    from eval.backtest import KEEPS_NAMES, run_persona
    from eval.keeps_coming_eval import folders

    days = [date(2026, 6, 1)]
    out = tmp_path / "on"
    assert (
        run_persona("v2-SAL-01", out, days, 20, 42, tmp_path / "w", False, KEEPS_NAMES["with-own"])
        == 1
    )
    line = json.loads((out / "v2-SAL-01.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["keeps_coming"] == "with-own" and "arrivals_in" in line and "arrivals_out" in line
    with pytest.raises(ValueError, match="different settings"):
        run_persona("v2-SAL-01", out, days, 20, 42, tmp_path / "w", False, None)  # the switch off
    assert set(folders(tmp_path)) == {"off", "with-own", "without-own"}
