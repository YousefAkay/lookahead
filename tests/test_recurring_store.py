"""Tests for step 3a-iii: stored recurring series and the dated decision log.
Made-up rows in a temporary database."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect, connect_readonly
from lookahead.recurring_store import (
    Stored,
    add_series,
    confirm,
    current_series,
    edit,
    end,
    load_decisions,
    load_series,
    refresh,
    reject,
)

ACCOUNT = "test"
CARD = "Opos {:<20}toron "


def insert(conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str) -> None:
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:03d}"),
        )


def monthly(conn: sqlite3.Connection, name: str, day: int, months: range, cents: int) -> None:
    for m in months:
        insert(conn, shift_to_business_day(monthly_date(2026, m, day), "later"), cents,
               "bill payment", f"{name} ")  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    monthly(c, "Phone Co", 10, range(1, 7), -6500)
    monthly(c, "Gym Club", 5, range(1, 7), -4000)
    for m in range(1, 7):  # same-amount e-Transfers on the 1st: a candidate
        insert(c, shift_to_business_day(date(2026, m, 1), "later"), -75000, "withdrawal",
               "Free Interac E-Transfer ")  # fmt: skip
    insert(c, date(2026, 6, 20), -2500, "pos purchase", CARD.format("Bookshop"))
    return c


def by_name(series: list[Stored]) -> dict[str | None, Stored]:
    return {s.name: s for s in series}


def test_refresh_stores_detected_series_with_fingerprints_as_evidence(
    conn: sqlite3.Connection,
) -> None:
    series = refresh(conn, ACCOUNT)
    assert {s.status for s in series} == {"detected"} and len(series) == 3
    phone = by_name(series)["Phone Co"]
    assert phone.evidence == tuple(f"fp{n:03d}" for n in range(1, 7))
    assert (phone.cadence, phone.expected_cents, phone.last_paid) == (
        "monthly", -6500, date(2026, 6, 10),
    )  # fmt: skip
    assert load_series(conn, ACCOUNT) == series  # what the pages read


def test_confirm_reject_and_the_latest_one_wins(conn: sqlite3.Connection) -> None:
    series = by_name(refresh(conn, ACCOUNT))
    confirm(conn, ACCOUNT, series["Phone Co"], date(2026, 6, 21))
    reject(conn, ACCOUNT, series["Gym Club"].key, date(2026, 6, 21))
    got = by_name(refresh(conn, ACCOUNT))
    assert (got["Phone Co"].status, got["Gym Club"].status) == ("confirmed", "rejected")
    assert got[None].status == "detected"  # the e-Transfer candidate: untouched
    confirm(conn, ACCOUNT, got["Gym Club"], date(2026, 6, 22))  # changed my mind
    assert by_name(refresh(conn, ACCOUNT))["Gym Club"].status == "confirmed"


def test_an_edit_holds_until_the_next_payment_posts(conn: sqlite3.Connection) -> None:
    phone = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    assert phone.next_expected == date(2026, 7, 10)
    edit(conn, ACCOUNT, phone.key, next_date=date(2026, 7, 15), amount_cents=-7000,
         when=date(2026, 6, 21))  # fmt: skip
    edited = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    assert (edited.next_expected, edited.expected_cents, edited.edited) == (
        date(2026, 7, 15), -7000, True,
    )  # fmt: skip
    insert(conn, date(2026, 7, 10), -6500, "bill payment", "Phone Co ")  # the July payment
    after = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    assert (after.next_expected, after.expected_cents, after.edited) == (
        date(2026, 8, 10), -6500, False,
    )  # fmt: skip
    with pytest.raises(ValueError, match="nothing to edit"):
        edit(conn, ACCOUNT, phone.key)


def test_an_ended_series_stays_ended_and_flags_a_later_payment(conn: sqlite3.Connection) -> None:
    gym = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    end(conn, ACCOUNT, gym.key, date(2026, 6, 21))
    ended = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    assert not ended.active and not ended.paid_after_end
    insert(conn, date(2026, 7, 6), -4000, "bill payment", "Gym Club ")
    after = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    # not switched back on quietly: it stays ended, and the payment is flagged for an alert
    assert not after.active and after.paid_after_end


def test_a_series_added_by_hand(conn: sqlite3.Connection) -> None:
    key = add_series(conn, ACCOUNT, "Family support", "in", "monthly", date(2026, 7, 3), 150000,
                     when=date(2026, 6, 21))  # fmt: skip
    added = by_name(refresh(conn, ACCOUNT))["Family support"]
    assert (added.key, added.status, added.kind) == (key, "user-added", "user")
    assert (added.expected_cents, added.direction, added.evidence) == (150000, "in", ())
    assert added.next_expected == date(2026, 7, 3)
    out = add_series(conn, ACCOUNT, "Tuition", "out", "yearly", date(2026, 9, 1), 250000)
    assert by_name(refresh(conn, ACCOUNT))["Tuition"].expected_cents == -250000 and out != key
    reject(conn, ACCOUNT, key, date(2026, 6, 25))  # removing one I added
    assert by_name(refresh(conn, ACCOUNT))["Family support"].status == "rejected"
    for bad in (("", "in", "monthly"), ("X", "sideways", "monthly"), ("X", "in", "daily")):
        with pytest.raises(ValueError):
            add_series(conn, ACCOUNT, bad[0], bad[1], bad[2], date(2026, 7, 1), 100)


def test_a_confirmed_series_stays_if_detection_loses_it(conn: sqlite3.Connection) -> None:
    gym = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    confirm(conn, ACCOUNT, gym, date(2026, 6, 21))
    with conn:  # most of its history disappears: detection alone no longer finds it
        conn.execute("DELETE FROM transactions WHERE sub_description = 'Gym Club '"
                     " AND posted_date < '2026-05-01'")  # fmt: skip
    kept = by_name(refresh(conn, ACCOUNT))["Gym Club"]
    assert (kept.status, kept.cadence, kept.expected_cents) == ("confirmed", "monthly", -4000)
    assert kept.evidence == () and kept.next_expected >= date(2026, 6, 20)
    assert kept.detected_now is False  # for "expected payment not observed"
    assert by_name(refresh(conn, ACCOUNT))["Phone Co"].detected_now is True


def test_replay_uses_only_decisions_made_by_the_date(conn: sqlite3.Connection) -> None:
    series = by_name(refresh(conn, ACCOUNT))
    confirm(conn, ACCOUNT, series["Phone Co"], date(2026, 6, 25))
    reject(conn, ACCOUNT, series["Gym Club"].key, date(2026, 7, 2))
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions"
    ).fetchall()
    from lookahead.flows import Txn

    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    prints = {r[0]: r[5] for r in raw}

    def at(day: date) -> dict[str | None, str]:
        got = current_series(txns, prints, load_decisions(conn, ACCOUNT, as_of=day), day)
        return {s.name: s.status for s in got}

    assert at(date(2026, 6, 24)) == {"Phone Co": "detected", "Gym Club": "detected",
                                     None: "detected"}  # fmt: skip
    assert at(date(2026, 6, 30))["Phone Co"] == "confirmed"
    assert at(date(2026, 6, 30))["Gym Club"] == "detected"
    assert at(date(2026, 7, 5))["Gym Club"] == "rejected"
    # and only rows posted by the date: in March there are too few payments for any series
    assert at(date(2026, 2, 20)) == {}


def test_an_older_database_has_no_series(tmp_path: Path, conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE recurring_series")
    conn.commit()
    conn.close()
    assert load_series(connect_readonly(tmp_path / "t.db"), ACCOUNT) == []


def test_series_as_of_reads_rows_and_decisions_up_to_the_date(conn: sqlite3.Connection) -> None:
    from lookahead.recurring_store import series_as_of

    series = by_name(refresh(conn, ACCOUNT))
    confirm(conn, ACCOUNT, series["Phone Co"], date(2026, 6, 25))
    add_series(conn, ACCOUNT, "Family support", "in", "monthly", date(2026, 7, 3), 150000,
               when=date(2026, 6, 28))  # fmt: skip
    cache_before = load_series(conn, ACCOUNT)

    early, day = series_as_of(conn, ACCOUNT, date(2026, 4, 20))
    assert day == date(2026, 4, 20)
    got = by_name(early)
    assert got["Phone Co"].status == "detected" and "Family support" not in got
    assert len(got["Phone Co"].evidence) == 4  # January to April only
    assert got["Phone Co"].next_expected == date(2026, 5, 11)  # May 10 is a Sunday

    late, day = series_as_of(conn, ACCOUNT, date(2026, 6, 30))
    assert by_name(late)["Phone Co"].status == "confirmed"
    assert by_name(late)["Family support"].status == "user-added"
    latest, day = series_as_of(conn, ACCOUNT)
    assert day == date(2026, 6, 20)  # no date given: the last posting date
    assert load_series(conn, ACCOUNT) == cache_before  # reading never touches the cache


def test_a_loose_candidate_keeps_its_spread_when_confirmed(tmp_path: Path) -> None:
    c = connect(tmp_path / "rent.db")
    days = (date(2026, 1, 24), date(2026, 3, 6), date(2026, 3, 27), date(2026, 5, 8),
            date(2026, 5, 26), date(2026, 7, 9), date(2026, 8, 5), date(2026, 8, 28))  # fmt: skip
    for day in days:
        insert(c, day, -82000, "withdrawal", "Free Interac E-Transfer ")
    (rent,) = refresh(c, ACCOUNT)
    assert rent.loose and rent.status == "detected" and len(rent.offsets) == 8
    assert load_series(c, ACCOUNT) == [rent]  # the spread survives the cache
    confirm(c, ACCOUNT, rent, date(2026, 9, 5))
    (confirmed,) = refresh(c, ACCOUNT)
    assert confirmed.status == "confirmed" and confirmed.loose
    assert confirmed.offsets == rent.offsets and confirmed.anchor_day == rent.anchor_day
    with c:  # even if detection later loses it, the confirmed series keeps its spread
        c.execute("DELETE FROM transactions WHERE posted_date < '2026-07-01'")
    (kept,) = refresh(c, ACCOUNT)
    assert (kept.status, kept.loose, kept.detected_now) == ("confirmed", True, False)
    assert kept.offsets == rent.offsets


# --- A series category, and one subscription under two merchant names ---


def subscription_db(tmp_path: Path) -> sqlite3.Connection:
    """A store where I shop, which billed a membership under an old name, then a new one."""
    c = connect(tmp_path / "sub.db")
    for m in range(1, 7):  # the old name, until June
        insert(c, shift_to_business_day(monthly_date(2026, m, 8), "later"), -999,
               "pos purchase", CARD.format("Bigstore Member"))  # fmt: skip
    for m in range(7, 12):  # the new name, from July, at a new price
        insert(c, shift_to_business_day(monthly_date(2026, m, 8), "later"), -1099,
               "pos purchase", CARD.format("Bigstore.Ca Club"))  # fmt: skip
    for k, cents in enumerate([-2350, -4710, -1288, -6040, -3115]):  # ordinary shopping
        insert(c, date(2026, 2, 3 + 4 * k), cents, "pos purchase", CARD.format("Bigstore.Ca Club"))
    return c


def test_a_series_category_applies_to_its_payments_only(tmp_path: Path) -> None:
    from lookahead.recurring_store import row_overrides, set_category

    c = subscription_db(tmp_path)
    series = {s.name: s for s in refresh(c, ACCOUNT)}
    club = series["Bigstore.Ca Club"]
    assert len(club.evidence) == 5  # the monthly charges, not the shopping
    confirm(c, ACCOUNT, club, date(2026, 11, 20))
    set_category(c, ACCOUNT, club.key, "Subscriptions", date(2026, 11, 20))
    club = {s.name: s for s in refresh(c, ACCOUNT)}["Bigstore.Ca Club"]
    assert club.category == "Subscriptions" and club.status == "confirmed"
    changes = row_overrides(refresh(c, ACCOUNT))
    assert {f for f, o in changes.items() if o.category == "Subscriptions"} == set(club.evidence)
    assert all(o.flow_type == "recurring_outflow" and o.only_if_flow == "everyday"
               for o in changes.values())  # fmt: skip
    shopping = {f"fp{n:03d}" for n in range(12, 17)}
    assert not shopping & set(changes)  # my other purchases there: untouched
    set_category(c, ACCOUNT, club.key, None, date(2026, 11, 25))  # back to each row's own
    assert {s.name: s for s in refresh(c, ACCOUNT)}["Bigstore.Ca Club"].category is None
    with pytest.raises(ValueError, match="unknown category"):
        set_category(c, ACCOUNT, club.key, "Fun")


def test_linking_a_continuation_shows_one_series_with_one_history(tmp_path: Path) -> None:
    from lookahead.recurring_store import link_continuation, row_overrides, set_category

    c = subscription_db(tmp_path)
    before = {s.name: s for s in refresh(c, ACCOUNT)}
    old, new = before["Bigstore Member"], before["Bigstore.Ca Club"]
    assert not old.active and new.active and len(before) == 2
    confirm(c, ACCOUNT, old, date(2026, 11, 20))
    set_category(c, ACCOUNT, old.key, "Subscriptions", date(2026, 11, 20))
    link_continuation(c, ACCOUNT, new.key, old.key, date(2026, 11, 21))
    (one,) = refresh(c, ACCOUNT)
    assert one.key == new.key and one.name == "Bigstore.Ca Club" and one.active
    assert one.evidence == old.evidence + new.evidence and len(one.evidence) == 11
    assert one.continues == (old.key,) and len(one.offsets) == 11
    assert (one.expected_cents, one.previous_cents) == (-1099, -999)  # the price change
    assert one.changed_fingerprint == new.evidence[0]
    assert one.category == "Subscriptions" and one.status == "confirmed"  # carried over
    assert load_series(c, ACCOUNT) == [one]
    changes = row_overrides([one])
    assert set(changes) == set(one.evidence)  # only the 11 membership payments
    assert not {f"fp{n:03d}" for n in range(12, 17)} & set(changes)
    # no merchant was merged: the two names are still two merchant groups
    from lookahead.flows import Txn, merchant_groups

    raw = c.execute("SELECT id, posted_date, amount_cents, description, sub_description"
                    " FROM transactions").fetchall()  # fmt: skip
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    assert len({g for g in merchant_groups(txns).values() if g}) == 2
    with pytest.raises(ValueError, match="itself"):
        link_continuation(c, ACCOUNT, new.key, new.key)


def test_a_link_is_replayed_only_from_its_date(tmp_path: Path) -> None:
    from lookahead.recurring_store import link_continuation, series_as_of

    c = subscription_db(tmp_path)
    before = {s.name: s for s in refresh(c, ACCOUNT)}
    link_continuation(c, ACCOUNT, before["Bigstore.Ca Club"].key, before["Bigstore Member"].key,
                      date(2026, 11, 21))  # fmt: skip
    earlier, _ = series_as_of(c, ACCOUNT, date(2026, 11, 15))
    assert len(earlier) == 2  # the link did not exist yet
    later, _ = series_as_of(c, ACCOUNT, date(2026, 11, 25))
    assert len(later) == 1


def test_an_older_decision_table_is_migrated_keeping_every_decision(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE recurring_decisions (decision_id INTEGER PRIMARY KEY, account TEXT NOT"
        " NULL, series_key TEXT NOT NULL, action TEXT NOT NULL CHECK (action IN ('confirm',"
        " 'reject', 'end', 'edit', 'add')), details TEXT NOT NULL, decided_at TEXT NOT NULL);"
        "INSERT INTO recurring_decisions VALUES (7, 'test', 'k1', 'confirm', '{}', '2026-10-02');"
        "INSERT INTO recurring_decisions VALUES (9, 'test', 'k2', 'reject', '{}', '2026-10-02');"
    )
    old.close()
    c = connect(path)  # migrates
    rows = c.execute("SELECT decision_id, series_key, action FROM recurring_decisions").fetchall()
    assert [tuple(r) for r in rows] == [(7, "k1", "confirm"), (9, "k2", "reject")]
    from lookahead.recurring_store import decide

    decide(c, "test", "k1", "category", {"category": "Subscriptions"})  # now allowed
    decide(c, "test", "k1", "continue", {"previous": "k2"})
    c.close()
    assert connect(path).execute("SELECT COUNT(*) FROM recurring_decisions").fetchone()[0] == 4


# --- Rent: allowed amounts and a window of days ---


def rent_db(tmp_path: Path) -> tuple[sqlite3.Connection, str, dict[str, str]]:
    """Rent by e-Transfer in one of two amounts, between the 30th and the 7th, plus rows that
    must not count. Returns the database, the hand-added series' key and fingerprints by name."""
    c = connect(tmp_path / "rent.db")
    fingerprints: dict[str, str] = {}

    def put(name: str, day: date, cents: int, kind: str = "withdrawal",
            text: str = "Free Interac E-Transfer ") -> None:  # fmt: skip
        insert(c, day, cents, kind, text)
        fingerprints[name] = c.execute(
            "SELECT fingerprint FROM transactions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

    put("jan", date(2026, 1, 31), -82000)
    put("mar", date(2026, 3, 1), -79500)  # the window that starts on Feb 28
    put("apr", date(2026, 3, 31), -82000)
    put("jun", date(2026, 5, 2), -82000)
    put("jul", date(2026, 6, 30), -79500)
    put("aug", date(2026, 8, 1), -82000)
    put("late", date(2026, 7, 15), -82000)  # right amount, mid-month: rent paid late
    put("other_amount", date(2026, 4, 1), -50000)  # inside the window, not an allowed amount
    put("not_etransfer", date(2026, 2, 2), -82000, "bill payment", "Landlord Inc ")
    key = add_series(c, ACCOUNT, "Rent", "out", "monthly", date(2026, 8, 1), 82000,
                     date(2026, 1, 1))  # fmt: skip
    return c, key, fingerprints


def test_a_rent_rule_uses_the_larger_amount_and_finds_both_amounts(tmp_path: Path) -> None:
    from lookahead.recurring_store import set_rent_rule
    from lookahead.rent import RentRule

    c, key, fp = rent_db(tmp_path)
    set_rent_rule(c, ACCOUNT, key, RentRule((79500, 82000), 30, 7), date(2026, 9, 5))
    series = {s.key: s for s in refresh(c, ACCOUNT)}
    rent = series[key]
    assert rent.expected_cents == -82000  # the larger one, always
    assert rent.allowed_cents == (79500, 82000) and rent.window == (30, 7)
    # any day counts, so the mid-month payment is rent too; the decoys are not
    assert set(rent.evidence) == {fp[n] for n in ("jan", "mar", "apr", "jun", "jul", "late", "aug")}
    assert rent.last_paid == date(2026, 8, 1)
    assert rent.next_expected == date(2026, 8, 30)  # the earliest day of the next window


def test_a_rent_rule_survives_the_cache(tmp_path: Path) -> None:
    from lookahead.recurring_store import set_rent_rule
    from lookahead.rent import RentRule

    c, key, _ = rent_db(tmp_path)
    set_rent_rule(c, ACCOUNT, key, RentRule((79500, 82000), 30, 7), date(2026, 9, 5))
    refreshed = {s.key: s for s in refresh(c, ACCOUNT)}[key]
    stored = {s.key: s for s in load_series(c, ACCOUNT)}[key]
    assert stored == refreshed and isinstance(stored.allowed_cents, tuple)
    assert isinstance(stored.window, tuple)


def test_a_rent_rule_applies_only_from_the_day_it_was_decided(tmp_path: Path) -> None:
    from lookahead.recurring_store import series_as_of, set_rent_rule
    from lookahead.rent import RentRule

    c, key, _ = rent_db(tmp_path)
    set_rent_rule(c, ACCOUNT, key, RentRule((79500, 82000), 30, 7), date(2026, 9, 5))
    before, _ = series_as_of(c, ACCOUNT, date(2026, 9, 4))
    after, _ = series_as_of(c, ACCOUNT, date(2026, 9, 6))
    assert {s.key: s for s in before}[key].allowed_cents == ()
    assert {s.key: s for s in after}[key].allowed_cents == (79500, 82000)


def test_rent_rows_posted_after_the_forecast_date_are_not_evidence(tmp_path: Path) -> None:
    from lookahead.recurring_store import series_as_of, set_rent_rule
    from lookahead.rent import RentRule

    c, key, fp = rent_db(tmp_path)
    set_rent_rule(c, ACCOUNT, key, RentRule((79500, 82000), 30, 7), date(2026, 1, 1))
    early, _ = series_as_of(c, ACCOUNT, date(2026, 4, 15))
    rent = {s.key: s for s in early}[key]
    assert set(rent.evidence) == {fp[n] for n in ("jan", "mar", "apr")}
    assert rent.last_paid == date(2026, 3, 31)


def test_the_latest_rent_rule_wins_and_it_can_be_removed(tmp_path: Path) -> None:
    from lookahead.recurring_store import set_rent_rule
    from lookahead.rent import RentRule

    c, key, _ = rent_db(tmp_path)
    set_rent_rule(c, ACCOUNT, key, RentRule((79500, 82000), 30, 7), date(2026, 9, 5))
    set_rent_rule(c, ACCOUNT, key, RentRule((84000,), 3, 8), date(2026, 9, 6))
    rent = {s.key: s for s in refresh(c, ACCOUNT)}[key]
    assert (rent.allowed_cents, rent.window, rent.expected_cents) == ((84000,), (3, 8), -84000)
    set_rent_rule(c, ACCOUNT, key, None, date(2026, 9, 7))
    plain = {s.key: s for s in refresh(c, ACCOUNT)}[key]
    assert (plain.allowed_cents, plain.window) == ((), None)


def test_my_edited_date_is_kept_but_the_amount_stays_the_larger(tmp_path: Path) -> None:
    from lookahead.recurring_store import set_rent_rule
    from lookahead.rent import RentRule

    c, key, _ = rent_db(tmp_path)
    set_rent_rule(c, ACCOUNT, key, RentRule((79500, 82000), 30, 7), date(2026, 9, 5))
    edit(c, ACCOUNT, key, next_date=date(2026, 9, 3), amount_cents=-60000, when=date(2026, 9, 6))
    rent = {s.key: s for s in refresh(c, ACCOUNT)}[key]
    assert rent.next_expected == date(2026, 9, 3) and rent.expected_cents == -82000


def test_the_decision_table_accepts_rent_after_migrating_an_old_one(tmp_path: Path) -> None:
    from lookahead.recurring_store import decide

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE recurring_decisions (decision_id INTEGER PRIMARY KEY, account TEXT NOT"
        " NULL, series_key TEXT NOT NULL, action TEXT NOT NULL CHECK (action IN ('confirm',"
        " 'reject', 'end', 'edit', 'add', 'category', 'continue')), details TEXT NOT NULL,"
        " decided_at TEXT NOT NULL);"
        "INSERT INTO recurring_decisions VALUES (3, 'test', 'k1', 'continue', '{}', '2026-10-02');"
    )
    old.close()
    c = connect(path)  # migrates a table that has category and continue but not rent or rename
    decide(c, "test", "k1", "rent", {"amounts_cents": [1], "from_day": 30, "to_day": 7})
    decide(c, "test", "k1", "rename", {"name": "Mobile"})
    ids = [r[0] for r in c.execute("SELECT decision_id FROM recurring_decisions ORDER BY 1")]
    assert ids[0] == 3 and len(ids) == 3


def test_the_window_does_not_decide_which_payments_are_rent(tmp_path: Path) -> None:
    from lookahead.recurring_store import set_rent_rule
    from lookahead.rent import RentRule

    c, key, fp = rent_db(tmp_path)
    narrow = RentRule((79500, 82000), 1, 2)  # a window none of the payments fall in
    set_rent_rule(c, ACCOUNT, key, narrow, date(2026, 9, 5))
    rent = {s.key: s for s in refresh(c, ACCOUNT)}[key]
    assert len(rent.evidence) == 7 and fp["late"] in rent.evidence


# --- A series added by hand finds its own past payments, by name only ---


def corvalink_db(tmp_path: Path) -> tuple[sqlite3.Connection, str, dict[str, str]]:
    c = connect(tmp_path / "hand.db")
    fp: dict[str, str] = {}

    def put(name: str, day: date, cents: int, text: str) -> None:
        insert(c, day, cents, "pos purchase", CARD.format(text))
        fp[name] = c.execute(
            "SELECT fingerprint FROM transactions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

    put("first", date(2026, 8, 12), -2800, "Corvalink")
    put("second", date(2026, 9, 12), -2850, "CORVALINK")  # another amount, another case
    put("refund", date(2026, 9, 20), 2800, "Corvalink")  # money in: not a payment
    put("other_word", date(2026, 9, 3), -900, "Corvalinks Shop")  # a different name
    put("other_shop", date(2026, 9, 5), -2800, "Bookshop")
    put("later", date(2026, 11, 12), -2850, "Corvalink")
    key = add_series(c, ACCOUNT, "Corvalink", "out", "monthly", date(2026, 9, 12), 2800,
                     date(2026, 9, 14))  # fmt: skip
    return c, key, fp


def test_a_hand_added_series_picks_up_matching_past_charges_by_name(tmp_path: Path) -> None:
    c, key, fp = corvalink_db(tmp_path)
    series = {s.key: s for s in refresh(c, ACCOUNT)}
    hand = series[key]
    assert hand.status == "user-added" and hand.expected_cents == -2800  # amount is mine
    assert hand.evidence == (fp["first"], fp["second"], fp["later"])  # oldest first
    assert hand.last_paid == date(2026, 11, 12)


def test_a_hand_added_series_uses_only_rows_posted_by_the_date(tmp_path: Path) -> None:
    from lookahead.recurring_store import series_as_of

    c, key, fp = corvalink_db(tmp_path)
    early, _ = series_as_of(c, ACCOUNT, date(2026, 10, 1))
    assert {s.key: s for s in early}[key].evidence == (fp["first"], fp["second"])


def test_a_series_added_by_hand_with_no_charges_still_shows_none(tmp_path: Path) -> None:
    c = connect(tmp_path / "none.db")
    insert(c, date(2026, 9, 1), -100, "pos purchase", CARD.format("Bookshop"))
    key = add_series(c, ACCOUNT, "Corvalink", "out", "monthly", date(2026, 9, 12), 2800,
                     date(2026, 9, 14))  # fmt: skip
    assert {s.key: s for s in refresh(c, ACCOUNT)}[key].evidence == ()


# --- A name of my own for a series (the label only) ---


def test_a_series_can_be_given_a_name_of_my_own(tmp_path: Path) -> None:
    from lookahead.recurring_store import set_name

    c, key, fp = corvalink_db(tmp_path)
    set_name(c, ACCOUNT, key, "AI subscription", date(2026, 9, 15))
    hand = {s.key: s for s in refresh(c, ACCOUNT)}[key]
    assert hand.name == "AI subscription" and hand.named_by_me
    assert hand.evidence == (fp["first"], fp["second"], fp["later"])  # matching is unchanged


def test_a_detected_series_can_be_renamed_and_the_name_taken_back(conn: sqlite3.Connection) -> None:
    from lookahead.recurring_store import set_name

    phone = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    set_name(conn, ACCOUNT, phone.key, "Mobile", date(2026, 9, 1))
    renamed = {s.key: s for s in refresh(conn, ACCOUNT)}[phone.key]
    assert (renamed.name, renamed.named_by_me) == ("Mobile", True)
    assert renamed.evidence == phone.evidence and renamed.expected_cents == phone.expected_cents
    set_name(conn, ACCOUNT, phone.key, None, date(2026, 9, 2))  # back to the detected name
    back = {s.key: s for s in refresh(conn, ACCOUNT)}[phone.key]
    assert (back.name, back.named_by_me) == ("Phone Co", False)


def test_a_rename_applies_only_from_the_day_it_was_made(conn: sqlite3.Connection) -> None:
    from lookahead.recurring_store import series_as_of, set_name

    phone = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    set_name(conn, ACCOUNT, phone.key, "Mobile", date(2026, 9, 1))
    before, _ = series_as_of(conn, ACCOUNT, date(2026, 8, 31))
    after, _ = series_as_of(conn, ACCOUNT, date(2026, 9, 2))
    assert {s.key: s for s in before}[phone.key].name == "Phone Co"
    assert {s.key: s for s in after}[phone.key].name == "Mobile"


# --- The saved series refresh themselves when the logic or the data has changed ---


def test_a_saved_copy_from_an_older_logic_version_is_refreshed(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lookahead import recurring_store
    from lookahead.recurring_store import cache_current, ensure_current

    assert not cache_current(conn, ACCOUNT)  # nothing saved yet
    first = ensure_current(conn, ACCOUNT)
    assert len(first) == 3 and cache_current(conn, ACCOUNT)
    assert ensure_current(conn, ACCOUNT) == first  # current: read as saved
    monkeypatch.setattr(recurring_store, "LOGIC_VERSION", recurring_store.LOGIC_VERSION + 1)
    assert not cache_current(conn, ACCOUNT)  # the code changed what a series holds
    ensure_current(conn, ACCOUNT)
    assert cache_current(conn, ACCOUNT)


def test_a_saved_copy_is_refreshed_after_newer_rows_are_imported(conn: sqlite3.Connection) -> None:
    from lookahead.recurring_store import cache_current, ensure_current

    ensure_current(conn, ACCOUNT)
    monthly(conn, "Phone Co", 10, range(7, 8), -6500)  # July: a newer payment
    assert not cache_current(conn, ACCOUNT)
    phone = by_name(ensure_current(conn, ACCOUNT))["Phone Co"]
    assert len(phone.evidence) == 7 and cache_current(conn, ACCOUNT)


def test_an_account_with_no_rows_is_not_refreshed_over_and_over(tmp_path: Path) -> None:
    from lookahead.recurring_store import cache_current, ensure_current

    c = connect(tmp_path / "empty.db")
    assert ensure_current(c, ACCOUNT) == [] and cache_current(c, ACCOUNT)


def test_a_database_without_the_info_table_counts_as_not_current(tmp_path: Path) -> None:
    from lookahead.recurring_store import cache_current

    old = sqlite3.connect(tmp_path / "old.db")  # no tables at all
    assert not cache_current(old, ACCOUNT)


# --- A series with a rent rule is called "Rent" unless I named it ---


def test_a_series_with_a_rent_rule_is_called_rent_unless_i_named_it(
    conn: sqlite3.Connection,
) -> None:
    from lookahead.recurring_store import set_name, set_rent_rule
    from lookahead.rent import RentRule

    phone = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    rule = RentRule((6500,), 8, 12)
    set_rent_rule(conn, ACCOUNT, phone.key, rule, date(2026, 9, 1))
    rent = {s.key: s for s in refresh(conn, ACCOUNT)}[phone.key]
    assert (rent.name, rent.named_by_me) == ("Rent", True)  # safe to show: my word, not text

    set_name(conn, ACCOUNT, phone.key, "My flat", date(2026, 9, 2))  # a name I set wins
    mine = {s.key: s for s in refresh(conn, ACCOUNT)}[phone.key]
    assert mine.name == "My flat"

    set_name(conn, ACCOUNT, phone.key, None, date(2026, 9, 3))  # taken back: the default again
    again = {s.key: s for s in refresh(conn, ACCOUNT)}[phone.key]
    assert again.name == "Rent"

    set_rent_rule(conn, ACCOUNT, phone.key, None, date(2026, 9, 4))  # no rule: detected name
    plain = {s.key: s for s in refresh(conn, ACCOUNT)}[phone.key]
    assert (plain.name, plain.named_by_me) == ("Phone Co", False)


def test_the_rent_name_is_in_the_saved_series_too(conn: sqlite3.Connection) -> None:
    from lookahead.recurring_store import set_rent_rule
    from lookahead.rent import RentRule

    phone = by_name(refresh(conn, ACCOUNT))["Phone Co"]
    set_rent_rule(conn, ACCOUNT, phone.key, RentRule((6500,), 8, 12), date(2026, 9, 1))
    refresh(conn, ACCOUNT)
    assert {s.key: s for s in load_series(conn, ACCOUNT)}[phone.key].name == "Rent"


# --- Confirm every detected series in one go ("Looks right, confirm all") ---


def test_confirm_all_confirms_each_detected_series_and_logs_it_like_a_single_confirm(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    import shutil

    from lookahead.recurring_store import confirm_all

    path = conn.execute("PRAGMA database_list").fetchone()[2]
    twin_path = tmp_path / "twin.db"
    shutil.copy(path, twin_path)
    twin = connect(twin_path)

    series = refresh(conn, ACCOUNT)
    waiting = [s for s in series if s.status == "detected" and s.active]
    assert len(waiting) >= 3
    assert confirm_all(conn, ACCOUNT, series, date(2026, 9, 5)) == len(waiting)
    for s in waiting:  # the same decisions, one at a time, in the twin
        confirm(twin, ACCOUNT, s, date(2026, 9, 5))

    def log(c: sqlite3.Connection) -> list[tuple[str, str, str, date]]:
        return [(d.key, d.action, repr(sorted(d.details.items())), d.decided_at)
                for d in load_decisions(c, ACCOUNT)]  # fmt: skip

    assert log(conn) == log(twin) and len(log(conn)) == len(waiting)
    assert {s.status for s in refresh(conn, ACCOUNT)} == {"confirmed"}


def test_confirm_all_leaves_rejected_ended_and_confirmed_series_alone(
    conn: sqlite3.Connection,
) -> None:
    from lookahead.recurring_store import confirm_all

    series = {s.name: s for s in refresh(conn, ACCOUNT)}
    reject(conn, ACCOUNT, series["Gym Club"].key, date(2026, 9, 1))
    end(conn, ACCOUNT, series["Phone Co"].key, date(2026, 9, 1))
    for_all = refresh(conn, ACCOUNT)
    before = len(load_decisions(conn, ACCOUNT))
    done = confirm_all(conn, ACCOUNT, for_all, date(2026, 9, 2))
    decisions = load_decisions(conn, ACCOUNT)[before:]
    confirmed_keys = {d.key for d in decisions}
    assert done == len(decisions)
    assert series["Gym Club"].key not in confirmed_keys  # rejected: left as rejected
    assert series["Phone Co"].key not in confirmed_keys  # ended: not a payment to look over
    assert confirm_all(conn, ACCOUNT, refresh(conn, ACCOUNT), date(2026, 9, 3)) == 0  # nothing left
