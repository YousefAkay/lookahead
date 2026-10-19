"""What a series key change does to my saved decisions (DECISIONS.md, 2026-10-12). Made-up
purchases only.

A weekly habit among other purchases at one merchant is found as a cluster of purchases of about
one amount, and its key ends in the median of that amount. The median moves as purchases arrive,
so the key moves (the key stays as it is: the forecast draws from it). A decision is stored
against the series' stable id (lookahead.series_identity), which does not move, so it keeps
applying to the habit it was made about and the habit is not counted twice. A decision stored
against an old key that has since moved is not recoverable by replay; the migration handles
those (lookahead.identity)."""

import random
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead import recurring_store as rs
from lookahead.db import connect
from lookahead.flows import Txn
from lookahead.recurring import detect

CARD = "Opos {:<20}toron "
EARLY, LATER = date(2026, 3, 31), date(2026, 5, 5)


def purchases() -> list[Txn]:
    """A weekly purchase of about $12 at one cafe, with other purchases of other amounts there
    so that the cafe as a whole is not one series: only the cluster of about $12 is."""
    rng = random.Random(3)
    rows, row_id = [], 1
    for i in range(30):
        amount = -(1200 + rng.randint(-60, 60))
        rows.append(Txn(row_id, date(2026, 1, 6) + timedelta(days=7 * i), amount, "pos purchase",
                        CARD.format("Corner Cafe")))  # fmt: skip
        row_id += 1
    for i in range(16):
        day = date(2026, 1, 8) + timedelta(days=int(13.1 * i) + 1)
        amount = -rng.choice([350, 475, 520, 830, 910, 2300, 2750, 3100])
        rows.append(Txn(row_id, day, amount, "pos purchase", CARD.format("Corner Cafe")))
        row_id += 1
    return rows


ROWS = purchases()
FINGERPRINT = {t.row_id: f"fp{t.row_id}" for t in ROWS}


def series_on(day: date, decisions: list[rs.Decision]) -> list[rs.Stored]:
    past = [t for t in ROWS if t.posted_date <= day]
    return rs.current_series(past, FINGERPRINT, decisions, day)


def amount_of(key: str) -> str:
    return key.rsplit("|", 1)[-1]


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    c = connect(tmp_path / "d.db")
    yield c
    c.close()


def test_the_key_of_a_card_cluster_moves_with_its_median_amount_but_not_its_id() -> None:
    (early,) = series_on(EARLY, [])
    (later,) = series_on(LATER, [])
    assert early.key != later.key and amount_of(early.key) != amount_of(later.key)
    assert early.key.rsplit("|", 1)[0] == later.key.rsplit("|", 1)[0]  # the same family
    assert early.id == later.id and early.id.startswith("sid|")


def test_a_confirmation_made_before_the_key_moved_gives_one_series(conn) -> None:  # type: ignore[no-untyped-def]
    (early,) = series_on(EARLY, [])
    rs.confirm(conn, "a", early, EARLY)
    later = series_on(LATER, rs.load_decisions(conn, "a", LATER))
    assert [s.status for s in later] == ["confirmed"]  # one series, still confirmed


