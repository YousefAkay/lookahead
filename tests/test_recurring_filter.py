"""Tests for (b), the "possibly ended" rule (DECISIONS.md, 2026-10-12). Made-up values and a
synthetic persona only."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.forecast import path as path_module
from lookahead.forecast.path import ForecastOptions, build_forecast
from lookahead.forecast.recurring_filter import (
    RecurringFix,
    is_possibly_ended,
    last_real_payment,
    possibly_ended_keys,
)
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.recurring_store import Stored

END_DAY = date(2026, 6, 30)
ON = ForecastOptions(recurring_fix=RecurringFix())  # (b), the default
OFF = ForecastOptions(recurring_fix=None)  # the fix switched off


def series(
    cadence: str = "monthly", evidence: tuple[str, ...] = ("a",), **fields: object
) -> Stored:
    base: dict[str, object] = dict(
        key="s", name="s", kind="merchant", direction="out", cadence=cadence,
        anchor=date(2026, 1, 5), anchor_day=5, amount_type="fixed", expected_cents=-6500,
        range_cents=(-6500, -6500), next_expected=END_DAY + timedelta(days=3),
        status="detected", active=True, evidence=evidence,
    )  # fmt: skip
    return Stored(**{**base, **fields})  # type: ignore[arg-type]


def posted_days_ago(days: int) -> dict[str, date]:
    return {"a": END_DAY - timedelta(days=days)}


@pytest.mark.parametrize(
    ("cadence", "limit"), [("weekly", 10.5), ("biweekly", 21), ("monthly", 45), ("yearly", 547.5)]
)
def test_more_than_one_and_a_half_periods_since_the_last_payment_is_possibly_ended(
    cadence: str, limit: float
) -> None:  # fmt: skip
    s = series(cadence)
    inside, outside = int(limit), int(limit) + 1  # whole days: the limit is strictly exceeded
    assert not is_possibly_ended(s, posted_days_ago(inside), END_DAY)
    assert is_possibly_ended(s, posted_days_ago(outside), END_DAY)


def test_semimonthly_is_left_alone() -> None:
    assert not is_possibly_ended(series("semimonthly"), posted_days_ago(400), END_DAY)


def test_what_i_decided_and_rent_are_never_second_guessed() -> None:
    stale = posted_days_ago(200)
    assert is_possibly_ended(series(), stale, END_DAY)
    assert not is_possibly_ended(series(status="confirmed"), stale, END_DAY)
    assert not is_possibly_ended(series(status="user-added"), stale, END_DAY)
    assert not is_possibly_ended(series(edited=True), stale, END_DAY)
    assert not is_possibly_ended(series(allowed_cents=(90000, 95000)), stale, END_DAY)


def test_no_payment_found_means_nothing_says_it_stopped() -> None:
    assert not is_possibly_ended(series(evidence=()), {}, END_DAY)
    # a payment posted after the data's last day does not count as found, or as recent
    later = {"a": END_DAY + timedelta(days=5)}
    assert last_real_payment(series(), later, END_DAY) is None
    assert not is_possibly_ended(series(), later, END_DAY)


def test_a_new_payment_brings_it_back() -> None:
    s = series(evidence=("a", "b"))
    posted = {"a": END_DAY - timedelta(days=100), "b": END_DAY - timedelta(days=100)}
    assert possibly_ended_keys([s], posted, END_DAY) == {"s"}
    posted["b"] = END_DAY - timedelta(days=3)  # the latest payment decides
    assert possibly_ended_keys([s], posted, END_DAY) == frozenset()


# --- Through the forecast ---


@pytest.fixture(scope="module")
def persona_conn(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    folder: Path = tmp_path_factory.mktemp("rf")
    export, _ = write_persona(build_persona("v2-SAL-01").persona, folder, 42, START, END)
    conn = connect(folder / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    yield conn
    conn.close()


def a_payment(conn) -> tuple[str, date]:  # type: ignore[no-untyped-def]
    row = conn.execute(
        "SELECT fingerprint, posted_date FROM transactions WHERE account = 'acct'"
        " AND posted_date >= '2026-02-01' ORDER BY posted_date, id LIMIT 1"
    ).fetchone()
    return row[0], date.fromisoformat(row[1])


def forecast_with(monkeypatch, conn, s: Stored, origin: date, options: ForecastOptions):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(path_module, "series_as_of", lambda *a, **k: ([s], origin))
    f = build_forecast(conn, "acct", "weekday", origin, None, 30, 20, options=options, keeps=None)
    assert f is not None
    return f


def has_item(forecast) -> bool:  # type: ignore[no-untyped-def]
    return any(i.series_key == "s" for i in forecast.items)


@pytest.mark.slow
def test_a_possibly_ended_series_is_not_placed_and_one_inside_the_limit_is(
    persona_conn, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    fp, day = a_payment(persona_conn)
    s = series(evidence=(fp,))
    for gap, placed_on in ((45, True), (46, False)):
        origin = day + timedelta(days=gap)
        s_here = replace(s, next_expected=origin + timedelta(days=3))
        off = forecast_with(monkeypatch, persona_conn, s_here, origin, OFF)
        on = forecast_with(monkeypatch, persona_conn, s_here, origin, ON)
        assert has_item(off)  # off: placed in both cases
        assert has_item(on) is placed_on, gap
    # the exemption holds through the forecast too
    origin = day + timedelta(days=200)
    confirmed = replace(s, status="confirmed", next_expected=origin + timedelta(days=3))
    assert has_item(forecast_with(monkeypatch, persona_conn, confirmed, origin, ON))


@pytest.mark.slow
def test_a_possibly_ended_series_is_never_placed_on_the_first_day_as_due_and_not_seen(
    persona_conn, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    fp, day = a_payment(persona_conn)
    origin = day + timedelta(days=60)
    due = replace(series(evidence=(fp,)), next_expected=origin - timedelta(days=15))  # overdue
    off = forecast_with(monkeypatch, persona_conn, due, origin, OFF)
    on = forecast_with(monkeypatch, persona_conn, due, origin, ON)
    first = off.start.first_day
    assert any(i.series_key == "s" and i.day == first and "not seen yet" in i.note
               for i in off.items)  # fmt: skip
    assert not has_item(on)


@pytest.mark.slow
def test_b_is_the_default_and_off_changes_only_what_is_possibly_ended(
    persona_conn,  # type: ignore[no-untyped-def]
) -> None:
    origin = date(2026, 6, 1)
    default = build_forecast(persona_conn, "acct", "weekday", origin, None, 30, 60)
    on = build_forecast(persona_conn, "acct", "weekday", origin, None, 30, 60, options=ON)
    off = build_forecast(persona_conn, "acct", "weekday", origin, None, 30, 60, options=OFF)
    assert default is not None and on is not None and off is not None
    assert default.simulation == on.simulation and default.items == on.items  # (b) is the default
    posted = {
        fp: date.fromisoformat(d) for fp, d in persona_conn.execute(
            "SELECT fingerprint, posted_date FROM transactions WHERE account = 'acct'")
    }  # fmt: skip
    ended = possibly_ended_keys(on.series, posted, origin)
    if not ended:  # nothing possibly ended at this origin: switched off or on is the same
        assert on.simulation == off.simulation and on.items == off.items
    else:  # otherwise only those series' items are gone
        assert {i.series_key for i in off.items} - {i.series_key for i in on.items} <= ended
