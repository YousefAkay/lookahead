"""Tests for the recurring alerts (week 5, item 3). Made-up rows in a temporary database.

Four kinds, in forecast words: an expected payment not seen yet, an amount that looks different,
a series that may have stopped, a new recurring payment. Episodes and dismissal follow the
shortfall alert's shape (own tables); a dismissed alert speaks again only if it gets worse."""

import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.recurring_alerts import (
    GRACE_DAYS,
    AlertEpisode,
    LookResult,
    conditions,
    dismiss,
    episodes,
    message,
    open_alerts,
    record_look,
    rent_cycles_overdue,
    restore,
)
from lookahead.recurring_store import (
    Stored,
    add_series,
    confirm,
    end,
    refresh,
    reject,
    series_as_of,
    set_rent_rule,
)
from lookahead.rent import RentRule

ACCOUNT = "test"
CARD = "Opos {:<20}toron "
LONG_AGO = date(2026, 1, 1)  # decisions are dated before the data, so a replay always has them


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:04d}"),
        )


def bill(conn: sqlite3.Connection, name: str, day: int, months: range, cents: int) -> None:
    for m in months:
        insert(conn, shift_to_business_day(monthly_date(2026, m, day), "later"), cents,
               "bill payment", f"{name} ")  # fmt: skip


def buy(conn: sqlite3.Connection, day: date) -> None:
    """One unrelated purchase: moves the data date without making a pattern."""
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    insert(conn, day, -1234 - n, "pos purchase", CARD.format(f"Shop{n}"))


