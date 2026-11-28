"""Forecaster v2's parts (pre-registration 2026-11-27): F fees on their real dates, a an own
spending rate per future, b a stretch per day ahead, d random-walk futures. Every part is off by
default; made-up accounts only."""

import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from eval import v2_dev
from eval.baselines import crps_samples
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
    assert fee_days == [(date(2026, 10, 4), 495)]  # the latest charge's day and amount
    assert f.days[0].fees_cents == 0  # no certain cut on the first day
    v1 = forecast(conn)
    assert all(d.fees_cents > 0 for d in v1.days)  # v1 spreads them over every day
    # the interest (seen once) is drawn with its week: it is in the everyday spending pool
    assert f.inputs is not None
    assert f.inputs.spending_daily.get(date(2026, 9, 20), 0) >= 300


def _fees(*rows: tuple[date, int, str]) -> tuple[list[Txn], dict[int, Flow]]:
    txns = [Txn(i, d, c, "service charge", text) for i, (d, c, text) in enumerate(rows)]
    return txns, {t.row_id: Flow("bank_fee", "bank fee") for t in txns}


def test_a_fee_matched_only_from_the_end_of_the_month() -> None:
    """31 January and 28 February are 3 days apart by day number but both the last day."""
    txns, flows = _fees((date(2026, 1, 31), -900, "Monthly"), (date(2026, 2, 28), -900, "Monthly"))
    plan = fee_plan(txns, flows, date(2026, 3, 10), date(2026, 3, 11), 30)
    assert plan.placed == ((date(2026, 3, 31), 900),) and not plan.extra_daily


def test_a_fee_of_a_repeating_kind_on_another_day_is_drawn_with_its_week() -> None:
    txns, flows = _fees(
        (date(2026, 7, 5), -495, "Fee"), (date(2026, 8, 5), -495, "Fee"),
        (date(2026, 8, 20), -495, "Fee"), (date(2026, 9, 4), -495, "Fee"),
    )  # fmt: skip
    plan = fee_plan(txns, flows, date(2026, 9, 27), date(2026, 9, 28), 30)
    assert plan.placed == ((date(2026, 10, 4), 495),)
    assert plan.extra_daily == {date(2026, 8, 20): 495}  # not lost


def test_an_off_day_latest_fee_does_not_hide_the_monthly_one() -> None:
    txns, flows = _fees((date(2026, 7, 5), -495, "Fee"), (date(2026, 8, 5), -495, "Fee"),
                        (date(2026, 8, 28), -700, "Fee"))  # fmt: skip
    plan = fee_plan(txns, flows, date(2026, 9, 2), date(2026, 9, 3), 30)
    assert plan.placed == ((date(2026, 9, 5), 495),)
    assert plan.extra_daily == {date(2026, 8, 28): 700}


def test_the_latest_amount_is_placed_and_a_day_31_fee_lands_on_the_30th() -> None:
    txns, flows = _fees((date(2026, 7, 31), -400, "Fee"), (date(2026, 8, 31), -450, "Fee"))
    plan = fee_plan(txns, flows, date(2026, 9, 2), date(2026, 9, 3), 30)
    assert plan.placed == ((date(2026, 9, 30), 450),)


def test_a_fee_is_placed_only_after_its_latest_charge() -> None:
    txns, flows = _fees((date(2026, 7, 5), -495, "Fee"), (date(2026, 8, 5), -495, "Fee"),
                        (date(2026, 9, 3), -495, "Fee"))  # fmt: skip
    plan = fee_plan(txns, flows, date(2026, 9, 4), date(2026, 9, 5), 30)
    assert plan.placed == ((date(2026, 10, 3), 495),)  # not again on 5 September


def test_the_three_months_are_calendar_months() -> None:
    """A fee on 1 July counts for a forecast on 30 September (92 days would miss it)."""
    txns, flows = _fees((date(2026, 7, 1), -500, "Fee"), (date(2026, 9, 1), -500, "Fee"))
    plan = fee_plan(txns, flows, date(2026, 9, 30), date(2026, 10, 1), 30)
    assert plan.placed == ((date(2026, 10, 1), 500),)


def test_a_fee_reversal_is_drawn_as_money_back() -> None:
    txns = [Txn(1, date(2026, 8, 12), -1200, "service charge", "Wire"),
            Txn(2, date(2026, 8, 14), 1200, "service charge", "Wire reversal")]  # fmt: skip
    flows = {1: Flow("bank_fee", "bank fee"), 2: Flow("bank_fee", "fee reversal")}
    plan = fee_plan(txns, flows, date(2026, 9, 2), date(2026, 9, 3), 30)
    assert plan.extra_daily == {date(2026, 8, 12): 1200, date(2026, 8, 14): -1200}


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


