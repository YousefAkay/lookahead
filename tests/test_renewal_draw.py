"""Tests for the renewal draw of the keeps-coming deposits (an option of the forecaster, off by
default; DECISIONS.md, 2026-10-12). Made-up rows, and three synthetic development personas for the
check that the switch off changes nothing."""

import hashlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from eval.diagnose_real import PARTS, origin_record
from lookahead.db import connect, connect_readonly
from lookahead.forecast.path import ForecastOptions, build_forecast
from lookahead.forecast.scenario import Scenario, run_scenario
from lookahead.forecast.simulate import Simulation, _renewal_arrivals, run
from lookahead.forecast.spending import renewal_gaps
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

ACCOUNT = "test"
ON = ForecastOptions(renewal=True)
FIRST = date(2025, 1, 5)
GAPS = (30, 21, 35, 28, 24, 33, 27, 31, 29, 22, 36)  # the days between 12 family deposits


def days_of(gaps: tuple[int, ...], first: date = FIRST) -> list[date]:
    out = [first]
    for g in gaps:
        out.append(out[-1] + timedelta(days=g))
    return out


# --- the gaps and the fallback ---


def test_gaps_are_between_distinct_days_and_a_day_counts_once() -> None:
    days = days_of((30, 30, 30, 30, 30, 30, 30))
    gaps, last, state = renewal_gaps([*days, days[2]], days[-1] + timedelta(days=5))
    assert gaps == (30,) * 7 and last == days[-1] and state == "draw"


def test_fewer_than_six_gaps_fall_back_to_the_constant_rate() -> None:
    days = days_of((30,) * 5)  # 5 gaps
    assert renewal_gaps(days, days[-1])[::2] == ((), "fallback")
    days = days_of((30,) * 6)  # 6 gaps: enough
    assert renewal_gaps(days, days[-1])[2] == "draw"


def test_fewer_than_two_gaps_longer_than_the_wait_fall_back() -> None:
    days = days_of((10, 12, 14, 16, 18, 40))  # one gap longer than 20 days
    assert renewal_gaps(days, days[-1] + timedelta(days=20))[::2] == ((), "fallback")
    assert renewal_gaps(days, days[-1] + timedelta(days=5))[2] == "draw"  # five gaps longer
    days = days_of((10, 12, 14, 16, 40, 45))  # two longer than 20: a draw
    assert renewal_gaps(days, days[-1] + timedelta(days=20))[2] == "draw"
    assert renewal_gaps(days, days[-1] + timedelta(days=20))[0] == (10, 12, 14, 16, 40, 45)


# --- the draw itself ---


def test_the_first_arrival_follows_a_gap_longer_than_the_wait_and_the_rest_any_gap() -> None:
    last = date(2026, 3, 1)
    first_day = last + timedelta(days=26)  # 25 days waited
    gaps = (10, 12, 28, 33)  # only 28 and 33 are longer than 25
    for future in range(300):
        out = _renewal_arrivals(0, future, gaps, last, first_day, (700,), 60)
        hits = [k for k, v in enumerate(out) if v]
        assert hits, "an arrival comes inside 60 days"
        assert hits[0] in (28 - 26, 33 - 26)  # day of the gap, counted from the first forecast day
        assert all(b - a in gaps for a, b in zip(hits, hits[1:], strict=False))
        assert set(out) <= {0, 700}


def test_the_draw_is_the_same_for_the_same_seed_and_differs_between_futures() -> None:
    last, first_day = date(2026, 3, 1), date(2026, 3, 11)
    rest = ((20, 30, 40), last, first_day, (500, 900), 30)
    assert _renewal_arrivals(3, 5, *rest) == _renewal_arrivals(3, 5, *rest)
    assert len({tuple(_renewal_arrivals(3, f, *rest)) for f in range(40)}) > 3


def test_a_deposit_is_more_likely_when_due_than_right_after_the_last() -> None:
    gaps = (28, 30, 31, 29, 32, 30)
    first_day = date(2026, 3, 2)

    def share(waited: int) -> float:
        last = first_day - timedelta(days=waited + 1)
        hits = sum(any(_renewal_arrivals(0, f, gaps, last, first_day, (1,), 7))
                   for f in range(400))  # fmt: skip
        return hits / 400

    assert share(3) == 0.0  # 3 days after one, none is due inside the next week
    assert share(25) == 1.0  # 25 days after one, every gap is due inside the next week


