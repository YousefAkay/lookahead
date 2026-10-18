"""Experiment 3 (eval/anchor.py): the shift, its inputs, and the cases it must leave alone."""

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from eval import anchor
from eval.backtest import run_origin_full
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

PID = "v2-SAL-01"
LATE = date(2026, 5, 4)  # more than 91 days of history
EARLY = date(2025, 12, 1)  # eight weeks of history: under the 91 days


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    tmp = tmp_path_factory.mktemp("exp3")
    p = build_persona(PID)
    export, _ = write_persona(p.persona, tmp, 42, START, END)
    c = connect(tmp / "x.db")
    store_exports(c, PID, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(c, p.buffer_cents)
    yield c
    c.close()


def test_the_shift_grows_with_the_forecast_day() -> None:
    assert shifts_list(10.0, 4) == [10.0, 20.0, 30.0, 40.0]
    assert shifts_list(-5.5, 2) == [-5.5, -11.0]


def shifts_list(delta: float, days: int) -> list[float]:
    return anchor.shifts(delta, days).tolist()


def test_u_forecast_is_what_is_left_after_the_start_and_the_scheduled_items() -> None:
    # 5 futures, 30 days; the final balance averages start + scheduled + 30 x (-200)
    draws = np.zeros((5, 30))
    draws[:, -1] = 100_000 + 50_000 - 30 * 200
    assert anchor.unscheduled_forecast_flow(draws, 100_000, 50_000) == pytest.approx(-200.0)


def test_a_zero_shift_changes_nothing_and_the_definitions_match_the_simulator(conn) -> None:  # type: ignore[no-untyped-def]
    record, paths, _ = run_origin_full(conn, PID, "synthetic", LATE, 60, "SAL", False)
    draws = np.asarray(paths, dtype=float)
    realized = np.asarray(record["realized"], dtype=float)
    same = anchor.anchored(draws, record, 0.0, record["buffer_cents"], realized)
    for key in ("p10", "p50", "p90", "point"):
        assert same[key] == record[key]
    assert same["shortfall_probability"] == record["shortfall_probability"]
    assert same["median_lowest_cents"] == record["median_lowest_cents"]
    assert same["moved"] is False


def test_every_quantile_moves_by_the_same_amount_on_a_day(conn) -> None:  # type: ignore[no-untyped-def]
    record, paths, _ = run_origin_full(conn, PID, "synthetic", LATE, 60, "SAL", False)
    draws = np.asarray(paths, dtype=float)
    realized = np.asarray(record["realized"], dtype=float)
    moved = anchor.anchored(draws, record, 1000.0, record["buffer_cents"], realized)
    add = 1000 * np.arange(1, 31)
    assert np.array(moved["p50"]).tolist() == (np.array(record["p50"]) + add).tolist()
    assert moved["moved"] is True
    lower = anchor.anchored(draws, record, -1_000_000.0, record["buffer_cents"], realized)
    assert lower["shortfall_probability"] == 1.0  # everything is pushed under the buffer


def test_below_the_minimum_the_forecast_is_left_exactly_as_it_is(conn) -> None:  # type: ignore[no-untyped-def]
    record, paths, f = run_origin_full(conn, PID, "synthetic", EARLY, 40, "SAL", False)
    rec = anchor.finish_origin(conn, PID, EARLY, record, np.asarray(paths, float), f)
    assert rec["qualifies"] is False
    for key in ("1", "0.5"):
        v = rec["variants"][key]
        assert v["delta"] == 0.0 and v["moved"] is False
        assert v["p50"] == rec["p50"] and v["shortfall_probability"] == rec["shortfall_probability"]


def test_a_long_history_with_rows_is_moved_by_the_trailing_figure(conn) -> None:  # type: ignore[no-untyped-def]
    record, paths, f = run_origin_full(conn, PID, "synthetic", LATE, 40, "SAL", False)
    rec = anchor.finish_origin(conn, PID, LATE, record, np.asarray(paths, float), f)
    assert rec["qualifies"] is True
    one, half = rec["variants"]["1"]["delta"], rec["variants"]["0.5"]["delta"]
    assert one == pytest.approx(rec["u_trail"] - rec["u_fc"])
    assert half == pytest.approx(one / 2)
    assert len(rec["variants"]["1"]["crps"]) == 30 and len(rec["crps_default"]) == 30


def test_the_bootstrap_resamples_accounts_with_a_fixed_seed() -> None:
    default = np.linspace(100, 200, 50)
    better = default * 0.97
    a = anchor.bootstrap_gain(default, better)
    assert a == anchor.bootstrap_gain(default, better)  # the same every time
    assert a[0] == pytest.approx(0.03) and a[1] > 0  # a steady 3% gain: the lower end is above 0
    worse = default * 1.02
    assert anchor.bootstrap_gain(default, worse)[2] < 0


def test_held_out_personas_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        anchor.run_persona("v2-ITIGHT-04", False, tmp_path, [LATE], 20)


def _record(moved: bool, p50_shift: int = 0) -> dict:
    base = {f: [1, 2] for f in ("p10", "p50", "p90", "point")}
    base |= {"shortfall_probability": 0.1, "median_lowest_cents": 5}
    variant = {**{f: [x + p50_shift for x in base[f]] for f in ("p10", "p50", "p90", "point")},
               "shortfall_probability": 0.1, "median_lowest_cents": 5, "moved": moved}  # fmt: skip
    return {**base, "variants": {"1": dict(variant), "0.5": dict(variant)}}


def test_unmoved_origins_are_counted_and_checked_against_the_default() -> None:
    same = [_record(False), _record(False), _record(True, 7)]
    assert anchor.unmoved_differences(same) == (4, 0)  # 2 records x 2 variants, none differ
    broken = [_record(False, 3)]  # not moved, yet different: a bug the check must see
    assert anchor.unmoved_differences(broken) == (2, 2)