def test_walk_windows_are_the_baselines_random_walk() -> None:
    from eval.baselines import random_walk_paths

    rng = np.random.default_rng(3)
    closing = list(np.cumsum(rng.integers(-500, 500, 420)) + 100000)
    ours = np.asarray(walk_windows(closing, 30, history_days=len(closing)))
    theirs = random_walk_paths(closing)
    assert theirs is not None and np.array_equal(ours, theirs)


def test_d_reads_nothing_after_the_forecast_date(tmp_path: Path) -> None:
    """Rule zero for the walk futures: balances posted after a replayed forecast's date change
    nothing."""
    conn = account(tmp_path, [])
    as_of = date(2026, 9, 1)
    opts = replace(DEFAULT_OPTIONS, walk_share=0.5)
    before = build_forecast(conn, ACCOUNT, "weekday", as_of, None, 30, 100, options=opts)
    insert(conn, date(2026, 9, 25), -500000, "pos purchase", "Apos Big #1 Montr", -1)
    after = build_forecast(conn, ACCOUNT, "weekday", as_of, None, 30, 100, options=opts)
    assert before is not None and after is not None
    assert before.simulation == after.simulation


def test_b_and_d_leave_the_without_deposits_futures_alone(tmp_path: Path) -> None:
    conn = account(tmp_path, [])
    plain = forecast(conn)
    changed = forecast(conn, stretch=tuple([3.0] * 30), walk_share=0.5)
    assert changed.simulation.median_without == plain.simulation.median_without
    assert changed.simulation.p90 != plain.simulation.p90


# --- the development harness, on a tiny made-up saved set ---


def _fake_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(11)
    names = {s: [f"{s}-u{i}" for i in range(10)] for s in v2_dev.SETS}
    monkeypatch.setattr(v2_dev, "OUT", tmp_path / "v2dev")
    monkeypatch.setattr(v2_dev, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(v2_dev, "REPORT", tmp_path / "report.md")
    monkeypatch.setattr(v2_dev, "V1_EXPECTED", {})
    monkeypatch.setattr(v2_dev, "_code", lambda: "test")
    monkeypatch.setattr(v2_dev, "units", lambda which: names[which])
    for which, ids in names.items():
        (tmp_path / "v2dev" / which).mkdir(parents=True)
        for u in ids:
            records, paths, walks = [], {k: [] for k in v2_dev.RAW}, []
            for o in range(3):
                start = 100000
                raw = {k: start + np.cumsum(rng.normal(0, 300, (500, 30)), axis=1).astype(np.int64)
                       for k in v2_dev.RAW}  # fmt: skip
                realized = start + np.cumsum(rng.normal(0, 700, 30))
                walk = start + np.cumsum(rng.normal(0, 600, (40, 30)), axis=1)
                rec = {"persona": u, "origin": f"2026-0{o + 1}-01", "in_shortfall_at_origin": False,
                       "buffer_cents": 50000, "start_balance_cents": start,
                       "realized": realized.round().astype(int).tolist(), "history_days": 400,
                       "realized_first_below_buffer": None,
                       "realized_lowest_cents": int(realized.min())}  # fmt: skip
                rec.update(v2_dev.record_of({"record": rec}, raw["v1"]))
                rec["crps_stays"] = np.abs(start - realized).tolist()
                rec["crps_walk"] = crps_samples(walk, realized).tolist()
                rec["walk"] = {"shortfall_probability": 0.0}
                records.append(rec)
                walks.append(walk)
                for k in v2_dev.RAW:
                    paths[k].append(raw[k])
            v2_dev._save(tmp_path / "v2dev" / which / f"{u}.npz", records, paths, walks, "test")


def test_the_harness_scores_every_variant_and_records_them_before_showing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_sets(tmp_path, monkeypatch)
    assert v2_dev.score() == 0
    lines = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert [e.get("variant") for e in lines[:5]] == list(v2_dev.VARIANTS)
    for e in lines[:5]:
        assert len(e["folds"]) == 5 and e["futures_code"] == "test"
        if e["per_set"] is not None:
            assert all(m["n_origins"] == 30 and m["n_units"] == 10 for m in e["per_set"].values())
    stretched = next(e for e in lines if e.get("variant") == "F+b")
    assert all(min(f["k"]) >= 1 for f in stretched["folds"] if f["k"])
    assert (tmp_path / "report.md").exists()
    assert v2_dev.score() == 1  # no second run


def test_the_harness_refuses_futures_from_mixed_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_sets(tmp_path, monkeypatch)
    one = tmp_path / "v2dev" / "12" / "12-u0.npz"
    d = dict(np.load(one))
    d["code"] = np.array("other")
    np.savez_compressed(one, **d)
    assert v2_dev.score() == 1
    assert not (tmp_path / "ledger.jsonl").exists()