# --- inside the forecaster ---


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str, bal: int) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, bal, f"fp{n:04d}"),
        )


def fill(conn: sqlite3.Connection, deposits: int, until: date | None = None) -> date:
    """A card purchase every 4 days and `deposits` big family deposits. Returns the last day."""
    rows: list[tuple[date, int, str, str]] = []
    family = days_of(GAPS)[:deposits]
    stop = until or family[-1] + timedelta(days=6)
    day = FIRST - timedelta(days=60)
    while day <= stop:
        rows.append((day, -2000, "pos purchase", "Opos Bookshop        toron "))
        day += timedelta(days=4)
    rows += [(d, 300000 + 10000 * (k % 3), "credit memo", "1/Family ")
             for k, d in enumerate(family) if d <= stop]  # fmt: skip
    balance = 200000
    for when, cents, kind, text in sorted(rows, key=lambda r: r[0]):
        balance += cents
        insert(conn, when, cents, kind, text, balance)
    return stop


def fingerprint(s: Simulation) -> str:
    parts = (
        s.days, s.futures, s.p10, s.p50, s.p90, s.median_without, s.shortfall_probability,
        s.shortfall_probability_without, s.below_zero_probability,
        s.below_zero_probability_without, s.median_lowest_cents, s.median_lowest_without_cents,
        s.weeks_drawn_from, s.lowest_cents, s.paths,
    )  # fmt: skip
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    set_safety_buffer_cents(c, 20000)
    return c


def test_with_enough_gaps_the_forecast_uses_the_draw_and_the_option_off_does_not(
    conn: sqlite3.Connection,
) -> None:  # fmt: skip
    fill(conn, 12)
    off = build_forecast(conn, ACCOUNT, futures=50)
    on = build_forecast(conn, ACCOUNT, futures=50, options=ON)
    assert off is not None and on is not None
    assert off.arrivals.renewal == "" and off.arrivals.in_gaps == ()
    assert on.arrivals.renewal == "draw" and len(on.arrivals.in_gaps) >= 6
    assert on.arrivals.in_rate == off.arrivals.in_rate  # the rate stays (for what is shown)
    assert on.simulation is not None and off.simulation is not None
    assert fingerprint(on.simulation) != fingerprint(off.simulation)


def test_with_too_few_deposits_the_option_is_the_constant_rate_to_the_digit(
    conn: sqlite3.Connection,
) -> None:  # fmt: skip
    fill(conn, 8)  # 6 recurring-size deposits (the first two have no earlier ones): 5 gaps
    off = build_forecast(conn, ACCOUNT, futures=50, keep_paths=True)
    on = build_forecast(conn, ACCOUNT, futures=50, keep_paths=True, options=ON)
    assert off is not None and on is not None and on.simulation is not None
    assert on.arrivals.renewal == "fallback" and on.arrivals.in_gaps == ()
    assert on.arrivals.in_rate > 0
    assert off.simulation is not None
    assert fingerprint(on.simulation) == fingerprint(off.simulation)


def test_rule_zero_later_rows_change_nothing(tmp_path: Path) -> None:
    """Every forecast date uses only rows posted by then: deleting the later rows leaves the
    gaps, the days waited and the futures exactly as they were."""
    full = connect(tmp_path / "full.db")
    set_safety_buffer_cents(full, 20000)
    fill(full, 12)
    days = days_of(GAPS)
    for origin in (days[8] + timedelta(days=9), days[10] + timedelta(days=17)):
        c2 = connect(tmp_path / f"cut-{origin}.db")
        set_safety_buffer_cents(c2, 20000)
        fill(c2, 12, until=origin)
        a = build_forecast(full, ACCOUNT, as_of=origin, futures=40, keep_paths=True, options=ON)
        b = build_forecast(c2, ACCOUNT, as_of=origin, futures=40, keep_paths=True, options=ON)
        assert a is not None and b is not None and a.simulation and b.simulation
        assert a.arrivals.in_gaps == b.arrivals.in_gaps and a.arrivals.in_last == b.arrivals.in_last
        assert a.arrivals.renewal == b.arrivals.renewal == "draw"
        assert a.arrivals.in_last is not None and a.arrivals.in_last <= origin
        assert fingerprint(a.simulation) == fingerprint(b.simulation)
        c2.close()


