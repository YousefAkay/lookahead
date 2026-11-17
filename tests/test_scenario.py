"""The scenario calculator (lookahead/scenario.py): exact sums of what is entered, no randomness."""

import subprocess
import sys
from datetime import date, timedelta

import pytest

from lookahead.scenario import Flow, Inputs, OneOff, default_end, flow_dates, run

START = date(2026, 3, 31)  # the balance is at the end of this day
END = date(2026, 4, 30)  # 30 days: April 1 to 30

RENT = Flow("Rent", -80_000, "monthly", date(2024, 1, 1), card=True)  # an e-Transfer
PAY = Flow("Pay", 120_000, "biweekly", date(2026, 4, 3))  # Fridays
PHONE = Flow("Phone", -6_500, "monthly", date(2024, 1, 15))


def month(**more: object) -> Inputs:
    base: dict[str, object] = {
        "balance_cents": 100_000, "start": START, "end": END, "flows": (RENT, PAY, PHONE),
        "daily_spend_cents": 1_234, "one_offs": (OneOff(END, -5_000, "Concert"),),
    }  # fmt: skip
    return Inputs(**{**base, **more})  # type: ignore[arg-type]


def test_a_30_day_case_computed_by_hand_matches_to_the_cent() -> None:
    # Rent $800 on Wed Apr 1. Pay $1,200 due Fri Apr 3 (Good Friday, a holiday: income moves to
    # the business day before, Thu Apr 2) and Fri Apr 17. Phone $65 on Wed Apr 15. $12.34 a day
    # for 30 days. A $50 concert on the last day.
    # 1,000.00 - 370.20 - 800.00 + 2 x 1,200.00 - 65.00 - 50.00 = 2,114.80
    r = run(month())
    assert r.end_balance == 211_480
    assert len(r.days) == len(r.balances) == 30
    assert (r.days[0], r.days[-1]) == (date(2026, 4, 1), END)
    by_day = dict(zip(r.days, r.balances, strict=True))
    assert by_day[date(2026, 4, 1)] == 100_000 - 1_234 - 80_000
    assert by_day[date(2026, 4, 2)] == 100_000 - 2 * 1_234 - 80_000 + 120_000
    assert by_day[date(2026, 4, 3)] == 100_000 - 3 * 1_234 - 80_000 + 120_000
    assert r.first_below is None


def test_the_first_day_below_the_buffer() -> None:
    assert run(month(buffer_cents=20_000)).first_below == date(2026, 4, 1)  # 187.66 after rent
    assert run(month(buffer_cents=18_766)).first_below is None  # exactly at the line: not below


@pytest.mark.parametrize("more", [0, 1, 99, 5_000])
def test_spending_more_a_day_never_raises_the_end_balance(more: int) -> None:
    assert run(month(daily_spend_cents=1_234 + more)).end_balance <= run(month()).end_balance


@pytest.mark.parametrize("off", [RENT, PHONE])
def test_switching_off_an_expense_never_lowers_the_end_balance(off: Flow) -> None:
    flows = tuple(Flow(f.name, f.cents, f.cadence, f.anchor, f.card, on=f is not off)
                  for f in (RENT, PAY, PHONE))  # fmt: skip
    switched = run(month(flows=flows))
    assert switched.end_balance >= run(month()).end_balance
    assert all(a >= b for a, b in zip(switched.balances, run(month()).balances, strict=True))


def test_rent_on_the_31st_is_paid_on_the_last_day_of_shorter_months() -> None:
    rent31 = Flow("Rent", -80_000, "monthly", date(2024, 1, 31), card=True)
    got = flow_dates(rent31, date(2026, 2, 1), date(2026, 4, 30))
    # Feb 28 2026 is a Saturday: an e-Transfer posts on Saturdays. Mar 31, Apr 30.
    assert got == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_february_in_a_leap_year_and_a_bill_that_moves_off_a_weekend() -> None:
    rent30 = Flow("Rent", -80_000, "monthly", date(2024, 1, 30), card=True)
    assert flow_dates(rent30, date(2028, 2, 1), date(2028, 2, 29)) == [date(2028, 2, 29)]
    bill31 = Flow("Bill", -5_000, "monthly", date(2024, 1, 31))  # a bill payment
    # Jan 31 and Feb 28 2026 are Saturdays: a bill posts on the next business day. The January
    # one (due before the start) posts Monday Feb 2; the February one Monday March 2.
    assert flow_dates(bill31, date(2026, 2, 1), date(2026, 2, 28)) == [date(2026, 2, 2)]
    assert flow_dates(bill31, date(2026, 2, 1), date(2026, 3, 3)) == [
        date(2026, 2, 2), date(2026, 3, 2)
    ]  # fmt: skip


def test_a_one_off_on_the_last_day_counts_and_one_after_it_does_not() -> None:
    plain = month(one_offs=())
    assert run(month(one_offs=(OneOff(END, -5_000),))).end_balance == run(plain).end_balance - 5_000
    after = OneOff(END + timedelta(days=1), -5_000)
    before = OneOff(START, -5_000)  # the start day is already in the balance
    assert run(month(one_offs=(after, before))).end_balance == run(plain).end_balance


def test_an_end_date_on_or_before_the_start_gives_no_days() -> None:
    for end in (START, START - timedelta(days=10)):
        r = run(month(end=end))
        assert r.days == () and r.balances == ()
        assert r.end_balance == 100_000 and r.first_below is None


