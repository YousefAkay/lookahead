"""Forecaster v2's parts (pre-registration 2026-11-27): F fees on their real dates, a an own
spending rate per future, b a stretch per day ahead, d random-walk futures. Every part is off by
default; made-up accounts only."""

import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from eval import v2_dev
from lookahead.db import connect
from lookahead.flows import Flow, Txn
from lookahead.forecast.path import DEFAULT_OPTIONS, build_forecast
from lookahead.forecast.simulate import week_draws
from lookahead.forecast.v2 import fee_plan, mix_walk, stretch, walk_windows

ACCOUNT = "acct"


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str, bal: int) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, bal, f"fp{n:04d}"),
        )


def account(tmp_path: Path, fees: list[tuple[date, int, str, str]]) -> sqlite3.Connection:
    """Card purchases every 3 days of varying size from January to September, a pay deposit on the
    1st, and the given fee rows."""
    conn = connect(tmp_path / "a.db")
    rows: list[tuple[date, int, str, str]] = list(fees)
    day = date(2025, 9, 2)
    k = 0
    while day <= date(2026, 9, 27):
        rows.append((day, -(1500 + 700 * (k % 5)), "pos purchase", "Apos Bookshop #12 Montr"))
        day += timedelta(days=3)
        k += 1
    for m in range(9, 13):
        rows.append((date(2025, m, 1), 120000, "payroll deposit", "Employer "))
    for m in range(1, 10):
        rows.append((date(2026, m, 1), 120000, "payroll deposit", "Employer "))
    balance = 300000
    for when, cents, kind, text in sorted(rows, key=lambda r: r[0]):
        balance += cents
        insert(conn, when, cents, kind, text, balance)
    return conn


def forecast(conn: sqlite3.Connection, **changes: object):  # type: ignore[no-untyped-def]
    f = build_forecast(conn, ACCOUNT, "weekday", None, None, 30, 200, keep_paths=True,
                       options=replace(DEFAULT_OPTIONS, **changes))  # fmt: skip
    assert f is not None and f.simulation is not None
    return f


MONTHLY = [(date(2026, 7, 5), -495, "service charge", "Monthly fee "),
           (date(2026, 8, 5), -495, "service charge", "Monthly fee "),
           (date(2026, 9, 4), -495, "service charge", "Monthly fee "),
           (date(2026, 9, 20), -300, "overdraft interest", "Interest ")]  # fmt: skip


# --- F ---


def test_f_equals_v1_on_an_account_with_no_fee_rows(tmp_path: Path) -> None:
    conn = account(tmp_path, [])
    v1, f = forecast(conn), forecast(conn, fee_dates=True)
    assert f.simulation == v1.simulation and f.days == v1.days


def test_f_places_a_monthly_fee_on_its_day_and_draws_the_others_with_their_week(
    tmp_path: Path,
) -> None:
    conn = account(tmp_path, MONTHLY)
    f = forecast(conn, fee_dates=True)
    fee_days = [(d.day, d.fees_cents) for d in f.days if d.fees_cents]
    assert fee_days == [(date(2026, 10, 4), 495)]  # within 2 days of the 4th/5th: the 4th
    paths = np.asarray(f.simulation.paths)
    assert len(set(paths[:, 0])) > 1 or paths[0, 0] == f.start.balance_cents  # no certain cut
    v1 = forecast(conn)
    assert all(d.fees_cents > 0 for d in v1.days)  # v1 spreads them over every day


def test_a_fee_at_the_end_of_the_month_is_matched_from_the_end() -> None:
    rows = [Txn(i, d, -900, "service charge", "Monthly") for i, d in
            enumerate((date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31)))]  # fmt: skip
    flows = {t.row_id: Flow("bank_fee", "bank fee") for t in rows}
    plan = fee_plan(rows, flows, date(2026, 9, 2), date(2026, 9, 3), 30)
    assert plan.placed == ((date(2026, 9, 30), 900),)  # the last day of September
    assert not plan.extra_daily


def test_a_fee_seen_in_one_month_only_is_drawn_with_its_week() -> None:
    rows = [Txn(1, date(2026, 8, 12), -1200, "service charge", "Wire")]
    flows = {1: Flow("bank_fee", "bank fee")}
    plan = fee_plan(rows, flows, date(2026, 9, 2), date(2026, 9, 3), 30)
    assert plan.placed == () and plan.extra_daily == {date(2026, 8, 12): 1200}


