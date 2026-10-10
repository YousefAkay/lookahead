"""Tests for big money that keeps coming (DECISIONS.md, 2026-10-11). Made-up rows and dates."""

from datetime import date, datetime, timedelta

import pytest

from lookahead.db import connect
from lookahead.flows import Txn, flow_types
from lookahead.forecast.path import build_forecast
from lookahead.forecast.scenario import Scenario, run_scenario
from lookahead.forecast.simulate import SimulationInputs, run
from lookahead.forecast.spending import (
    KeepsComing,
    big_cutoffs,
    big_events,
    keeps_coming_arrivals,
    spending_by_day,
)
from lookahead.forecast.start import ForecastStart
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

CARD = "Opos {:<20}toron "
AS_OF = date(2026, 9, 30)
UNI = "City University "
ON = KeepsComing()


def small_rows(start_id: int = 100) -> list[Txn]:
    """Small purchases and small deposits (typical deposit $30), so both cutoffs sit at the
    $500 floor."""
    rows = [
        Txn(start_id + i, date(2026, 1, 5) + timedelta(days=4 * i), -2000, "pos purchase",
            CARD.format("Shop"))
        for i in range(60)
    ]  # fmt: skip
    rows += [
        Txn(start_id + 100 + i, date(2026, 1, 6) + timedelta(days=7 * i), 3000, "deposit",
            "Friend ")
        for i in range(36)
    ]  # fmt: skip
    return rows


def monthly(kind: str, text: str, sizes: list[int], first: date, start_id: int = 10) -> list[Txn]:
    return [
        Txn(start_id + i, first + timedelta(days=30 * i), c, kind, text)
        for i, c in enumerate(sizes)
    ]  # fmt: skip


def arrivals(rows: list[Txn], options: KeepsComing = ON, series: set[int] | None = None):  # type: ignore[no-untyped-def]
    return keeps_coming_arrivals(rows, flow_types(rows), series or set(), AS_OF, options)


# --- Which rows keep coming ---


def test_a_deposit_with_two_earlier_rows_of_similar_size_keeps_coming() -> None:
    sizes = [60000, 90000, 75000, 120000, 70000, 100000]
    rows = small_rows() + monthly("credit memo", "1/Person A ", sizes, date(2026, 4, 1))
    a = arrivals(rows)
    # the first two have fewer than 2 earlier rows of similar size: they are not recurring-size
    assert a.in_sizes == (75000, 120000, 70000, 100000)
    days = (AS_OF - date(2026, 1, 5)).days + 1  # the history is shorter than a year
    assert a.in_rate == pytest.approx(4 / days)
    assert a.out_rate == 0.0 and a.out_sizes == ()


def test_the_cutoff_function_is_the_one_the_events_use() -> None:
    rows = small_rows() + [
        Txn(1, date(2026, 9, 3), 50000, "credit memo", "1/Person A "),  # at the floor
        Txn(2, date(2026, 9, 5), 49900, "credit memo", "1/Person A "),
        Txn(3, date(2026, 9, 10), -1600000, "bill payment", UNI),
    ]  # fmt: skip
    flows = flow_types(rows)
    cut_in, cut_out = big_cutoffs(rows, flows, set(), AS_OF)
    assert (cut_in, cut_out) == (50000, 50000)
    events = big_events(rows, flows, set(), AS_OF)
    assert sorted(e.cents for e in events) == [-1600000, 50000]


def test_one_big_payment_is_not_recurring_size_and_stays_set_aside() -> None:
    """The $16,000 case: it must not repeat, and it is still listed as set aside."""
    rows = small_rows() + [Txn(1, date(2026, 9, 10), -1600000, "bill payment", UNI)]
    a = arrivals(rows)
    assert a == a.__class__() and a.members == frozenset()
    assert [e.cents for e in big_events(rows, flow_types(rows), set(), AS_OF)] == [-1600000]
    flows = flow_types(rows)
    assert 1600000 not in spending_by_day(rows, flows, set(), AS_OF).values()


