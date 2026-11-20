"""Budgets that forecast (spec docs/specs/2026-11-20-budgets-that-forecast.md): proposals,
limits, pace from the futures, partial weeks, rule zero, big one-off rows, staying on budget and
recategorizing. Synthetic demo personas A and C only (never held-out), temporary databases."""

import csv
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.budgets import (
    BUDGETABLE,
    DEFAULT_CATEGORIES,
    NEVER,
    OPTIONAL_CATEGORIES,
    CategoryRow,
    Proposal,
    addable,
    budget_forecast,
    budget_month,
    category_rows,
    days_left,
    full_months,
    future_category_days,
    is_stale,
    load_limits,
    month_end,
    on_budget,
    proposals_for,
    propose,
    remove,
    set_limit,
    shown_categories,
    waiting_proposals,
)
from lookahead.corrections import correct_row
from lookahead.db import connect
from lookahead.forecast.simulate import BLOCK, SpendingCut, pool_week_starts, run, week_draws
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"
FUTURES = 80
ALL_DEFAULTS = {c: 10_000 for c in DEFAULT_CATEGORIES}


def _categorize_from_answer_key(conn: sqlite3.Connection, account: str, name: str) -> None:
    """Write the generator's true category and flow type for each row as what the app shows."""
    export = SYNTHETIC / f"persona_{name}.csv"
    key = SYNTHETIC / f"persona_{name}_answer_key.csv"
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    parsed = parse_export(export)
    fingerprints = {
        (r[1], r[2], r[3]): r[0]
        for r in conn.execute(
            "SELECT fingerprint, posted_date, amount_cents, sub_description FROM transactions"
            " WHERE account = ?",
            (account,),
        )
    }
    with conn:
        for r, t in zip(parsed, truth, strict=True):
            fp = fingerprints[(r.posted_date.isoformat(), r.amount_cents, r.sub_description)]
            conn.execute(
                "INSERT OR REPLACE INTO category_predictions (fingerprint, account, merchant,"
                " flow_type, category, source, detail, flagged, confidence, model_version,"
                " predicted_at) VALUES (?, ?, ?, ?, ?, 'model', '', 0, 0.9, 'test', '2026-10-01')",
                (fp, account, t["merchant"], t["flow_type"], t["category"]),
            )


@pytest.fixture(scope="module")
def persona_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("budgets") / "b.db"
    conn = connect(db)
    for name in ("A", "C"):
        export = SYNTHETIC / f"persona_{name}.csv"
        store_exports(conn, f"synthetic-{name}", [(export.name, parse_export(export))],
                      datetime(2026, 10, 1))  # fmt: skip
        _categorize_from_answer_key(conn, f"synthetic-{name}", name)
    conn.close()
    return db


@pytest.fixture
def conn(persona_db: Path, tmp_path: Path) -> sqlite3.Connection:
    return connect(Path(shutil.copy(persona_db, tmp_path / "copy.db")))


def month_of(conn: sqlite3.Connection, account: str, as_of: date, limits=ALL_DEFAULTS):  # type: ignore[no-untyped-def]
    f = budget_forecast(conn, account, None, as_of, futures=FUTURES)
    assert f is not None
    return f, budget_month(f, category_rows(conn, account, as_of), limits)


def row(day: date, cents: int, category: str = "Shopping", row_id: int = 0) -> CategoryRow:
    return CategoryRow(row_id, f"fp{row_id}", day, cents, category, None)


# --- Months and proposals (pure) ---


def test_month_end_handles_every_length() -> None:
    assert month_end(date(2026, 2, 10)) == date(2026, 2, 28)
    assert month_end(date(2028, 2, 1)) == date(2028, 2, 29)
    assert month_end(date(2026, 12, 31)) == date(2026, 12, 31)
    assert month_end(date(2026, 4, 1)) == date(2026, 4, 30)


def test_full_months_need_the_whole_month_in_the_data() -> None:
    # Data from 3 June to 20 Sept, budget month September: June is partial, so July and August.
    assert full_months(date(2026, 6, 3), date(2026, 9, 20), date(2026, 9, 21)) == [
        date(2026, 8, 1), date(2026, 7, 1)]  # fmt: skip
    # Data ending on 31 August: the budget month is September and August is full.
    assert full_months(date(2026, 6, 1), date(2026, 8, 31), date(2026, 9, 1)) == [
        date(2026, 8, 1), date(2026, 7, 1), date(2026, 6, 1)]  # fmt: skip


