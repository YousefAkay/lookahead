"""Tests for "what changed since last time" (week 5, item 4). Made-up rows only.

The marker is the data date I last acknowledged; it moves only on "Got it". Everything shown is
counted here: new rows, the balance change, new merchants, category changes, alerts opened since."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from lookahead import since
from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.recurring_alerts import record_look
from lookahead.recurring_store import confirm, refresh

ACCOUNT = "test"


def insert(conn: sqlite3.Connection, day: date, cents: int, balance: int, text: str,
           kind: str = "pos purchase") -> None:  # fmt: skip
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, balance, f"fp{n:04d}"),
        )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    insert(c, date(2026, 6, 1), -1000, 50_000, "Opos Shop A")
    insert(c, date(2026, 6, 30), -2000, 48_000, "Opos Shop B")
    return c


# --- The marker ---


def test_a_database_with_no_marker_has_nothing_to_compare(conn: sqlite3.Connection) -> None:
    assert since.marker(conn, ACCOUNT) is None
    assert since.summarize(conn, ACCOUNT, pd.DataFrame()) is None


def test_got_it_sets_the_marker_to_the_data_date(conn: sqlite3.Connection) -> None:
    m = since.acknowledge(conn, ACCOUNT, when=date(2026, 7, 2))
    assert m == since.Marker(data_date=date(2026, 6, 30), set_on=date(2026, 7, 2))
    assert since.marker(conn, ACCOUNT) == m


def test_got_it_on_an_account_with_no_rows_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="no transactions"):
        since.acknowledge(conn, "empty")


def test_new_data_and_a_recurring_look_never_move_the_marker(conn: sqlite3.Connection) -> None:
    since.acknowledge(conn, ACCOUNT, when=date(2026, 7, 1))
    insert(conn, date(2026, 7, 15), -500, 47_500, "Opos Shop C")
    record_look(conn, ACCOUNT, refresh(conn, ACCOUNT))
    assert since.marker(conn, ACCOUNT).data_date == date(2026, 6, 30)  # type: ignore[union-attr]
    since.acknowledge(conn, ACCOUNT, when=date(2026, 7, 16))
    assert since.marker(conn, ACCOUNT).data_date == date(2026, 7, 15)  # type: ignore[union-attr]


def test_the_marker_is_per_account(conn: sqlite3.Connection) -> None:
    since.acknowledge(conn, ACCOUNT)
    assert since.marker(conn, "other") is None


# --- The balance ---


def test_the_balance_on_a_day_is_the_last_posting_of_that_day(conn: sqlite3.Connection) -> None:
    # Within a day rows are stored newest first (the export's order), as views.transactions reads.
    insert(conn, date(2026, 7, 3), -300, 47_400, "Opos Late")
    insert(conn, date(2026, 7, 3), -300, 47_700, "Opos Early")
    assert since.balance_at(conn, ACCOUNT, date(2026, 7, 3)) == 47_400
    assert since.balance_at(conn, ACCOUNT, date(2026, 7, 10)) == 47_400  # no rows: carried on
    assert since.balance_at(conn, ACCOUNT, date(2026, 5, 1)) is None  # before the data


# --- New merchants (from the rows the transactions view gives) ---

COLUMNS = ["date", "type", "text", "amount_cents", "balance_cents", "source_file", "merchant",
           "flow_type", "category", "source", "flagged", "fingerprint"]  # fmt: skip


def frame(rows: list[tuple]) -> pd.DataFrame:  # type: ignore[type-arg]
    """(date, cents, merchant, category, flow_type, source, flagged), newest first."""
    out = [{"date": pd.Timestamp(d), "type": "pos purchase", "text": m or "", "amount_cents": c,
            "balance_cents": 0, "source_file": "f", "merchant": m, "flow_type": flow,
            "category": cat, "source": src, "flagged": flag, "fingerprint": f"fp{i}"}
           for i, (d, c, m, cat, flow, src, flag) in enumerate(rows)]  # fmt: skip
    df = pd.DataFrame(out, columns=COLUMNS)
    return df.sort_values("date", ascending=False, kind="stable").reset_index(drop=True)


MARK = date(2026, 6, 30)


def test_a_merchant_seen_before_the_marker_is_not_new() -> None:
    df = frame([
        (date(2026, 7, 5), -800, "Cafe Velo", "Restaurants & cafés", "everyday", "model", False),
        (date(2026, 6, 10), -700, "Cafe Velo", "Restaurants & cafés", "everyday", "model", False),
        (date(2026, 7, 8), -900, "Bookshop", "Shopping", "everyday", "model", True),
        (date(2026, 7, 9), -400, "Bookshop", "Shopping", "everyday", "model", False),
    ])  # fmt: skip
    found = since.new_merchants(df, MARK)
    assert [(m.merchant, m.first_date, m.rows, m.flagged) for m in found] == [
        ("Bookshop", date(2026, 7, 8), 2, True)
    ]


def test_a_new_merchant_whose_name_was_hidden_stays_hidden() -> None:
    df = frame([(date(2026, 7, 5), -800, "Jane Doe", None, "transfer", "rule", False)])
    hidden = pd.Series([True])
    (m,) = since.new_merchants(df, MARK, hidden)
    assert m.hidden is True


def test_rows_with_no_merchant_are_not_merchants() -> None:
    df = frame([(date(2026, 7, 5), -800, None, None, "everyday", "none", False)])
    assert since.new_merchants(df, MARK) == []


# --- Category spending: since the marker against the same length just before it ---


def spend(day: date, cents: int, category: str, flow: str = "everyday", src: str = "model"):  # type: ignore[no-untyped-def]
    return (day, cents, "M", category, flow, src, False)


FIRST = date(2026, 1, 1)
UNTIL = date(2026, 7, 30)  # 30 days after the marker; the period before is June 1 to 30


@pytest.mark.parametrize(
    ("before", "after", "counts"),
    [
        (10_000, 13_000, True),  # +$30, 30%
        (20_000, 23_000, False),  # +$30, only 15%
        (5_000, 7_000, False),  # +$20, 40% but under $25
        (0, 3_000, True),  # from nothing to $30
        (10_000, 6_000, True),  # -$40, 40%: spending less counts too
        (10_000, 12_500, True),  # +$25 and 25%: both at the limit or above
    ],
)
def test_a_category_change_needs_25_dollars_and_20_percent(
    before: int, after: int, counts: bool
) -> None:
    rows = []
    if before:
        rows.append(spend(date(2026, 6, 15), -before, "Groceries"))
    if after:
        rows.append(spend(date(2026, 7, 15), -after, "Groceries"))
    changes = since.category_changes(frame(rows), MARK, UNTIL, FIRST)
    assert changes is not None
    assert [c.category for c in changes] == (["Groceries"] if counts else [])
    if counts:
        (c,) = changes
        assert (c.before_cents, c.after_cents, c.change_cents) == (before, after, after - before)


def test_the_two_periods_have_the_same_length_and_meet_at_the_marker() -> None:
    rows = [
        spend(date(2026, 5, 31), -50_000, "Shopping"),  # before the earlier period: ignored
        spend(date(2026, 6, 1), -4_000, "Shopping"),  # first day of the earlier period
        spend(date(2026, 6, 30), -4_000, "Shopping"),  # the marker day: earlier period
        spend(date(2026, 7, 1), -1_000, "Shopping"),  # first day after the marker
        spend(date(2026, 7, 31), -50_000, "Shopping"),  # after the data date: ignored
    ]
    (c,) = since.category_changes(frame(rows), MARK, UNTIL, FIRST)  # type: ignore[misc]
    assert (c.before_cents, c.after_cents) == (8_000, 1_000)


def test_transfers_netted_pairs_and_money_in_are_not_spending() -> None:
    rows = [
        spend(date(2026, 7, 10), -90_000, "Transfers", flow="transfer"),
        spend(date(2026, 7, 11), -90_000, "Shopping", src="netted"),
        spend(date(2026, 7, 12), 90_000, "Income", flow="income"),
    ]
    assert since.category_changes(frame(rows), MARK, UNTIL, FIRST) == []


def test_no_comparison_when_the_earlier_period_starts_before_the_data() -> None:
    rows = [spend(date(2026, 7, 10), -9_000, "Shopping")]
    assert since.category_changes(frame(rows), MARK, UNTIL, date(2026, 6, 15)) is None


def test_nothing_to_compare_when_no_day_has_passed() -> None:
    assert since.category_changes(frame([]), MARK, MARK, FIRST) == []


# --- The summary ---


def bill(conn: sqlite3.Connection, months: range) -> None:
    for m in months:
        day = shift_to_business_day(monthly_date(2026, m, 10), "later")
        insert(conn, day, -6500, 40_000, "Phone Co ", kind="bill payment")


def test_the_summary_counts_what_is_new_since_the_marker(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    bill(c, range(1, 7))
    insert(c, date(2026, 6, 30), -1000, 50_000, "Opos Shop A")
    confirm(c, ACCOUNT, next(s for s in refresh(c, ACCOUNT) if s.name == "Phone Co"),
            date(2026, 1, 1))  # fmt: skip
    series = refresh(c, ACCOUNT)
    record_look(c, ACCOUNT, series)  # the first look: the baseline
    since.acknowledge(c, ACCOUNT, when=date(2026, 7, 1))
    # Two months later: the phone bill stopped, two new purchases.
    insert(c, date(2026, 8, 20), -1500, 48_500, "Opos Shop New")
    insert(c, date(2026, 8, 31), -2500, 46_000, "Opos Shop New")
    series = refresh(c, ACCOUNT)
    record_look(c, ACCOUNT, series)
    s = since.summarize(c, ACCOUNT, pd.DataFrame(columns=COLUMNS))
    assert s is not None
    assert (s.marker.data_date, s.data_date, s.new_rows) == (date(2026, 6, 30),
                                                             date(2026, 8, 31), 2)  # fmt: skip
    assert (s.balance_then, s.balance_now) == (50_000, 46_000)
    # Two due dates passed unpaid, so detection calls the bill ended: "may have stopped".
    assert [e.kind for e in s.recurring] == ["stopped"]
    assert all(e.opened_on > s.marker.data_date for e in s.recurring)
    c.close()


def test_alerts_opened_on_or_before_the_marker_are_not_new(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    bill(c, range(1, 5))
    insert(c, date(2026, 6, 30), -1000, 50_000, "Opos Shop A")
    confirm(c, ACCOUNT, next(s for s in refresh(c, ACCOUNT) if s.name == "Phone Co"),
            date(2026, 1, 1))  # fmt: skip
    record_look(c, ACCOUNT, refresh(c, ACCOUNT))  # the bill is already late on June 30
    since.acknowledge(c, ACCOUNT)
    s = since.summarize(c, ACCOUNT, pd.DataFrame(columns=COLUMNS))
    assert s is not None and s.recurring == [] and s.new_rows == 0
    c.close()


def test_money_is_shown_with_a_sign_and_two_decimals() -> None:
    assert since.money(-123456) == "-$1,234.56"
    assert since.money(2500) == "$25.00"
    assert since.money(2500, sign=True) == "+$25.00"


def test_the_counts_on_the_card() -> None:
    s = since.Summary(
        marker=since.Marker(MARK, MARK), data_date=MARK + timedelta(days=5), new_rows=3,
        balance_then=1, balance_now=2, merchants=[], categories=None, recurring=[], shortfall=[],
    )  # fmt: skip
    assert since.card_counts(s) == [("New transactions", "3"), ("New merchants", "0"),
                                    ("Recurring changes", "0"), ("Category changes", "-"),
                                    ("New alerts", "0")]  # fmt: skip
