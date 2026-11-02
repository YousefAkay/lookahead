"""What changed since last time (week 5, item 4): what is new in the data since the date I last
said "Got it".

The marker is per account: the data date I last acknowledged. It moves only when I click "Got it"
(`acknowledge`); opening the page, importing and recording a recurring-alerts look never move it.
It is separate from the recurring alerts' last look (lookahead/recurring_alerts.py), which moves
whenever the data date does.

Five things are counted here, in code; the page only shows them:
- the balance at the marker and now, and how many rows are new;
- new merchants: first seen after the marker (a merchant seen on or before it is not new);
- recurring changes: the recurring alerts opened after the marker;
- category changes: money out by category since the marker against the same length of time just
  before it; a change counts only if it is at least CHANGE_CENTS and at least CHANGE_SHARE
  (provisional, picked to be noticeable and not measured);
- shortfall alerts opened after the marker.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from lookahead.alerts import Episode
from lookahead.alerts import episodes as shortfall_episodes
from lookahead.recurring_alerts import AlertEpisode
from lookahead.recurring_alerts import episodes as recurring_episodes

CHANGE_CENTS = 2_500  # a category change counts if it is at least $25 ...
CHANGE_SHARE = 0.20  # ... and at least 20 percent of the earlier period
PROVISIONAL = "provisional, not yet validated"


@dataclass(frozen=True)
class Marker:
    data_date: date  # the data date I acknowledged
    set_on: date  # the day I clicked "Got it"


@dataclass(frozen=True)
class NewMerchant:
    merchant: str
    first_date: date
    rows: int
    category: str | None  # of its newest row (an estimate unless I labelled it)
    flagged: bool  # a row since the marker is flagged for checking
    hidden: bool  # pseudonymizing replaced a name in one of its rows: do not show the name


@dataclass(frozen=True)
class CategoryChange:
    category: str
    before_cents: int  # money out in the earlier period (a size, positive)
    after_cents: int  # money out since the marker

    @property
    def change_cents(self) -> int:
        return self.after_cents - self.before_cents


@dataclass(frozen=True)
class Summary:
    marker: Marker
    data_date: date
    new_rows: int
    balance_then: int | None
    balance_now: int | None
    merchants: list[NewMerchant]
    categories: list[CategoryChange] | None  # None: not enough history before the marker
    recurring: list[AlertEpisode]
    shortfall: list[Episode]


# --- The marker ---


def data_date(conn: sqlite3.Connection, account: str) -> date | None:
    row = conn.execute(
        "SELECT MAX(posted_date) FROM transactions WHERE account = ?", (account,)
    ).fetchone()
    return date.fromisoformat(row[0]) if row[0] else None


def first_date(conn: sqlite3.Connection, account: str) -> date | None:
    row = conn.execute(
        "SELECT MIN(posted_date) FROM transactions WHERE account = ?", (account,)
    ).fetchone()
    return date.fromisoformat(row[0]) if row[0] else None


def marker(conn: sqlite3.Connection, account: str) -> Marker | None:
    """The marker, or None. A database opened read-only before this table existed has none."""
    try:
        row = conn.execute(
            "SELECT data_date, set_on FROM since_marker WHERE account = ?", (account,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return Marker(date.fromisoformat(row[0]), date.fromisoformat(row[1])) if row else None


def acknowledge(conn: sqlite3.Connection, account: str, when: date | None = None) -> Marker:
    """ "Got it": the marker moves to the account's data date. The only function that moves it."""
    last = data_date(conn, account)
    if last is None:
        raise ValueError("this account has no transactions yet")
    m = Marker(last, when or date.today())
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO since_marker (account, data_date, set_on) VALUES (?, ?, ?)",
            (account, m.data_date.isoformat(), m.set_on.isoformat()),
        )
    return m


# --- What is counted ---


def balance_at(conn: sqlite3.Connection, account: str, day: date) -> int | None:
    """The balance at the end of `day`: after the last posting on or before it. Within a day rows
    are stored newest first (the export's order), as the transactions view reads them."""
    row = conn.execute(
        "SELECT balance_after_cents FROM transactions WHERE account = ? AND posted_date <= ?"
        " ORDER BY posted_date DESC, id ASC LIMIT 1",
        (account, day.isoformat()),
    ).fetchone()
    return int(row[0]) if row else None