def test_with_no_series_only_the_daily_spend_moves_the_balance() -> None:
    r = run(month(flows=(), one_offs=()))
    assert r.end_balance == 100_000 - 30 * 1_234
    assert r.balances == tuple(100_000 - k * 1_234 for k in range(1, 31))


def test_money_in_with_no_spending_and_no_flows_stays_flat() -> None:
    r = run(Inputs(balance_cents=5_000, start=START, end=END))
    assert set(r.balances) == {5_000} and r.end_balance == 5_000


def test_the_default_end_is_90_days_after_the_last_day_of_data() -> None:
    """Scenario v2, definition of done 8 (it was December 31 of this year)."""
    assert default_end(date(2026, 10, 1)) == date(2026, 12, 30)
    assert default_end(date(2026, 12, 15)) == date(2027, 3, 15)  # crosses the year


def test_a_future_lump_sum_raises_my_scenario_by_exactly_its_amount_from_its_day() -> None:
    """Scenario v2, definition of done 5: an expected family transfer entered as a dated one-off
    "money in" changes nothing before its day and adds exactly its amount from that day on."""
    gift = OneOff(date(2026, 4, 20), 250_000, "family transfer")
    without, with_gift = run(month()), run(month(one_offs=(*month().one_offs, gift)))
    for day, a, b in zip(without.days, without.balances, with_gift.balances, strict=True):
        assert b - a == (250_000 if day >= gift.day else 0), day


def test_the_calculator_and_its_adapter_import_nothing_from_the_forecaster_or_proposers() -> None:
    code = (
        "import sys, lookahead.scenario, lookahead.scenario_prefill; "
        "print([m for m in sys.modules if m.startswith(('lookahead.forecast', "
        "'lookahead.proposals', 'lookahead.alerts'))])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- Income spread through the month, and top-ups ---

QUARTER_END = date(2026, 6, 30)  # April, May and June: three whole months


def test_a_spread_month_adds_up_to_the_monthly_amount_exactly() -> None:
    from lookahead.scenario import day_share

    for month_ in (date(2026, 2, 1), date(2026, 4, 1), date(2028, 2, 1), date(2026, 12, 1)):
        days = [month_.replace(day=d) for d in range(1, 32) if _valid(month_, d)]
        for cents in (300_000, 100_001, -45_678, 0):
            assert sum(day_share(cents, d) for d in days) == cents


def _valid(month_: date, day: int) -> bool:
    try:
        month_.replace(day=day)
    except ValueError:
        return False
    return True


def test_spread_and_set_day_income_end_the_same_but_take_different_paths() -> None:
    on_day = Flow("Income", 300_000, "monthly", date(2024, 1, 15))
    spread = Flow("Income", 300_000, "monthly", date(2024, 1, 15), spread=True)
    a = run(Inputs(100_000, START, QUARTER_END, (on_day,), daily_spend_cents=2_000))
    b = run(Inputs(100_000, START, QUARTER_END, (spread,), daily_spend_cents=2_000))
    assert a.end_balance == b.end_balance == 100_000 + 3 * 300_000 - 91 * 2_000
    assert a.balances != b.balances
    assert b.balances[0] == 100_000 + 10_000 - 2_000  # $3,000 over April's 30 days: $100 a day


def test_top_ups_from_savings_add_to_money_in() -> None:
    top_up = Flow("Top-ups from savings", 50_000, "monthly", date(2024, 1, 1), spread=True)
    base = Inputs(100_000, START, QUARTER_END, daily_spend_cents=2_000)
    with_top_ups = Inputs(100_000, START, QUARTER_END, (top_up,), daily_spend_cents=2_000)
    assert run(with_top_ups).end_balance == run(base).end_balance + 3 * 50_000


def test_the_end_balance_is_exactly_the_sum_of_its_parts_over_3_rent_days() -> None:
    """My check of 2026-11-17 (chequing today - rent x 3 - daily spend x 90 + income x 3): the
    page's "How we got here" adds up to the end balance exactly, and the daily spend is taken off
    every one of the 90 days."""
    rent = Flow("Rent", -120_000, "monthly", date(2024, 1, 1), card=True)
    income = Flow("Income", 250_000, "monthly", date(2024, 1, 25))
    phone = Flow("Phone", -6_500, "monthly", date(2024, 1, 15))
    start = date(2026, 10, 31)
    inputs = Inputs(balance_cents=300_000, start=start, end=start + timedelta(days=90),
                    flows=(rent, income, phone), daily_spend_cents=4_000,
                    one_offs=(OneOff(date(2026, 12, 20), 50_000, "gift"),))  # fmt: skip
    r = run(inputs)
    assert r.spend_days == 90 and r.spend_total == 90 * 4_000
    assert [n for n, _ in r.flow_totals] == [3, 3, 3]  # Nov 1 (Mon), Dec 1, Jan 2 (Jan 1: holiday)
    assert r.one_off_total == 50_000
    parts = 300_000 + sum(c for _, c in r.flow_totals) - r.spend_total + r.one_off_total
    assert r.end_balance == parts
    assert r.end_balance == 300_000 - 3 * 120_000 - 90 * 4_000 + 3 * 250_000 - 3 * 6_500 + 50_000
