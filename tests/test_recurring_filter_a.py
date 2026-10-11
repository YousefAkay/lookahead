"""Tests for (a), weekly series of card purchases are not placed and their rows go back to the
everyday-spending stream (DECISIONS.md, 2026-10-12). Made-up values and a synthetic persona."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.forecast import path as path_module
from lookahead.forecast.path import ForecastOptions, build_forecast
from lookahead.forecast.recurring_filter import RecurringFix, is_weekly_card, weekly_card_keys
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.recurring_store import Stored

OFF = ForecastOptions(recurring_fix=None)
B_ONLY = ForecastOptions(recurring_fix=RecurringFix(possibly_ended=True, weekly_card=False))
A = ForecastOptions(recurring_fix=RecurringFix(possibly_ended=False, weekly_card=True))
BOTH = ForecastOptions(recurring_fix=RecurringFix(possibly_ended=True, weekly_card=True))
CARD = ["card purchase"] * 5


def series(cadence: str = "weekly", **fields: object) -> Stored:
    base: dict[str, object] = dict(
        key="s", name="s", kind="merchant", direction="out", cadence=cadence,
        anchor=date(2026, 1, 6), anchor_day=None, amount_type="fixed", expected_cents=-1200,
        range_cents=(-1200, -1200), next_expected=date(2026, 6, 17), status="detected",
        active=True, evidence=("a",),
    )  # fmt: skip
    return Stored(**{**base, **fields})  # type: ignore[arg-type]


def test_which_series_are_weekly_card_series() -> None:
    assert is_weekly_card(series(), CARD)
    assert is_weekly_card(series(), ["card purchase"] * 3 + ["bill payment"] * 2)  # the majority
    assert not is_weekly_card(series(), ["card purchase"] * 2 + ["bill payment"] * 3)
    assert not is_weekly_card(series("monthly"), CARD) and not is_weekly_card(
        series("biweekly"), CARD
    )
    assert not is_weekly_card(series(), ["bill payment"] * 5)
    assert not is_weekly_card(series(), ["outgoing e-Transfer"] * 5)
    assert not is_weekly_card(series(direction="in"), CARD)
    assert not is_weekly_card(series(), [])  # no payment found: nothing says it is card


def test_what_i_confirmed_added_or_edited_is_never_taken_out() -> None:
    assert not is_weekly_card(series(status="confirmed"), CARD)
    assert not is_weekly_card(series(status="user-added"), CARD)
    assert not is_weekly_card(series(edited=True), CARD)


def test_the_keys_come_from_the_reasons_of_each_series_own_payments() -> None:
    cafe = series(key="cafe", evidence=("c1", "c2"))
    bill = series(key="bill", evidence=("b1", "b2"))
    reasons = {"c1": "card purchase", "c2": "card purchase", "b1": "bill payment",
               "b2": "bill payment"}  # fmt: skip
    assert weekly_card_keys([cafe, bill], reasons) == {"cafe"}
    assert weekly_card_keys([replace(cafe, status="rejected")], reasons) == frozenset()


# --- Through the forecast ---


@pytest.fixture(scope="module")
def persona_conn(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    folder: Path = tmp_path_factory.mktemp("rfa")
    export, _ = write_persona(build_persona("v2-SAL-01").persona, folder, 42, START, END)
    conn = connect(folder / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    yield conn
    conn.close()


ORIGIN = date(2026, 6, 15)


def card_series(conn) -> Stored:  # type: ignore[no-untyped-def]
    rows = conn.execute(
        "SELECT fingerprint FROM transactions WHERE account = 'acct' AND description = ? "
        "AND posted_date BETWEEN ? AND ? ORDER BY posted_date DESC, id LIMIT 6",
        ("pos purchase", (ORIGIN - timedelta(days=45)).isoformat(),
         (ORIGIN - timedelta(days=1)).isoformat()),
    ).fetchall()  # fmt: skip
    assert len(rows) == 6, "the test persona makes card purchases"
    return series(evidence=tuple(r[0] for r in rows), next_expected=ORIGIN + timedelta(days=2))


def forecast_with(monkeypatch, conn, s: Stored, options: ForecastOptions):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(path_module, "series_as_of", lambda *a, **k: ([s], ORIGIN))
    f = build_forecast(conn, "acct", "weekday", ORIGIN, None, 30, 20, options=options, keeps=None)
    assert f is not None
    return f


def spending_total(f) -> int:  # type: ignore[no-untyped-def]
    return sum(d.spending_cents for d in f.days)


@pytest.mark.slow
def test_a_weekly_card_series_is_not_placed_and_its_rows_go_back_to_everyday_spending(
    persona_conn, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    s = card_series(persona_conn)
    off = forecast_with(monkeypatch, persona_conn, s, OFF)
    on = forecast_with(monkeypatch, persona_conn, s, A)
    assert any(i.series_key == "s" for i in off.items)  # off: placed every week
    assert not any(i.series_key == "s" for i in on.items)  # (a): not placed
    # nothing lost: its rows are everyday spending again; nothing twice: it is not placed as well
    assert spending_total(on) > spending_total(off)
    # (b) alone leaves it as it is
    b_only = forecast_with(monkeypatch, persona_conn, s, B_ONLY)
    assert any(i.series_key == "s" for i in b_only.items)
    assert spending_total(b_only) == spending_total(off)


@pytest.mark.slow
def test_a_confirmed_or_added_weekly_card_series_stays_placed_and_out_of_the_stream(
    persona_conn, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    s = replace(card_series(persona_conn), status="confirmed")
    on = forecast_with(monkeypatch, persona_conn, s, A)
    off = forecast_with(monkeypatch, persona_conn, s, OFF)
    assert any(i.series_key == "s" for i in on.items)
    assert spending_total(on) == spending_total(off)


@pytest.mark.slow
def test_a_series_that_is_not_weekly_card_is_untouched_by_a(
    persona_conn, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    s = replace(card_series(persona_conn), cadence="monthly")
    on = forecast_with(monkeypatch, persona_conn, s, A)
    off = forecast_with(monkeypatch, persona_conn, s, OFF)
    assert on.items == off.items and on.simulation == off.simulation


@pytest.mark.slow
def test_a_switched_on_with_no_weekly_card_series_is_identical_to_off(
    persona_conn,  # type: ignore[no-untyped-def]
) -> None:
    origin = date(2026, 6, 1)
    off = build_forecast(persona_conn, "acct", "weekday", origin, None, 30, 60, options=OFF)
    both = build_forecast(persona_conn, "acct", "weekday", origin, None, 30, 60, options=BOTH)
    assert off is not None and both is not None
    cards = weekly_card_keys(
        both.series,
        {fp: "card purchase" for s in both.series for fp in s.evidence if s.cadence == "weekly"},
    )  # fmt: skip
    if not cards:  # no weekly series at all at this origin: the (a) part changes nothing
        a_only = build_forecast(persona_conn, "acct", "weekday", origin, None, 30, 60, options=A)
        assert a_only is not None and a_only.simulation == off.simulation
