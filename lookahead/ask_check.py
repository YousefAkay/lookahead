"""A counts-only check of Ask's offline parser, for my real database (spec
docs/specs/2026-11-20-ask.md, definition of done 12 and "My real-data checks", item 1).

It asks 24 fixed questions (English and French, written here and committed before any run on real
data) as the Ask page does (`ask_offline`, the same "today" and saved budgets; the pseudonym key
only from the environment, never from .env). For each it prints its id, its language, what
happened (ran / refused / asked back / not found / skipped / error) and whether the outcome
matches the same figure worked out another way (yes / no / n/a).
Nothing else: no amounts, dates, merchants, names, categories or row text. One question names the
merchant I use most; its name is used only to ask and is never printed. If that name holds a
word the parser reads as something else (a category or month word), that question can show
"no" for that reason; with no merchant it is "skipped".

"Another way" means independently of the parser, the plan, Ask's period rules and its runner:
- lookups: the rows the Transactions page shows (`lookahead.views.transactions`), filtered by
  dates worked out here and summed, counted or ranked with plain pandas;
- forecast figures: the forecast's 500 futures rebuilt with every daily balance kept, and the
  median, lowest or share below zero taken from those balances here;
- safe to spend: Home's own call (`safe_to_spend`), which builds its own forecast with the
  same code as Ask's block, so this one shows only that the question reaches the right block;
- recurring payments: the series the forecast reads (`series_as_of`), active and not rejected,
  the same filter as Ask's block: again it shows only that the question reaches the right block;
- budgets: each budget's spent so far summed here from the rows of the budget month.
A question whose expected outcome is a refusal (advice, a day past the forecast, no budget set)
matches when Ask refuses it.

The database is opened read-only (SQLite refuses writes), so nothing is saved.

    python -m lookahead.ask_check --db PATH [--account N]
"""

import argparse
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from lookahead import demo_mode
from lookahead.ask.answer import Asked, ask_offline
from lookahead.ask.engine import BudgetAnswer, ForecastAnswer, RecurringAnswer, SafeAnswer
from lookahead.ask.run import NO_CATEGORY, NO_MERCHANT, Context, Number, Table
from lookahead.budgets import load_limits
from lookahead.db import connect_readonly
from lookahead.forecast.path import build_forecast
from lookahead.forecast.simulate import run as simulate
from lookahead.pseudonymize import KEY_NAME
from lookahead.recurring_store import series_as_of
from lookahead.safe_to_spend import safe_to_spend
from lookahead.views import accounts, has_categories, transactions

OUTCOMES = ("ran", "refused", "asked back", "not found", "skipped", "error")
REFUSED = "refused"  # an expectation: Ask should refuse this one
UNKNOWN = "unknown"  # an expectation: cannot tell (no merchant to ask about)
MERCHANT = "{merchant}"  # replaced by the merchant I use most; never printed


@dataclass(frozen=True)
class Question:
    id: str
    lang: str
    text: str
    expect: Callable[["Facts"], Any]  # the expected figure (None: nothing), REFUSED or UNKNOWN
    agrees: Callable[[Any, Any], bool]  # does Ask's result agree with the expected figure?


# --- Facts worked out here, independently of Ask ---


def month_start(d: date) -> date:
    return d.replace(day=1)