def test_no_other_money_turns_the_draw_off_like_the_constant_rate(
    conn: sqlite3.Connection,
) -> None:  # fmt: skip
    fill(conn, 12)
    on = build_forecast(conn, ACCOUNT, futures=60, options=ON)
    assert on is not None and on.inputs is not None
    result = run_scenario(on, Scenario(no_other_money=True))
    plain = run(replace(on.inputs, arrivals_in_rate=0.0, deposits_daily={}, lump_rate=0.0))
    assert fingerprint(result.modified) == fingerprint(plain)


def test_the_real_account_breakdown_adds_up_with_the_draw_and_counts_its_mean(
    tmp_path: Path,
) -> None:  # fmt: skip
    db = connect(tmp_path / "d.db")
    set_safety_buffer_cents(db, 20000)
    days = days_of(GAPS)
    fill(db, 12, until=days[10] + timedelta(days=40))
    db.close()
    conn = connect_readonly(tmp_path / "d.db")
    drawn = 0
    try:
        for origin in (days[9] + timedelta(days=9), days[10] + timedelta(days=3)):
            a = origin_record(conn, ACCOUNT, origin, 60, options=ON)
            b = origin_record(conn, ACCOUNT, origin, 60)  # the constant rate
            assert sum(a["error_30"].values()) == pytest.approx(a["total_30"])
            for k in range(30):
                expected = sum(a["expected_cum"][p][k] for p in PARTS)
                assert expected == pytest.approx(a["mean_path"][k] - a["start_cents"])
            drawn += a["expected_cum"]["keeps_in"] != b["expected_cum"]["keeps_in"]
    finally:
        conn.close()
    assert drawn, "at least one origin used the draw, whose mean is not the constant rate's"


# --- the switch off is the forecaster of today ---

# Taken with the forecaster as merged (commit b76f40f, keeps-coming and the recurring fix on),
# 100 futures, seed 42 of the generated histories, every future's path kept.
BEFORE = {
    ("v2-ITIGHT-01", date(2026, 1, 5)): "9dc5e311eb2509a2",
    ("v2-ITIGHT-01", date(2026, 5, 4)): "f9c75794a1b58353",
    ("v2-ITIGHT-01", date(2026, 8, 31)): "19b6dd9c9007e75d",
    ("v2-IFAM-02", date(2026, 1, 5)): "f77e8bfa27b3f24f",
    ("v2-IFAM-02", date(2026, 5, 4)): "d489abb8c312cfeb",
    ("v2-IFAM-02", date(2026, 8, 31)): "5eceb637641ea479",
    ("v2-SAL-01", date(2026, 1, 5)): "9a25e7942358603c",
    ("v2-SAL-01", date(2026, 5, 4)): "fc41625634358928",
    ("v2-SAL-01", date(2026, 8, 31)): "f0930f734cc6375d",
}


@pytest.mark.slow
@pytest.mark.parametrize("pid", ["v2-ITIGHT-01", "v2-IFAM-02", "v2-SAL-01"])
def test_switched_off_three_synthetic_personas_are_byte_for_byte_as_before(
    pid: str, tmp_path: Path
) -> None:  # fmt: skip
    p = build_persona(pid)
    export, _ = write_persona(p.persona, tmp_path, 42, START, END)
    c = connect(tmp_path / "x.db")
    store_exports(c, pid, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(c, p.buffer_cents)
    seen_rate = 0
    for (who, origin), expected in BEFORE.items():
        if who != pid:
            continue
        for options in (None, ForecastOptions(renewal=False)):
            kwargs = {} if options is None else {"options": options}
            f = build_forecast(c, pid, as_of=origin, futures=100, keep_paths=True, **kwargs)
            assert f is not None and f.simulation is not None
            parts = (
                *(getattr(f.simulation, name) for name in (
                    "days", "futures", "p10", "p50", "p90", "median_without",
                    "shortfall_probability", "shortfall_probability_without",
                    "below_zero_probability", "below_zero_probability_without",
                    "median_lowest_cents", "median_lowest_without_cents", "weeks_drawn_from",
                    "lowest_cents", "paths")),
                f.arrivals.in_rate, f.arrivals.in_sizes, f.arrivals.out_rate,
                f.arrivals.out_sizes, sorted(f.arrivals.members),
                [(x.day, x.balance_cents) for x in f.days],
            )  # fmt: skip
            digest = hashlib.sha256(repr(parts).encode()).hexdigest()[:16]
            assert digest == expected, (who, origin, options)
            seen_rate += f.arrivals.in_rate > 0
    assert pid == "v2-SAL-01" or seen_rate, "the check touches an account with arrivals"
