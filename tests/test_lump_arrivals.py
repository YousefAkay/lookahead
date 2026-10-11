"""Tests for the random lump arrivals (an option of the forecaster, off by default; DECISIONS.md
2026-10-11, step 0). Made-up rows, and three synthetic development personas for the check that
the switch off changes nothing."""

import hashlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.flows import Txn, flow_types
from lookahead.forecast import path as _path
from lookahead.forecast.path import ForecastOptions
from lookahead.forecast.scenario import Scenario, run_scenario
from lookahead.forecast.simulate import Simulation, run
from lookahead.forecast.spending import lump_arrivals
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

KEEPS_OFF = {"keeps": None}  # these tests pin the option on the forecaster without keeps-coming


def build_forecast(*args, **kwargs):  # type: ignore[no-untyped-def]
    return _path.build_forecast(*args, **{**KEEPS_OFF, **kwargs})


ACCOUNT = "test"
CARD = "Opos {:<20}toron "
ON = ForecastOptions(lump_arrivals=True)
# These tests pin the lump option against the forecaster as it was before the recurring-placement
# fix became the default, so that fix is switched off in the "off" case.
NO_FIX = ForecastOptions(recurring_fix=None)
LUMPS = (date(2026, 1, 20), date(2026, 5, 15), date(2026, 8, 25))  # three big credit memos


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str, bal: int) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, bal, f"fp{n:04d}"),
        )


def fill(conn: sqlite3.Connection, extra: tuple[tuple[date, int, str, str], ...] = ()) -> None:
    """A phone bill on the 10th, a card purchase every 4 days, three big family deposits."""
    rows: list[tuple[date, int, str, str]] = []
    for m in range(1, 10):
        rows.append((shift_to_business_day(monthly_date(2026, m, 10), "later"), -6500,
                     "bill payment", "Phone Co "))  # fmt: skip
    day = date(2026, 1, 2)
    while day <= date(2026, 9, 28):
        rows.append((day, -2000, "pos purchase", CARD.format("Bookshop")))
        day += timedelta(days=4)
    rows += [(d, 300000 + 10000 * k, "credit memo", "1/Family ") for k, d in enumerate(LUMPS)]
    rows += list(extra)
    balance = 200000
    for when, cents, kind, text in sorted(rows, key=lambda r: r[0]):
        balance += cents
        insert(conn, when, cents, kind, text, balance)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "lumps.db")
    fill(c)
    return c


# --- The inputs: rate and sizes ---


def test_the_rate_is_the_lumps_of_the_window_over_its_days_and_the_sizes_are_theirs(
    conn: sqlite3.Connection,
) -> None:
    forecast = build_forecast(conn, ACCOUNT, "weekday", futures=10, options=ON)
    assert forecast is not None and forecast.inputs is not None
    inputs = forecast.inputs
    days = (date(2026, 9, 27) - date(2026, 1, 2)).days + 1  # history shorter than 365 days
    assert inputs.lump_rate == pytest.approx(3 / days)
    assert inputs.lump_sizes == (300000, 310000, 320000)


def test_the_window_is_365_days_when_the_history_is_longer() -> None:
    as_of = date(2026, 9, 30)
    rows = [Txn(1, date(2024, 1, 2), -2000, "pos purchase", CARD.format("Shop"))]
    rows += [Txn(10 + i, as_of - timedelta(days=3 * i), -2000, "pos purchase",
                 CARD.format("Shop")) for i in range(30)]  # fmt: skip
    rows += [Txn(2, date(2025, 9, 1), 400000, "credit memo", "1/Family "),  # too old
             Txn(3, date(2026, 3, 2), 400000, "credit memo", "1/Family ")]  # fmt: skip
    rate, sizes = lump_arrivals(rows, flow_types(rows), set(), as_of, date(2024, 1, 2))
    assert rate == pytest.approx(1 / 365) and sizes == (400000,)


def test_with_no_history_the_rate_is_zero() -> None:
    assert lump_arrivals([], {}, set(), date(2026, 9, 30), None) == (0.0, ())