def test_series_rows_and_pass_throughs_are_not_arrivals() -> None:
    sizes = [60000, 62000, 64000, 66000, 68000, 70000]
    rows = small_rows() + monthly("credit memo", "1/Person A ", sizes, date(2026, 4, 1))
    in_series = {12, 13}
    assert arrivals(rows, series=in_series).in_sizes == (68000, 70000)
    # the last deposit (29 August) is paid straight out: a pass-through, set aside with its payment
    rows.append(Txn(40, date(2026, 9, 5), -70000, "bill payment", UNI))
    a = arrivals(rows)
    assert 15 not in a.members and a.in_sizes[-1] == 68000


def test_payments_come_back_only_at_six_or_more_a_year() -> None:
    def payments(n: int) -> list[Txn]:
        return [
            Txn(10 + i, date(2026, 3, 1) + timedelta(days=25 * i), -60000 - 1000 * i,
                "bill payment", UNI)
            for i in range(n)
        ]  # fmt: skip

    few = arrivals(small_rows() + payments(7))
    assert few.out_sizes == () and few.payments_below_floor == 5  # 5 recurring-size: under 6
    many = arrivals(small_rows() + payments(8))
    assert len(many.out_sizes) == 6 and many.payments_below_floor == 0
    assert many.out_rate > 0 and many.in_rate == 0


def test_own_account_transfers_in_can_be_left_out_both_as_rows_and_as_earlier_rows() -> None:
    rows = small_rows() + monthly(
        "customer transfer cr.", "To checking ", [80000, 82000, 84000, 86000], date(2026, 6, 1)
    )  # fmt: skip
    assert arrivals(rows, KeepsComing(own_account=True)).in_sizes == (84000, 86000)
    without = arrivals(rows, KeepsComing(own_account=False))
    assert without.in_sizes == () and without.members == frozenset()
    # and they do not make a credit memo of similar size recurring-size
    mixed = rows + [Txn(60, date(2026, 9, 25), 83000, "credit memo", "1/Person A ")]
    assert arrivals(mixed, KeepsComing(own_account=False)).in_sizes == ()
    assert arrivals(mixed, KeepsComing(own_account=True)).in_sizes[-1:] == (83000,)


def test_rows_posted_after_the_forecast_date_change_nothing() -> None:
    sizes = [60000, 62000, 64000, 66000]
    rows = small_rows() + monthly("credit memo", "1/Person A ", sizes, date(2026, 4, 1))
    later = [Txn(90 + i, AS_OF + timedelta(days=1 + i), 65000, "credit memo", "1/Person A ")
             for i in range(5)]  # fmt: skip
    assert arrivals(rows + later) == arrivals(rows)


# --- The simulation ---


def inputs(**kw: object) -> SimulationInputs:
    first = AS_OF + timedelta(days=1)
    start = ForecastStart("a", AS_OF, 100000, first, first + timedelta(days=9))
    base: dict[str, object] = dict(
        start=start, items=(), series=(), posted={}, spending_daily={}, deposits_daily={},
        history_start=AS_OF - timedelta(days=200), buffer_cents=20000, futures=40,
    )  # fmt: skip
    return SimulationInputs(**{**base, **kw})  # type: ignore[arg-type]


def test_a_deposit_that_arrives_every_day_lifts_the_balance_every_day() -> None:
    sim = run(inputs(arrivals_in_rate=1.0, arrivals_in_sizes=(5000,)))
    assert sim.p50 == tuple(100000 + 5000 * (k + 1) for k in range(10))
    assert sim.median_without == (100000,) * 10  # a deposit is not in the "without" line


def test_a_payment_that_arrives_every_day_lowers_both_lines() -> None:
    sim = run(inputs(arrivals_out_rate=1.0, arrivals_out_sizes=(5000,)))
    assert sim.p50 == tuple(100000 - 5000 * (k + 1) for k in range(10))
    assert sim.median_without == sim.p50


