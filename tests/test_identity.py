"""The migration to stable series ids (lookahead.identity), on a made-up database.

The database holds a gym paid monthly and a cafe habit that changed price (two clusters), with
a decision log as it looked before ids existed: decisions that still name a series by its key,
and orphans whose key names no series today."""

import hashlib
import json
import random
import shutil
import sqlite3
from collections import Counter
from dataclasses import replace
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


def build_db(path: Path, old_weeks: int = 20, new_weeks: int = 11, log: bool = True) -> Path:
    """The cafe habit pays the old price for `old_weeks` weeks and the new one for `new_weeks`."""
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
    for i in range(old_weeks + new_weeks):
        cents = -((1200 if i < old_weeks else 1550) + rng.randint(-20, 20))
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
    if log:
        old_log(conn)
    conn.close()
    return path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return build_db(tmp_path / "t.db")


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


# One habit that a price change had already joined must stay one series after the migration


@pytest.mark.parametrize(("old_weeks", "new_weeks"), [(20, 11), (10, 21), (15, 16)])
def test_a_joined_habit_stays_one_series_whichever_cluster_is_bigger(
    old_weeks: int, new_weeks: int, tmp_path: Path
) -> None:
    db = build_db(tmp_path / "t.db", old_weeks, new_weeks, log=False)
    old, new = sorted(k for k in today(db) if "Corner Cafe" in k)  # the two clusters' keys
    older, newer = sorted((old, new), key=lambda k: today(db)[k].anchor)
    snapshot = {**SNAPSHOT, "key": older}
    conn = sqlite3.connect(db)
    with conn:
        conn.executemany(
            "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(ACCOUNT, older, "confirm", json.dumps(snapshot), "2026-05-25"),
             (ACCOUNT, newer, "category", json.dumps({"category": "Fun"}), "2026-08-01")],
        )  # fmt: skip
    conn.close()
    before = today(db)
    assert len([k for k in before if "Corner Cafe" in k]) == 1  # already joined by the old keys
    counts = identity.dry_run(db, None)
    assert counts["collisions"] == 1  # two old keys now name one series: harmless, and counted
    assert counts["series_before"] == counts["series_after"]
    assert counts["decisions_applying_before"] == counts["decisions_applying_after"]
    identity.apply(db, None)
    (cafe,) = [s for k, s in today(db).items() if "Corner Cafe" in k]
    assert cafe.status == "confirmed" and cafe.category == "Fun" and len(cafe.continues) == 1
    assert identity.dry_run(db, None)["decisions_moved_to_id"] == 0  # and it is stable


# --explain: counts and kinds only


def test_explain_says_what_the_collision_is_and_prints_no_names(tmp_path: Path) -> None:
    db = build_db(tmp_path / "t.db", 10, 21, log=False)
    cafes = [k for k in today(db) if "Corner Cafe" in k]
    older, newer = sorted(cafes, key=lambda k: today(db)[k].anchor)
    conn = sqlite3.connect(db)
    with conn:
        conn.executemany(
            "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(ACCOUNT, older, "confirm", json.dumps({**SNAPSHOT, "key": older}), "2026-05-25"),
             (ACCOUNT, newer, "category", json.dumps({"category": "Fun"}), "2026-08-01")],
        )  # fmt: skip
    conn.close()
    lines: list[str] = []
    identity.dry_run(db, None, explain=lines)
    text = "\n".join(lines)
    assert "collision 1: kind = two old decision keys now name ONE series (2 old keys" in text
    assert "price-change rule" in text and "confirm x1" in text and "category x1" in text
    assert "only in the after list: 0" in text
    assert "still share remembered payments with another series: 0" in text
    assert not any(word in text for word in ("Cafe", "Gym", "Pool", "Phone", "sid|", "|out|"))
    assert identity.main(["dry-run", "--db", str(db), "--explain"]) == 0


def test_explain_names_an_extra_series_its_kind_its_actions_and_a_split() -> None:
    state = {
        "series": {"a|k1": object()},
        "kinds": {"a|k1": "active"},
        "actions": {"a|k1": Counter()},
        "remembered": {"a|k1": {"i"}},
    }
    after = {
        "series": {"a|k1": object(), "a|k2": object()},
        "kinds": {"a|k1": "active", "a|k2": "inactive (stopped itself)"},
        "actions": {"a|k1": Counter(), "a|k2": Counter({"confirm": 1})},
        "remembered": {"a|k1": {"i"}, "a|k2": {"i"}},
    }
    text = "\n".join(identity.explain_lines(identity.Plan(), state, after))
    assert "series before (1): active 1" in text and "series after (2)" in text
    assert "extra series 1: kind = inactive (stopped itself)" in text
    assert "decision actions attached: confirm x1" in text
    assert "one habit split in two" in text


# A habit joined by a continue link I made (a subscription under a new merchant name)

FIT_OLD, FIT_NEW = "merchant|out|Fitplace", "merchant|out|Fitplace Online"