def test_later_rows_never_change_the_rate_or_the_sizes(tmp_path: Path) -> None:
    """Rule zero: rows after the forecast date are deleted, then rewritten; nothing moves."""
    as_of = date(2026, 6, 30)
    a = connect(tmp_path / "a.db")
    fill(a)
    b = connect(tmp_path / "b.db")
    fill(b, ((date(2026, 7, 6), 900000, "credit memo", "1/Family "),
             (date(2026, 7, 20), 800000, "deposit", "Someone ")))  # fmt: skip
    with b:  # rewrite every later amount
        b.execute("UPDATE transactions SET amount_cents = amount_cents * 3 WHERE posted_date > ?",
                  (as_of.isoformat(),))  # fmt: skip
    c = connect(tmp_path / "c.db")
    fill(c)
    with c:  # delete every later row
        c.execute("DELETE FROM transactions WHERE posted_date > ?", (as_of.isoformat(),))
    forecasts = [build_forecast(x, ACCOUNT, "weekday", as_of, futures=50, options=ON)
                 for x in (a, b, c)]  # fmt: skip
    inputs = [f.inputs for f in forecasts if f is not None and f.inputs is not None]
    assert len(inputs) == 3
    assert len({(i.lump_rate, i.lump_sizes) for i in inputs}) == 1
    assert inputs[0].lump_sizes == (300000, 310000)  # the August lump is after the date
    sims = [f.simulation for f in forecasts if f is not None]
    assert sims[0] == sims[1] == sims[2]


def test_rows_after_the_date_are_ignored_even_when_they_are_passed_in() -> None:
    as_of = date(2026, 9, 30)
    rows = [Txn(10 + i, as_of - timedelta(days=3 * i), -2000, "pos purchase",
                CARD.format("Shop")) for i in range(60)]  # fmt: skip
    early = rows + [Txn(1, date(2026, 6, 1), 400000, "credit memo", "1/Family ")]
    later = early + [Txn(2, date(2026, 10, 20), 900000, "credit memo", "1/Family ")]
    start = as_of - timedelta(days=200)
    assert lump_arrivals(later, flow_types(later), set(), as_of, start) == lump_arrivals(
        early, flow_types(early), set(), as_of, start
    )


def test_pass_throughs_and_series_deposits_are_not_lumps() -> None:
    as_of = date(2026, 9, 30)
    rows = [Txn(10 + i, as_of - timedelta(days=3 * i), -2000, "pos purchase",
                CARD.format("Shop")) for i in range(60)]  # fmt: skip
    rows += [
        Txn(1, date(2026, 9, 1), 2000000, "credit memo", "1/Family "),  # paid straight out
        Txn(2, date(2026, 9, 2), -1950000, "bill payment", "City University "),
        Txn(3, date(2026, 6, 1), 500000, "credit memo", "1/Family "),  # in a series (below)
        Txn(4, date(2026, 4, 1), 600000, "credit memo", "1/Family "),  # a lump
    ]  # fmt: skip
    rate, sizes = lump_arrivals(rows, flow_types(rows), {3}, as_of, as_of - timedelta(days=200))
    assert sizes == (600000,)


# --- In the futures ---


def test_switched_off_the_forecast_is_unchanged(conn: sqlite3.Connection) -> None:
    off = build_forecast(conn, ACCOUNT, "weekday", futures=200)
    default = build_forecast(conn, ACCOUNT, "weekday", futures=200, options=ForecastOptions())
    assert off is not None and default is not None and off.simulation == default.simulation
    assert off.inputs is not None and off.inputs.lump_rate == 0 and off.inputs.lump_sizes == ()
    assert off.simulation is not None
    assert off.simulation.p50 == off.simulation.median_without  # the old rule: lumps set aside


def test_a_lump_comes_back_in_the_futures_with_deposits_and_not_in_the_median_without(
    conn: sqlite3.Connection,
) -> None:
    off = build_forecast(conn, ACCOUNT, "weekday", futures=500, keep_paths=True)
    on = build_forecast(conn, ACCOUNT, "weekday", futures=500, keep_paths=True, options=ON)
    assert off is not None and on is not None
    a, b = off.simulation, on.simulation
    assert a is not None and b is not None
    assert b.median_without == a.median_without  # the median without deposits ignores lumps
    assert b.shortfall_probability_without == a.shortfall_probability_without
    assert on.days == off.days  # the single-path estimate does not change
    with_lump = sum(p[-1] != q[-1] for p, q in zip(a.paths, b.paths, strict=True))
    assert 0 < with_lump < 500  # about 1 - (1 - 3/269) ** 30, a quarter of the futures
    assert max(b.p90) >= max(a.p90)