@pytest.mark.parametrize("action", ["reject", "end", "rename", "category"])
def test_a_decision_keeps_applying_after_the_key_moved(
    action: str, conn  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    (early,) = series_on(EARLY, [])
    if action == "reject":
        rs.reject(conn, "a", early.id, EARLY)
    elif action == "end":
        rs.end(conn, "a", early.id, EARLY)
    elif action == "rename":
        rs.set_name(conn, "a", early.id, "My cafe", EARLY)
    else:
        rs.set_category(conn, "a", early.id, "Groceries", EARLY)
    (later,) = series_on(LATER, rs.load_decisions(conn, "a", LATER))
    assert (
        later.status == "rejected" if action == "reject"
        else not later.active if action == "end"
        else later.name == "My cafe" if action == "rename"
        else later.category == "Groceries"
    )  # fmt: skip


def old_key_decision(conn, key: str, action: str, day: date) -> None:  # type: ignore[no-untyped-def]
    """A row as the log looked before ids existed: written straight, since nothing may write it."""
    with conn:
        conn.execute(
            "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
            " VALUES ('a', ?, ?, '{}', ?)", (key, action, day.isoformat()),
        )  # fmt: skip


def test_a_decision_in_an_old_log_still_applies_while_its_key_exists_and_is_lost_after(
    conn,  # type: ignore[no-untyped-def]
) -> None:
    """Reading old keys keeps working; once the key moved only the migration can bring it back."""
    (early,) = series_on(EARLY, [])
    old_key_decision(conn, early.key, "reject", EARLY)
    assert series_on(EARLY, rs.load_decisions(conn, "a", EARLY))[0].status == "rejected"
    (later,) = series_on(LATER, rs.load_decisions(conn, "a", LATER))
    assert later.status == "detected"


def test_detection_alone_is_fine_the_key_is_the_only_thing_that_moves() -> None:
    first, second = detect(ROWS, None, as_of=EARLY), detect(ROWS, None, as_of=LATER)
    assert len(first) == len(second) == 1
    assert first[0].cadence == second[0].cadence == "weekly"


# --- A price change: the new cluster carries on the old one ---


def price_change() -> list[Txn]:
    """A weekly purchase of about $12 for 20 weeks, then about $15.50, among other purchases."""
    rng = random.Random(5)
    rows, row_id = [], 1
    for i in range(40):
        amount = -((1200 if i < 20 else 1550) + rng.randint(-20, 20))
        rows.append(Txn(row_id, date(2026, 1, 6) + timedelta(days=7 * i), amount, "pos purchase",
                        CARD.format("Corner Cafe")))  # fmt: skip
        row_id += 1
    for i in range(18):
        day = date(2026, 1, 8) + timedelta(days=int(15.3 * i) + 1)
        amount = -rng.choice([350, 475, 520, 830, 910, 2300, 2750, 3100])
        rows.append(Txn(row_id, day, amount, "pos purchase", CARD.format("Corner Cafe")))
        row_id += 1
    return rows


PRICE_ROWS = price_change()
PRICE_FP = {t.row_id: f"fp{t.row_id}" for t in PRICE_ROWS}
BEFORE, GAP, AFTER = date(2026, 5, 25), date(2026, 6, 15), date(2026, 8, 3)


def price_on(day: date, decisions: list[rs.Decision]) -> list[rs.Stored]:
    past = [t for t in PRICE_ROWS if t.posted_date <= day]
    return rs.current_series(past, PRICE_FP, decisions, day)


def test_a_confirmed_habit_stays_one_confirmed_series_at_the_new_price(conn) -> None:  # type: ignore[no-untyped-def]
    (old,) = price_on(BEFORE, [])
    rs.confirm(conn, "a", old, BEFORE)
    (now,) = price_on(AFTER, rs.load_decisions(conn, "a", AFTER))
    assert now.status == "confirmed" and now.id == old.id
    assert now.expected_cents == -1550 and now.active  # the new detected amount, not the old
    assert len(now.evidence) == 30 and len(now.continues) == 1  # one history


def test_before_the_new_price_is_detected_the_old_series_is_just_stopped(conn) -> None:  # type: ignore[no-untyped-def]
    (old,) = price_on(BEFORE, [])
    rs.confirm(conn, "a", old, BEFORE)
    (gap,) = price_on(GAP, rs.load_decisions(conn, "a", GAP))
    assert gap.status == "confirmed" and not gap.active and gap.expected_cents == -1196


@pytest.mark.parametrize("action", ["end", "reject"])
def test_a_series_i_ended_or_rejected_is_never_carried_on(
    action: str, conn  # type: ignore[no-untyped-def]
) -> None:  # fmt: skip
    (old,) = price_on(BEFORE, [])
    (rs.end if action == "end" else rs.reject)(conn, "a", old.id, BEFORE)
    first, second = price_on(AFTER, rs.load_decisions(conn, "a", AFTER))
    assert first.id != second.id  # two series, two ids
    assert (first.status == "rejected" or not first.active) and first.expected_cents == -1196
    assert second.status == "detected" and second.active and second.expected_cents == -1550


def test_confirming_again_after_an_end_makes_it_carry_on(conn) -> None:  # type: ignore[no-untyped-def]
    (old,) = price_on(BEFORE, [])
    rs.end(conn, "a", old.id, BEFORE)
    rs.confirm(conn, "a", old, BEFORE + timedelta(days=1))
    (now,) = price_on(AFTER, rs.load_decisions(conn, "a", AFTER))
    assert now.status == "confirmed" and len(now.evidence) == 30


def test_without_decisions_a_price_change_stays_two_series() -> None:
    old, new = price_on(AFTER, [])
    assert old.expected_cents == -1196 and new.expected_cents == -1550 and old.id != new.id


def test_with_no_decisions_the_series_are_exactly_what_detection_found() -> None:
    past = [t for t in ROWS if t.posted_date <= LATER]
    posted = {t.row_id: t.posted_date for t in past}
    found = [rs._from_detected(s, FINGERPRINT, posted) for s in detect(past, None, as_of=LATER)]
    assert [replace(s, id="") for s in series_on(LATER, [])] == found


def test_two_price_changes_make_one_series_with_the_oldest_id(conn) -> None:  # type: ignore[no-untyped-def]
    """A chain of three clusters is folded into one, whatever order the pairs were found in."""
    rng = random.Random(8)
    rows, row_id = [], 1
    for i in range(60):
        base = 1200 if i < 20 else 1550 if i < 40 else 1900
        amount = -(base + rng.randint(-20, 20))
        rows.append(Txn(row_id, date(2026, 1, 6) + timedelta(days=7 * i), amount, "pos purchase",
                        CARD.format("Corner Cafe")))  # fmt: skip
        row_id += 1
    for i in range(26):
        day = date(2026, 1, 8) + timedelta(days=int(15.3 * i) + 1)
        rows.append(Txn(row_id, day, -rng.choice([350, 475, 520, 830, 910, 2300, 2750, 3100]),
                        "pos purchase", CARD.format("Corner Cafe")))  # fmt: skip
        row_id += 1
    fp = {t.row_id: f"fp{t.row_id}" for t in rows}

    def on(day: date, decisions: list[rs.Decision]) -> list[rs.Stored]:
        return rs.current_series([t for t in rows if t.posted_date <= day], fp, decisions, day)

    (first,) = on(BEFORE, [])
    rs.confirm(conn, "a", first, BEFORE)
    last = date(2026, 12, 28)
    (now,) = on(last, rs.load_decisions(conn, "a", last))  # one series, not three
    assert now.status == "confirmed" and now.id == first.id and now.expected_cents < -1800
    assert len(now.evidence) == 51 and len(now.continues) == 2  # one history of three clusters


# --- The payments remembered with an id keep it stable (condition F) ---


def with_members(conn, day: date, rows=ROWS):  # type: ignore[no-untyped-def]
    past = [t for t in rows if t.posted_date <= day]
    fp = {t.row_id: f"fp{t.row_id}" for t in rows}
    decisions = rs.load_decisions(conn, "a", day)
    return rs.current_series(past, fp, decisions, day, members=rs.load_members(conn, "a", day))


def test_older_payments_added_later_do_not_change_the_id(conn) -> None:  # type: ignore[no-untyped-def]
    """I decide when only the later payments are known; an earlier import then adds older ones."""
    recent = [t for t in ROWS if t.posted_date >= date(2026, 2, 17)]
    (known,) = rs.current_series(
        [t for t in recent if t.posted_date <= EARLY], FINGERPRINT, [], EARLY
    )
    rs.reject(conn, "a", known, EARLY)
    (now,) = with_members(conn, EARLY)  # every payment, including the older ones
    assert now.id == known.id and now.status == "rejected"
    assert now.evidence[0] != known.evidence[0]  # the oldest payment is not the one it started with


def test_without_the_remembered_payments_the_older_payments_would_change_the_id() -> None:
    recent = [t for t in ROWS if t.posted_date >= date(2026, 2, 17) and t.posted_date <= EARLY]
    (known,) = rs.current_series(recent, FINGERPRINT, [], EARLY)
    (now,) = series_on(EARLY, [])
    assert now.id != known.id  # this is what the table is for


def test_the_oldest_payment_dropping_out_does_not_change_the_id(conn) -> None:  # type: ignore[no-untyped-def]
    (known,) = series_on(EARLY, [])
    rs.end(conn, "a", known, EARLY)
    later_rows = [t for t in ROWS if t.posted_date >= date(2026, 2, 17)]
    (now,) = with_members(conn, LATER, later_rows)
    assert now.id == known.id and not now.active
    assert now.evidence[0] != known.evidence[0]


def test_two_habits_never_share_an_id_through_the_table(conn) -> None:  # type: ignore[no-untyped-def]
    (first,) = series_on(EARLY, [])
    rs.confirm(conn, "a", first, EARLY)
    members = rs.load_members(conn, "a")
    assert set(members.values()) == {first.id}


# --- One function writes decisions, always by id (condition G) ---


@pytest.mark.parametrize("old_key", ["merchant|out|Corner Cafe|1204", "merchant|out|Corner Cafe",
                                     "own transfer|out|5000", "e-transfer|in|tok"])  # fmt: skip
def test_no_decision_can_be_written_against_an_old_key(old_key: str, conn) -> None:  # type: ignore[no-untyped-def]
    for write in (rs.reject, rs.end, lambda c, a, k: rs.set_name(c, a, k, "x"),
                  lambda c, a, k: rs.set_category(c, a, k, "Groceries"),
                  lambda c, a, k: rs.edit(c, a, k, amount_cents=-100),
                  lambda c, a, k: rs.set_rent_rule(c, a, k, None)):  # fmt: skip
        with pytest.raises(ValueError, match="series id"):
            write(conn, "a", old_key)
    with pytest.raises(ValueError, match="series id"):
        rs.link_continuation(conn, "a", old_key, "sid|x|@fp1")
    assert conn.execute("SELECT COUNT(*) FROM recurring_decisions").fetchone()[0] == 0


def test_every_decision_a_screen_or_function_writes_is_an_id(conn) -> None:  # type: ignore[no-untyped-def]
    (early,) = series_on(EARLY, [])
    rs.confirm(conn, "a", early, EARLY)
    rs.set_name(conn, "a", early, "My cafe", EARLY)
    rs.link_continuation(conn, "a", early, "sid|merchant|out|Old|@fp0", EARLY)
    key = rs.add_series(conn, "a", "Rent", "out", "monthly", EARLY, 100000, EARLY)
    stored = [r[0] for r in conn.execute("SELECT series_key FROM recurring_decisions")]
    assert all(k.startswith(("sid|", "user|")) for k in stored) and key.startswith("user|")


def test_the_only_insert_into_the_decision_log_is_in_decide() -> None:
    """No screen or module writes the log by hand (the migration, once built, is the one
    exception and is listed here by name)."""
    root = Path(__file__).resolve().parent.parent
    allowed = {"lookahead/recurring_store.py", "lookahead/db.py", "lookahead/identity.py"}
    found = {
        str(f.relative_to(root)).replace("\\", "/")
        for folder in ("lookahead", "app", "eval")
        for f in (root / folder).rglob("*.py")
        if "INSERT INTO recurring_decisions" in f.read_text("utf-8")
    }
    assert found <= allowed, found - allowed
    store = (root / "lookahead/recurring_store.py").read_text("utf-8")
    assert store.count("INSERT INTO recurring_decisions") == 1  # decide(), nowhere else
