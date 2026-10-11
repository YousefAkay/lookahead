"""Tests for the income-timing diagnostic. Made-up populations and a synthetic persona only."""

import re
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from eval.backtest_real import real_origins
from eval.income_timing import (
    GROUP_NAMES,
    Dep,
    chance_lines,
    gap_lines,
    group_of,
    implied_chance,
    main,
    observations,
    populations_at,
    rate_at,
)
from lookahead.db import connect, connect_readonly
from lookahead.flows import Txn
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

FIRST = date(2025, 1, 6)


def test_the_groups_cover_every_number_of_days_once() -> None:
    edges = {0: 0, 7: 0, 8: 1, 14: 1, 15: 2, 21: 2, 22: 3, 28: 3, 29: 4, 35: 4, 36: 5, 400: 5}
    assert {d: group_of(d) for d in edges} == edges
    assert len(GROUP_NAMES) == 6


def test_the_implied_chance_is_one_minus_the_chance_of_none_in_seven_days() -> None:
    assert implied_chance(0.0) == 0.0 and implied_chance(1.0) == 1.0
    assert implied_chance(0.1) == pytest.approx(1 - 0.9**7)


def test_the_rate_is_the_deposits_of_the_last_year_over_the_days_observed() -> None:
    deps = [Dep(date(2026, 1, 5) + timedelta(days=30 * i), i, "x") for i in range(6)]
    origin = date(2026, 6, 30)
    assert rate_at(deps, origin, FIRST) == pytest.approx(6 / 365)  # a year of history
    short_first = date(2026, 3, 1)  # a shorter history: the days it has
    days = (origin - short_first).days + 1
    inside = sum(1 for d in deps if short_first <= d.day <= origin)
    assert rate_at(deps, origin, short_first) == pytest.approx(inside / days)
    assert rate_at(deps, date(2026, 1, 4), FIRST) == 0.0  # nothing yet


def pops_of(deposits: list[tuple[date, str]], dates: list[date], name: str = "keeps"):  # type: ignore[no-untyped-def]
    """The population on each date: the deposits posted by then."""
    return {
        d: {name: [Dep(day, i, reason) for i, (day, reason) in enumerate(deposits) if day <= d]}
        for d in dates
    }


def test_each_origin_with_a_previous_deposit_is_counted_once_with_what_followed() -> None:
    deposits = [(date(2026, 1, 5), "credit memo"), (date(2026, 2, 3), "credit memo"),
                (date(2026, 3, 4), "incoming e-Transfer")]  # fmt: skip
    origins = [date(2026, 1, 5) + timedelta(weeks=k) for k in range(10)]
    dates = sorted({*origins, *(o + timedelta(days=7) for o in origins)})
    pops = pops_of(deposits, dates)
    obs, without = observations(pops, origins, FIRST, "keeps")
    assert without == 0 and len(obs) == 10  # the first origin has a deposit on the same day
    by_origin = dict(zip(origins, obs, strict=True))
    assert by_origin[date(2026, 1, 5)].days_since == 0 and not by_origin[date(2026, 1, 5)].event
    # Jan 26 is 21 days after the previous deposit; the next one, Feb 3, is the 8th day after it
    assert by_origin[date(2026, 1, 26)].days_since == 21 and not by_origin[date(2026, 1, 26)].event
    # Feb 2 is 28 days after it, and Feb 3 is in the 7 days that follow
    assert by_origin[date(2026, 2, 2)].days_since == 28 and by_origin[date(2026, 2, 2)].event
    # by type: e-Transfers: the only one is Mar 4
    t_obs, t_without = observations(pops, origins, FIRST, "keeps", "incoming e-Transfer")
    assert (
        len(t_obs) == 1 and t_without == 9 and t_obs[0].days_since == 5
    )  # only Mar 9 (10 weeks from Jan 5)


def test_a_deposit_after_the_window_is_not_an_event_and_nothing_is_dated_in_the_output() -> None:
    deposits = [(date(2026, 1, 5), "credit memo"), (date(2026, 1, 20), "credit memo")]
    origins = [date(2026, 1, 12)]
    pops = pops_of(deposits, [date(2026, 1, 12), date(2026, 1, 19)])
    (obs,), _ = observations(pops, origins, FIRST, "keeps")
    assert not obs.event  # Jan 20 is the 8th day, outside the 7 days
    text = "\n".join(chance_lines([obs], 0))
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text) and "7-14" not in text


def test_gap_statistics() -> None:
    days = [date(2026, 1, 1) + timedelta(days=g) for g in (*range(0, 301, 30), 341)]  # 11 gaps
    lines = gap_lines([Dep(d, i, "x") for i, d in enumerate(days)])
    assert (
        "n 11" in lines[0] and "median 30 days" in lines[0] and "91% of them 20 to 35" in lines[0]
    )
    assert "too few" in gap_lines([Dep(d, i, "x") for i, d in enumerate(days[:10])])[0]  # 9 gaps
    assert "too few" in gap_lines([Dep(days[0], 0, "x")])[0]


# --- On a synthetic persona ---


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("timing")
    export, _ = write_persona(build_persona("v2-ITIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    conn.close()
    return folder / "d.db"


def load(conn: sqlite3.Connection):  # type: ignore[no-untyped-def]
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = 'acct'"
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    return txns, {r[5]: r[0] for r in raw}


@pytest.mark.slow
def test_a_population_does_not_change_when_every_later_row_is_deleted(
    synthetic_db: Path, tmp_path: Path
) -> None:  # fmt: skip
    day = date(2026, 5, 25)
    copy = tmp_path / "cut.db"
    shutil.copy(synthetic_db, copy)
    raw = sqlite3.connect(copy)
    raw.execute("DELETE FROM transactions WHERE posted_date > ?", (day.isoformat(),))
    raw.commit()
    raw.close()
    full, cut = connect_readonly(synthetic_db), connect_readonly(copy)
    try:
        a = populations_at(full, "acct", *load(full), day)
        b = populations_at(cut, "acct", *load(cut), day)
    finally:
        full.close()
        cut.close()
    assert a == b and a["all"], "the persona has deposits of $500 or more"


@pytest.mark.slow
def test_the_command_prints_counts_shares_and_no_dates_or_amounts(
    synthetic_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    assert main(["--db", str(synthetic_db), "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "account-1" in out and "acct" not in out
    assert "all deposits of $500 or more in no series" in out
    assert "days since the previous deposit: n / came within 7 days" in out
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out) and "$" not in out.replace("$500", "")
    conn = connect_readonly(synthetic_db)
    try:
        txns, _ = load(conn)
    finally:
        conn.close()
    origins = real_origins(min(t.posted_date for t in txns), max(t.posted_date for t in txns))
    assert len(origins) > 20
