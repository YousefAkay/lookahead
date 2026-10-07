"""Tests for 3c: the shortfall alert (provisional rule), episodes, evidence and suppression.
Made-up forecasts in a temporary database; nothing here reads real data."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.alerts import (
    BANDS,
    DATE_SHIFT_DAYS,
    MATERIAL_LOW_CENTS,
    PROVISIONAL,
    SAME_LOW_DAYS,
    Snapshot,
    active_alert,
    alert_message,
    band_of,
    build_evidence,
    dismiss,
    episodes,
    material_change,
    record_run,
    restore,
    should_alert,
    snapshot_of,
)
from lookahead.db import connect
from lookahead.forecast.path import Forecast, ForecastDay
from lookahead.forecast.schedule import ScheduledItem
from lookahead.forecast.simulate import Simulation
from lookahead.forecast.spending import BigEvent
from lookahead.forecast.start import ForecastStart

ACCOUNT = "test"
BUFFER = 20000


def simulation(
    first: date, low_day: date, low_cents: int, probability: float, days: int = 30
) -> Simulation:
    """A simulation whose middle future bottoms out on `low_day` at `low_cents`."""
    all_days = tuple(first + timedelta(days=k) for k in range(days))
    p50 = tuple(low_cents if d == low_day else low_cents + 50000 for d in all_days)
    return Simulation(
        days=all_days, futures=500, p10=p50, p50=p50, p90=p50, median_without=p50,
        shortfall_probability=probability, shortfall_probability_without=probability,
        below_zero_probability=0.0, below_zero_probability_without=0.0,
        median_lowest_cents=low_cents, median_lowest_without_cents=low_cents,
        weeks_drawn_from=12,
    )  # fmt: skip


def forecast(
    data_date: date = date(2026, 10, 1),
    low_offset: int = 7,
    low_cents: int = 12600,
    probability: float = 0.97,
    buffer: int = BUFFER,
    items: list[ScheduledItem] | None = None,
    big_events: tuple[BigEvent, ...] = (),
    with_simulation: bool = True,
) -> Forecast:
    first = data_date + timedelta(days=1)
    start = ForecastStart(ACCOUNT, data_date, 83688, first, first + timedelta(days=29))
    low_day = first + timedelta(days=low_offset)
    days = [ForecastDay(first + timedelta(days=k), 80000, 0, 2094, 0) for k in range(30)]
    sim = simulation(first, low_day, low_cents, probability) if with_simulation else None
    return Forecast(
        start, "weekday", days, items or [], True, buffer_cents=buffer, simulation=sim,
        big_events=big_events,
    )  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "a.db")


# --- The provisional rule ---


def test_the_rule_is_provisional_and_says_so() -> None:
    assert PROVISIONAL == "provisional, not yet validated"


def test_alert_when_the_chance_is_above_twenty_percent() -> None:
    assert should_alert(simulation(date(2026, 10, 2), date(2026, 10, 9), 90000, 0.21), BUFFER)
    assert not should_alert(simulation(date(2026, 10, 2), date(2026, 10, 9), 90000, 0.20), BUFFER)
    assert not should_alert(simulation(date(2026, 10, 2), date(2026, 10, 9), 90000, 0.0), BUFFER)


def test_alert_when_the_median_lowest_balance_is_below_the_buffer() -> None:
    low = simulation(date(2026, 10, 2), date(2026, 10, 9), BUFFER - 1, 0.05)
    assert should_alert(low, BUFFER)  # the chance is small but the middle future dips under
    exactly = simulation(date(2026, 10, 2), date(2026, 10, 9), BUFFER, 0.05)
    assert not should_alert(exactly, BUFFER)  # at the buffer is not below it


def test_a_forecast_without_simulated_futures_never_alerts() -> None:
    assert snapshot_of(forecast(with_simulation=False)) is None


def test_the_snapshot_holds_the_low_and_when_it_comes() -> None:
    snap = snapshot_of(forecast(data_date=date(2026, 10, 1), low_offset=6, low_cents=12632))
    assert snap is not None
    assert snap.data_date == date(2026, 10, 1)
    assert snap.low_day == date(2026, 10, 8)  # the day the middle future bottoms out
    assert snap.low_cents == 12632 and snap.probability == 0.97 and snap.buffer_cents == BUFFER
    assert snap.days_of_warning == 7  # from the day the data ends


def test_a_calm_forecast_has_no_snapshot() -> None:
    assert snapshot_of(forecast(low_cents=900000, probability=0.0)) is None


def test_probability_bands() -> None:
    assert BANDS == (0.20, 0.50, 0.80)
    assert [band_of(p) for p in (0.0, 0.20, 0.21, 0.50, 0.51, 0.80, 0.81, 1.0)] == [
        0, 0, 1, 1, 2, 2, 3, 3,
    ]  # fmt: skip


# --- What counts as a material change ---


def snap(**changes: object) -> Snapshot:
    fields: dict[str, object] = {
        "data_date": date(2026, 10, 1), "low_day": date(2026, 10, 8), "low_cents": 12600,
        "probability": 0.60, "buffer_cents": BUFFER, "median_lowest_cents": 12600,
    }  # fmt: skip
    return Snapshot(**{**fields, **changes})  # type: ignore[arg-type]


def test_a_small_move_of_the_low_is_not_material() -> None:
    assert material_change(snap(), snap(low_cents=12600 - (MATERIAL_LOW_CENTS - 1))) is None
    assert material_change(snap(), snap(low_cents=12600 + (MATERIAL_LOW_CENTS - 1))) is None


def test_a_large_move_of_the_low_is_material_either_way() -> None:
    down = material_change(snap(), snap(low_cents=12600 - MATERIAL_LOW_CENTS))
    up = material_change(snap(), snap(low_cents=12600 + MATERIAL_LOW_CENTS))
    assert down and "lower" in down and up and "higher" in up


def test_crossing_a_probability_band_is_material() -> None:
    reason = material_change(snap(probability=0.60), snap(probability=0.81))
    assert reason and "chance" in reason
    assert material_change(snap(probability=0.60), snap(probability=0.79)) is None


def test_a_shifted_date_is_material_from_the_set_number_of_days() -> None:
    day = date(2026, 10, 8)
    assert material_change(snap(), snap(low_day=day + timedelta(days=DATE_SHIFT_DAYS - 1))) is None
    later = material_change(snap(), snap(low_day=day + timedelta(days=DATE_SHIFT_DAYS)))
    earlier = material_change(snap(), snap(low_day=day - timedelta(days=DATE_SHIFT_DAYS)))
    assert later and "later" in later and earlier and "earlier" in earlier


# --- Episodes and suppression ---


def run(conn: sqlite3.Connection, **kwargs: object):  # type: ignore[no-untyped-def]
    return record_run(conn, ACCOUNT, forecast(**kwargs))  # type: ignore[arg-type]


def test_the_first_alert_opens_an_episode_and_is_sent(conn: sqlite3.Connection) -> None:
    result = run(conn)
    assert result.notified and result.episode_id is not None
    [episode] = episodes(conn, ACCOUNT)
    assert episode.status == "open" and episode.opened_on == date(2026, 10, 1)
    assert active_alert(conn, ACCOUNT) == episode


def test_consecutive_alerts_about_the_same_low_merge_into_one_episode(
    conn: sqlite3.Connection,
) -> None:
    first = run(conn, data_date=date(2026, 10, 1), low_offset=7)
    again = run(conn, data_date=date(2026, 10, 2), low_offset=6)  # the same low, a day closer
    assert again.episode_id == first.episode_id
    assert not again.notified  # nothing material changed: no new alert
    [episode] = episodes(conn, ACCOUNT)
    assert episode.last_seen_on == date(2026, 10, 2) and episode.opened_on == date(2026, 10, 1)


def test_a_material_change_inside_an_episode_alerts_again_without_a_new_episode(
    conn: sqlite3.Connection,
) -> None:
    first = run(conn, low_cents=12600)
    worse = run(conn, data_date=date(2026, 10, 2), low_offset=6, low_cents=1000)  # $116 lower
    assert worse.episode_id == first.episode_id and worse.notified
    assert "lower" in worse.reason
    [episode] = episodes(conn, ACCOUNT)
    assert episode.low_cents == 1000


def test_drift_is_measured_from_the_last_alert_not_the_last_look(
    conn: sqlite3.Connection,
) -> None:
    run(conn, data_date=date(2026, 10, 1), low_cents=12600)
    step = MATERIAL_LOW_CENTS // 2 + 1  # each step is small, two steps are not
    one = run(conn, data_date=date(2026, 10, 2), low_offset=6, low_cents=12600 - step)
    two = run(conn, data_date=date(2026, 10, 3), low_offset=5, low_cents=12600 - 2 * step)
    assert not one.notified and two.notified


def test_a_different_low_far_from_the_first_is_a_new_episode(conn: sqlite3.Connection) -> None:
    first = run(conn, data_date=date(2026, 10, 1), low_offset=3)
    other = run(conn, data_date=date(2026, 10, 2), low_offset=3 + SAME_LOW_DAYS + 5)
    assert other.episode_id != first.episode_id and other.notified
    old, new = episodes(conn, ACCOUNT)[1], episodes(conn, ACCOUNT)[0]
    assert old.status == "closed" and new.status == "open"


def test_an_episode_ends_when_the_alert_condition_goes_away(conn: sqlite3.Connection) -> None:
    run(conn, data_date=date(2026, 10, 1))
    calm = run(conn, data_date=date(2026, 10, 2), low_cents=900000, probability=0.0)
    assert calm.episode_id is None and not calm.notified
    [episode] = episodes(conn, ACCOUNT)
    assert episode.status == "closed" and episode.closed_on == date(2026, 10, 2)
    assert active_alert(conn, ACCOUNT) is None
    back = run(conn, data_date=date(2026, 10, 20))  # a later shortfall is a new episode
    assert back.notified and back.episode_id != episode.episode_id


def test_looking_again_at_the_same_numbers_records_nothing_new(conn: sqlite3.Connection) -> None:
    run(conn)
    run(conn)
    run(conn)
    count = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
    assert count == 1


def test_a_dismissed_episode_stays_quiet_even_after_a_material_change(
    conn: sqlite3.Connection,
) -> None:
    first = run(conn)
    dismiss(conn, ACCOUNT, first.episode_id or 0, date(2026, 10, 1))
    assert active_alert(conn, ACCOUNT) is None
    worse = run(conn, data_date=date(2026, 10, 2), low_offset=6, low_cents=1000)
    assert worse.episode_id == first.episode_id and not worse.notified
    [episode] = episodes(conn, ACCOUNT)
    assert episode.status == "dismissed" and episode.low_cents == 1000  # still tracked


def test_a_dismissed_episode_can_be_brought_back(conn: sqlite3.Connection) -> None:
    first = run(conn)
    dismiss(conn, ACCOUNT, first.episode_id or 0, date(2026, 10, 1))
    restore(conn, ACCOUNT, first.episode_id or 0)
    assert active_alert(conn, ACCOUNT) is not None


def test_dismissing_an_unknown_episode_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        dismiss(conn, ACCOUNT, 999)


def test_episodes_belong_to_one_account(conn: sqlite3.Connection) -> None:
    run(conn)
    assert episodes(conn, "someone-else") == []
    assert active_alert(conn, "someone-else") is None


def test_an_alert_uses_only_the_forecasts_own_data_date(conn: sqlite3.Connection) -> None:
    """Rule zero: the record carries the forecast's data date, never today's."""
    run(conn, data_date=date(2026, 3, 1))
    [episode] = episodes(conn, ACCOUNT)
    assert episode.opened_on == date(2026, 3, 1)  # not date.today()


# --- Evidence ---


def item(day: date, cents: int, name: str, basis: str, key: str = "k") -> ScheduledItem:
    return ScheduledItem(day, cents, key, name, basis, "")


def test_evidence_lists_the_low_the_largest_payments_before_it_and_the_assumptions() -> None:
    first = date(2026, 10, 2)
    items = [
        item(first + timedelta(days=1), -75000, "Rent", "confirmed", "rent"),
        item(first + timedelta(days=2), -6500, "Phone Co", "detected", "phone"),
        item(first + timedelta(days=3), -120000, "Tuition", "entered", "planned|1"),
        item(first + timedelta(days=4), -2000, "Gym", "detected", "gym"),
        item(first + timedelta(days=5), -3000, "Netflix", "detected", "nf"),
        item(first + timedelta(days=5), -4000, "Spotify", "detected", "sp"),
        item(first + timedelta(days=6), -9000, "Water", "detected", "w"),
        item(first + timedelta(days=9), -500000, "After the low", "detected", "late"),
        item(first + timedelta(days=2), 190000, "Pay", "detected", "pay"),
    ]
    events = (BigEvent(date(2026, 9, 1), "Credit memo", 640000, "Large deposit"),)
    evidence = build_evidence(forecast(low_offset=7, items=items, big_events=events))
    assert evidence["data_date"] == "2026-10-01" and evidence["buffer_cents"] == BUFFER
    assert evidence["low_day"] == "2026-10-09" and evidence["low_cents"] == 12600
    biggest = evidence["largest_payments"]
    assert [p["name"] for p in biggest] == ["Tuition", "Rent", "Water", "Phone Co", "Spotify"]
    assert all(p["cents"] < 0 and p["day"] <= "2026-10-09" for p in biggest)  # before the low
    assert [p["basis"] for p in biggest][:2] == ["entered", "confirmed"]
    assert evidence["assumptions"] == {"detected": 7, "confirmed": 1, "entered": 1}
    assert evidence["big_events"] == [
        {"day": "2026-09-01", "kind": "Credit memo", "cents": 640000, "group": "Large deposit"}
    ]  # set aside, not forecast
    assert evidence["method"] == "weekday" and evidence["spending_per_day_cents"] == 2094


def test_evidence_is_stored_with_the_alert_and_read_back(conn: sqlite3.Connection) -> None:
    items = [item(date(2026, 10, 3), -75000, "Rent", "confirmed", "rent")]
    result = record_run(conn, ACCOUNT, forecast(items=items))
    assert result.notified
    episode = active_alert(conn, ACCOUNT)
    assert episode is not None and episode.evidence["largest_payments"][0]["name"] == "Rent"
    assert episode.evidence["data_date"] == "2026-10-01"


# --- Wording ---


def test_the_message_is_plain_and_uses_forecast_words() -> None:
    snapshot = snapshot_of(forecast(low_cents=12632, probability=0.97))
    assert snapshot is not None
    text = alert_message(snapshot)
    assert "97%" in text and "$200" in text and "$126.32" in text and "Oct 9" in text
    for forbidden in ("guarantee", "fraud", "missed"):
        assert forbidden not in text.lower()
    assert "possible" in text.lower()