def months_back(first: date, n: int) -> date:
    """The first day of the month n months before the month of `first`."""
    index = first.year * 12 + first.month - 1 - n
    return date(index // 12, index % 12 + 1, 1)


class Facts:
    """The account's rows and dates, and figures computed from them without Ask's code."""

    def __init__(self, conn: sqlite3.Connection, account: str, today: date, key: bytes | None):
        self.conn, self.account, self.today, self.key = conn, account, today, key
        lo, hi = conn.execute(
            "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
            (account,),
        ).fetchone()
        self.first, self.last = date.fromisoformat(lo), date.fromisoformat(hi)
        self.rows = transactions(conn, account)
        self.rows["day"] = self.rows["date"].dt.date
        self._paths: list[tuple[int, ...]] | None = None
        self._forecast: Any = None
        self.merchant = self._busiest_merchant()

    # Periods, cut to the data
    def cut(self, start: date, end: date) -> tuple[date, date] | None:
        a, b = max(start, self.first), min(end, self.last)
        return (a, b) if a <= b else None

    def last_month(self) -> tuple[date, date] | None:
        this = month_start(self.today)
        return self.cut(months_back(this, 1), this - timedelta(days=1))

    def last_months(self, n: int) -> tuple[date, date] | None:
        this = month_start(self.today)
        return self.cut(months_back(this, n), this - timedelta(days=1))

    def last_week(self) -> tuple[date, date] | None:
        monday = self.today - timedelta(days=self.today.weekday())
        return self.cut(monday - timedelta(days=7), monday - timedelta(days=1))

    def named_month(self, month: int) -> tuple[date, date] | None:
        year = self.today.year if month <= self.today.month else self.today.year - 1
        nxt = date(year + month // 12, month % 12 + 1, 1)
        return self.cut(date(year, month, 1), nxt - timedelta(days=1))

    # Rows
    def chosen(
        self,
        span: tuple[date, date] | None,
        direction: str = "out",
        category: str | None = None,
        merchant: str | None = None,
    ) -> pd.DataFrame:
        df = self.rows
        if span is None:
            return df.iloc[0:0]
        df = df[(df["day"] >= span[0]) & (df["day"] <= span[1])]
        df = df[df["amount_cents"] < 0] if direction == "out" else df[df["amount_cents"] > 0]
        df = df[df["flow_type"] != "transfer"]
        if category is not None:
            df = df[df["category"] == category]
        if merchant is not None:
            df = df[df["merchant"] == merchant]
        return df

    def total(self, span: tuple[date, date] | None, **kw: Any) -> int:
        return int(self.chosen(span, **kw)["amount_cents"].abs().sum())

    def count(self, span: tuple[date, date] | None, **kw: Any) -> int:
        return len(self.chosen(span, **kw))

    def largest(self, span: tuple[date, date] | None) -> int | None:
        sizes = self.chosen(span)["amount_cents"].abs()
        return int(sizes.max()) if len(sizes) else None

    def per_month(self, span: tuple[date, date] | None, category: str) -> int | None:
        """Average a month: the total over every month the period touches (empty months: $0)."""
        if span is None:
            return None
        months = (span[1].year - span[0].year) * 12 + span[1].month - span[0].month + 1
        return round(self.total(span, category=category) / months)

    def per_week(self, span: tuple[date, date] | None, category: str) -> int | None:
        """Average a week: the total over every Monday-to-Sunday week the period touches."""
        if span is None:
            return None
        first_monday = span[0] - timedelta(days=span[0].weekday())
        weeks = (span[1] - first_monday).days // 7 + 1
        return round(self.total(span, category=category) / weeks)

    def top(self, span: tuple[date, date] | None, by: str, n: int) -> list[tuple[str, int]]:
        df = self.chosen(span)
        missing = NO_CATEGORY if by == "category" else NO_MERCHANT
        labels = df[by].map(lambda v: v if isinstance(v, str) else missing)
        sums = df["amount_cents"].abs().groupby(labels.to_numpy()).sum()
        ranked = sorted(((str(k), int(v)) for k, v in sums.items()), key=lambda p: (-p[1], p[0]))
        return ranked[:n]

    def _busiest_merchant(self) -> str | None:
        """The merchant with the most money-out rows over the last 3 months (4 letters or more)."""
        df = self.chosen(self.last_months(3))
        names = [m for m in df["merchant"] if isinstance(m, str) and sum(map(str.isalpha, m)) >= 4]
        if not names:
            return None
        counts = pd.Series(names).value_counts()
        best = counts[counts == counts.max()].index
        return str(sorted(best)[0])

    # The forecast's futures, every daily balance kept
    def forecast(self) -> Any:
        if self._forecast is None:
            self._forecast = build_forecast(self.conn, self.account, "weekday", None, self.key)
        return self._forecast

    def paths(self) -> list[tuple[int, ...]]:
        if self._paths is None:
            fc = self.forecast()
            self._paths = list(simulate(fc.inputs, keep_paths=True).paths)
        return self._paths

    def window(self) -> tuple[date, date] | None:
        fc = self.forecast()
        if fc is None or fc.inputs is None:
            return None
        return fc.start.first_day, fc.start.last_day


def median(values: list[int]) -> int:
    """The forecast's middle value: the sorted list at round(0.5 * (n - 1)), Python's rounding
    (for 500 futures, index 250, the upper of the two middle values)."""
    ordered = sorted(values)
    return ordered[round(0.5 * (len(ordered) - 1))]


def balance_in(days: int) -> Callable[[Facts], Any]:
    def expect(f: Facts) -> Any:
        window = f.window()
        if window is None:
            return REFUSED
        day = f.today + timedelta(days=days)
        if not window[0] <= day <= window[1]:
            return REFUSED
        k = (day - window[0]).days
        return median([p[k] for p in f.paths()])

    return expect


def lowest(f: Facts) -> Any:
    if f.window() is None:
        return REFUSED
    paths = f.paths()
    return min(median([p[k] for p in paths]) for k in range(len(paths[0])))


def chance_below_zero(f: Facts) -> Any:
    if f.window() is None:
        return REFUSED
    paths = f.paths()
    return sum(min(p) < 0 for p in paths) / len(paths)


def safe(f: Facts) -> Any:
    s = safe_to_spend(f.conn, f.account, "weekday", None, f.key)
    return REFUSED if s is None else s.cents_per_day


def recurring_out(f: Facts) -> Any:
    series, _ = series_as_of(f.conn, f.account, None, f.key)
    return sorted(s.key for s in series if s.active and s.status != "rejected"
                  and s.direction == "out")  # fmt: skip


def budgets(f: Facts) -> Any:
    saved = {c: v for c, v in load_limits(f.conn, f.account).items() if v is not None}
    if not saved:
        return REFUSED
    month = month_start(f.last + timedelta(days=1))  # the forecast's first day's month
    df = f.rows[(f.rows["day"] >= month) & (f.rows["day"] <= f.last)]
    counted = (df["amount_cents"] < 0) | (df["flow_type"] == "refund")
    df = df[counted]
    return {c: int(-df.loc[df["category"] == c, "amount_cents"].sum()) for c in saved}


# --- How Ask's result is compared ---


def number_is(result: Any, expected: Any) -> bool:
    return isinstance(result, Number) and result.value == expected


def table_is(result: Any, expected: Any) -> bool:
    return isinstance(result, Table) and [(k, v) for k, v in result.pairs] == expected


def forecast_middle_is(result: Any, expected: Any) -> bool:
    return isinstance(result, ForecastAnswer) and result.middle_cents == expected


def forecast_chance_is(result: Any, expected: Any) -> bool:
    return isinstance(result, ForecastAnswer) and result.chance == expected


def safe_is(result: Any, expected: Any) -> bool:
    return isinstance(result, SafeAnswer) and result.cents_per_day == expected


def recurring_is(result: Any, expected: Any) -> bool:
    return isinstance(result, RecurringAnswer) and sorted(r.key for r in result.lines) == expected


def budgets_is(result: Any, expected: Any) -> bool:
    return isinstance(result, BudgetAnswer) and {b.category: b.spent_cents
                                                 for b in result.lines} == expected  # fmt: skip


def nothing(result: Any, expected: Any) -> bool:
    return False  # only a refusal is expected


GROCERIES, RESTAURANTS, INCOME = "Groceries", "Restaurants & cafés", "Income"

QUESTIONS: tuple[Question, ...] = (
    Question("Q01", "en", "How much did I spend in total last month?",
             lambda f: f.total(f.last_month()), number_is),
    Question("Q02", "en", "How much did I spend on groceries last month?",
             lambda f: f.total(f.last_month(), category=GROCERIES), number_is),
    Question("Q03", "en", "How much did I spend on restaurants in the last 3 months?",
             lambda f: f.total(f.last_months(3), category=RESTAURANTS), number_is),
    Question("Q04", "en",
             "What was my average monthly spending on groceries over the last 3 months?",
             lambda f: f.per_month(f.last_months(3), GROCERIES), number_is),
    Question("Q05", "en",
             "How much did I spend on average per week on groceries in the last 3 months?",
             lambda f: f.per_week(f.last_months(3), GROCERIES), number_is),
    Question("Q06", "en", "How many times did I buy coffee last month?",
             lambda f: f.count(f.last_month(), category=RESTAURANTS), number_is),
    Question("Q07", "en", "What was my largest purchase last month?",
             lambda f: f.largest(f.last_month()), number_is),
    Question("Q08", "en", "What are my top 3 categories last month?",
             lambda f: f.top(f.last_month(), "category", 3), table_is),
    Question("Q09", "en", "What are my top 5 merchants in the last 3 months?",
             lambda f: f.top(f.last_months(3), "merchant", 5), table_is),
    Question("Q10", "en", "How much income did I get last month?",
             lambda f: f.total(f.last_month(), direction="in", category=INCOME), number_is),
    Question("Q11", "en", "How much did I spend on groceries in September?",
             lambda f: f.total(f.named_month(9), category=GROCERIES), number_is),
    Question("Q12", "en", "How much did I spend last week?",
             lambda f: f.total(f.last_week()), number_is),
    Question("Q13", "en", f"How much did I spend at {MERCHANT} in the last 3 months?",
             lambda f: UNKNOWN if f.merchant is None else f.total(f.last_months(3),
                                                                  merchant=f.merchant),
             number_is),
    Question("Q14", "fr", "Combien ai-je dépensé en épicerie le mois dernier?",
             lambda f: f.total(f.last_month(), category=GROCERIES), number_is),
    Question("Q15", "fr", "Quelles sont mes 3 principales catégories le mois dernier?",
             lambda f: f.top(f.last_month(), "category", 3), table_is),
    Question("Q16", "fr", "Combien de fois suis-je allé au restaurant le mois dernier?",
             lambda f: f.count(f.last_month(), category=RESTAURANTS), number_is),
    Question("Q17", "fr", "Ma plus grosse dépense le mois dernier?",
             lambda f: f.largest(f.last_month()), number_is),
    Question("Q18", "en", "What will my balance be in 14 days?", balance_in(14),
             forecast_middle_is),
    Question("Q19", "fr", "Quelle est la probabilité de passer sous zéro dans les 30 prochains "
             "jours?", chance_below_zero, forecast_chance_is),
    Question("Q20", "en", "What is my lowest balance in the next 30 days?", lowest,
             forecast_middle_is),
    Question("Q21", "en", "How much is safe to spend?", safe, safe_is),
    Question("Q22", "fr", "Quels sont mes paiements récurrents?", recurring_out, recurring_is),
    Question("Q23", "en", "How am I doing on my budgets?", budgets, budgets_is),
    Question("Q24", "en", "Should I buy a car?", lambda f: REFUSED, nothing),
)  # fmt: skip


# --- Running the check ---


def outcome_of(asked: Asked) -> str:
    if asked.problems:
        return "error"  # a plan that failed its checks
    if asked.outcome is None:
        return "refused"  # no plan: the page says "I can't answer that yet"
    return {"answer": "ran", "refused": "refused", "clarify": "asked back",
            "not_found": "not found"}[asked.outcome.kind]  # fmt: skip


def _judge(q: Question, facts: Facts, ctx: Context) -> tuple[str, str]:
    if MERCHANT in q.text and facts.merchant is None:
        return "skipped", "n/a"  # no merchant to ask about
    text = q.text.replace(MERCHANT, facts.merchant or "")
    try:
        asked = ask_offline(text, ctx)
        outcome = outcome_of(asked)
    except Exception:  # noqa: BLE001  (counted, never shown)
        return "error", "n/a"
    try:
        expected = q.expect(facts)
    except Exception:  # noqa: BLE001
        return outcome, "n/a"
    if expected == REFUSED:  # anything but a refusal is a miss, asking back included
        return outcome, "n/a" if outcome == "error" else "yes" if outcome == "refused" else "no"
    if expected == UNKNOWN or outcome in ("asked back", "error"):
        return outcome, "n/a"
    if outcome != "ran":
        return outcome, "no"
    assert asked.outcome is not None
    return outcome, "yes" if q.agrees(asked.outcome.result, expected) else "no"


def check_one(q: Question, facts: Facts, ctx: Context) -> tuple[str, str]:
    """(outcome, matches) for one question; never any figure or text from the data. Any
    unforeseen failure is an error, with no message shown."""
    try:
        return _judge(q, facts, ctx)
    except Exception:  # noqa: BLE001
        return "error", "n/a"


def report(conn: sqlite3.Connection, account: str, today: date, key: bytes | None) -> list[str]:
    """One line per question and a summary: ids, outcomes and yes/no only."""
    try:
        if not has_categories(conn, account):
            return ["No categories yet: run the categorizer first (Ask needs them)."]
        facts = Facts(conn, account, today, key)
    except Exception as e:  # noqa: BLE001  (the type only: a message could hold data)
        return [f"Could not prepare the check: {type(e).__name__}."]
    ctx = Context(conn, account, today, key)
    lines = [f"{len(QUESTIONS)} fixed questions, asked as the Ask page asks them (offline)."]
    outcomes: dict[str, int] = dict.fromkeys(OUTCOMES, 0)
    matches = {"yes": 0, "no": 0, "n/a": 0}
    for q in QUESTIONS:
        outcome, match = check_one(q, facts, ctx)
        outcomes[outcome] += 1
        matches[match] += 1
        lines.append(f"{q.id} {q.lang}  {outcome:<10}  matches: {match}")
    lines.append(
        "Summary: " + ", ".join(f"{k} {v}" for k, v in outcomes.items())
        + "; matches: " + ", ".join(f"{k} {v}" for k, v in matches.items())
    )  # fmt: skip
    return lines


def key_from_environment() -> bytes | None:
    """The pseudonym key from the PSEUDONYM_KEY environment variable, or None. Never the .env
    file: this check is offline and sends nothing, so it needs no secret from there. Without the
    key, series found from e-Transfer names and decisions stored against them may differ from
    the app's; both sides of every comparison still use the same (missing) key."""
    text = os.environ.get(KEY_NAME, "").strip()
    return bytes.fromhex(text) if text else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.ask_check", description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--account", type=int, default=1, help="account number (1 = the first)")
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"No database at {args.db}", file=sys.stderr)
        return 2
    try:
        conn = connect_readonly(args.db)
        names = accounts(conn)
    except Exception as e:  # noqa: BLE001  (the type only)
        print(f"Could not open the database: {type(e).__name__}.", file=sys.stderr)
        return 2
    try:
        if not 1 <= args.account <= len(names):
            print(f"Account number 1 to {len(names)}", file=sys.stderr)
            return 2
        account = names[args.account - 1]
        try:
            key = key_from_environment()
        except ValueError as e:  # the type only: a message could quote the value
            print(f"Could not read the key: {type(e).__name__}.", file=sys.stderr)
            return 2
        print(f"Account {args.account} of {len(names)}")
        print("Pseudonym key: " + ("from the environment" if key else
                                   "not used (this check never reads .env)"))  # fmt: skip
        try:
            last = conn.execute("SELECT MAX(posted_date) FROM transactions WHERE account = ?",
                                (account,)).fetchone()[0]  # fmt: skip
            today = demo_mode.today(date.fromisoformat(last))  # the page's today
        except Exception as e:  # noqa: BLE001
            print(f"Could not prepare the check: {type(e).__name__}.")
            return 1
        print("\n".join(report(conn, account, today, key)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