def test_adding_lumps_moves_no_other_draw(conn: sqlite3.Connection) -> None:
    """Every future with lumps is the same future without them plus whole lumps on some days."""
    off = build_forecast(conn, ACCOUNT, "weekday", futures=300, keep_paths=True)
    on = build_forecast(conn, ACCOUNT, "weekday", futures=300, keep_paths=True, options=ON)
    assert off is not None and on is not None and on.inputs is not None
    sizes = set(on.inputs.lump_sizes)
    assert off.simulation is not None and on.simulation is not None
    for p, q in zip(off.simulation.paths, on.simulation.paths, strict=True):
        diff = [b - a for a, b in zip(p, q, strict=True)]
        steps = [d - c for c, d in zip([0, *diff], diff, strict=False)]
        assert all(s == 0 or s in sizes for s in steps)  # at most one whole lump a day


def test_the_same_seed_gives_the_same_lumps_and_another_seed_others(
    conn: sqlite3.Connection,
) -> None:
    forecast = build_forecast(conn, ACCOUNT, "weekday", futures=200, options=ON)
    assert forecast is not None and forecast.inputs is not None
    inputs = replace(forecast.inputs, lump_rate=0.2)
    assert run(inputs) == run(inputs)
    assert run(inputs).p90 != run(replace(inputs, seed=1)).p90


def test_no_other_money_turns_the_lumps_off(conn: sqlite3.Connection) -> None:
    forecast = build_forecast(conn, ACCOUNT, "weekday", futures=300, options=ON)
    assert forecast is not None and forecast.simulation is not None
    result = run_scenario(forecast, Scenario(no_other_money=True))
    assert result.modified.p50 == forecast.simulation.median_without


# --- Switched off, the forecaster is the one from before the option existed ---


def fingerprint(s: Simulation) -> str:
    parts = (
        s.days, s.futures, s.p10, s.p50, s.p90, s.median_without, s.shortfall_probability,
        s.shortfall_probability_without, s.below_zero_probability,
        s.below_zero_probability_without, s.median_lowest_cents, s.median_lowest_without_cents,
        s.weeks_drawn_from, s.lowest_cents, s.paths,
    )  # fmt: skip
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


# Taken with the forecaster before the option was written (commit 98dbcf9), 100 futures, seed 42
# of the generated histories, every future's path kept.
BEFORE = {
    ("v2-ITIGHT-01", date(2026, 1, 5)): "9566668ef1eac4b9",
    ("v2-ITIGHT-01", date(2026, 5, 4)): "74a069044a2f0c89",
    ("v2-ITIGHT-01", date(2026, 8, 31)): "b91d605e12ea132b",
    ("v2-IFAM-02", date(2026, 1, 5)): "da1639a9d6e55d68",
    ("v2-IFAM-02", date(2026, 5, 4)): "9f7bee2d69e6c51f",
    ("v2-IFAM-02", date(2026, 8, 31)): "26c18d0048b73eb9",
    ("v2-SAL-01", date(2026, 1, 5)): "46fd35bcbe429f7e",
    ("v2-SAL-01", date(2026, 5, 4)): "190a187cddee71e6",
    ("v2-SAL-01", date(2026, 8, 31)): "9f00739a1ae48028",
}


@pytest.mark.slow
@pytest.mark.parametrize("pid", ["v2-ITIGHT-01", "v2-IFAM-02", "v2-SAL-01"])
def test_switched_off_three_synthetic_personas_are_byte_for_byte_as_before(
    pid: str, tmp_path: Path
) -> None:  # fmt: skip
    p = build_persona(pid)
    export, _ = write_persona(p.persona, tmp_path, 42, START, END)
    conn = connect(tmp_path / "x.db")
    store_exports(conn, pid, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, p.buffer_cents)
    lumps_seen = 0
    for (who, origin), expected in BEFORE.items():
        if who != pid:
            continue
        off = build_forecast(conn, pid, "weekday", origin, None, 30, 100, keep_paths=True,
                             options=NO_FIX)  # fmt: skip
        assert off is not None and off.simulation is not None
        assert fingerprint(off.simulation) == expected
        on = build_forecast(conn, pid, "weekday", origin, None, 30, 100, keep_paths=True,
                            options=replace(ON, recurring_fix=None))  # fmt: skip
        assert on is not None and on.inputs is not None
        lumps_seen += len(on.inputs.lump_sizes)
    if pid != "v2-SAL-01":
        assert lumps_seen > 0  # the students have lumps, so the option has something to do
