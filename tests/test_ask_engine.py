"""Ask step 3: the engine blocks give the same figures as their pages (spec
docs/specs/2026-11-20-ask.md, definition of done 4), on synthetic demo persona A (never held-out),
its data cut mid-month so the budget month has days left."""

import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.ask.engine import ForecastAnswer, WhatIfAnswer, forecast_of
from lookahead.ask.plan import parse
from lookahead.ask.run import Context, Outcome, run
from lookahead.budgets import budget_forecast, budget_month, category_rows, set_limit
from lookahead.db import connect
from lookahead.demo import build_demo_db
from lookahead.forecast.path import build_forecast
from lookahead.forecast.scenario import ExtraItem, Scenario, SpendingChange, run_scenario
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.safe_to_spend import safe_to_spend

ACCOUNT = "synthetic-A"
KEY = bytes(range(32))
DATA_END = date(2026, 9, 14)
TODAY = DATA_END + timedelta(days=1)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("ask_engine")
    for pid in "ABC":
        write_persona(PERSONAS[pid], tmp / "synthetic", seed=42, start=date(2026, 3, 1),
                      export_date=date(2026, 9, 30))  # fmt: skip
    build_demo_db(tmp / "demo.db", tmp / "synthetic")
    conn = connect(tmp / "demo.db")
    with conn:
        conn.execute("DELETE FROM transactions WHERE posted_date > ?", (DATA_END.isoformat(),))
    conn.close()
    return tmp / "demo.db"


@pytest.fixture
def conn(built: Path, tmp_path: Path) -> sqlite3.Connection:
    shutil.copy(built, tmp_path / "a.db")
    return connect(tmp_path / "a.db")


def ask(conn: sqlite3.Connection, *steps: dict) -> Outcome:  # type: ignore[type-arg]
    return run(parse({"steps": list(steps)}), Context(conn, ACCOUNT, TODAY, KEY))


def page_forecast(conn: sqlite3.Connection):  # type: ignore[no-untyped-def]
    """What the Forecast page builds (app/pages/5_Forecast.py: weekday, live, the stored key)."""
    return build_forecast(conn, ACCOUNT, "weekday", None, KEY)


def test_balance_on_a_day_is_the_forecast_pages(conn: sqlite3.Connection) -> None:
    sim = page_forecast(conn).simulation
    out = ask(conn, {"op": "forecast", "name": "f", "measure": "balance_on",
                     "on": {"kind": "in_days", "n": 10}})  # fmt: skip
    a = out.result
    assert isinstance(a, ForecastAnswer)
    k = (a.day - a.first_day).days
    assert a.day == TODAY + timedelta(days=10)
    assert (a.middle_cents, a.low_cents, a.high_cents) == (sim.p50[k], sim.p10[k], sim.p90[k])


def test_lowest_and_chance_are_the_forecast_pages(conn: sqlite3.Connection) -> None:
    sim = page_forecast(conn).simulation
    low = ask(conn, {"op": "forecast", "name": "f", "measure": "lowest"}).result
    assert low.middle_cents == min(sim.p50)  # "Lowest expected balance": the middle future's
    chance = ask(conn, {"op": "forecast", "name": "f", "measure": "chance_below"}).result
    assert chance.chance == sim.shortfall_probability and chance.below_is_buffer


def test_a_window_run_uses_the_same_futures(conn: sqlite3.Connection) -> None:
    # Asking about the whole 30 days with the buffer as a dollar amount goes through the window
    # run and must give the page's chance exactly.
    fc = page_forecast(conn)
    out = ask(conn, {"op": "forecast", "name": "f", "measure": "chance_below",
                     "below": {"dollars": fc.buffer_cents / 100},
                     "until": {"kind": "date", "date": fc.start.last_day.isoformat()}})  # fmt: skip
    assert out.result.chance == fc.simulation.shortfall_probability
    zero = ask(conn, {"op": "forecast", "name": "f", "measure": "chance_below",
                      "below": {"dollars": 0.01}}).result  # fmt: skip
    assert zero.chance <= out.result.chance


def test_past_the_horizon_or_before_the_forecast_is_refused(conn: sqlite3.Connection) -> None:
    out = ask(conn, {"op": "forecast", "name": "f", "measure": "balance_on",
                     "on": {"kind": "in_days", "n": 45}})  # fmt: skip
    assert out.kind == "refused" and out.result.reason == "beyond_horizon"
    out = ask(conn, {"op": "forecast", "name": "f", "measure": "balance_on",
                     "on": {"kind": "date", "date": "2026-09-01"}})  # fmt: skip
    assert out.kind == "refused" and out.result.reason == "before_forecast"


