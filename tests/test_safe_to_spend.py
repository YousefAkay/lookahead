"""Safe-to-spend (DECISIONS.md, 2026-10-11): the largest everyday spending a day that keeps 80% of
the futures at or above the buffer. Synthetic personas only, temporary databases only."""

import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.forecast.path import build_forecast
from lookahead.forecast.simulate import SpendingCut, run
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.safe_to_spend import (
    CONFIDENCE,
    dip_day,
    is_safe,
    largest_safe,
    next_income_day,
    safe_to_spend,
    safe_to_spend_from,
    sentence,
    window_forecast,
)

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"
FUTURES = 120  # fewer futures than the app's 500: the same method, faster tests


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The three demo personas, imported (no categories needed for a forecast)."""
    db = tmp_path_factory.mktemp("sts") / "demo.db"
    conn = connect(db)
    for name in ("A", "B", "C"):
        export = SYNTHETIC / f"persona_{name}.csv"
        store_exports(conn, f"synthetic-{name}", [(export.name, parse_export(export))],
                      datetime(2026, 10, 1))  # fmt: skip
    conn.close()
    return db


@pytest.fixture
def conn(demo_db: Path, tmp_path: Path) -> sqlite3.Connection:
    copy = tmp_path / "copy.db"
    shutil.copy(demo_db, copy)
    return connect(copy)


def forecast(conn: sqlite3.Connection, account: str, as_of: date | None = None):  # type: ignore[no-untyped-def]
    f = build_forecast(conn, account, "weekday", as_of, None, futures=FUTURES)
    assert f is not None and f.inputs is not None
    return f


def exact_answer(window, allowed: int) -> int | None:  # type: ignore[no-untyped-def]
    """The same answer computed another way: each future's own largest safe level from its path
    with no everyday spending, then the level that all but `allowed` futures can afford."""
    inputs = window.inputs
    first, last = inputs.start.first_day, inputs.start.last_day
    zero = run(inputs, SpendingCut(first, last, percent=100), keep_paths=True)
    own = []
    for path in zero.paths:
        own.append(min((b - inputs.buffer_cents) // (k + 1) for k, b in enumerate(path)))
    own.sort()
    best = own[allowed]
    return best if best >= 0 else None


# --- The search ---


def test_the_search_finds_the_exact_largest_safe_level(conn: sqlite3.Connection) -> None:
    f = forecast(conn, "synthetic-B")
    s = safe_to_spend_from(f)
    window = window_forecast(f, s.last_day)
    allowed = int(FUTURES * (1 - CONFIDENCE) + 1e-9)
    assert s.cents_per_day is not None and s.cents_per_day > 0
    assert s.cents_per_day == exact_answer(window, allowed)
    assert is_safe(window, s.cents_per_day) and not is_safe(window, s.cents_per_day + 1)


@pytest.mark.parametrize("buffer", [-200_000, 0, 50_000, 200_000])
def test_the_search_is_exact_for_other_buffers_too(conn: sqlite3.Connection, buffer: int) -> None:
    f = forecast(conn, "synthetic-B")
    s = safe_to_spend_from(f, buffer)
    window = window_forecast(f, s.last_day, buffer)
    allowed = int(FUTURES * (1 - CONFIDENCE) + 1e-9)
    assert s.cents_per_day == exact_answer(window, allowed)


def test_a_higher_buffer_never_raises_the_number(conn: sqlite3.Connection) -> None:
    f = forecast(conn, "synthetic-B")
    levels = [safe_to_spend_from(f, b).cents_per_day for b in
              (-100_000, 0, 20_000, 100_000, 300_000, 450_000, 600_000)]  # fmt: skip
    as_numbers = [-1 if x is None else x for x in levels]
    assert as_numbers == sorted(as_numbers, reverse=True)
    assert as_numbers[0] > as_numbers[-1]  # it does move


# --- The window and the edge cases ---


def test_the_window_ends_the_day_before_the_next_expected_income(conn: sqlite3.Connection) -> None:
    f = forecast(conn, "synthetic-B")
    income = next_income_day(f)
    s = safe_to_spend_from(f)
    assert income is not None and s.income_day == income
    assert s.last_day == income - timedelta(days=1) and s.first_day == f.start.first_day
    assert "until" in sentence(s) and "80% confidence" in sentence(s)
    assert "estimate" in sentence(s)


def test_no_income_expected_gives_a_30_day_window(conn: sqlite3.Connection) -> None:
    f = forecast(conn, "synthetic-C")  # irregular top-ups only: no income series
    assert next_income_day(f) is None
    s = safe_to_spend_from(f, -10_000_000)  # a buffer far down, so that an amount exists
    assert s.income_day is None and s.days == 30
    assert s.cents_per_day is not None and "for the next 30 days" in sentence(s)


def test_already_below_the_buffer_has_no_safe_amount_and_says_so(conn: sqlite3.Connection) -> None:
    f = forecast(conn, "synthetic-B")
    above = f.start.balance_cents + 100_000  # the buffer is above the starting balance
    s = safe_to_spend_from(f, above)
    assert s.cents_per_day is None and s.dip_by == s.first_day
    text = sentence(s)
    assert text.startswith("No safe amount") and "already below" in text and "-" not in text


def test_no_safe_amount_names_the_day_by_which_a_dip_is_likely(conn: sqlite3.Connection) -> None:
    f = forecast(conn, "synthetic-B")
    first = f.start.first_day
    # a buffer between the start and the low of the futures with no spending: they dip later on
    zero = run(f.inputs, SpendingCut(first, f.start.last_day, percent=100), keep_paths=True)
    buffer = sorted(min(p) for p in zero.paths)[FUTURES // 2] + 1
    s = safe_to_spend_from(f, buffer)
    assert s.cents_per_day is None and s.dip_by is not None
    assert s.dip_by == dip_day(f, s.last_day, buffer)
    assert not is_safe(window_forecast(f, s.dip_by, buffer), 0)
    if s.dip_by > first:
        assert is_safe(window_forecast(f, s.dip_by - timedelta(days=1), buffer), 0)
        assert "likely before" in sentence(s)
    assert "$-" not in sentence(s)


def test_the_cap_stops_the_search(conn: sqlite3.Connection, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import lookahead.safe_to_spend as sts

    monkeypatch.setattr(sts, "MAX_CENTS", 20_000)
    f = forecast(conn, "synthetic-B")
    window = window_forecast(f, f.start.first_day + timedelta(days=5), -10_000_000)
    assert largest_safe(window) == (20_000, True)


# --- Rule zero ---


def test_rows_after_the_forecast_date_change_nothing(conn: sqlite3.Connection) -> None:
    as_of = date(2026, 8, 31)
    before = safe_to_spend(conn, "synthetic-B", as_of=as_of)
    conn.execute("UPDATE transactions SET amount_cents = amount_cents * 3, "
                 "balance_after_cents = balance_after_cents - 900000 WHERE posted_date > ?",
                 (as_of.isoformat(),))  # fmt: skip
    conn.execute("DELETE FROM transactions WHERE posted_date > ? AND id % 2 = 0",
                 (as_of.isoformat(),))  # fmt: skip
    conn.commit()
    after = safe_to_spend(conn, "synthetic-B", as_of=as_of)
    assert before is not None and after == before
    assert before.first_day == as_of + timedelta(days=1)


def test_the_window_is_the_first_days_of_the_forecasts_own_futures(
    conn: sqlite3.Connection,
) -> None:
    f = forecast(conn, "synthetic-B")
    last = f.start.first_day + timedelta(days=9)
    full = run(f.inputs, keep_paths=True)
    short = run(window_forecast(f, last).inputs, keep_paths=True)
    assert all(tuple(a[:10]) == tuple(b) for a, b in zip(full.paths, short.paths, strict=True))


# --- "Plenty of room" above 3 times the usual spending (DECISIONS.md, 2026-10-11) ---


def test_far_above_the_usual_spending_reads_as_plenty_of_room() -> None:
    from lookahead.safe_to_spend import PLENTY_RATIO, SafeToSpend, exact_figure

    day = date(2026, 10, 2)

    def made(safe: int | None, usual: int) -> SafeToSpend:
        return SafeToSpend(safe, day, day + timedelta(days=12), day + timedelta(days=13), usual,
                           20_000, None, 500, 500_000)  # fmt: skip

    assert PLENTY_RATIO == 3
    plenty = made(36_631, 4_074)
    assert plenty.plenty
    text = sentence(plenty)
    assert text.startswith("Plenty of room: you could spend well above your usual $41/day")
    assert "80% confidence" in text and "$366" not in text  # no big daily figure up front
    assert exact_figure(plenty) == "Exact figure: about $366/day (80% confidence)."
    edge = made(3 * 4_074, 4_074)  # exactly 3 times: the figure leads
    assert not edge.plenty and sentence(edge).startswith("About $122/day")
    assert not made(10_000, 0).plenty  # no usual spending to compare with
    assert not made(None, 4_074).plenty
