"""Experiment 1 (eval/baselines.py): the scores and the random-walk baseline."""

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from eval.backtest import run_origin
from eval.baselines import (
    BINS,
    MIN_DIPS,
    alert_pool,
    balance_series,
    brier,
    crps_samples,
    dip_share,
    random_walk_paths,
    reliability,
    run_persona,
    scored_record,
    skill,
)
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

PID = "v2-SAL-01"
ORIGIN = date(2026, 3, 2)


# --- CRPS ---


def test_crps_of_one_draw_is_the_absolute_error() -> None:
    assert crps_samples(np.array([[100.0, 50.0]]), np.array([130.0, 20.0])).tolist() == [30, 30]


def test_crps_of_two_draws_by_hand() -> None:
    # draws 0 and 2, observed 1: mean |x - y| = 1, mean pair distance = (0 + 2 + 2 + 0) / 4 = 1,
    # CRPS = 1 - 1 / 2 = 0.5
    assert crps_samples(np.array([[0.0], [2.0]]), np.array([1.0])).tolist() == [0.5]


def test_crps_matches_the_pair_definition() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(37, 4)) * 1000
    y = rng.normal(size=4) * 1000
    brute = [
        np.abs(x[:, d] - y[d]).mean() - 0.5 * np.abs(x[:, d][:, None] - x[:, d][None, :]).mean()
        for d in range(4)
    ]
    assert crps_samples(x, y) == pytest.approx(brute)


def test_a_sharper_correct_forecast_scores_better() -> None:
    rng = np.random.default_rng(2)
    wide = rng.normal(0, 100, size=(200, 1))
    narrow = rng.normal(0, 10, size=(200, 1))
    assert crps_samples(narrow, np.array([0.0]))[0] < crps_samples(wide, np.array([0.0]))[0]


# --- The random walk ---


def test_random_walk_windows_end_on_or_before_the_origin() -> None:
    series = list(range(0, 1000, 10))  # 100 days, origin = index 99, +10 a day
    walk = random_walk_paths(series, horizon=30, lookback=365, minimum=20)
    assert walk is not None
    assert walk.shape == (70, 30)  # starts 0 .. 69: the last window ends exactly at the origin
    assert walk[0].tolist() == [series[-1] + 10 * k for k in range(1, 31)]


def test_random_walk_uses_only_the_lookback_days() -> None:
    series = [0] * 400 + [5] * 200  # the big jump is far back; only the last 365 days count
    walk = random_walk_paths(series, horizon=30, lookback=365, minimum=20)
    assert walk is not None and walk.shape[0] == 365 - 30 + 1


def test_random_walk_needs_enough_windows() -> None:
    assert random_walk_paths([0] * 49, horizon=30, minimum=20) is None
    assert random_walk_paths([0] * 50, horizon=30, minimum=20) is not None


def test_dip_share() -> None:
    paths = np.array([[10, 5], [10, 20], [3, 30]], dtype=float)
    assert dip_share(paths, 6) == pytest.approx(2 / 3)


# --- Brier, skill, reliability ---


def test_brier_and_skill() -> None:
    assert brier([(0.0, 0.0), (1.0, 1.0)]) == 0
    assert brier([(0.5, 1.0), (0.5, 0.0)]) == pytest.approx(0.25)
    assert brier([]) is None
    assert skill(0.1, 0.2) == pytest.approx(0.5)
    assert skill(0.1, 0.0) is None  # nothing to improve on
    assert skill(None, 0.2) is None


def _row(p: float, dipped: bool) -> dict:
    first = "2026-01-01" if dipped else None
    return {"shortfall_probability": p, "realized_first_below_buffer": first,
            "walk": {"shortfall_probability": p}}  # fmt: skip


def test_reliability_bins_count_and_average() -> None:
    rows = [_row(0.0, False), _row(0.005, False), _row(0.07, True), _row(0.07, False),
            _row(1.0, True)]  # fmt: skip
    table = reliability(rows, "f")
    assert [b["n"] for b in table] == [2, 0, 2, 0, 0, 0, 0, 1]
    assert table[2]["mean_p"] == pytest.approx(0.07) and table[2]["seen"] == 0.5
    assert table[-1]["seen"] == 1.0
    assert len(table) == len(BINS) - 1


