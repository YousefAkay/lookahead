"""The migration to stable series ids (lookahead.identity), on a made-up database.

The database holds a gym paid monthly and a cafe habit that changed price (two clusters), with
a decision log as it looked before ids existed: decisions that still name a series by its key,
and orphans whose key names no series today."""

import hashlib
import json
import random
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead import identity
from lookahead import recurring_store as rs
from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect

CARD = "Opos {:<20}toron "
ACCOUNT = "a"
CAFE_OLD, CAFE_NEW, GYM = (
    "merchant|out|Corner Cafe|1196",
    "merchant|out|Corner Cafe|1547",
    "merchant|out|Gym Club",
)
SNAPSHOT = {
    "name": "Corner Cafe", "kind": "merchant", "direction": "out", "cadence": "weekly",
    "anchor": "2026-01-06", "anchor_day": None, "amount_type": "fixed",
    "expected_cents": -1196, "range_cents": [-1196, -1196], "key": CAFE_OLD,
}  # fmt: skip


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def old_log(conn: sqlite3.Connection) -> None:
    rows = [
        (GYM, "reject", {}, "2026-06-01"),  # names a series that exists: moves
        (CAFE_OLD, "confirm", SNAPSHOT, "2026-05-25"),  # moves; the new price then carries it on
        ("merchant|out|City Pool|1500", "rename", {"name": "Pool"}, "2026-05-26"),  # ambiguous
        ("merchant|out|Gym Club|4100", "category", {"category": "Fun"}, "2026-06-02"),  # clear
        ("merchant|out|Gone Co|900", "end", {}, "2026-06-03"),  # nothing like it today
    ]
    with conn:
        conn.executemany(
            "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(ACCOUNT, k, a, json.dumps(d), day) for k, a, d, day in rows],
        )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    conn = connect(path)
    n = 0

    def insert(day: date, cents: int, kind: str, text: str) -> None:
        nonlocal n
        n += 1
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:04d}"),
        )

    for m in range(1, 9):
        day = shift_to_business_day(monthly_date(2026, m, 5), "later")
        insert(day, -4000, "bill payment", "Gym Club ")
    for m in range(1, 9):  # a phone bill nobody has decided anything about yet
        day = shift_to_business_day(monthly_date(2026, m, 10), "later")
        insert(day, -6500, "bill payment", "Phone Co ")
    rng = random.Random(5)
    for i in range(31):
        cents = -((1200 if i < 20 else 1550) + rng.randint(-20, 20))
        day = date(2026, 1, 6) + timedelta(days=7 * i)
        insert(day, cents, "pos purchase", CARD.format("Corner Cafe"))
    for i in range(14):
        cents = -rng.choice([350, 475, 520, 830, 910, 2300, 2750, 3100])
        day = date(2026, 1, 8) + timedelta(days=int(15.3 * i) + 1)
        insert(day, cents, "pos purchase", CARD.format("Corner Cafe"))
    for i in range(30):  # a pool with two weekly habits at once: its family is ambiguous
        for weekday, base in ((1, 900), (4, 4000)):
            day = date(2026, 1, 6) + timedelta(days=7 * i + weekday - 1)
            insert(day, -(base + rng.randint(-15, 15)), "pos purchase", CARD.format("City Pool"))
    for i in range(20):
        day = date(2026, 1, 9) + timedelta(days=int(10.9 * i) + 1)
        insert(day, -rng.choice([1500, 2300, 2750, 3100]), "pos purchase", CARD.format("City Pool"))
    conn.commit()
    old_log(conn)
    conn.close()
    return path


def keys(path: Path) -> dict[int, str]:
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT decision_id, series_key FROM recurring_decisions"))
    finally:
        conn.close()


def today(path: Path) -> dict[str, rs.Stored]:
    conn = connect(path)
    try:
        return {s.key: s for s in rs.series_as_of(conn, ACCOUNT)[0]}
    finally:
        conn.close()


def test_the_made_up_database_has_the_cases_we_need(db: Path) -> None:
    # reading old keys keeps working: the confirmed old cluster already carries on as the new one
    assert {CAFE_NEW, GYM} < set(today(db)) and CAFE_OLD not in today(db)
    assert today(db)[CAFE_NEW].status == "confirmed" and today(db)[GYM].status == "rejected"


def test_report_counts_decisions_that_apply_and_that_do_not(db: Path) -> None:
    counts = identity.report(db, None)
    assert counts["decisions"] == 5 and counts["by id"] == 0
    assert counts["apply to a series today"] == 2 and counts["apply to no series today"] == 3


def test_a_dry_run_writes_nothing(db: Path) -> None:
    before = sha(db)
    identity.dry_run(db, None)
    assert sha(db) == before


# A) the dry run and the apply report identical counts


def test_the_dry_run_and_the_apply_report_the_same_counts(db: Path, tmp_path: Path) -> None:
    dry = identity.dry_run(db, None)
    backup, applied = identity.apply(db, None)
    assert dry == applied and backup.exists()
    assert dry["decisions"] == 5 and dry["decisions_moved_to_id"] == 2
    assert dry["orphans_reattachable"] == 1 and dry["orphans_ambiguous"] == 1
    assert dry["orphans_without_candidate"] == 1 and dry["orphans_reattached"] == 0
    assert dry["orphans_kept_as_they_are"] == 3 and dry["members_added"] > 0
    assert dry["decisions_applying_after"] >= dry["decisions_applying_before"]


