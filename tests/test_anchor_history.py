"""Experiment 3's minimum history (eval/anchor_history.py): counts, nothing scored."""

from datetime import date, timedelta
from pathlib import Path

from eval import anchor_history as ah
from eval.berka import build_db
from tests.test_berka import START, made_up_account


def test_the_counts_add_up_and_recurring_rows_are_not_unscheduled(tmp_path: Path) -> None:
    frame = made_up_account(500)
    conn = build_db(frame, 99001, tmp_path / "a.db", 100000)
    try:
        origin = START + timedelta(days=450)
        c = ah.unscheduled_count(conn, "99001", origin)
    finally:
        conn.close()
    assert c["series"] + c["set_aside"] + c["unscheduled"] == c["rows"]
    assert c["history_days"] == (origin - START).days + 1  # from the first posting day
    assert c["series"] > 0  # the monthly pension and the household payment form series
    assert c["unscheduled"] > 0  # the cash withdrawals are not scheduled


def test_a_short_history_does_not_qualify(tmp_path: Path) -> None:
    frame = made_up_account(500)
    conn = build_db(frame, 99001, tmp_path / "b.db", 100000)
    try:
        c = ah.unscheduled_count(conn, "99001", START + timedelta(days=60))
    finally:
        conn.close()
    assert c["history_days"] < ah.MIN_HISTORY_DAYS and not ah.qualifies(c)


def test_both_conditions_are_needed() -> None:
    assert ah.qualifies({"history_days": 91, "unscheduled": 20})
    assert not ah.qualifies({"history_days": 90, "unscheduled": 99})
    assert not ah.qualifies({"history_days": 400, "unscheduled": 19})
    assert ah.qualifies({"history_days": 400, "unscheduled": 19}, 15)


def test_the_window_is_the_91_days_ending_on_the_origin(tmp_path: Path) -> None:
    frame = made_up_account(500)
    conn = build_db(frame, 99001, tmp_path / "c.db", 100000)
    try:
        origin = START + timedelta(days=300)
        c = ah.unscheduled_count(conn, "99001", origin)
        n = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE posted_date BETWEEN ? AND ?",
            ((origin - timedelta(days=90)).isoformat(), origin.isoformat()),
        ).fetchone()[0]
    finally:
        conn.close()
    assert c["rows"] == n
    assert isinstance(origin, date)
