"""Ask's engine blocks on a tiny made-up account, with every answer worked out by hand (spec
docs/specs/2026-11-20-ask.md, definition of done 1).

The account makes every simulated future the same, so the forecast has one path that can be
worked out with a pencil:
- $3,000.00 at the start; one $10.00 card purchase at Quorvel Market every day from 2026-05-01
  to 2026-09-14 (137 days: May 31 + June 30 + July 31 + August 31 + 14), category Groceries;
- a $100.00 bill payment to Lumen Hydro on the 6th of each month, moved to the next business day
  when the 6th is not one (June 8, September 8), five payments up to the data date;
- nothing else: no income, no deposits, no fees.

Balance at the end of 2026-09-14: 300,000 - 137 x 1,000 - 5 x 10,000 = 113,000 cents.
Today is 2026-09-15, the forecast's first day; its 30 days run to 2026-10-14; the next bill is
on Tuesday 2026-10-06. The buffer is the default $200.00.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.ask.plan import parse
from lookahead.ask.run import Context, Outcome, run
from lookahead.budgets import set_limit
from lookahead.db import connect

ACCOUNT = "hand"
KEY = bytes(range(32))
TODAY = date(2026, 9, 15)
BILLS = [date(2026, 5, 6), date(2026, 6, 8), date(2026, 7, 6), date(2026, 8, 6),
         date(2026, 9, 8)]  # fmt: skip
START = 113_000  # cents, end of 2026-09-14

pytestmark = pytest.mark.slow


def build(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    balance, n = 300_000, 0
    rows: list[tuple[date, int, str, str, str]] = []
    day = date(2026, 5, 1)
    while day <= date(2026, 9, 14):
        if day in BILLS:
            rows.append((day, -10_000, "Bill Payment", "Lumen Hydro", "recurring_outflow"))
        rows.append((day, -1_000, "POS Purchase", "Quorvel Market", "everyday"))
        day += timedelta(days=1)
    with conn:
        for day, cents, kind, text, flow in rows:
            balance += cents
            n += 1
            fp = f"hand-{n:04d}"
            conn.execute(
                "INSERT INTO transactions (account, posted_date, amount_cents, description,"
                " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'hand.csv', '2026-09-15')",
                (ACCOUNT, day.isoformat(), cents, kind, text, balance, fp),
            )
            category = "Groceries" if flow == "everyday" else "Phone, internet & utilities"
            conn.execute(
                "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
                " category, source, detail, flagged, confidence, model_version, predicted_at)"
                " VALUES (?, ?, ?, ?, ?, 'rule', '', 0, NULL, 'test', '2026-09-15')",
                (fp, ACCOUNT, text, flow, category),
            )
    assert balance == START
    return conn


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return build(tmp_path / "hand.db")


def ask(conn: sqlite3.Connection, *steps: dict) -> Outcome:  # type: ignore[type-arg]
    out = run(parse({"steps": list(steps)}), Context(conn, ACCOUNT, TODAY, KEY))
    assert out.kind == "answer", out.result
    return out


def forecast(conn: sqlite3.Connection, **step: object) -> object:
    return ask(conn, {"op": "forecast", "name": "f", **step}).result


def whatif(conn: sqlite3.Connection, *changes: dict) -> object:  # type: ignore[type-arg]
    return ask(conn, {"op": "whatif", "name": "w", "changes": list(changes)}).result


def test_balance_on_a_day(conn: sqlite3.Connection) -> None:
    # 2026-09-25: eleven purchases (the 15th to the 25th), no bill yet
    a = forecast(conn, measure="balance_on", on={"kind": "in_days", "n": 10})
    assert (a.day, a.middle_cents, a.low_cents, a.high_cents) == (
        date(2026, 9, 25), START - 11_000, START - 11_000, START - 11_000)  # fmt: skip
    # 2026-10-06: twenty-two purchases and the October bill
    a = forecast(conn, measure="balance_on", on={"kind": "named_date", "month": 10, "day": 6})
    assert a.middle_cents == START - 22_000 - 10_000


def test_lowest_balance(conn: sqlite3.Connection) -> None:
    # The balance only falls: the lowest is the last day, after 30 purchases and the bill.
    a = forecast(conn, measure="lowest")
    assert (a.middle_cents, a.lowest_day) == (START - 30_000 - 10_000, date(2026, 10, 14))


def test_chance_of_going_below(conn: sqlite3.Connection) -> None:
    assert forecast(conn, measure="chance_below").chance == 0.0  # never near $200
    # below $1,000.00: reached on the 14th purchase day (2026-09-28: 113,000 - 14,000 < 100,000)
    whole = forecast(conn, measure="chance_below", below={"dollars": 1000})
    assert whole.chance == 1.0
    # within the next 10 days (to 2026-09-24) the lowest is 103,000: never below
    ten = forecast(conn, measure="chance_below", below={"dollars": 1000},
                   until={"kind": "in_days", "n": 10})  # fmt: skip
    assert ten.chance == 0.0 and ten.day == date(2026, 9, 24)


def test_safe_to_spend(conn: sqlite3.Connection) -> None:
    # No income, so the window is the 30 days. Spending c a day instead of the usual $10.00:
    # 113,000 - 10,000 (bill) - 30c must stay at or above 20,000, so c <= 2,766.67 -> 2,766.
    a = ask(conn, {"op": "safe_to_spend", "name": "s"}).result
    assert (a.cents_per_day, a.last_day) == (2_766, date(2026, 10, 14))


def test_what_ifs(conn: sqlite3.Connection) -> None:
    low = START - 30_000 - 10_000
    # a $500.00 purchase on 2026-09-20
    a = whatif(conn, {"kind": "one_off", "direction": "out", "amount": {"dollars": 500},
                      "day": {"kind": "date", "date": "2026-09-20"}})  # fmt: skip
    assert (a.low_now_cents, a.low_after_cents) == (low, low - 50_000)
    # no October bill
    a = whatif(conn, {"kind": "cancel", "target": "Lumen Hydro"})
    assert a.low_after_cents == low + 10_000
    # the bill at $60.00 instead
    a = whatif(conn, {"kind": "swap", "target": "Lumen Hydro", "amount": {"dollars": 60}})
    assert a.low_after_cents == low + 4_000
    # half the groceries: $5.00 less on each of the 30 days
    a = whatif(conn, {"kind": "category", "category": "Groceries", "direction": "less",
                      "percent": 50})  # fmt: skip
    assert a.low_after_cents == low + 15_000
    # $152.00 less a month of everyday spending: the usual month is $10.00 x 30.4 = $304.00,
    # so half of each day's spending, $5.00 a day again
    a = whatif(conn, {"kind": "everyday", "direction": "less", "amount": {"dollars": 152}})
    assert a.low_after_cents == low + 15_000
    # $304.00 more a month on groceries, spread over each month's days: September's 30 days get
    # 1,013 cents each (the 16 days from the 15th: 16,208); October's 31 days get 980 with the
    # 20 cents left over one each on the 1st to the 20th (the 14 days: 13,734)
    a = whatif(conn, {"kind": "category", "category": "Groceries", "direction": "more",
                      "amount": {"dollars": 304}})  # fmt: skip
    assert a.low_after_cents == low - 16_208 - 13_734


def test_budget(conn: sqlite3.Connection) -> None:
    # September so far: 14 days x $10.00; the whole month: 30 x $10.00 in every future
    set_limit(conn, ACCOUNT, "Groceries", 25_000)
    (line,) = ask(conn, {"op": "budget", "name": "b"}).result.lines
    assert (line.spent_cents, line.likely_end_cents, line.chance_over) == (14_000, 30_000, 1.0)
    set_limit(conn, ACCOUNT, "Groceries", 40_000)
    (line,) = ask(conn, {"op": "budget", "name": "b"}).result.lines
    assert (line.likely_end_cents, line.chance_over) == (30_000, 0.0)


def test_recurring(conn: sqlite3.Connection) -> None:
    (line,) = ask(conn, {"op": "recurring", "name": "r"}).result.lines
    assert (line.name, line.cadence, line.usual_cents, line.next_day) == (
        "Lumen Hydro", "monthly", 10_000, date(2026, 10, 6))  # fmt: skip