def new_merchants(
    rows: pd.DataFrame, since: date, hidden: pd.Series | None = None
) -> list[NewMerchant]:  # fmt: skip
    """Merchants whose first row is after `since`. `rows` come from views.transactions (newest
    first); `hidden` marks the rows whose text pseudonymizing changed (same index)."""
    if rows.empty:
        return []
    known = rows[rows["merchant"].notna()]
    out = []
    for name, group in known.groupby("merchant", sort=False):
        first = group["date"].min().date()
        if first <= since:
            continue
        newest = group.iloc[0]  # rows are newest first
        out.append(NewMerchant(
            merchant=str(name), first_date=first, rows=len(group),
            category=None if pd.isna(newest["category"]) else str(newest["category"]),
            flagged=bool(group["flagged"].any()),
            hidden=bool(hidden is not None and hidden.loc[group.index].any()),
        ))  # fmt: skip
    return sorted(out, key=lambda m: (-m.first_date.toordinal(), m.merchant))


def _spending(rows: pd.DataFrame) -> pd.DataFrame:
    """Money out with a category: transfers and netted pairs are not spending."""
    keep = (
        (rows["amount_cents"] < 0) & rows["category"].notna()
        & (rows["flow_type"] != "transfer") & (rows["source"] != "netted")
    )  # fmt: skip
    return rows[keep]


def counts(change: CategoryChange) -> bool:
    """At least CHANGE_CENTS and at least CHANGE_SHARE of the earlier period (any rise from
    nothing is a share above it)."""
    size = abs(change.change_cents)
    return size >= CHANGE_CENTS and (
        change.before_cents == 0 or size >= CHANGE_SHARE * change.before_cents
    )


def category_changes(
    rows: pd.DataFrame, since: date, until: date, first_day: date | None
) -> list[CategoryChange] | None:  # fmt: skip
    """Money out by category in (since, until] against the same number of days just before it,
    (since - n, since]. None if that earlier period starts before the data does."""
    n = (until - since).days
    if n <= 0:
        return []
    start = since - timedelta(days=n)  # the earlier period is (start, since]
    if first_day is None or first_day > start + timedelta(days=1):
        return None
    if rows.empty:
        return []
    spent = _spending(rows)
    day = spent["date"].dt.date
    out = []
    for category in sorted(set(spent["category"])):
        mine = spent["category"] == category
        before = -int(spent.loc[mine & (day > start) & (day <= since), "amount_cents"].sum())
        after = -int(spent.loc[mine & (day > since) & (day <= until), "amount_cents"].sum())
        change = CategoryChange(str(category), before, after)
        if counts(change):
            out.append(change)
    return sorted(out, key=lambda c: (-abs(c.change_cents), c.category))


def summarize(
    conn: sqlite3.Connection, account: str, rows: pd.DataFrame, hidden: pd.Series | None = None
) -> Summary | None:  # fmt: skip
    """Everything since the marker, or None when there is no marker yet (nothing to compare)."""
    m = marker(conn, account)
    last = data_date(conn, account)
    if m is None or last is None:
        return None
    new_rows = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE account = ? AND posted_date > ?",
        (account, m.data_date.isoformat()),
    ).fetchone()[0]
    return Summary(
        marker=m, data_date=last, new_rows=int(new_rows),
        balance_then=balance_at(conn, account, m.data_date),
        balance_now=balance_at(conn, account, last),
        merchants=new_merchants(rows, m.data_date, hidden),
        categories=category_changes(rows, m.data_date, last, first_date(conn, account)),
        recurring=[e for e in recurring_episodes(conn, account) if e.opened_on > m.data_date],
        shortfall=[e for e in shortfall_episodes(conn, account) if e.opened_on > m.data_date],
    )  # fmt: skip


# --- For the page (no Streamlit here) ---


def card_counts(s: Summary) -> list[tuple[str, str]]:
    """The counts on the Home card."""
    return [
        ("New transactions", f"{s.new_rows:,}"),
        ("New merchants", f"{len(s.merchants):,}"),
        ("Recurring changes", f"{len(s.recurring):,}"),
        ("Category changes", "-" if s.categories is None else f"{len(s.categories):,}"),
        ("New alerts", f"{len(s.recurring) + len(s.shortfall):,}"),
    ]
