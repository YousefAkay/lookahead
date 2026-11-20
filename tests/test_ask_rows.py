"""Ask step 2: period words and the row blocks, on a tiny made-up account whose answers are worked
out by hand below (spec docs/specs/2026-11-20-ask.md, definition of done 1).

Today is Friday 2026-10-16; the data runs from 2026-08-03 to 2026-10-15.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from lookahead.ask.periods import asked, future_day, span
from lookahead.ask.plan import Day, Period, parse
from lookahead.ask.run import Context, Listing, Number, Table, run, signature
from lookahead.db import connect

ACCOUNT = "made-up"
TODAY = date(2026, 10, 16)
G, R, INC, H, T, S = (
    "Groceries", "Restaurants & cafés", "Income", "Rent & housing", "Transfers", "Shopping",
)  # fmt: skip
ROWS = [  # day, cents, merchant, flow type, category
    ("2026-08-03", -5000, "Quorvel Market", "everyday", G),
    ("2026-08-20", -3000, "Quorvel Market", "everyday", G),
    ("2026-08-25", -1200, "Cafe Velisaro", "everyday", R),
    ("2026-09-01", 250000, "Northwind Payroll", "income", INC),
    ("2026-09-02", -120000, "Lumen Property", "recurring_outflow", H),
    ("2026-09-05", -4500, "Quorvel Market", "everyday", G),
    ("2026-09-06", -800, "Cafe Velisaro", "everyday", R),
    ("2026-09-06", -1500, "Velisaro Bakery", "everyday", R),
    ("2026-09-10", -20000, "Own Savings", "transfer", T),
    ("2026-09-12", 1000, "Quorvel Market", "refund", G),
    ("2026-10-01", 250000, "Northwind Payroll", "income", INC),
    ("2026-10-02", -120000, "Lumen Property", "recurring_outflow", H),
    ("2026-10-04", -6000, "Quorvel Market", "everyday", G),
    ("2026-10-10", -2500, "Petit Orme", "everyday", S),
    ("2026-10-15", -900, "Cafe Velisaro", "everyday", R),
]


def build(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    balance = 100000
    with conn:
        for i, (day, cents, merchant, flow, category) in enumerate(ROWS):
            balance += cents
            fp = f"fp{i:02d}"
            conn.execute(
                "INSERT INTO transactions (account, posted_date, amount_cents, description,"
                " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
                " VALUES (?, ?, ?, 'Purchase', ?, ?, ?, 'made-up.csv', '2026-10-16')",
                (ACCOUNT, day, cents, merchant, balance, fp),
            )
            conn.execute(
                "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
                " category, source, detail, flagged, confidence, model_version, predicted_at)"
                " VALUES (?, ?, ?, ?, ?, 'rule', '', 0, NULL, 'test', '2026-10-16')",
                (fp, ACCOUNT, merchant, flow, category),
            )
    return conn


@pytest.fixture
def ctx(tmp_path: Path) -> Context:
    return Context(build(tmp_path / "tiny.db"), ACCOUNT, TODAY)


def ask(ctx: Context, *steps: dict):  # type: ignore[no-untyped-def,type-arg]
    return run(parse({"steps": list(steps)}), ctx)


def rows(name: str = "r", **filters: object) -> dict:  # type: ignore[type-arg]
    return {"op": "rows", "name": name, **filters}


def total(source: str = "r", name: str = "t", measure: str = "sum") -> dict:  # type: ignore[type-arg]
    return {"op": "aggregate", "name": name, "source": source, "measure": measure}


def value(ctx: Context, *steps: dict) -> object:  # type: ignore[type-arg]
    out = ask(ctx, *steps)
    assert out.kind == "answer", out.result
    return out.result.value


# --- Periods ---


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ({"kind": "this_month"}, (date(2026, 10, 1), TODAY)),
        ({"kind": "last_month"}, (date(2026, 9, 1), date(2026, 9, 30))),
        ({"kind": "this_week"}, (date(2026, 10, 12), TODAY)),
        ({"kind": "last_week"}, (date(2026, 10, 5), date(2026, 10, 11))),
        ({"kind": "last_n_days", "n": 7}, (date(2026, 10, 9), date(2026, 10, 15))),
        ({"kind": "last_n_weeks", "n": 2}, (date(2026, 9, 28), date(2026, 10, 11))),
        ({"kind": "last_n_months", "n": 3}, (date(2026, 7, 1), date(2026, 9, 30))),
        ({"kind": "named_month", "month": 3}, (date(2026, 3, 1), date(2026, 3, 31))),
        ({"kind": "named_month", "month": 11}, (date(2025, 11, 1), date(2025, 11, 30))),
        ({"kind": "month", "year": 2024, "month": 2}, (date(2024, 2, 1), date(2024, 2, 29))),
        ({"kind": "last_year"}, (date(2025, 1, 1), date(2025, 12, 31))),
        ({"kind": "this_year"}, (date(2026, 1, 1), TODAY)),
        ({"kind": "yesterday"}, (date(2026, 10, 15), date(2026, 10, 15))),
    ],
)
def test_period_words_become_dates(period: dict, expected: tuple[date, date]) -> None:  # type: ignore[type-arg]
    assert asked(Period(**period), TODAY, date(2026, 8, 3)) == expected


def test_a_period_is_cut_to_the_data_with_notes() -> None:
    first, last = date(2026, 8, 3), date(2026, 10, 15)
    s = span(Period(kind="this_month"), TODAY, first, last)
    assert (s.start, s.end) == (date(2026, 10, 1), last)
    assert s.notes == ("Your data ends on 2026-10-15: later days are not in it yet.",)
    s = span(Period(kind="last_n_months", n=6), TODAY, first, last)
    assert s.start == first and "Your data starts on 2026-08-03" in s.notes[0]
    assert span(Period(kind="named_month", month=3), TODAY, first, last).empty


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        ({"kind": "tomorrow"}, date(2026, 10, 17)),
        ({"kind": "in_days", "n": 14}, date(2026, 10, 30)),
        ({"kind": "day_of_month", "day": 20}, date(2026, 10, 20)),
        ({"kind": "day_of_month", "day": 3}, date(2026, 11, 3)),
        ({"kind": "day_of_month", "day": 31}, date(2026, 10, 31)),
        ({"kind": "named_date", "month": 1, "day": 2}, date(2027, 1, 2)),
        ({"kind": "end_of_week"}, date(2026, 10, 18)),
        ({"kind": "end_of_month"}, date(2026, 10, 31)),
    ],
)
def test_day_words_become_dates(day: dict, expected: date) -> None:  # type: ignore[type-arg]
    assert future_day(Day(**day), TODAY) == expected


# --- Row blocks, answers worked out by hand ---


def test_spending_in_a_category_last_month(ctx: Context) -> None:
    # September groceries: 45.00 out; the 10.00 refund is money in, so it is not spending.
    assert value(ctx, rows(categories=[G], period={"kind": "last_month"}), total()) == 4500


def test_spending_leaves_out_transfers_unless_asked(ctx: Context) -> None:
    sep = {"kind": "month", "year": 2026, "month": 9}
    assert value(ctx, rows(period=sep), total()) == 120000 + 4500 + 800 + 1500
    assert value(ctx, rows(period=sep, kinds=["transfer"]), total()) == 20000
    assert value(ctx, rows(period=sep, categories=[T]), total()) == 20000


def test_money_in_and_both_directions(ctx: Context) -> None:
    sep = {"kind": "month", "year": 2026, "month": 9}
    assert value(ctx, rows(period=sep, direction="in"), total()) == 250000 + 1000
    # both: signed; the transfer is still left out
    assert value(ctx, rows(period=sep, direction="both"), total()) == 251000 - 126800


def test_average_of_monthly_totals_counts_every_month(ctx: Context) -> None:
    # this year cut to the data: August 80.00, September 45.00, October 60.00
    out = ask(ctx, rows(categories=[G], period={"kind": "this_year"}),
              {"op": "group", "name": "m", "source": "r", "by": "month"},
              total("m", "avg", "average"))  # fmt: skip
    assert out.values["m"].pairs == (("2026-08", 8000), ("2026-09", 4500), ("2026-10", 6000))
    assert out.result.value == round(18500 / 3)


def test_an_empty_month_counts_as_zero_in_an_average(ctx: Context) -> None:
    out = ask(ctx, rows(categories=[S], period={"kind": "this_year"}),
              {"op": "group", "name": "m", "source": "r", "by": "month"},
              total("m", "avg", "average"))  # fmt: skip
    assert out.values["m"].pairs == (("2026-08", 0), ("2026-09", 0), ("2026-10", 2500))
    assert out.result.value == round(2500 / 3)


def test_top_categories(ctx: Context) -> None:
    out = ask(ctx, rows(period={"kind": "last_month"}),
              {"op": "group", "name": "g", "source": "r", "by": "category"},
              {"op": "top", "name": "top", "source": "g", "n": 2})  # fmt: skip
    assert isinstance(out.result, Table)
    assert out.result.pairs == ((H, 120000), (G, 4500))  # restaurants 23.00 is third


def test_count_max_and_the_row_behind_it(ctx: Context) -> None:
    assert value(ctx, rows(categories=[R]), total(measure="count")) == 4
    out = ask(ctx, rows(period={"kind": "last_month"}), total(measure="max"))
    assert out.result.value == 120000 and out.result.row["merchant"] == "Lumen Property"


def test_compare_two_months(ctx: Context) -> None:
    steps = (
        rows("oct", categories=[G], period={"kind": "this_month"}), total("oct", "a"),
        rows("sep", categories=[G], period={"kind": "last_month"}), total("sep", "b"),
    )  # fmt: skip
    diff = {"op": "compare", "name": "d", "a": "a", "b": "b", "how": "difference"}
    assert value(ctx, *steps, diff) == 1500
    assert value(ctx, *steps, diff | {"how": "percent_change"}) == 33.3
    assert value(ctx, *steps, diff | {"how": "share"}) == 133.3


def test_a_share_of_nothing_has_no_value(ctx: Context) -> None:
    steps = (
        rows("a1", categories=[G]), total("a1", "a"),
        rows("b1", categories=[S], period={"kind": "last_month"}), total("b1", "b"),
        {"op": "compare", "name": "d", "a": "a", "b": "b", "how": "share"},
    )  # fmt: skip
    assert value(ctx, *steps) is None


def test_list_largest_first_and_weeks(ctx: Context) -> None:
    out = ask(ctx, rows(period={"kind": "this_month"}),
              {"op": "list", "name": "l", "source": "r", "order": "largest",
               "limit": 2})  # fmt: skip
    assert isinstance(out.result, Listing)
    assert list(out.result.rows["amount_cents"]) == [-120000, -6000] and out.result.total == 4
    assert "Your data ends on 2026-10-15" in out.notes[0]
    out = ask(ctx, rows(period={"kind": "this_month"}),
              {"op": "group", "name": "w", "source": "r", "by": "week"})  # fmt: skip
    assert out.result.pairs == (
        ("week of 2026-09-28", 126000), ("week of 2026-10-05", 2500),
        ("week of 2026-10-12", 900),
    )  # fmt: skip


def test_amount_filters(ctx: Context) -> None:
    assert value(ctx, rows(min_dollars=100), total()) == 240000
    assert value(ctx, rows(max_dollars=10, categories=[R]), total(measure="count")) == 2


def test_an_empty_period_and_one_past_the_data(ctx: Context) -> None:
    out = ask(ctx, rows(categories=[G], period={"kind": "named_month", "month": 3}), total())
    assert out.result.value == 0
    assert any("Your data starts on 2026-08-03" in n for n in out.notes)
    out = ask(ctx, rows(categories=[G], period={"kind": "range", "start": "2026-11-01",
                                                "end": "2026-11-30"}), total())  # fmt: skip
    assert out.result.value == 0 and any("Your data ends" in n for n in out.notes)
    assert (
        value(ctx, rows(categories=[G], period={"kind": "today"}), total("r", "a", "average"))
        is None
    )


# --- Merchants (basic matching; step 4 adds the rest) ---


def test_merchant_exact_inside_and_several(ctx: Context) -> None:
    assert value(ctx, rows(merchants=["cafe velisaro"]), total()) == 1200 + 800 + 900
    assert value(ctx, rows(merchants=["orme"]), total()) == 2500
    out = ask(ctx, rows(merchants=["velisaro"]), total())
    assert out.kind == "clarify"
    assert out.result.candidates == ("Cafe Velisaro", "Velisaro Bakery")
    out = run(parse({"steps": [rows(merchants=["velisaro"]), total()]}),
              Context(ctx.conn, ACCOUNT, TODAY,
                      choices={"velisaro": "Velisaro Bakery"}))  # fmt: skip
    assert out.result.value == 1500
    out = ask(ctx, rows(merchants=["costco"]), total())
    assert (
        out.kind == "not_found" and out.result.message == "No merchant like “costco” in your data."
    )


def test_signatures_compare_answers(ctx: Context) -> None:
    a = ask(ctx, rows(categories=[G], period={"kind": "last_month"}), total())
    b = ask(ctx, rows(categories=[G], period={"kind": "month", "year": 2026, "month": 9}), total())
    c = ask(ctx, rows(categories=[G]), total())
    assert signature(a) == signature(b) != signature(c)
    assert signature(ask(ctx, {"op": "cannot_answer", "reason": "advice"})) == ("refused",)
    assert isinstance(a.result, Number)


def test_no_categories_or_no_data_is_said_plainly(tmp_path: Path) -> None:
    conn = build(tmp_path / "nocat.db")
    with conn:
        conn.execute("DELETE FROM category_predictions")
    out = run(parse({"steps": [rows(), total()]}), Context(conn, ACCOUNT, TODAY))
    assert out.kind == "refused" and "run the categorizer" in out.result.message
    out = run(parse({"steps": [rows(), total()]}), Context(conn, "nobody", TODAY))
    assert out.kind == "refused" and "import a bank export" in out.result.message