def etransfer(conn: sqlite3.Connection, months: range, cents: int) -> None:
    for m in months:
        insert(conn, shift_to_business_day(date(2026, m, 1), "later"), -cents, "withdrawal",
               "Free Interac E-Transfer ")  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Two bills paid every month January to June, data ending on June 30."""
    c = connect(tmp_path / "t.db")
    bill(c, "Phone Co", 10, range(1, 7), -6500)
    bill(c, "Gym Club", 5, range(1, 7), -4000)
    buy(c, date(2026, 6, 30))
    return c


def named(series: list[Stored], name: str) -> Stored:
    return next(s for s in series if s.name == name and s.kind != "user")


def confirm_both(conn: sqlite3.Connection) -> None:
    series = refresh(conn, ACCOUNT)
    for name in ("Phone Co", "Gym Club"):
        confirm(conn, ACCOUNT, named(series, name), LONG_AGO)


def look(conn: sqlite3.Connection) -> list[LookResult]:
    return record_look(conn, ACCOUNT, refresh(conn, ACCOUNT))


def kinds(conn: sqlite3.Connection) -> list[str]:
    return sorted(e.kind for e in open_alerts(conn, ACCOUNT))


def told(results: list[LookResult], kind: str) -> list[LookResult]:
    return [r for r in results if r.kind == kind and r.notified]


# --- Not seen yet ---


def test_first_look_on_a_quiet_account_raises_nothing(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    assert look(conn) == []
    assert kinds(conn) == []


def test_not_seen_waits_for_the_grace_period_and_only_for_decided_series(
    conn: sqlite3.Connection,
) -> None:
    assert GRACE_DAYS == 3
    bill(conn, "Gym Club", 5, range(7, 8), -4000)
    buy(conn, date(2026, 7, 15))  # Phone Co was due Fri Jul 10: Jul 15 is its third business day

    def now(day: date) -> list[tuple[str, int]]:  # "new" is another kind's business
        got = conditions(conn, ACCOUNT, refresh(conn, ACCOUNT), day)
        return [(c.kind, c.level) for c in got if c.kind != "new"]

    assert now(date(2026, 7, 15)) == []
    buy(conn, date(2026, 7, 16))  # the fourth
    assert now(date(2026, 7, 16)) == []  # not confirmed: no alert
    confirm(conn, ACCOUNT, named(refresh(conn, ACCOUNT), "Phone Co"), LONG_AGO)
    assert now(date(2026, 7, 16)) == [("not_seen", 1)]


def test_a_late_payment_closes_the_episode(conn: sqlite3.Connection) -> None:
    # A series I added matches its payments by name on any day, so a late one is part of it.
    # (A detected series only counts payments within its tolerance; a much later one is not its.)
    add_series(conn, ACCOUNT, "Phone Co", "out", "monthly", date(2026, 1, 10), 6500, LONG_AGO)
    look(conn)
    buy(conn, date(2026, 7, 20))
    assert told(look(conn), "not_seen")
    assert kinds(conn) == ["not_seen"]
    insert(conn, date(2026, 7, 21), -6500, "bill payment", "Phone Co ")  # paid, 11 days late
    assert look(conn) == []
    assert kinds(conn) == []
    assert [e.status for e in episodes(conn, ACCOUNT)] == ["closed"]


def test_looking_again_at_the_same_numbers_adds_nothing(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    look(conn)
    bill(conn, "Gym Club", 5, range(7, 8), -4000)
    buy(conn, date(2026, 7, 20))
    first = look(conn)
    count = conn.execute("SELECT COUNT(*) FROM recurring_alert_events").fetchone()[0]
    again = look(conn)
    assert told(first, "not_seen") and not told(again, "not_seen")
    assert conn.execute("SELECT COUNT(*) FROM recurring_alert_events").fetchone()[0] == count
    assert len(episodes(conn, ACCOUNT)) == 1


def test_a_dismissed_not_seen_speaks_again_only_when_another_due_date_passes(
    conn: sqlite3.Connection,
) -> None:
    # A series I added myself stays active, so a second unpaid due date can be counted.
    add_series(conn, ACCOUNT, "Phone Co", "out", "monthly", date(2026, 1, 10), 6500, LONG_AGO)
    buy(conn, date(2026, 7, 20))
    first = look(conn)
    assert [(r.kind, r.notified) for r in first] == [("not_seen", True)]
    episode = open_alerts(conn, ACCOUNT)[0]
    dismiss(conn, ACCOUNT, episode.episode_id, date(2026, 7, 21))
    assert kinds(conn) == []
    buy(conn, date(2026, 7, 28))  # a week on, the same single due date: still quiet
    assert not told(look(conn), "not_seen")
    assert kinds(conn) == []
    buy(conn, date(2026, 8, 20))  # the August due date has passed too
    again = look(conn)
    assert [r.notified for r in again if r.kind == "not_seen"] == [True]
    assert kinds(conn) == ["not_seen"]  # shown again
    assert open_alerts(conn, ACCOUNT)[0].level == 2


# --- Amount looks different ---


def test_amount_alert_needs_ten_percent_and_ten_dollars(conn: sqlite3.Connection) -> None:
    add_series(conn, ACCOUNT, "Streaming Co", "out", "monthly", date(2026, 1, 8), 1000, LONG_AGO)
    bill(conn, "Streaming Co", 8, range(1, 6), -1000)
    bill(conn, "Streaming Co", 8, range(6, 7), -1500)  # +$5 and +50 percent: under $10
    assert [
        c.kind
        for c in conditions(conn, ACCOUNT, refresh(conn, ACCOUNT), date(2026, 6, 30))
        if c.kind == "amount"
    ] == []
    insert(conn, date(2026, 7, 8), -1100, "bill payment", "Streaming Co ")  # +$1: no
    insert(conn, date(2026, 8, 10), -2500, "bill payment", "Streaming Co ")  # +$15, +150 percent
    got = [
        c
        for c in conditions(conn, ACCOUNT, refresh(conn, ACCOUNT), date(2026, 8, 10))
        if c.kind == "amount"
    ]
    assert len(got) == 1 and got[0].amount_cents == 2500


def test_amount_alert_for_a_confirmed_series_whose_price_rose(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    look(conn)
    bill(conn, "Gym Club", 5, range(7, 8), -5500)  # $15 and 37 percent more
    got = look(conn)
    assert [r.notified for r in got if r.kind == "amount"] == [True]
    episode = next(e for e in open_alerts(conn, ACCOUNT) if e.kind == "amount")
    assert episode.amount_cents == 5500
    assert "Gym" in message(episode, "Gym Club") and "looks different" in message(episode, "x")


def test_a_dismissed_amount_alert_speaks_again_only_if_it_gets_worse(
    conn: sqlite3.Connection,
) -> None:
    add_series(conn, ACCOUNT, "Streaming Co", "out", "monthly", date(2026, 1, 8), 2000, LONG_AGO)
    bill(conn, "Streaming Co", 8, range(1, 7), -2000)
    insert(conn, date(2026, 7, 8), -3500, "bill payment", "Streaming Co ")
    assert [r.notified for r in look(conn) if r.kind == "amount"] == [True]
    dismiss(conn, ACCOUNT, open_alerts(conn, ACCOUNT)[0].episode_id, date(2026, 7, 9))
    insert(conn, date(2026, 8, 10), -3600, "bill payment", "Streaming Co ")  # +3 percent: quiet
    assert not told(look(conn), "amount")
    insert(conn, date(2026, 9, 8), -2800, "bill payment", "Streaming Co ")  # better: quiet
    assert not told(look(conn), "amount")
    assert kinds(conn) == [] or kinds(conn) == ["not_seen"]  # still dismissed (or other kind)
    insert(conn, date(2026, 10, 8), -5200, "bill payment", "Streaming Co ")  # +49 percent, +$17
    assert told(look(conn), "amount")
    assert "amount" in kinds(conn)


def test_a_smaller_income_is_the_worse_direction(conn: sqlite3.Connection) -> None:
    add_series(conn, ACCOUNT, "Payroll Inc", "in", "monthly", date(2026, 1, 15), 200000, LONG_AGO)
    for m in range(1, 7):
        insert(conn, shift_to_business_day(monthly_date(2026, m, 15), "earlier"), 200000,
               "deposit", "Payroll Inc ")  # fmt: skip
    insert(conn, date(2026, 7, 15), 150000, "deposit", "Payroll Inc ")
    assert told(look(conn), "amount")
    dismiss(conn, ACCOUNT, open_alerts(conn, ACCOUNT)[0].episode_id, date(2026, 7, 16))
    insert(conn, date(2026, 8, 14), 180000, "deposit", "Payroll Inc ")  # higher: quiet
    assert not told(look(conn), "amount")
    insert(conn, date(2026, 9, 15), 100000, "deposit", "Payroll Inc ")  # lower again: speaks
    assert told(look(conn), "amount")


# --- May have stopped ---


def test_stopped_needs_an_active_series_at_the_last_look(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    look(conn)  # June 30: both active
    bill(conn, "Gym Club", 5, range(7, 10), -4000)
    buy(conn, date(2026, 9, 25))  # Phone Co: July, August and September 10th all unpaid
    got = look(conn)
    assert [(r.kind, r.notified) for r in got if r.kind == "stopped"] == [("stopped", True)]
    assert kinds(conn) == ["stopped"]  # not also "not seen": it is past that


def test_a_series_already_stopped_at_the_first_look_is_not_news(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    buy(conn, date(2026, 10, 20))  # both bills have been silent for months; no look before
    assert [r for r in look(conn) if r.kind == "stopped"] == []


def test_stopped_is_never_raised_for_a_series_I_ended_or_rejected(
    conn: sqlite3.Connection,
) -> None:
    confirm_both(conn)
    look(conn)
    series = refresh(conn, ACCOUNT)
    end(conn, ACCOUNT, named(series, "Phone Co"), LONG_AGO)
    reject(conn, ACCOUNT, named(series, "Gym Club"), LONG_AGO)
    buy(conn, date(2026, 10, 20))
    assert look(conn) == []


def test_stopped_never_closes_into_a_dismissal_loop(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    look(conn)
    bill(conn, "Gym Club", 5, range(7, 10), -4000)
    buy(conn, date(2026, 9, 25))
    look(conn)
    dismiss(conn, ACCOUNT, open_alerts(conn, ACCOUNT)[0].episode_id, date(2026, 9, 26))
    bill(conn, "Gym Club", 5, range(10, 13), -4000)
    buy(conn, date(2026, 12, 20))  # months more of silence: nothing worse in kind
    assert not told(look(conn), "stopped")
    assert kinds(conn) == []


def test_a_series_with_a_rent_rule_never_raises_stopped(conn: sqlite3.Connection) -> None:
    etransfer(conn, range(1, 7), 75000)
    series = refresh(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    confirm(conn, ACCOUNT, rent, LONG_AGO)
    set_rent_rule(conn, ACCOUNT, rent, RentRule((75000,), 30, 7), LONG_AGO)
    look(conn)  # June 30: baseline
    buy(conn, date(2026, 9, 15))  # three rent windows (Jun 30, Jul 30, Aug 30) have ended unpaid
    got = look(conn)
    assert not [r for r in got if r.kind == "stopped"]
    assert "stopped" not in kinds(conn)
    rent_alert = [e for e in open_alerts(conn, ACCOUNT) if e.kind == "not_seen"]
    assert len(rent_alert) == 1 and rent_alert[0].level == 3


def test_rent_cycles_overdue_waits_for_the_window_to_end_plus_grace() -> None:
    rule = RentRule((80000,), 30, 7)
    paid = [date(2026, 11, 2)]  # belongs to the cycle that started Oct 30
    assert rent_cycles_overdue(rule, paid, date(2026, 12, 10)) == []  # Nov 30 window ended Dec 7
    got = rent_cycles_overdue(rule, paid, date(2027, 1, 20))
    assert got == [date(2026, 11, 30), date(2026, 12, 30)]  # a window that wraps the year
    assert rent_cycles_overdue(rule, paid, date(2026, 12, 9)) == []  # Dec 7 + 2 business days
    assert rent_cycles_overdue(rule, paid, date(2026, 12, 14)) == [date(2026, 11, 30)]


# --- New recurring payment ---


def two_streaming_payments(conn: sqlite3.Connection) -> None:
    bill(conn, "Streaming Co", 20, range(5, 7), -1299)  # two payments: below the detector's three


def test_a_new_series_is_raised_once_and_resolved_by_a_decision(conn: sqlite3.Connection) -> None:
    two_streaming_payments(conn)
    buy(conn, date(2026, 6, 30))
    look(conn)  # baseline
    bill(conn, "Streaming Co", 20, range(7, 8), -1299)  # the third makes it a series
    got = look(conn)
    assert [(r.kind, r.notified) for r in got] == [("new", True)]
    assert kinds(conn) == ["new"]
    streaming = named(refresh(conn, ACCOUNT), "Streaming Co")
    confirm(conn, ACCOUNT, streaming, LONG_AGO)
    look(conn)
    assert kinds(conn) == []


def test_a_dismissed_new_series_never_speaks_again(conn: sqlite3.Connection) -> None:
    two_streaming_payments(conn)
    look(conn)
    bill(conn, "Streaming Co", 20, range(7, 8), -1299)
    look(conn)
    dismiss(conn, ACCOUNT, open_alerts(conn, ACCOUNT)[0].episode_id, date(2026, 7, 21))
    bill(conn, "Streaming Co", 20, range(8, 10), -1299)
    bill(conn, "Streaming Co", 20, range(10, 11), -4000)
    assert not told(look(conn), "new")
    assert kinds(conn) == []


def test_the_first_look_makes_no_series_new(conn: sqlite3.Connection) -> None:
    assert look(conn) == []  # two detected, undecided series and no earlier look to compare


def test_a_rejected_new_series_closes_its_episode(conn: sqlite3.Connection) -> None:
    two_streaming_payments(conn)
    look(conn)
    bill(conn, "Streaming Co", 20, range(7, 8), -1299)
    look(conn)
    reject(conn, ACCOUNT, named(refresh(conn, ACCOUNT), "Streaming Co"), LONG_AGO)
    look(conn)
    assert kinds(conn) == []


# --- Dismissing by hand, the record, rule zero ---


def test_dismiss_and_restore(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    look(conn)
    bill(conn, "Gym Club", 5, range(7, 8), -4000)
    buy(conn, date(2026, 7, 20))
    look(conn)
    episode = open_alerts(conn, ACCOUNT)[0]
    dismiss(conn, ACCOUNT, episode.episode_id, date(2026, 7, 21))
    assert open_alerts(conn, ACCOUNT) == []
    assert [e.status for e in episodes(conn, ACCOUNT)] == ["dismissed"]
    restore(conn, ACCOUNT, episode.episode_id)
    assert [e.status for e in episodes(conn, ACCOUNT)] == ["open"]
    with pytest.raises(ValueError, match="no such alert"):
        dismiss(conn, ACCOUNT, 999)
    with pytest.raises(ValueError, match="no such alert"):
        dismiss(conn, "another account", episode.episode_id)


def test_rows_posted_later_never_change_a_look(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    bill(conn, "Gym Club", 5, range(7, 8), -5500)
    buy(conn, date(2026, 7, 20))
    day = date(2026, 7, 20)

    def as_of_day() -> list[tuple[str, str, int, int | None]]:
        series, _ = series_as_of(conn, ACCOUNT, day)
        return [(c.series_id, c.kind, c.level, c.amount_cents)
                for c in conditions(conn, ACCOUNT, series, day)]  # fmt: skip

    before = as_of_day()
    assert before  # something to compare
    insert(conn, date(2026, 7, 22), -6500, "bill payment", "Phone Co ")  # posted after the look
    bill(conn, "Gym Club", 5, range(8, 9), -4000)
    assert as_of_day() == before


def test_wording_never_says_missed_or_fraud(conn: sqlite3.Connection) -> None:
    confirm_both(conn)
    look(conn)
    two_streaming_payments(conn)
    bill(conn, "Streaming Co", 20, range(7, 8), -1299)
    bill(conn, "Gym Club", 5, range(7, 9), -5500)
    buy(conn, date(2026, 8, 22))  # Phone Co: July and August unpaid; Streaming Co not yet late
    look(conn)
    texts = [message(e, "Some Series") for e in episodes(conn, ACCOUNT)]
    assert {e.kind for e in episodes(conn, ACCOUNT)} >= {"stopped", "new"}
    for text in texts:
        assert not any(w in text.lower() for w in ("missed", "fraud", "guarantee"))
    assert any("may have stopped" in t for t in texts)
    assert any("new recurring" in t.lower() for t in texts)


def test_not_seen_wording() -> None:
    episode = AlertEpisode(1, "id", "not_seen", date(2026, 7, 16), date(2026, 7, 16), None, None,
                           1, None, {"due": ["2026-07-10"], "last_paid": "2026-06-10"})  # fmt: skip
    assert "expected payment not seen yet" in message(episode, "Phone Co").lower()


# --- Import guard ---


def test_the_module_imports_neither_the_forecaster_nor_the_proposers() -> None:
    code = (
        "import sys, lookahead.recurring_alerts\n"
        "bad = [m for m in sys.modules if m.startswith(('lookahead.forecast',"
        " 'lookahead.proposals', 'lookahead.setting_proposals', 'lookahead.alerts'))]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[1], check=True)  # fmt: skip
    assert out.stdout.strip() == ""