def test_f_reads_nothing_after_the_forecast_date(tmp_path: Path) -> None:
    """Rule zero: fees posted after the date of a replayed forecast change nothing."""
    conn = account(tmp_path, MONTHLY)
    as_of = date(2026, 8, 31)
    before = build_forecast(conn, ACCOUNT, "weekday", as_of, None, 30, 100,
                            options=replace(DEFAULT_OPTIONS, fee_dates=True))  # fmt: skip
    insert(conn, date(2026, 9, 25), -9999, "service charge", "Late fee ", 1)
    after = build_forecast(conn, ACCOUNT, "weekday", as_of, None, 30, 100,
                           options=replace(DEFAULT_OPTIONS, fee_dates=True))  # fmt: skip
    assert before is not None and after is not None
    assert before.simulation == after.simulation and before.days == after.days


# --- a ---


def test_own_rate_resamples_the_pool_per_future_and_keeps_the_week_stream() -> None:
    plain = week_draws(7, 3, 13, 5)
    own = week_draws(7, 3, 13, 5, own_rate=True)
    assert own == week_draws(7, 3, 13, 5, own_rate=True)  # same numbers, same weeks
    assert all(0 <= i < 13 for i in own) and own != plain
    assert week_draws(7, 3, 0, 5, own_rate=True) == []


def test_own_rate_widens_the_band_on_a_made_up_account(tmp_path: Path) -> None:
    conn = account(tmp_path, [])
    v1, a = forecast(conn), forecast(conn, own_rate=True)
    width = [hi - lo for lo, hi in zip(v1.simulation.p10, v1.simulation.p90, strict=True)]
    width_a = [hi - lo for lo, hi in zip(a.simulation.p10, a.simulation.p90, strict=True)]
    assert width_a[-1] > width[-1]


# --- b ---


def test_stretch_widens_around_the_median_and_never_narrows() -> None:
    paths = [[100, 100], [90, 80], [110, 130]]
    assert stretch(paths, []) == paths and stretch(paths, [1.0, 1.0]) == paths
    assert stretch(paths, [2.0, 1.5]) == [[100, 100], [80, 70], [120, 145]]
    with pytest.raises(ValueError):
        stretch(paths, [0.9, 1.0])


def test_the_fitted_k_is_at_least_one_and_never_falls_with_the_day() -> None:
    rng = np.random.default_rng(0)
    rows = []
    for i in range(40):
        raw = np.cumsum(rng.normal(0, 10, (500, 30)), axis=1).astype(np.int64)
        realized = np.cumsum(rng.normal(0, 25 if i % 2 else 5, 30))  # sometimes wider than the band
        rows.append({"raw": {"F": raw}, "record": {"realized": realized.tolist()}})
    k = v2_dev.fit_k({"a": rows[:20], "b": rows[20:]}, "F")
    assert len(k) == 30 and all(x >= 1 for x in k)
    assert all(b >= a for a, b in zip(k, k[1:], strict=False))


def test_the_cross_fit_groups_put_every_unit_in_one_of_five() -> None:
    groups = v2_dev.group_of("12")
    assert set(groups.values()) == set(range(5))
    assert sorted(groups) == sorted(v2_dev.units("12"))


# --- d ---


def test_no_walk_futures_with_less_than_a_year_of_history() -> None:
    closing = list(range(400))
    assert walk_windows(closing, 30, history_days=364) == ()
    windows = walk_windows(closing, 30, history_days=400)
    assert windows and all(len(w) == 30 for w in windows)
    assert windows[0][0] == 399 + 1  # the origin's balance plus a one-day change of 1


def test_mix_walk_replaces_a_fixed_share_from_day_one_the_same_every_time() -> None:
    paths = [[0] * 30 for _ in range(10)]
    windows = [tuple(range(1, 31)), tuple(range(2, 32))]
    mixed = mix_walk(paths, windows, 0.3, 0)
    assert mixed == mix_walk(paths, windows, 0.3, 0)
    assert all(m[0] > 0 for m in mixed[:3]) and mixed[3:] == paths[3:]
    assert mix_walk(paths, windows, 0.0, 0) == paths and mix_walk(paths, (), 0.5, 0) == paths


# --- all parts together ---


def test_the_same_random_numbers_give_the_same_futures(tmp_path: Path) -> None:
    conn = account(tmp_path, MONTHLY)
    changes = {"fee_dates": True, "own_rate": True, "stretch": tuple([1.2] * 30),
               "walk_share": 0.2}  # fmt: skip
    one, two = forecast(conn, **changes), forecast(conn, **changes)
    assert one.simulation == two.simulation