def build_link_db(path: Path, old_n: int, new_n: int, with_log: bool = True) -> Path:
    """A monthly charge of $25: `old_n` payments under one merchant name, then `new_n` under
    another. The old log: confirms on both, and 'the new one continues the old one'."""
    conn = connect(path)
    for i in range(old_n + new_n):
        year, month = 2025 + i // 12, i % 12 + 1
        day = shift_to_business_day(monthly_date(year, month, 8), "later")
        name = "Fitplace " if i < old_n else "Fitplace Online "
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, -2500, 'bill payment', ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), name, f"fp{i:04d}"),
        )  # fmt: skip
    conn.commit()
    if with_log:
        snap = {
            "kind": "merchant", "direction": "out", "cadence": "monthly", "anchor": "2025-01-08",
            "anchor_day": 8, "amount_type": "fixed", "expected_cents": -2500,
            "range_cents": [-2500, -2500], "name": "Fitplace",
        }  # fmt: skip
        rows = [(FIT_OLD, "confirm", snap, "2026-03-01"), (FIT_NEW, "confirm", snap, "2026-03-02"),
                (FIT_NEW, "continue", {"previous": FIT_OLD}, "2026-03-03")]  # fmt: skip
        conn.executemany(
            "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(ACCOUNT, k, a, json.dumps(d), day) for k, a, d, day in rows],
        )
        conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize(("old_n", "new_n"), [(8, 4), (4, 8), (6, 6)])
def test_a_habit_joined_by_my_continue_link_stays_one_series(
    old_n: int, new_n: int, tmp_path: Path
) -> None:
    db = build_link_db(tmp_path / "t.db", old_n, new_n)
    assert len(today(db)) == 1  # the link already joins them
    counts = identity.dry_run(db, None)
    assert counts["collisions"] == 1 and counts["decisions"] == 3
    assert counts["series_before"] == counts["series_after"] == 1
    assert counts["decisions_applying_before"] == counts["decisions_applying_after"] == 3
    identity.apply(db, None)
    (habit,) = today(db).values()
    assert habit.status == "confirmed" and len(habit.evidence) == old_n + new_n
    assert len(habit.continues) == 1


# A leftover of a habit never shows as a separate series, whatever joined it (condition 3);
# the three cases that must stay separate (condition 4)


def clusters(db: Path, merchant: str) -> list[rs.Stored]:
    conn = connect(db)
    try:
        found = [s for s in rs.series_as_of(conn, ACCOUNT)[0] if merchant in s.key]
    finally:
        conn.close()
    return sorted(found, key=lambda s: s.anchor)


def test_a_confirmed_snapshot_folds_into_the_one_series_that_holds_its_payments(
    tmp_path: Path,
) -> None:
    """Two confirmations were made about one habit under two ids: the first on a few of its
    payments, the second on most. The first id is held by nobody, but exactly one series has its
    payments: the decision applies to that series, not to a separate snapshot."""
    db = build_db(tmp_path / "t.db", log=False)
    conn = connect(db)
    (old, new) = clusters(db, "Corner Cafe")
    few = replace(new, id="sid|test|@few", evidence=new.evidence[:3])
    most = replace(new, id="sid|test|@most", evidence=new.evidence[3:])
    rs.confirm(conn, ACCOUNT, few, date(2026, 8, 2))
    rs.set_category(conn, ACCOUNT, few, "Groceries", date(2026, 8, 3))
    rs.confirm(conn, ACCOUNT, most, date(2026, 8, 4))
    conn.close()
    found = clusters(db, "Corner Cafe")
    assert len(found) == 2  # the old and the new cluster; no snapshot beside them
    assert all(s.detected_now for s in found)
    assert found[1].status == "confirmed" and found[1].category == "Groceries"


def test_two_real_habits_at_one_merchant_stay_two_series(tmp_path: Path) -> None:
    db = build_db(tmp_path / "t.db", log=False)
    conn = connect(db)
    for habit in clusters(db, "City Pool"):
        rs.confirm(conn, ACCOUNT, habit, date(2026, 8, 5))
    conn.close()
    pools = clusters(db, "City Pool")
    assert len(pools) == 2 and {s.status for s in pools} == {"confirmed"}
    assert pools[0].id != pools[1].id


def test_two_habits_recorded_under_one_id_are_left_separate_not_merged(tmp_path: Path) -> None:
    """Genuinely ambiguous: one id remembers the payments of two habits paid in the same weeks.
    They overlap in time, so nothing is folded."""
    db = build_db(tmp_path / "t.db", log=False)
    conn = connect(db)
    first, second = clusters(db, "City Pool")
    both = replace(first, evidence=first.evidence + second.evidence)
    rs.confirm(conn, ACCOUNT, both, date(2026, 8, 5))
    conn.close()
    pools = clusters(db, "City Pool")
    assert len(pools) == 2
    sizes = sorted(len(s.evidence) for s in pools)
    assert sizes == sorted([len(first.evidence), len(second.evidence)])


def test_a_series_i_ended_is_not_carried_on_and_a_new_one_starts_a_new_id(tmp_path: Path) -> None:
    db = build_db(tmp_path / "t.db", log=False)
    conn = connect(db)
    old, new = clusters(db, "Corner Cafe")
    rs.end(conn, ACCOUNT, old, date(2026, 5, 25))
    conn.close()
    old_after, new_after = clusters(db, "Corner Cafe")
    assert not old_after.active and old_after.id == old.id
    assert new_after.status == "detected" and new_after.active  # not ended with it
    assert new_after.id != old.id and len(new_after.evidence) == len(new.evidence)


def test_a_rejected_series_is_not_carried_on_either(tmp_path: Path) -> None:
    db = build_db(tmp_path / "t.db", log=False)
    conn = connect(db)
    old, new = clusters(db, "Corner Cafe")
    rs.reject(conn, ACCOUNT, old, date(2026, 5, 25))
    conn.close()
    old_after, new_after = clusters(db, "Corner Cafe")
    assert old_after.status == "rejected" and new_after.status == "detected"