def test_a_one_off_what_if_matches_the_forecast_pages_engine(conn: sqlite3.Connection) -> None:
    fc = page_forecast(conn)
    day = fc.start.first_day + timedelta(days=3)
    out = ask(conn, {"op": "whatif", "name": "w", "changes": [
        {"kind": "one_off", "direction": "out", "amount": {"dollars": 500},
         "day": {"kind": "date", "date": day.isoformat()}}]})  # fmt: skip
    a = out.result
    assert isinstance(a, WhatIfAnswer)
    page = run_scenario(fc, Scenario(extras=(ExtraItem(day, -50000, "purchase"),)))
    assert a.low_now_cents == min(page.original.p50)
    assert a.low_after_cents == min(page.modified.p50)
    assert a.chance_after == page.modified.shortfall_probability


def test_everyday_percent_less_matches_the_forecast_pages_engine(conn: sqlite3.Connection) -> None:
    fc = page_forecast(conn)
    out = ask(conn, {"op": "whatif", "name": "w", "changes": [
        {"kind": "everyday", "direction": "less", "percent": 20}]})  # fmt: skip
    cut = SpendingChange(fc.start.first_day, fc.start.last_day, percent=20)
    page = run_scenario(fc, Scenario(spending=cut))
    assert abs(out.result.low_after_cents - min(page.modified.p50)) <= 100  # rounding per day
    assert abs(out.result.chance_after - page.modified.shortfall_probability) <= 0.01


def test_spending_more_or_less_in_a_category_moves_the_right_way(conn: sqlite3.Connection) -> None:
    def whatif(direction: str, **size: object) -> WhatIfAnswer:
        out = ask(conn, {"op": "whatif", "name": "w", "changes": [
            {"kind": "category", "category": "Groceries", "direction": direction,
             **size}]})  # fmt: skip
        return out.result  # type: ignore[no-any-return]

    less = whatif("less", amount={"dollars": 100})
    more = whatif("more", amount={"dollars": 100})
    assert less.end_after_cents > less.end_now_cents > more.end_after_cents
    # $100 more a month, spread evenly: the 30 days take about 30/30.4 months of it
    assert abs((more.end_now_cents - more.end_after_cents) - 10000) <= 400
    assert less.notes and "of your usual spending in Groceries" in less.notes[0]
    assert whatif("less", percent=100).end_after_cents >= less.end_after_cents


def test_cancelling_a_recurring_payment(conn: sqlite3.Connection) -> None:
    fc = forecast_of(Context(conn, ACCOUNT, TODAY, KEY))
    placed = {i.series_key for i in fc.items}
    paid = [s for s in fc.series if s.name and s.direction == "out" and s.active
            and s.status != "rejected" and s.key in placed]  # fmt: skip
    target = paid[0]
    out = ask(conn, {"op": "whatif", "name": "w", "changes": [
        {"kind": "cancel", "target": target.name}]})  # fmt: skip
    assert out.kind == "answer", out.result
    assert out.result.end_after_cents > out.result.end_now_cents
    assert out.matches[0].names == (target.name,)


def test_budget_is_the_budgets_tabs(conn: sqlite3.Connection) -> None:
    set_limit(conn, ACCOUNT, "Shopping", 20000)
    set_limit(conn, ACCOUNT, "Restaurants & cafés", 15000)
    fc = budget_forecast(conn, ACCOUNT, KEY)
    month = budget_month(fc, category_rows(conn, ACCOUNT, fc.start.data_last_updated),
                         {"Shopping": 20000, "Restaurants & cafés": 15000})  # fmt: skip
    out = ask(conn, {"op": "budget", "name": "b"})
    got = {b.category: (b.spent_cents, b.likely_end_cents, b.chance_over) for b in out.result.lines}
    want = {p.category: (p.spent_cents, p.likely_end_cents, p.chance_over) for p in month.paces}
    assert got == want
    one = ask(conn, {"op": "budget", "name": "b", "category": "Shopping"}).result
    assert [b.category for b in one.lines] == ["Shopping"]
    out = ask(conn, {"op": "budget", "name": "b", "category": "Groceries"})
    assert out.kind == "refused" and out.result.reason == "no_budget"


def test_safe_to_spend_is_homes(conn: sqlite3.Connection) -> None:
    home = safe_to_spend(conn, ACCOUNT, "weekday", None, KEY)  # app/theme.cached_safe_to_spend
    out = ask(conn, {"op": "safe_to_spend", "name": "s"})
    assert out.result.cents_per_day == home.cents_per_day
    assert out.result.last_day == home.last_day


def test_recurring_lists_the_forecasts_series(conn: sqlite3.Connection) -> None:
    fc = page_forecast(conn)
    want = {s.key for s in fc.series if s.active and s.status != "rejected"
            and s.direction == "out"}  # fmt: skip
    got = ask(conn, {"op": "recurring", "name": "r"}).result
    assert {line.key for line in got.lines} == want
    out = ask(conn, {"op": "recurring", "name": "r", "find": "zzqx"})
    assert out.kind == "not_found" and "recurring payment" in out.result.message
