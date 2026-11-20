"""Ask step 4: merchant names in a question matched locally (spec docs/specs/2026-11-20-ask.md,
definition of done 5), and period words at the edges (month and year turns, Mondays, leap years).
Names are made up."""

from datetime import date
from pathlib import Path

import pytest

from lookahead.ask.match import match, stored_aliases
from lookahead.ask.periods import asked, future_day
from lookahead.ask.plan import Day, Period
from lookahead.db import connect

NAMES = [
    "Cafe Velisaro", "Velisaro Bakery", "Petit Orme", "Uber Eats", "Quorvel Market",
    "Lumenwick Bookstore", "Pho Tavo", "Pho Tavi",
]  # fmt: skip


def one(phrase: str, aliases: dict[str, str] | None = None) -> tuple[str, str]:
    m = match(phrase, NAMES, aliases)
    assert m.status == "one", m
    return m.names[0], m.how


def test_exact_ignores_case_accents_and_punctuation() -> None:
    assert one("CAFÉ velisaro") == ("Cafe Velisaro", "same name")
    assert one("petit-orme") == ("Petit Orme", "same name")


def test_a_known_bank_spelling() -> None:
    from lookahead.resolve import ALIAS_KEYS

    assert one("ubereats", ALIAS_KEYS) == ("Uber Eats", "known spelling")


def test_a_merge_stored_on_the_merchants_page(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    with conn:
        conn.execute("INSERT INTO merchants VALUES (1, 'Quorvel Market', 'user')")
        conn.execute("INSERT INTO merchant_aliases VALUES ('qvl mkt', 1, 'user')")
    assert one("QVL MKT", stored_aliases(conn)) == ("Quorvel Market", "known spelling")


def test_cleaned_spelling_and_whole_words() -> None:
    assert one("petitorme") == ("Petit Orme", "same letters")
    assert one("lumenwick") == ("Lumenwick Bookstore", "your words are in the name")
    assert one("my petit orme order") == ("Petit Orme", "the name is in your words")


def test_a_typo_is_a_close_match() -> None:
    assert one("quorvell") == ("Quorvel Market", "close spelling")
    assert one("lumenwik bookstore") == ("Lumenwick Bookstore", "close spelling")


def test_several_close_matches_ask_back() -> None:
    m = match("velisaro", NAMES)
    assert m.status == "many" and m.names == ("Cafe Velisaro", "Velisaro Bakery")
    m = match("pho tava", NAMES)  # one letter from both
    assert m.status == "many" and m.names == ("Pho Tavi", "Pho Tavo")


def test_no_match_and_short_phrases_are_never_guessed() -> None:
    assert match("costco", NAMES).status == "none"
    assert match("pho", NAMES).status == "many"  # whole word of two names: asks
    assert match("phx", NAMES).status == "none"  # too short for a close match
    assert match("", NAMES).status == "none"


def test_only_names_in_the_data_can_match() -> None:
    from lookahead.resolve import ALIAS_KEYS

    # "netflix" is a known spelling, but there is no Netflix in this data
    assert match("netflix", NAMES, ALIAS_KEYS).status == "none"


# --- Period words at the edges ---


@pytest.mark.parametrize(
    ("today", "period", "expected"),
    [
        # January: last month is December of the year before
        (date(2027, 1, 5), {"kind": "last_month"}, (date(2026, 12, 1), date(2026, 12, 31))),
        (date(2027, 1, 5), {"kind": "last_n_months", "n": 2},
         (date(2026, 11, 1), date(2026, 12, 31))),
        # a Monday: this week is today only; last week the 7 days before
        (date(2026, 10, 12), {"kind": "this_week"}, (date(2026, 10, 12), date(2026, 10, 12))),
        (date(2026, 10, 12), {"kind": "last_week"}, (date(2026, 10, 5), date(2026, 10, 11))),
        # a month without a year, asked in that month: this one so far
        (date(2026, 3, 10), {"kind": "named_month", "month": 3},
         (date(2026, 3, 1), date(2026, 3, 31))),
        # since a date
        (date(2026, 3, 10), {"kind": "since", "start": "2026-02-27"},
         (date(2026, 2, 27), date(2026, 3, 10))),
    ],
)  # fmt: skip
def test_period_edges(today: date, period: dict, expected: tuple[date, date]) -> None:  # type: ignore[type-arg]
    assert asked(Period(**period), today, date(2020, 1, 1)) == expected


def test_day_edges() -> None:
    # the 30th asked on January 31: February has none, so its last day
    assert future_day(Day(kind="day_of_month", day=30), date(2028, 1, 31)) == date(2028, 2, 29)
    # February 29 asked in a year without one: the 28th
    assert future_day(Day(kind="named_date", month=2, day=29), date(2026, 2, 1)) == date(
        2026, 2, 28
    )
    # end of week asked on a Sunday is today
    assert future_day(Day(kind="end_of_week"), date(2026, 10, 18)) == date(2026, 10, 18)