def test_no_other_money_turns_off_deposit_arrivals_only() -> None:
    from lookahead.forecast.path import Forecast

    base = inputs(arrivals_in_rate=1.0, arrivals_in_sizes=(5000,), arrivals_out_rate=1.0,
                  arrivals_out_sizes=(1000,))  # fmt: skip
    forecast = Forecast(base.start, "weekday", [], [], False, inputs=base, simulation=run(base))
    result = run_scenario(forecast, Scenario(no_other_money=True))
    assert result.original.p50[0] == 100000 + 5000 - 1000
    assert result.modified.p50[0] == 100000 - 1000  # the payment still arrives


def test_arrivals_use_their_own_streams_so_other_draws_do_not_move() -> None:
    history = {AS_OF - timedelta(days=i): (i * 37) % 900 for i in range(91)}
    plain = run(inputs(spending_daily=history, seed=3))
    with_arrivals = run(inputs(spending_daily=history, seed=3, arrivals_in_rate=0.3,
                               arrivals_in_sizes=(7000, 9000)))  # fmt: skip
    # the spending part is the same draw for draw: the "without deposits" median is unchanged
    assert with_arrivals.median_without == plain.median_without
    assert with_arrivals.p50 != plain.p50


# --- The whole forecast ---


@pytest.fixture(scope="module")
def persona_db(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    folder = tmp_path_factory.mktemp("kc")
    export, _ = write_persona(build_persona("v2-SAL-01").persona, folder, 42, START, END)
    conn = connect(folder / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    yield conn
    conn.close()


@pytest.mark.slow
def test_switched_on_with_nothing_that_keeps_coming_gives_exactly_the_same_forecast(
    persona_db,  # type: ignore[no-untyped-def]
) -> None:
    origin = date(2026, 5, 4)
    off = build_forecast(persona_db, "acct", "weekday", origin, None, 30, 60, keeps=None)
    on = build_forecast(persona_db, "acct", "weekday", origin, None, 30, 60, keeps=ON)
    assert off is not None and on is not None
    assert on.arrivals.members == frozenset()  # a salaried persona has no big irregular money
    assert (on.simulation, on.big_events) == (off.simulation, off.big_events)


def test_listed_events_drop_the_rows_that_became_arrivals() -> None:
    sizes = [60000, 90000, 75000, 120000, 70000, 100000]
    rows = small_rows() + monthly("credit memo", "1/Person A ", sizes, date(2026, 4, 1))
    flows = flow_types(rows)
    listed = big_events(rows, flows, set(), AS_OF)
    a = arrivals(rows)
    after = big_events(rows, flows, set(), AS_OF, a.members)
    assert len(after) == len(listed) - len([e for e in listed if e.cents in a.in_sizes])
    assert not any(e.cents in a.in_sizes for e in after)


def test_an_own_account_transfer_is_not_an_arrival_without_them_even_after_real_income() -> None:
    rows = small_rows() + monthly(
        "credit memo", "1/Person A ", [80000, 82000, 84000], date(2026, 6, 1)
    )
    rows.append(Txn(70, date(2026, 9, 20), 83000, "customer transfer cr.", "To checking "))
    with_own = arrivals(rows, KeepsComing(own_account=True))
    without = arrivals(rows, KeepsComing(own_account=False))
    assert with_own.in_sizes[-1] == 83000 and 70 in with_own.members
    assert 70 not in without.members and 83000 not in without.in_sizes


@pytest.mark.slow
def test_keeps_coming_is_the_default_and_none_gives_the_old_forecaster(
    persona_db,  # type: ignore[no-untyped-def]
) -> None:
    origin = date(2026, 5, 4)
    default = build_forecast(persona_db, "acct", "weekday", origin, None, 30, 60)
    on = build_forecast(persona_db, "acct", "weekday", origin, None, 30, 60, keeps=ON)
    assert default is not None and on is not None and default.simulation == on.simulation
    off = build_forecast(persona_db, "acct", "weekday", origin, None, 30, 60, keeps=None)
    assert off is not None and off.arrivals.members == frozenset() and off.arrivals.in_rate == 0
