"""Tests for the Time Machine back end (week 4, step 2): a forecast and its alert state as of any
past date, from only the data available then. Made-up personas only."""

import hashlib
import json
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.db import connect, connect_readonly
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.planned import add_planned
from lookahead.recurring_store import reject, series_as_of
from lookahead.settings import set_safety_buffer_cents
from lookahead.timemachine import (
    available_dates,
    precompute,
    replay,
    step_to_dict,
)

PID = "v2-TIGHT-01"  # a tight-budget persona: it alerts often
FUTURES = 60
DAYS = [date(2026, 2, 2), date(2026, 2, 9), date(2026, 2, 16), date(2026, 2, 23), date(2026, 3, 2)]


@pytest.fixture(scope="module")
def year_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("tm")
    persona = build_persona(PID)
    export, _ = write_persona(persona.persona, folder, 42, START, END)
    conn = connect(folder / "year.db")
    store_exports(conn, PID, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, persona.buffer_cents)
    conn.close()
    return folder / "year.db"


@pytest.fixture
def conn(year_db: Path, tmp_path: Path) -> sqlite3.Connection:
    copy = tmp_path / "copy.db"
    shutil.copy(year_db, copy)
    return connect(copy)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(step) -> dict:  # type: ignore[no-untyped-def,type-arg]
    """What a step says, for comparing two replays."""
    return step_to_dict(step)


# --- Which dates can be replayed ---


def test_available_dates_start_after_eight_weeks_of_history_and_end_with_the_data(
    conn: sqlite3.Connection,
) -> None:
    days = available_dates(conn, PID)
    first = conn.execute("SELECT MIN(posted_date) FROM transactions").fetchone()[0]
    last = conn.execute("SELECT MAX(posted_date) FROM transactions").fetchone()[0]
    assert days[0] == date.fromisoformat(first) + timedelta(weeks=8)
    assert days[-1] == date.fromisoformat(last)
    assert all(b - a == timedelta(days=1) for a, b in zip(days, days[1:], strict=False))


def test_an_account_with_too_little_history_has_no_dates(tmp_path: Path) -> None:
    c = connect(tmp_path / "e.db")
    assert available_dates(c, "nobody") == []
    c.close()


# --- A step ---


def test_a_step_is_the_forecast_and_the_alert_state_as_of_that_date(
    conn: sqlite3.Connection,
) -> None:
    [step] = list(replay(conn, PID, [DAYS[0]], futures=FUTURES))
    assert step.as_of == DAYS[0]
    assert step.forecast.start.data_last_updated == DAYS[0]  # the data ends where the date says
    assert step.history_days >= 8 * 7
    assert step.series_count >= 1
    assert step.alert.episode_id is None or step.alert.notified  # the first look is new or quiet


def test_replay_gives_one_step_per_date_in_order(conn: sqlite3.Connection) -> None:
    steps = list(replay(conn, PID, DAYS, futures=FUTURES))
    assert [s.as_of for s in steps] == DAYS


def test_dates_must_be_in_increasing_order(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="increasing"):
        list(replay(conn, PID, [DAYS[1], DAYS[0]], futures=FUTURES))
    with pytest.raises(ValueError, match="increasing"):
        list(replay(conn, PID, [DAYS[0], DAYS[0]], futures=FUTURES))


def test_a_date_with_too_little_history_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="history"):
        list(replay(conn, PID, [date(2025, 10, 20)], futures=FUTURES))


# --- Rule zero: only data available then ---


@pytest.mark.slow
def test_data_that_arrives_later_cannot_change_an_earlier_step(
    year_db: Path, tmp_path: Path
) -> None:
    """Two data ends: the replay of a date is the same whether the database ends at that date or
    a half year later."""
    full = connect(year_db)
    early_path = tmp_path / "early.db"
    shutil.copy(year_db, early_path)
    early = connect(early_path)
    with early:
        early.execute("DELETE FROM transactions WHERE posted_date > ?", (DAYS[-1].isoformat(),))
    a = [summary(s) for s in replay(full, PID, DAYS, futures=FUTURES)]
    b = [summary(s) for s in replay(early, PID, DAYS, futures=FUTURES)]
    assert a == b
    full.close()
    early.close()


@pytest.mark.slow
def test_rewriting_the_future_cannot_change_a_step(conn: sqlite3.Connection) -> None:
    before = [summary(s) for s in replay(conn, PID, DAYS[:3], futures=FUTURES)]
    with conn:
        conn.execute(
            "UPDATE transactions SET amount_cents = amount_cents * 3,"
            " balance_after_cents = balance_after_cents + 555555 WHERE posted_date > ?",
            (DAYS[2].isoformat(),),
        )
    after = [summary(s) for s in replay(conn, PID, DAYS[:3], futures=FUTURES)]
    assert before == after


