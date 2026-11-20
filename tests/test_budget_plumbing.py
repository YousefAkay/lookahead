"""The forecaster pieces the budgets read (spec docs/specs/2026-11-20-budgets-that-forecast.md):
the shared week draws, the per-future cut, and the rows behind the everyday spending. None of
them changes the forecast. Synthetic demo personas only (never held-out), temporary databases."""

import shutil
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.forecast.path import build_forecast
from lookahead.forecast.simulate import BLOCK, SpendingCut, pool_week_starts, run, week_draws
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"
FUTURES = 60


@pytest.fixture(scope="module")
def persona_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("plumbing") / "p.db"
    conn = connect(db)
    for name in ("A", "C"):
        export = SYNTHETIC / f"persona_{name}.csv"
        store_exports(conn, f"synthetic-{name}", [(export.name, parse_export(export))],
                      datetime(2026, 10, 1))  # fmt: skip
    conn.close()
    return db


@pytest.fixture
def conn(persona_db: Path, tmp_path: Path) -> sqlite3.Connection:
    return connect(Path(shutil.copy(persona_db, tmp_path / "copy.db")))


def drawn_spending(f, future: int) -> list[int]:  # type: ignore[no-untyped-def]
    """Future `future`'s everyday spending by forecast day, rebuilt from the shared week draws."""
    inputs = f.inputs
    starts = pool_week_starts(inputs.start, inputs.history_start)
    horizon = (inputs.start.last_day - inputs.start.first_day).days + 1
    weeks = week_draws(inputs.seed, future, len(starts), -(-horizon // BLOCK))
    return [
        inputs.spending_daily.get(starts[weeks[k // BLOCK]] + timedelta(days=k % BLOCK), 0)
        for k in range(horizon)
    ]


@pytest.mark.parametrize("account", ["synthetic-A", "synthetic-C"])
@pytest.mark.parametrize("as_of", [date(2026, 7, 31), date(2026, 9, 10)])
def test_the_shared_week_draws_are_the_ones_the_futures_use(
    conn: sqlite3.Connection, account: str, as_of: date
) -> None:
    """Cutting exactly each future's rebuilt spending (money out only) gives the same futures as
    the existing 100 percent cut: the rebuilt days are the days `run` drew, to the cent."""
    f = build_forecast(conn, account, "weekday", as_of, None, futures=FUTURES)
    assert f is not None and f.inputs is not None
    cuts = [[max(s, 0) for s in drawn_spending(f, n)] for n in range(FUTURES)]
    start = f.inputs.start
    full = SpendingCut(start.first_day, start.last_day, percent=100)
    assert run(f.inputs, future_cuts=cuts, keep_paths=True) == run(f.inputs, full, keep_paths=True)


def test_no_cut_and_zero_cuts_change_nothing(conn: sqlite3.Connection) -> None:
    f = build_forecast(conn, "synthetic-A", "weekday", date(2026, 8, 14), None, futures=FUTURES)
    assert f is not None and f.inputs is not None
    horizon = len(f.days)
    zeros = [[0] * horizon for _ in range(FUTURES)]
    assert run(f.inputs, future_cuts=zeros, keep_paths=True) == run(f.inputs, keep_paths=True)
    assert run(f.inputs) == f.simulation


def test_a_cut_raises_each_future_by_its_running_total(conn: sqlite3.Connection) -> None:
    f = build_forecast(conn, "synthetic-A", "weekday", date(2026, 8, 14), None, futures=FUTURES)
    assert f is not None and f.inputs is not None
    horizon = len(f.days)
    cuts = [[(n + k) % 3 * 100 - 50 for k in range(horizon)] for n in range(FUTURES)]
    before = run(f.inputs, keep_paths=True).paths
    after = run(f.inputs, future_cuts=cuts, keep_paths=True).paths
    for n in range(FUTURES):
        total = 0
        for k in range(horizon):
            total += cuts[n][k]
            assert after[n][k] == before[n][k] + total


def test_the_spending_rows_add_up_to_the_everyday_spending(conn: sqlite3.Connection) -> None:
    for as_of in (date(2026, 6, 30), date(2026, 9, 1), None):
        f = build_forecast(conn, "synthetic-C", "weekday", as_of, None, futures=FUTURES)
        assert f is not None and f.inputs is not None
        daily: dict[date, int] = defaultdict(int)
        for _, day, cents in f.spending_rows.kept:
            daily[day] += cents
        assert dict(daily) == dict(f.inputs.spending_daily)
        assert not {r for r, _, _ in f.spending_rows.kept} & f.spending_rows.set_aside


def test_a_longer_horizon_keeps_the_first_30_days(conn: sqlite3.Connection) -> None:
    """The budgets may ask for 31 days (a 31-day month from its 1st): the first 30 days of each
    future are the forecast's own."""
    short = build_forecast(conn, "synthetic-A", "weekday", date(2026, 7, 31), None,
                           futures=FUTURES, keep_paths=True)  # fmt: skip
    long = build_forecast(conn, "synthetic-A", "weekday", date(2026, 7, 31), None,
                          horizon_days=31, futures=FUTURES, keep_paths=True)  # fmt: skip
    assert short is not None and long is not None and long.inputs is not None
    assert short.simulation is not None and long.simulation is not None
    assert [p[:30] for p in long.simulation.paths] == list(short.simulation.paths)
    window = replace(long.inputs, start=replace(long.inputs.start, last_day=short.start.last_day))
    assert run(window, keep_paths=True) == short.simulation