def test_the_proposal_is_the_median_of_the_last_three_full_months_rounded_up() -> None:
    rows = [row(date(2026, 5, 3), 99_999), row(date(2026, 6, 3), 10_000),
            row(date(2026, 7, 9), 4_000), row(date(2026, 7, 20), 2_001),
            row(date(2026, 8, 1), 30_000)]  # fmt: skip
    # May is the 4th month back: left out. June 100.00, July 60.01, August 300.00: median 100.00.
    p = propose(rows, "Shopping", date(2026, 1, 1), date(2026, 8, 31), date(2026, 9, 1))
    assert p == Proposal("Shopping", 10_000, 3)
    rows[1] = row(date(2026, 6, 3), 10_001)  # 100.01 rounds up to the next $5
    assert propose(rows, "Shopping", date(2026, 1, 1), date(2026, 8, 31),
                   date(2026, 9, 1)).limit_cents == 10_500  # fmt: skip


def test_a_month_with_nothing_spent_counts_as_zero() -> None:
    rows = [row(date(2026, 6, 3), 10_000), row(date(2026, 8, 3), 2_000)]  # July: nothing
    p = propose(rows, "Shopping", date(2026, 1, 1), date(2026, 8, 31), date(2026, 9, 1))
    assert p.limit_cents == 2_000 and p.months == 3  # median of 100, 0, 20 is 20
    nothing = propose([row(date(2026, 6, 3), 10_000)], "Shopping", date(2026, 1, 1),
                      date(2026, 8, 31), date(2026, 9, 1))  # fmt: skip
    assert nothing.limit_cents is None  # median of 100, 0, 0 is 0: enter a limit instead


def test_fewer_than_two_full_months_gives_no_proposal() -> None:
    rows = [row(date(2026, 7, 3), 10_000), row(date(2026, 8, 3), 10_000)]
    p = propose(rows, "Shopping", date(2026, 7, 2), date(2026, 8, 31), date(2026, 9, 1))
    assert p == Proposal("Shopping", None, 1)  # July starts on the 2nd: only August is full
    two = propose(rows, "Shopping", date(2026, 7, 1), date(2026, 8, 31), date(2026, 9, 1))
    assert two == Proposal("Shopping", 10_000, 2)


def test_two_full_months_use_the_median_of_the_two() -> None:
    rows = [row(date(2026, 7, 3), 10_000), row(date(2026, 8, 3), 12_100)]
    p = propose(rows, "Shopping", date(2026, 7, 1), date(2026, 8, 31), date(2026, 9, 1))
    assert p.limit_cents == 11_500  # 110.50 rounded up to 115


def test_stale_is_a_different_calendar_month() -> None:
    assert is_stale(date(2026, 10, 1), date(2026, 11, 20))
    assert not is_stale(date(2026, 11, 1), date(2026, 11, 20))
    assert not is_stale(date(2026, 11, 30), date(2026, 11, 1))


# --- Limits ---


def test_limits_are_saved_edited_turned_off_and_removed(conn: sqlite3.Connection) -> None:
    set_limit(conn, "synthetic-A", "Shopping", 20_000, date(2026, 10, 1))
    set_limit(conn, "synthetic-A", "Shopping", 25_000, date(2026, 10, 2))
    set_limit(conn, "synthetic-A", "Food delivery", None, date(2026, 10, 2))
    set_limit(conn, "synthetic-A", "Groceries", 40_000, date(2026, 10, 2))
    assert load_limits(conn, "synthetic-A") == {"Shopping": 25_000, "Food delivery": None,
                                                "Groceries": 40_000}  # fmt: skip
    assert load_limits(conn, "synthetic-C") == {}
    remove(conn, "synthetic-A", "Groceries")
    assert "Groceries" not in load_limits(conn, "synthetic-A")


@pytest.mark.parametrize("category", NEVER)
def test_categories_that_never_have_a_budget_are_refused(
    conn: sqlite3.Connection, category: str
) -> None:
    with pytest.raises(ValueError):
        set_limit(conn, "synthetic-A", category, 10_000)