def test_after_apply_the_decisions_are_on_ids_and_the_orphans_are_kept(db: Path) -> None:
    before_ids = set(keys(db))
    identity.apply(db, None)
    after = keys(db)
    assert set(after) == before_ids  # no row lost
    assert sum(k.startswith("sid|") for k in after.values()) == 2
    assert "merchant|out|Gone Co|900" in after.values()  # an orphan stays as it was
    series = today(db)
    assert series[GYM].status == "rejected"
    assert CAFE_OLD not in series and series[CAFE_NEW].status == "confirmed"  # one habit


def test_the_forecast_counts_say_what_changes(db: Path) -> None:
    counts = identity.dry_run(db, None)
    # decisions that already applied by their old key keep applying, so no series changes
    assert counts["series_before"] == counts["series_after"] == 5
    assert counts["forecast_series_removed"] == counts["forecast_series_added"] == 0
    assert counts["forecast_series_changed"] == 0


def test_applying_twice_changes_nothing_the_second_time(db: Path) -> None:
    identity.apply(db, None)
    again = identity.dry_run(db, None)
    assert again["decisions_moved_to_id"] == 0 and again["members_added"] == 0
    assert again["decisions_by_id_already"] == 2


# D) orphans are re-attached only with the flag, and only when the family is clear


def test_orphans_are_not_re_attached_unless_asked(db: Path) -> None:
    identity.apply(db, None)
    kept = keys(db)
    assert "merchant|out|Gym Club|4100" in kept.values()  # reattachable, but not asked for


def test_the_flag_re_attaches_only_the_clear_orphan(db: Path) -> None:
    dry = identity.dry_run(db, None, reattach=True)
    assert dry["orphans_reattached"] == 1 and dry["orphans_ambiguous"] == 1
    identity.apply(db, None, reattach=True)
    after = keys(db)
    assert "merchant|out|Gym Club|4100" not in after.values()
    assert "merchant|out|City Pool|1500" in after.values()  # ambiguous: never moved
    assert "merchant|out|Gone Co|900" in after.values()
    assert today(db)[GYM].category == "Fun"  # the re-attached decision now applies


# B) rollback


def test_a_failing_check_leaves_the_database_byte_identical(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = identity.execute

    def lose_a_decision(conn: sqlite3.Connection, plan: identity.Plan) -> None:
        real(conn, plan)
        conn.execute("DELETE FROM recurring_decisions WHERE action = 'end'")

    monkeypatch.setattr(identity, "execute", lose_a_decision)
    before = sha(db)
    with pytest.raises(identity.IdentityCheckError):
        identity.apply(db, None)
    assert sha(db) == before
    for leftover in db.parent.glob("*.bak-*"):
        leftover.unlink()  # a backup is named by the second; make room for the next try
    assert identity.main(["apply", "--db", str(db)]) == 1
    assert sha(db) == before


def test_restoring_the_backup_gives_back_the_identical_database(db: Path) -> None:
    before = sha(db)
    backup, _ = identity.apply(db, None)
    assert sha(backup) == before and sha(db) != before
    shutil.copyfile(backup, db)
    assert sha(db) == before


def test_apply_refuses_to_run_if_the_backup_cannot_be_written(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "afile"
    blocker.write_text("not a folder")
    before = sha(db)
    with pytest.raises(OSError):
        identity.apply(db, None, backup_folder=blocker / "inside")
    assert sha(db) == before

    def broken(*args: object, **kwargs: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(identity, "write_backup", broken)
    assert identity.main(["apply", "--db", str(db)]) == 2
    assert sha(db) == before


def test_the_check_command_compares_with_the_backup(db: Path) -> None:
    backup, _ = identity.apply(db, None)
    assert identity.check_against_backup(db, backup, None) == []
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM recurring_decisions WHERE action = 'end'")
    conn.close()
    assert identity.check_against_backup(db, backup, None)
    assert identity.main(["check", "--db", str(db), "--backup", str(backup)]) == 1


# E) the counts of series whose forecast contribution changed


def test_the_forecast_counts_move_when_a_re_attached_decision_changes_what_is_placed(
    db: Path,
) -> None:
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
            " VALUES (?, 'merchant|out|Phone Co|6500', 'reject', '{}', '2026-05-27')", (ACCOUNT,),
        )  # fmt: skip
    conn.close()
    without = identity.dry_run(db, None)
    assert without["forecast_series_removed"] == without["forecast_series_changed"] == 0
    assert without["forecast_series_added"] == 0
    flagged = identity.dry_run(db, None, reattach=True)
    assert flagged["forecast_series_removed"] == 1  # the phone bill is no longer placed
    assert flagged["forecast_series_added"] == flagged["forecast_series_changed"] == 0
    assert identity.dry_run(db, None, reattach=True) == flagged  # and it is repeatable