# --- A real persona, end to end ---


@pytest.fixture(scope="module")
def persona_conn(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    tmp = tmp_path_factory.mktemp("exp1")
    p = build_persona(PID)
    export, _ = write_persona(p.persona, tmp, 42, START, END)
    conn = connect(tmp / "x.db")
    store_exports(conn, PID, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, p.buffer_cents)
    yield conn
    conn.close()


def test_balance_series_stops_at_the_origin(persona_conn) -> None:  # type: ignore[no-untyped-def]
    series = balance_series(persona_conn, PID, ORIGIN)
    first = persona_conn.execute(
        "SELECT MIN(posted_date) FROM transactions WHERE account = ?", (PID,)
    ).fetchone()[0]
    assert len(series) == (ORIGIN - date.fromisoformat(first)).days + 1
    last = persona_conn.execute(
        "SELECT balance_after_cents FROM transactions WHERE account = ? AND posted_date <= ?"
        " ORDER BY posted_date DESC, id LIMIT 1", (PID, ORIGIN.isoformat()),
    ).fetchone()[0]  # fmt: skip
    assert series[-1] == last


def test_a_later_origin_only_extends_the_series(persona_conn) -> None:  # type: ignore[no-untyped-def]
    early = balance_series(persona_conn, PID, ORIGIN)
    later = balance_series(persona_conn, PID, date(2026, 6, 1))
    assert later[: len(early)] == early  # what came after the origin never rewrites the past


def test_the_scored_record_is_the_default_forecast_with_extras(  # type: ignore[no-untyped-def]
    persona_conn,
) -> None:
    p = build_persona(PID)
    plain = run_origin(persona_conn, PID, "synthetic", ORIGIN, 40, p.archetype, False)
    rec = scored_record(persona_conn, PID, ORIGIN, 40, p.archetype)
    for key in ("p10", "p50", "p90", "shortfall_probability", "realized", "lowest_cents"):
        assert rec[key] == plain[key]  # the same forecast: only things are added
    q = np.array(rec["quantiles"])
    assert q.shape == (19, 30) and (np.diff(q, axis=0) >= 0).all()
    assert rec["crps_stays"] == pytest.approx(
        [round(abs(rec["start_balance_cents"] - b), 1) for b in rec["realized"]]
    )
    assert len(rec["crps_forecast"]) == 30 and min(rec["crps_forecast"]) >= 0
    assert rec["walk_windows"] >= 20 and len(rec["crps_walk"]) == 30
    assert 0 <= rec["walk"]["shortfall_probability"] <= 1


def test_a_run_is_resumable_and_refuses_held_out(tmp_path: Path) -> None:
    days = [date(2025, 12, 1), date(2025, 12, 8)]
    assert run_persona(PID, tmp_path / "out", days, 20, work=tmp_path / "work") == 2
    assert run_persona(PID, tmp_path / "out", days, 20, work=tmp_path / "work") == 0
    lines = (tmp_path / "out" / f"{PID}.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(x)["origin"] for x in lines] == ["2025-12-01", "2025-12-08"]
    with pytest.raises(ValueError):
        run_persona("v2-ITIGHT-04", tmp_path / "out2", days, 20, work=tmp_path / "work")


def test_the_alert_pool_keeps_origins_already_in_shortfall() -> None:
    rows = [
        {"in_shortfall_at_origin": False, "walk": {"x": 1}},
        {"in_shortfall_at_origin": True, "walk": {"x": 1}},
        {"in_shortfall_at_origin": False, "walk": None},
    ]
    assert alert_pool(rows) == rows[:2]  # the engine needs the run through a shortfall
    assert MIN_DIPS >= 2  # a skill from a single dip would be a ratio of noise


def test_a_tagged_rerun_keeps_its_own_records_and_report(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from eval import baselines

    for name in ("OUT", "REPORT", "TAG_ARG"):
        monkeypatch.setattr(baselines, name, getattr(baselines, name))
    baselines.use_tag("2026-11-19")
    assert baselines.OUT == Path("data/backtest/exp1-2026-11-19")
    assert baselines.REPORT == Path("docs/results/baselines_2026-11-19.md")
    assert baselines.TAG_ARG == " --tag 2026-11-19"
    with pytest.raises(ValueError):
        baselines.use_tag("../x")
