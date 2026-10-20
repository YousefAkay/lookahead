"""After a rent increase, accepting the rent proposal must place the next rent.

The rent rule covers the old and the new amount. Detection may call the old-amount series
inactive (its payments stopped) while the new amount is not yet a series of its own; the forecast
must still place the next rent, at the larger allowed amount, in the window. A rent proposal is
offered on the live series when there is one, and never for rent that stopped. "It ended" and
"Not recurring" still win. Made-up payments only."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead import recurring_store as rs
from lookahead.db import connect
from lookahead.forecast.schedule import scheduled_items
from lookahead.forecast.start import ForecastStart
from lookahead.setting_proposals import accept, current_proposals

ACCOUNT = "a"
# monthly rent by e-Transfer, sent between the 1st and the 4th
SENT = [
    date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3), date(2025, 4, 1), date(2025, 5, 2),
    date(2025, 6, 4), date(2025, 7, 2), date(2025, 8, 1), date(2025, 9, 3), date(2025, 10, 2),
    date(2025, 11, 3), date(2025, 12, 1),
]  # fmt: skip
OLD, NEW = 90000, 95000
ETRANSFER = "Free Interac E-Transfer "
CARD_PURCHASE = "Opos Cornershop         toron "


def make_db(
    path: Path, payments: list[tuple[date, int]], elsewhere: date | None = None
) -> sqlite3.Connection:
    """Rent e-Transfers, and optionally one card purchase elsewhere (so the data ends later)."""
    conn = connect(path)
    sql = (
        "INSERT INTO transactions (account, posted_date, amount_cents, description,"
        " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
        " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')"
    )
    for n, (day, cents) in enumerate(payments):
        conn.execute(
            sql, (ACCOUNT, day.isoformat(), -cents, "withdrawal", ETRANSFER, f"fp{n:03d}")
        )  # fmt: skip
    if elsewhere is not None:
        conn.execute(
            sql, (ACCOUNT, elsewhere.isoformat(), -1500, "pos purchase", CARD_PURCHASE, "fp-else")
        )  # fmt: skip
    conn.commit()
    return conn


def forecast_items(conn: sqlite3.Connection, series_id: str) -> list:  # type: ignore[type-arg]
    series, last = rs.series_as_of(conn, ACCOUNT)
    assert last is not None
    posted = {
        fp: date.fromisoformat(d)
        for fp, d in conn.execute("SELECT fingerprint, posted_date FROM transactions")
    }
    start = ForecastStart(ACCOUNT, last, 0, last + timedelta(days=1), last + timedelta(days=45))
    mine = {s.key for s in series if s.id == series_id}
    return [i for i in scheduled_items(series, posted, start) if i.series_key in mine]


@pytest.fixture
def increased(tmp_path: Path) -> sqlite3.Connection:
    """Eight months at $900, then two at $950 after two months with no e-Transfer: the new amount
    is not a series, and detection calls the old-amount series inactive."""
    payments = [(d, OLD) for d in SENT[:8]] + [(d, NEW) for d in SENT[10:]]
    conn = make_db(tmp_path / "t.db", payments)
    yield conn
    conn.close()


def test_the_situation_detection_calls_the_only_series_inactive(
    increased: sqlite3.Connection,
) -> None:
    series, _ = rs.series_as_of(increased, ACCOUNT)
    assert len(series) == 1 and series[0].active is False  # the case the tests are about


def test_accepting_rent_after_an_increase_places_the_next_rent_at_the_larger_amount(
    increased: sqlite3.Connection,
) -> None:
    (proposal,) = current_proposals(increased, ACCOUNT)
    assert proposal.rent is not None and proposal.rent.amounts_cents == (OLD, NEW)
    series, _ = rs.series_as_of(increased, ACCOUNT)
    accept(increased, ACCOUNT, proposal, series, date(2026, 1, 5))
    items = forecast_items(increased, proposal.series_id)
    assert items, "the rent rule was accepted but no rent is placed"
    first = items[0]
    assert first.cents == -NEW  # the larger allowed amount
    assert first.day >= date(2025, 12, 2)
    rule = proposal.rent
    assert rule.from_day <= first.day.day <= rule.to_day  # in the window


def test_a_rent_proposal_goes_to_the_live_series_when_one_exists(tmp_path: Path) -> None:
    payments = [(d, OLD) for d in SENT[:8]] + [(d, NEW) for d in SENT[8:]]
    conn = make_db(tmp_path / "t.db", payments)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    assert sorted(s.active for s in series) == [False, True]  # old size stopped, new size live
    (proposal,) = current_proposals(conn, ACCOUNT)
    live = next(s for s in series if s.active)
    assert proposal.series_id == live.id
    conn.close()


def test_no_rent_proposal_for_rent_that_stopped(tmp_path: Path) -> None:
    payments = [(d, OLD) for d in SENT[:3]] + [(d, NEW) for d in SENT[3:7]]  # the last in July
    conn = make_db(tmp_path / "t.db", payments, elsewhere=date(2025, 12, 15))
    assert current_proposals(conn, ACCOUNT) == []  # the account goes on; the rent did not
    conn.close()


def test_a_rent_rule_does_not_put_back_a_series_i_ended(increased: sqlite3.Connection) -> None:
    (proposal,) = current_proposals(increased, ACCOUNT)
    series, _ = rs.series_as_of(increased, ACCOUNT)
    accept(increased, ACCOUNT, proposal, series, date(2026, 1, 5))
    rs.end(increased, ACCOUNT, series[0], date(2026, 1, 6))
    assert forecast_items(increased, proposal.series_id) == []


def test_a_rent_rule_does_not_put_back_a_series_i_called_not_recurring(
    increased: sqlite3.Connection,
) -> None:
    (proposal,) = current_proposals(increased, ACCOUNT)
    series, _ = rs.series_as_of(increased, ACCOUNT)
    accept(increased, ACCOUNT, proposal, series, date(2026, 1, 5))
    rs.reject(increased, ACCOUNT, series[0], date(2026, 1, 6))
    assert forecast_items(increased, proposal.series_id) == []


def test_confirming_again_after_an_end_puts_the_rent_back(increased: sqlite3.Connection) -> None:
    (proposal,) = current_proposals(increased, ACCOUNT)
    series, _ = rs.series_as_of(increased, ACCOUNT)
    accept(increased, ACCOUNT, proposal, series, date(2026, 1, 5))
    rs.end(increased, ACCOUNT, series[0], date(2026, 1, 6))
    rs.confirm(increased, ACCOUNT, series[0], date(2026, 1, 7))
    assert forecast_items(increased, proposal.series_id)


def test_the_rent_is_placed_once_per_window(increased: sqlite3.Connection) -> None:
    (proposal,) = current_proposals(increased, ACCOUNT)
    series, _ = rs.series_as_of(increased, ACCOUNT)
    accept(increased, ACCOUNT, proposal, series, date(2026, 1, 5))
    items = forecast_items(increased, proposal.series_id)
    assert len({i.day for i in items}) == len(items)