@pytest.mark.slow
def test_a_confirmation_or_planned_item_made_after_a_date_is_not_used_then(
    conn: sqlite3.Connection,
) -> None:
    origin = DAYS[1]
    base = summary(next(iter(replay(conn, PID, [origin], futures=FUTURES))))
    series, _ = series_as_of(conn, PID, origin)
    biggest = min((s for s in series if s.direction == "out"), key=lambda s: s.expected_cents)
    reject(conn, PID, biggest.key, origin + timedelta(days=1))  # decided the day after
    add_planned(conn, PID, origin + timedelta(days=9), -90_000, "Later", origin + timedelta(days=2))
    assert summary(next(iter(replay(conn, PID, [origin], futures=FUTURES)))) == base
    reject(conn, PID, biggest.key, origin - timedelta(days=1))  # decided the day before
    changed = summary(next(iter(replay(conn, PID, [origin], futures=FUTURES))))
    assert changed != base  # known by then, so it counts


def test_a_replay_never_writes_to_the_database(year_db: Path, tmp_path: Path) -> None:
    copy = tmp_path / "ro.db"
    shutil.copy(year_db, copy)
    before = digest(copy)
    ro = connect_readonly(copy)
    steps = list(replay(ro, PID, DAYS, futures=FUTURES))
    ro.close()
    assert len(steps) == len(DAYS)
    assert digest(copy) == before  # not a byte changed


def test_the_replay_keeps_its_own_alert_record_and_leaves_the_live_one_alone(
    conn: sqlite3.Connection,
) -> None:
    list(replay(conn, PID, DAYS, futures=FUTURES))
    assert conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM alert_episodes").fetchone()[0] == 0


# --- Alerts across a replay ---


def test_alerts_across_a_replay_merge_into_episodes_and_stay_quiet_between(
    conn: sqlite3.Connection,
) -> None:
    days = [date(2025, 12, 1) + timedelta(weeks=k) for k in range(30)]
    steps = list(replay(conn, PID, days, futures=FUTURES))
    notified = [s for s in steps if s.alert.notified]
    alerting = [s for s in steps if s.snapshot is not None]
    assert alerting, "the tight-budget persona should alert at some origin"
    assert 1 <= len(notified) <= len(alerting)
    ids = {s.alert.episode_id for s in alerting}
    assert len(ids) < len(alerting)  # consecutive alerts about the same low merged
    quiet = [s for s in alerting if not s.alert.notified]
    assert quiet  # and the repeats did not each send a new alert
    for s in steps:
        if s.snapshot is None:
            assert s.alert.episode_id is None and s.episode is None


def test_an_active_alert_carries_its_evidence(conn: sqlite3.Connection) -> None:
    days = [date(2025, 12, 1) + timedelta(weeks=k) for k in range(30)]
    steps = [s for s in replay(conn, PID, days, futures=FUTURES) if s.episode is not None]
    assert steps
    evidence = steps[0].episode.evidence
    assert evidence["data_date"] == steps[0].as_of.isoformat()  # evidence from that date
    assert evidence["largest_payments"] is not None and "assumptions" in evidence


# --- Saving snapshots for a live demo ---


def test_a_step_is_saveable_and_says_what_it_is(conn: sqlite3.Connection) -> None:
    [step] = list(replay(conn, PID, [DAYS[0]], futures=FUTURES))
    record = step_to_dict(step)
    json.dumps(record)
    assert record["as_of"] == DAYS[0].isoformat() == record["data_date"]
    for key in ("p10", "p50", "p90", "point", "days"):
        assert len(record[key]) == 30
    assert record["history_days"] == step.history_days
    assert record["n_series"] == step.series_count
    assert "alert" in record and "notified" in record["alert"]
    assert "series_keys" not in record  # no text from the data


def test_precompute_saves_resumes_and_matches_a_direct_replay(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "snap.jsonl"
    assert precompute(conn, PID, DAYS[:3], out, futures=FUTURES) == 3
    assert precompute(conn, PID, DAYS, out, futures=FUTURES) == 2  # only the two new dates
    assert precompute(conn, PID, DAYS, out, futures=FUTURES) == 0
    saved = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    direct = [step_to_dict(s) for s in replay(conn, PID, DAYS, futures=FUTURES)]
    assert saved == direct  # resuming in pieces gives the same answer as one pass


def test_precompute_refuses_other_settings(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "snap.jsonl"
    precompute(conn, PID, DAYS[:1], out, futures=FUTURES)
    with pytest.raises(ValueError, match="different settings"):
        precompute(conn, PID, DAYS[:1], out, futures=FUTURES + 1)
