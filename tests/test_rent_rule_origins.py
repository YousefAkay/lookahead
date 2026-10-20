"""Where the rent fix could change a forecast (eval.rent_rule_origins), on made-up data."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from eval import rent_rule_origins as ro
from lookahead import recurring_store as rs
from lookahead.db import connect
from lookahead.rent import RentRule

ACCOUNT = "a"
ETRANSFER = "Free Interac E-Transfer "
SENT = [
    date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3), date(2025, 4, 1), date(2025, 5, 2),
    date(2025, 6, 4), date(2025, 7, 2), date(2025, 8, 1), date(2025, 9, 3), date(2025, 10, 2),
    date(2025, 11, 3), date(2025, 12, 1), date(2026, 1, 5), date(2026, 2, 2),
]  # fmt: skip


def make_db(path: Path, payments: list[tuple[date, int]]) -> sqlite3.Connection:
    conn = connect(path)
    for n, (day, cents) in enumerate(payments):
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, 'withdrawal', ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), -cents, ETRANSFER, f"fp{n:03d}"),
        )
    conn.commit()
    return conn


def give_rent_rule(conn: sqlite3.Connection, when: date) -> None:
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = max(series, key=lambda s: len(s.evidence))
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((90000, 95000), 1, 4), when)


def steady(path: Path) -> sqlite3.Connection:
    return make_db(path, [(d, 90000) for d in SENT])


def stopped_then_raised(path: Path) -> sqlite3.Connection:
    """Eight months at $900, then no e-Transfer for a long time, then $950: detection calls the
    $900 series inactive in between."""
    return make_db(path, [(d, 90000) for d in SENT[:8]] + [(d, 95000) for d in SENT[12:]])


def test_the_origins_are_mondays_with_eight_weeks_behind_and_thirty_days_ahead() -> None:
    days = ro.origin_days(date(2025, 1, 1), date(2025, 12, 31))
    assert all(d.weekday() == 0 for d in days)
    assert days[0] >= date(2025, 1, 1) + timedelta(days=56) and days[-1] <= date(2025, 12, 1)
    assert ro.origin_days(date(2025, 1, 1), date(2025, 2, 1)) == []


def test_a_rent_rule_on_a_series_that_is_always_active_is_never_affected(tmp_path: Path) -> None:
    conn = steady(tmp_path / "t.db")
    give_rent_rule(conn, date(2025, 3, 1))
    checked, with_rule, changed = ro.affected_origins(conn, ACCOUNT)
    assert checked > 10 and with_rule > 10 and changed == 0


def test_origins_where_detection_calls_the_rent_series_inactive_are_counted(tmp_path: Path) -> None:
    conn = stopped_then_raised(tmp_path / "t.db")
    give_rent_rule(conn, date(2025, 3, 1))
    checked, with_rule, changed = ro.affected_origins(conn, ACCOUNT)
    assert with_rule > 0 and 0 < changed <= with_rule <= checked


def test_origins_before_the_rule_was_made_do_not_count(tmp_path: Path) -> None:
    conn = stopped_then_raised(tmp_path / "t.db")
    give_rent_rule(conn, date(2026, 1, 10))  # after every origin that fits
    _, with_rule, changed = ro.affected_origins(conn, ACCOUNT)
    assert with_rule == 0 and changed == 0


def test_a_series_i_ended_is_not_counted(tmp_path: Path) -> None:
    conn = stopped_then_raised(tmp_path / "t.db")
    give_rent_rule(conn, date(2025, 3, 1))
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rs.end(conn, ACCOUNT, max(series, key=lambda s: len(s.evidence)), date(2025, 3, 2))
    assert ro.affected_origins(conn, ACCOUNT)[2] == 0


def test_the_script_prints_counts_only_and_leaves_the_database_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib

    conn = stopped_then_raised(tmp_path / "t.db")
    give_rent_rule(conn, date(2025, 3, 1))
    conn.close()
    before = hashlib.sha256((tmp_path / "t.db").read_bytes()).hexdigest()
    assert ro.main(["--db", str(tmp_path / "t.db")]) == 0
    out = capsys.readouterr().out
    assert hashlib.sha256((tmp_path / "t.db").read_bytes()).hexdigest() == before
    assert "rerun the real backtest" in out
    assert "sid|" not in out and "2025" not in out and "e-transfer" not in out.lower()


def test_a_missing_database_is_reported(tmp_path: Path) -> None:
    assert ro.main(["--db", str(tmp_path / "nope.db")]) == 2