def test_a_limit_must_be_above_zero(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        set_limit(conn, "synthetic-A", "Shopping", 0)
    with pytest.raises(sqlite3.IntegrityError):  # the table refuses it too
        with conn:
            conn.execute("INSERT INTO budgets VALUES ('x', 'Shopping', -5, '2026-10-01')")


def test_the_never_list_and_the_budgetable_list_split_the_categories() -> None:
    assert set(NEVER) == {
        "Income", "Transfers", "Rent & housing", "Subscriptions", "Phone, internet & utilities",
        "Fees & interest", "Education",
    }  # fmt: skip
    assert len(BUDGETABLE) == 10 and DEFAULT_CATEGORIES == BUDGETABLE[:4]


def test_shown_categories_and_one_looks_right() -> None:
    proposals = {c: Proposal(c, 5_000, 3) for c in BUDGETABLE}
    proposals["Entertainment"] = Proposal("Entertainment", None, 3)
    stored: dict[str, int | None] = {"Shopping": 9_000, "Food delivery": None, "Cash": 2_000}
    shown = shown_categories(stored, proposals)
    assert [s.category for s in shown] == ["Restaurants & cafés", "Entertainment", "Shopping",
                                           "Cash"]  # fmt: skip
    # One click accepts what is proposed and undecided; Entertainment waits for a typed limit.
    assert waiting_proposals(shown) == {"Restaurants & cafés": 5_000}
    # Food delivery was turned off: it can be added back, with the optional ones not on the tab.
    assert addable(shown) == ["Food delivery", *[c for c in OPTIONAL_CATEGORIES if c != "Cash"]]


# --- Pace from the futures ---


@pytest.mark.parametrize(("as_of", "left"), [(date(2026, 8, 30), 1), (date(2026, 8, 28), 3),
                                             (date(2026, 8, 25), 6)])  # fmt: skip
def test_partial_weeks_are_cut_by_day_from_the_drawn_week(
    conn: sqlite3.Connection, as_of: date, left: int
) -> None:
    """With 1, 3 or 6 days left, the rest of the month in each future is exactly the category's
    spending on the first 1, 3 or 6 days of that future's first drawn week: cut, never scaled."""
    f, month = month_of(conn, "synthetic-A", as_of)
    assert days_left(f) == left and month.days_left == left
    inputs = f.inputs
    assert inputs is not None
    starts = pool_week_starts(inputs.start, inputs.history_start)
    category_of = {r.row_id: r.category for r in category_rows(conn, "synthetic-A", as_of)}
    checked = 0
    for pace in month.paces:
        for future, days in enumerate(pace.remaining):
            assert len(days) == left
            first_week = starts[week_draws(inputs.seed, future, len(starts), 1)[0]]
            wanted = [first_week + timedelta(days=k) for k in range(left)]
            expected = sum(cents for rid, day, cents in f.spending_rows.kept
                           if day in wanted and category_of.get(rid) == pace.category)  # fmt: skip
            assert sum(days) == expected
            checked += expected != 0
    assert checked > 0  # some future actually drew spending in a budgeted category


@pytest.mark.parametrize("account", ["synthetic-A", "synthetic-C"])
@pytest.mark.parametrize("as_of", [date(2026, 7, 31), date(2026, 8, 14), date(2026, 9, 3)])
def test_the_categories_of_each_day_add_up_to_the_futures_everyday_spending(
    conn: sqlite3.Connection, account: str, as_of: date
) -> None:
    """In every future, on every forecast day, the parts by category (with the part in no budget
    category) add up to the everyday spending `run` used that day, to the cent: cutting the sum
    of the parts gives the same futures as cutting all spending."""
    f = budget_forecast(conn, account, None, as_of, futures=FUTURES)
    assert f is not None and f.inputs is not None
    category_of = {r.row_id: r.category for r in category_rows(conn, account, as_of)}
    horizon = len(f.days)
    sums = [[sum(day.values()) for day in future_category_days(f, category_of, n, horizon)]
            for n in range(FUTURES)]  # fmt: skip
    cut_all = SpendingCut(f.start.first_day, f.start.last_day, percent=100)
    assert run(f.inputs, keep_paths=True, future_cuts=[[max(s, 0) for s in d] for d in sums]) == (
        run(f.inputs, cut_all, keep_paths=True))  # fmt: skip
    # and directly against the drawn days of the forecast's own daily spending
    starts = pool_week_starts(f.inputs.start, f.inputs.history_start)
    for n in range(FUTURES):
        weeks = week_draws(f.inputs.seed, n, len(starts), -(-horizon // BLOCK))
        for k in range(horizon):
            drawn = starts[weeks[k // BLOCK]] + timedelta(days=k % BLOCK)
            assert sums[n][k] == f.inputs.spending_daily.get(drawn, 0)


def test_pace_reads_spent_so_far_and_the_futures(conn: sqlite3.Connection) -> None:
    f, month = month_of(conn, "synthetic-A", date(2026, 9, 14))
    assert month.month == date(2026, 9, 1) and month.data_date == date(2026, 9, 14)
    rows = category_rows(conn, "synthetic-A", date(2026, 9, 14))
    for pace in month.paces:
        mine = [r for r in rows if r.category == pace.category and r.day >= date(2026, 9, 1)]
        assert pace.spent_cents == sum(r.cents for r in mine)
        assert {r.row_id for r in pace.rows} == {r.row_id for r in mine}
        totals = sorted(pace.spent_cents + sum(d) for d in pace.remaining)
        assert pace.futures == FUTURES and len(totals) == FUTURES
        assert pace.likely_end_cents == totals[round(0.5 * (FUTURES - 1))]
        assert pace.chance_over == sum(t > pace.limit_cents for t in totals) / FUTURES
    food = next(p for p in month.paces if p.category == "Food delivery")
    assert food.spent_cents > 0 and 0 < food.chance_over <= 1  # persona A orders a lot


def test_a_refund_lowers_spent_so_far_and_income_is_not_spending(
    conn: sqlite3.Connection,
) -> None:
    rows = category_rows(conn, "synthetic-A", date(2026, 9, 30))
    assert any(r.cents < 0 and r.category == "Food delivery" for r in rows)  # refunds
    assert not any(r.category == "Income" for r in rows)


def test_nothing_after_the_data_date_counts(conn: sqlite3.Connection) -> None:
    """Rule zero: rows posted after the data date (already stored, or added later, even a big
    one in a budgeted category) change nothing in the budgets as of that date."""
    as_of = date(2026, 8, 14)
    _, before = month_of(conn, "synthetic-A", as_of)
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES ('synthetic-A', '2026-08-15', -90000, 'pos purchase', 'Late Shop',"
            " 0, 'late-row', 'x.csv', '2026-10-02')"
        )
        conn.execute(
            "INSERT INTO category_predictions VALUES ('late-row', 'synthetic-A', 'Late Shop',"
            " 'everyday', 'Shopping', 'model', '', 0, 0.9, 'test', '2026-10-02')"
        )
    f = budget_forecast(conn, "synthetic-A", None, as_of, futures=FUTURES)
    assert f is not None
    everything = category_rows(conn, "synthetic-A", date(2026, 12, 31))  # later rows included
    assert budget_month(f, everything, ALL_DEFAULTS) == before


def test_a_big_one_off_counts_in_spent_so_far_but_is_never_drawn(
    conn: sqlite3.Connection,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES ('synthetic-A', '2026-09-05', -150000, 'pos purchase', 'Big Sofa Store',"
            " 0, 'big-row', 'x.csv', '2026-10-02')"
        )
        conn.execute(
            "INSERT INTO category_predictions VALUES ('big-row', 'synthetic-A', 'Big Sofa Store',"
            " 'everyday', 'Shopping', 'model', '', 0, 0.9, 'test', '2026-10-02')"
        )
    big_id = conn.execute("SELECT id FROM transactions WHERE fingerprint = 'big-row'").fetchone()[0]
    f, month = month_of(conn, "synthetic-A", date(2026, 9, 14))
    shopping = next(p for p in month.paces if p.category == "Shopping")
    assert big_id in {r.row_id for r in shopping.rows} and shopping.spent_cents >= 150_000
    assert big_id in f.spending_rows.set_aside
    assert big_id not in {r for r, _, _ in f.spending_rows.kept}
    assert shopping.big_one_off
    assert not any(p.big_one_off for p in month.paces if p.category != "Shopping")
    _, august = month_of(conn, "synthetic-A", date(2026, 8, 31))  # not in August's total
    assert not any(p.big_one_off for p in august.paces)


def test_a_forecast_that_stops_before_the_month_end_is_refused(conn: sqlite3.Connection) -> None:
    from lookahead.forecast.path import build_forecast

    short = build_forecast(conn, "synthetic-A", "weekday", date(2026, 8, 31), None,
                           horizon_days=10, futures=10)  # fmt: skip
    assert short is not None
    with pytest.raises(ValueError):
        budget_month(short, [], ALL_DEFAULTS)


def test_a_31_day_month_from_its_first_day_gets_31_days(conn: sqlite3.Connection) -> None:
    f, month = month_of(conn, "synthetic-A", date(2026, 7, 31))  # August has 31 days
    assert len(f.days) == 31 and month.days_left == 31
    assert all(len(d) == 31 for p in month.paces for d in p.remaining)


# --- Staying on budget ---


@pytest.mark.parametrize("account", ["synthetic-A", "synthetic-C"])
@pytest.mark.parametrize("as_of", [date(2026, 7, 31), date(2026, 8, 20), date(2026, 9, 3)])
def test_staying_on_budget_is_never_lower(
    conn: sqlite3.Connection, account: str, as_of: date
) -> None:
    tight = {c: 500 for c in DEFAULT_CATEGORIES}  # $5: almost always over
    f, month = month_of(conn, account, as_of, tight)
    result = on_budget(f, month)
    assert result.never_lower
    assert result.low_on_budget_cents >= result.low_now_cents
    assert result.chance_on_budget <= result.chance_now
    assert f.simulation is not None


def test_as_things_are_now_matches_the_forecast_page(conn: sqlite3.Connection) -> None:
    """The what-if's "now" is the 30-day forecast the Forecast page shows, even when the budgets
    needed 31 days."""
    from lookahead.forecast.path import build_forecast

    f, month = month_of(conn, "synthetic-A", date(2026, 7, 31))
    page = build_forecast(conn, "synthetic-A", "weekday", date(2026, 7, 31), None, futures=FUTURES)
    assert page is not None and page.simulation is not None
    result = on_budget(f, month)
    assert result.low_now_cents == min(page.simulation.p50)
    assert result.chance_now == page.simulation.shortfall_probability


def test_a_generous_budget_changes_nothing(conn: sqlite3.Connection) -> None:
    roomy = {c: 100_000_000 for c in DEFAULT_CATEGORIES}
    f, month = month_of(conn, "synthetic-A", date(2026, 8, 20), roomy)
    result = on_budget(f, month)
    assert result.low_on_budget_cents == result.low_now_cents
    assert result.chance_on_budget == result.chance_now


# --- Recategorize from the budget view ---


def test_recategorizing_a_row_moves_it_out_of_spent_so_far(conn: sqlite3.Connection) -> None:
    as_of = date(2026, 9, 14)
    _, month = month_of(conn, "synthetic-A", as_of)
    food = next(p for p in month.paces if p.category == "Food delivery")
    moved = next(r for r in food.rows if r.cents > 0)
    correct_row(conn, "synthetic-A", moved.fingerprint, "Groceries", date(2026, 10, 3))
    _, after = month_of(conn, "synthetic-A", as_of)
    food_after = next(p for p in after.paces if p.category == "Food delivery")
    assert food_after.spent_cents == food.spent_cents - moved.cents
    assert moved.row_id not in {r.row_id for r in food_after.rows}


# --- Budget alerts on a replay of synthetic data dates ---


def test_budget_alerts_on_a_replay(conn: sqlite3.Connection) -> None:
    """Looks every few days from July to September, in data-date order, with tight limits: no
    alert in a month's first week, at most one episode per category and month, every earlier
    month's episode ended at its last day, nothing open while the chance is not above 50%."""
    from lookahead.budget_alerts import budget_episodes, record_look

    tight = {"Food delivery": 5_000, "Restaurants & cafés": 3_000, "Shopping": 2_000}
    day, alerts = date(2026, 7, 2), 0
    while day <= date(2026, 9, 28):
        f, month = month_of(conn, "synthetic-A", day, tight)
        results = record_look(conn, "synthetic-A", month, f.start.first_day)
        for r, p in zip(results, month.paces, strict=True):
            on = f.start.first_day.day >= 8 and p.chance_over > 0.5
            assert (r.episode_id is not None) == on
            alerts += on
        day += timedelta(days=3)
    episodes = budget_episodes(conn, "synthetic-A")
    assert alerts > 0 and episodes
    keys = [(e.category, e.month) for e in episodes]
    assert len(keys) == len(set(keys))
    for e in episodes:
        assert e.opened_on.day >= 7  # data date the 7th or later: first forecast day the 8th+
        if e.month < "2026-09":
            assert e.closed_on == month_end(date.fromisoformat(f"{e.month}-01"))


# --- The counts-only check for my real database ---


def test_the_check_prints_counts_only_and_writes_nothing(
    persona_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib
    import re

    from lookahead.budget_check import main

    db = Path(shutil.copy(persona_db, tmp_path / "check.db"))
    conn = connect(db)
    with conn:  # mid-month, with one saved limit
        conn.execute("DELETE FROM transactions WHERE posted_date > '2026-09-14'")
    set_limit(conn, "synthetic-A", "Food delivery", 5_000, date(2026, 9, 14))
    merchants = {r[0] for r in conn.execute("SELECT merchant FROM category_predictions") if r[0]}
    conn.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert main(["--db", str(db), "--account", "1"]) == 0
    out = capsys.readouterr().out
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert "$" not in out and "%" not in out
    assert not re.search(r"\d{4}-\d{2}|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", out)
    assert "synthetic" not in out
    assert not any(m in out for m in merchants if len(m) > 3)
    assert "Categories with a saved limit: 1" in out
    assert "Pace computed on: saved limits" in out
    assert "Categories with a pace computed from the futures: 1 of 1" in out
    assert re.search(r"Categories with a chance of going over above 0: [01] of 1", out)
    assert "at or above as-now in every future on every day: yes" in out
    # beside the all-months total, the current month's count, as Home lists them (reviewer N-a)
    assert "Open budget alerts of this budget month (as Home lists them): 0" in out
    assert main(["--db", str(db), "--account", "9"]) == 2


# --- Rule zero against a database that never had the later rows (reviewer N1) ---


@pytest.mark.parametrize("account", ["synthetic-A", "synthetic-C"])
@pytest.mark.parametrize("as_of", [date(2026, 7, 14), date(2026, 8, 20), date(2026, 9, 9)])
def test_the_budgets_are_the_same_as_on_a_copy_cut_at_the_data_date(
    conn: sqlite3.Connection, tmp_path: Path, account: str, as_of: date
) -> None:
    """The budget month (pace, chance, likely end, the rows and every future's remaining days)
    and the proposals as of a date are exactly those of a copy holding only the rows posted up to
    that date. Each copy has its recurring series stored from its own rows, so a series detected
    with later rows cannot reach the earlier date either."""
    from lookahead.recurring_store import refresh

    cut = connect(Path(shutil.copy(tmp_path / "copy.db", tmp_path / "cut.db")))
    with cut:
        cut.execute("DELETE FROM transactions WHERE posted_date > ?", (as_of.isoformat(),))
    refresh(conn, account)
    refresh(cut, account)

    def everything(c: sqlite3.Connection):  # type: ignore[no-untyped-def]
        f = budget_forecast(c, account, None, as_of, futures=FUTURES)
        assert f is not None
        rows = category_rows(c, account, date(2026, 12, 31))
        return budget_month(f, rows, ALL_DEFAULTS), proposals_for(c, account, rows, f)

    assert everything(conn) == everything(cut)
    cut.close()


# --- The chance when spent so far is already over ---


def test_the_chance_rule() -> None:
    from lookahead.budgets import chance_over

    assert chance_over(12_000, 10_000, [9_000, 13_000]) == 1.0  # over already: a drawn refund
    assert chance_over(12_000, 10_000, []) == 1.0  # does not undo it
    assert chance_over(10_000, 10_000, [10_000, 10_001, 9_000, 15_000]) == 0.5  # at the limit
    assert chance_over(5_000, 10_000, []) == 0.0


def test_over_the_limit_already_is_100_percent_otherwise_the_futures_decide(
    conn: sqlite3.Connection,
) -> None:
    as_of = date(2026, 9, 14)
    _, roomy = month_of(conn, "synthetic-A", as_of)
    food = next(p for p in roomy.paces if p.category == "Food delivery")
    assert food.spent_cents > 1_000
    _, tight = month_of(conn, "synthetic-A", as_of, {"Food delivery": food.spent_cents - 100})
    assert tight.paces[0].chance_over == 1.0  # spent so far is already above the limit
    # A limit between spent so far and the futures' totals: some futures go over, some don't.
    totals = sorted(food.spent_cents + sum(d) for d in food.remaining)
    middle = totals[len(totals) // 2]
    _, mid = month_of(conn, "synthetic-A", as_of, {"Food delivery": middle})
    pace = mid.paces[0]
    assert pace.spent_cents <= middle
    assert pace.chance_over == sum(t > middle for t in totals) / len(totals)
    assert 0 < pace.chance_over < 1


def test_the_checks_rows_this_month_follow_a_one_row_move(persona_db: Path, tmp_path: Path) -> None:
    from lookahead.budget_check import report

    db = Path(shutil.copy(persona_db, tmp_path / "rows.db"))
    c = connect(db)
    with c:
        c.execute("DELETE FROM transactions WHERE posted_date > '2026-09-14'")

    def count(lines: list[str], category: str) -> int:
        line = next(x for x in lines if x.startswith(category))
        return int(line.split()[-1])

    before = report(c, "synthetic-A", date(2026, 9, 20))
    assert any("rows this month" in x for x in before)
    september = [r for r in category_rows(c, "synthetic-A", date(2026, 9, 14))
                 if r.day >= date(2026, 9, 1) and r.cents > 0]  # fmt: skip
    moved = next(r for r in september if r.category == "Food delivery")
    correct_row(c, "synthetic-A", moved.fingerprint, "Groceries", date(2026, 9, 20))
    after = report(c, "synthetic-A", date(2026, 9, 20))
    assert count(after, "Food delivery") == count(before, "Food delivery") - 1
    assert count(after, "Groceries") == count(before, "Groceries") + 1
    assert "Category corrections stored: 0 one-row, 0 whole-merchant" in before
    assert "Category corrections stored: 1 one-row, 0 whole-merchant" in after
    assert "Corrected rows this month showing the corrected category: 1" in after
    assert "Corrected rows this month showing their recurring series' category instead: 0" in after
    c.close()


def test_the_checks_correction_lines_count_only_app_corrections(
    persona_db: Path, tmp_path: Path
) -> None:
    """N1: a label from the label review page is not a correction; a corrected row of a series
    I gave a category is counted as showing the series' category."""
    from lookahead.budget_check import report
    from lookahead.labels import label_row
    from lookahead.recurring_store import load_series, refresh, set_category

    db = Path(shutil.copy(persona_db, tmp_path / "labels.db"))
    c = connect(db)
    with c:
        c.execute("DELETE FROM transactions WHERE posted_date > '2026-09-14'")
    september = [r for r in category_rows(c, "synthetic-A", date(2026, 9, 14))
                 if r.day >= date(2026, 9, 1)]  # fmt: skip
    labelled = september[0]
    label_row(c, "synthetic-A", labelled.fingerprint, labelled.category)
    with c:  # what the categorizer writes for a reviewed row
        c.execute("UPDATE category_predictions SET source = 'your label', detail = 'confirmed by "
                  "you' WHERE fingerprint = ?", (labelled.fingerprint,))  # fmt: skip
    lines = report(c, "synthetic-A", date(2026, 9, 20))
    assert "Category corrections stored: 0 one-row, 0 whole-merchant" in lines
    assert "Corrected rows this month showing the corrected category: 0" in lines
    refresh(c, "synthetic-A")
    fizz = next(s for s in load_series(c, "synthetic-A") if "Fizz" in s.key)
    set_category(c, "synthetic-A", fizz, "Shopping", date(2026, 9, 20))
    refresh(c, "synthetic-A")  # as the Recurring page does after a decision
    payment = next(f for f in fizz.evidence if f in {r.fingerprint for r in september})
    correct_row(c, "synthetic-A", payment, "Groceries", date(2026, 9, 20))
    lines = report(c, "synthetic-A", date(2026, 9, 20))
    assert "Category corrections stored: 1 one-row, 0 whole-merchant" in lines
    assert "Corrected rows this month showing the corrected category: 0" in lines
    assert "Corrected rows this month showing their recurring series' category instead: 1" in lines
    c.close()


def test_already_over_and_the_series_helper() -> None:
    from lookahead.budgets import Pace, series_categorized

    over = Pace("Shopping", 10_000, 10_001, 9_000, 1.0, 10, False, ())
    assert over.already_over
    assert not Pace("Shopping", 10_000, 10_000, 12_000, 0.9, 10, False, ()).already_over
    assert series_categorized(["a", "b", "c"], {"a": "series", "b": "model"}) == ["a"]


# --- Whole-merchant moves store only the rows they move (reviewer NB1, NB2, NB3) ---


def mixed_merchant(conn: sqlite3.Connection, account: str = "synthetic-A"):  # type: ignore[no-untyped-def]
    """Build the mixed case here: a merchant with some payments in a series I give a category
    and some rows outside it, one of which I correct one row at a time. Returns (merchant, the
    series' payments, the one-row-corrected row, the rest)."""
    from lookahead.corrections import merchant_fingerprints
    from lookahead.recurring_store import load_series, refresh, set_category

    refresh(conn, account)
    rows = conn.execute(
        "SELECT fingerprint, merchant FROM category_predictions WHERE account = ?", (account,)
    )
    merchant_of = dict(rows.fetchall())
    for s in load_series(conn, account):
        if s.direction != "out" or not s.evidence:
            continue
        merchant = merchant_of.get(s.evidence[0])
        covered = merchant_fingerprints(conn, account, merchant) if merchant else []
        outside = [f for f in covered if f not in set(s.evidence)]
        if len(outside) >= 2 and set(s.evidence) <= set(covered):
            set_category(conn, account, s, "Subscriptions", date(2026, 9, 20))
            refresh(conn, account)  # as the Recurring page does
            correct_row(conn, account, outside[0], "Gifts & donations", date(2026, 9, 20))
            return merchant, list(s.evidence), outside[0], outside[1:]
    raise AssertionError("no merchant with both series and non-series rows")


def test_a_whole_merchant_move_locks_in_every_row_but_series_and_one_row_choices(
    conn: sqlite3.Connection,
) -> None:
    from lookahead.budgets import move_merchant
    from lookahead.corrections import corrections
    from lookahead.views import transactions

    merchant, in_series, own_row, rest = mixed_merchant(conn)
    with conn:  # one of the other rows already shows the target, as the model might have it
        conn.execute("UPDATE category_predictions SET category = 'Groceries' WHERE fingerprint = ?",
                     (rest[0],))  # fmt: skip
    before = dict(corrections(conn, "synthetic-A"))
    result = move_merchant(conn, "synthetic-A", merchant, "Groceries", date(2026, 9, 21))
    assert result.kept_by_series == len(in_series) and result.kept_by_row == 1
    assert result.already_there == 1 and result.moved == len(rest) - 1
    assert result.covered == len(in_series) + 1 + len(rest)
    after = corrections(conn, "synthetic-A")
    stored = {f for f in after if after[f] != before.get(f)}
    assert stored == set(rest)  # the row already there is locked in too; series and own row not
    shown = transactions(conn, "synthetic-A")
    category = dict(zip(shown["fingerprint"], shown["category"], strict=True))
    assert all(category[f] == "Groceries" for f in rest)
    assert all(category[f] == "Subscriptions" for f in in_series)
    assert category[own_row] == "Gifts & donations"
    # Clearing the series' category later brings back each payment's own category, not Groceries.
    from lookahead.recurring_store import load_series, refresh, set_category

    series = next(s for s in load_series(conn, "synthetic-A") if s.evidence[0] in in_series)
    set_category(conn, "synthetic-A", series, None, date(2026, 9, 22))
    refresh(conn, "synthetic-A")
    shown = transactions(conn, "synthetic-A")
    category = dict(zip(shown["fingerprint"], shown["category"], strict=True))
    assert not any(category[f] == "Groceries" for f in in_series)


def test_a_second_move_to_the_same_category_stores_nothing_new(conn: sqlite3.Connection) -> None:
    from lookahead.budgets import merchant_move_message, move_merchant

    merchant, in_series, _, rest = mixed_merchant(conn)
    move_merchant(conn, "synthetic-A", merchant, "Groceries")
    rows_before = conn.execute("SELECT COUNT(*) FROM category_corrections").fetchone()[0]
    again = move_merchant(conn, "synthetic-A", merchant, "Groceries")
    assert conn.execute("SELECT COUNT(*) FROM category_corrections").fetchone()[0] == rows_before
    assert again.moved == 0 and again.already_there == len(rest)
    wanted = f"0 moved, {len(rest)} already there, {len(in_series)} kept by their series"
    assert merchant_move_message(again).startswith(wanted)


def test_the_move_message_reports_moved_already_there_and_kept() -> None:
    from lookahead.budgets import MerchantMove, merchant_move_message

    assert merchant_move_message(MerchantMove(7, 3, 2, 1, 1)) == (
        "3 moved, 2 already there, 1 kept by their series, 1 kept by your one-row choice. "
        "A series' category is changed on the Recurring page."
    )
    assert merchant_move_message(MerchantMove(1, 1, 0, 0, 0)) == (
        "1 moved, 0 already there, 0 kept by their series."
    )


def test_home_says_already_over_like_the_tab() -> None:
    from lookahead.budget_alerts import BudgetEpisode, alert_message, home_line

    def episode(spent: int) -> BudgetEpisode:
        return BudgetEpisode(1, "a", "Shopping", "2026-09", date(2026, 9, 10), date(2026, 9, 10),
                             None, None, 1.0, 10_000, spent, 15_000, {})  # fmt: skip

    over = episode(12_000)
    assert home_line(over) == alert_message(over)
    assert home_line(over).startswith("You're already over this budget: Shopping")
    assert "simulated futures" not in home_line(over)
    assert "100% of simulated futures" in home_line(episode(6_000))
