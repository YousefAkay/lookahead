"""Where a forecast starts: the day after the data ends, from the balance on that day.

The forecast never starts "today". It starts after the last posting date in the data, so the
days between that date and today are forecast too, and the page says when the data was last
updated.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

HORIZON_DAYS = 30


@dataclass(frozen=True)
class ForecastStart:
    account: str
    data_last_updated: date  # the last posting date used
    balance_cents: int  # balance after the last posting of that day
    first_day: date  # the first forecast day: the day after the data ends
    last_day: date  # the last forecast day (inclusive)


def forecast_start(
    conn: sqlite3.Connection, account: str, as_of: date | None = None,
    horizon_days: int = HORIZON_DAYS,
) -> ForecastStart | None:  # fmt: skip
    """The start of a forecast made as of `as_of` (default: now, with the data as it stands, ending
    at the account's last posting date), or None if nothing was posted by then.

    A bank export lists a day's postings newest first, so the first row stored for a day is its
    last posting and carries that day's closing balance (the same rule the balance chart uses).
    """
    row = conn.execute(
        "SELECT posted_date, balance_after_cents FROM transactions WHERE account = ?"
        " AND posted_date <= ? ORDER BY posted_date DESC, id ASC LIMIT 1",
        (account, (as_of or date.max).isoformat()),
    ).fetchone()
    if row is None:
        return None
    # Live (no `as_of`): the data ends at the last posting. A replay as of a date has the whole
    # export through that date, so the quiet days after the last posting are known to be quiet.
    last = date.fromisoformat(row[0]) if as_of is None else as_of
    first = last + timedelta(days=1)
    return ForecastStart(
        account, last, int(row[1]), first, first + timedelta(days=horizon_days - 1)
    )
