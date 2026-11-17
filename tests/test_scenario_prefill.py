"""Prefilling the scenario calculator from the database (lookahead/scenario_prefill.py). Made-up
rows in a temporary database."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.recurring_store import add_series, confirm, refresh, set_rent_rule
from lookahead.rent import RentRule
from lookahead.resolve import mark_own_account
from lookahead.scenario import Flow, Inputs, run
from lookahead.scenario_prefill import (
    Prefill,
    counts_top_up_twice,
    prefill,
    starting_point,
    usual_day,
)
from lookahead.settings import (
    savings_balance,
    set_safety_buffer_cents,
    set_savings_balance,
)

ACCOUNT = "test"
LAST = date(2026, 6, 30)
LONG_AGO = date(2026, 1, 1)


def insert(conn: sqlite3.Connection, day: date, cents: int, text: str, kind: str = "pos purchase",
           balance: int = 0, flow: str | None = None, source: str = "model") -> None:  # fmt: skip
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] + 1
    fp = f"fp{n:04d}"
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, balance, fp),
        )
        if flow is not None:
            conn.execute(
                "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
                " category, source, detail, flagged, confidence, model_version, predicted_at)"
                " VALUES (?, ?, ?, ?, 'Shopping', ?, '', 0, 0.9, 'test', '2026-10-01')",
                (fp, ACCOUNT, text, flow, source),
            )


def days_back(n: int) -> date:
    return LAST - timedelta(days=n)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """January to June: a phone bill on the 15th, rent by e-Transfer around the 1st, pay on the
    25th (not in a series), spending on some days; the newest row holds the balance."""
    c = connect(tmp_path / "t.db")
    for m in range(1, 7):
        insert(c, shift_to_business_day(monthly_date(2026, m, 15), "later"), -6_500, "Phone Co ",
               kind="bill payment", flow="everyday")  # fmt: skip
        rent_day = 2 if m != 6 else 3  # usually the 2nd
        insert(c, date(2026, m, rent_day), -90_000 if m < 6 else -95_000,
               "Free Interac E-Transfer ", kind="withdrawal", flow="transfer")  # fmt: skip
        insert(c, date(2026, m, 25), 150_000, "Payroll Dep ", kind="deposit", flow="income")
    insert(c, LAST, -1_000, "Opos Shop", flow="everyday", balance=321_000)
    return c


def stored(c: sqlite3.Connection) -> list:  # type: ignore[type-arg]
    return refresh(c, ACCOUNT)


def test_the_latest_balance_and_the_data_date(conn: sqlite3.Connection) -> None:
    p = prefill(conn, ACCOUNT, stored(conn))
    assert (p.balance_cents, p.data_date) == (321_000, LAST)


def test_only_confirmed_and_hand_added_series_are_used(conn: sqlite3.Connection) -> None:
    series = stored(conn)
    assert prefill(conn, ACCOUNT, series).flows == ()  # detected only: not used
    phone = next(s for s in series if s.name == "Phone Co")
    confirm(conn, ACCOUNT, phone, LONG_AGO)
    add_series(conn, ACCOUNT, "Gym", "out", "monthly", date(2026, 1, 5), 4_000, LONG_AGO)
    p = prefill(conn, ACCOUNT, stored(conn))
    assert sorted((f.name, f.cents, f.cadence) for f in p.flows) == [
        ("Gym", -4_000, "monthly"), ("Phone Co", -6_500, "monthly")
    ]  # fmt: skip


def test_rent_uses_its_latest_allowed_amount_on_its_usual_day(conn: sqlite3.Connection) -> None:
    rent = next(s for s in stored(conn) if s.kind == "e-transfer" and s.direction == "out")
    set_rent_rule(conn, ACCOUNT, rent, RentRule((90_000, 95_000), 1, 5), LONG_AGO)
    confirm(conn, ACCOUNT, next(s for s in stored(conn) if s.key == rent.key), LONG_AGO)
    p = prefill(conn, ACCOUNT, stored(conn))
    assert p.rent is not None
    assert (p.rent.cents, p.rent.anchor.day, p.rent.card) == (-95_000, 2, True)


def test_rent_whose_latest_payment_is_not_allowed_uses_the_larger_amount() -> None:
    from lookahead.scenario_prefill import rent_amount

    rule = RentRule((90_000, 95_000), 1, 5)
    assert rent_amount(rule, [(date(2026, 5, 2), 90_000), (date(2026, 6, 2), 91_000)]) == 95_000
    assert rent_amount(rule, [(date(2026, 5, 2), 95_000), (date(2026, 6, 2), 90_000)]) == 90_000
    assert rent_amount(rule, []) == 95_000


def test_income_is_the_average_per_month_over_90_days_on_its_usual_day(
    conn: sqlite3.Connection,
) -> None:
    p = prefill(conn, ACCOUNT, stored(conn))
    assert p.income is not None
    # Pay on April 25, May 25 and June 25 (inside the 90 days ending June 30): $4,500 in 90 days.
    assert p.income.cents == round(450_000 * (365.25 / 12) / 90)
    assert (p.income.anchor.day, p.income.cadence, p.income.cents > 0) == (25, "monthly", True)


def test_income_already_in_a_confirmed_series_is_not_counted_twice(
    conn: sqlite3.Connection,
) -> None:
    pay = next(s for s in stored(conn) if s.direction == "in")
    confirm(conn, ACCOUNT, pay, LONG_AGO)
    p = prefill(conn, ACCOUNT, stored(conn))
    assert p.income is None and "income" not in p.missing
    assert any(f.cents > 0 for f in p.flows)


def test_the_daily_spend_leaves_out_own_account_transfers_netted_pairs_and_money_in(
    tmp_path: Path,
) -> None:
    c = connect(tmp_path / "s.db")
    # 90 days: on 50 of them $10 of everyday spending, 40 with none. Median day: $10.
    for k in range(90):
        if k < 50:
            insert(c, days_back(k), -1_000, f"Opos Shop{k}", flow="everyday")
    # Not spending: a transfer to my own account, a netted pair, and money in.
    insert(c, days_back(3), -500_000, "Customer Transfer Dr. MB-Transfer To 1234",
           kind="customer transfer dr.", flow="transfer")  # fmt: skip
    insert(c, days_back(4), -400_000, "Opos Big", flow="everyday", source="netted")
    insert(c, days_back(5), 300_000, "Payroll Dep ", kind="deposit", flow="income")
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert p.daily_spend_cents == 1_000
    c.close()


def test_with_little_history_the_spend_and_income_stay_at_zero_and_say_so(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    insert(c, LAST, -1_000, "Opos Shop", flow="everyday", balance=5_000)
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert (p.daily_spend_cents, p.income) == (0, None)
    assert {"daily spend", "income"} <= set(p.missing)
    c.close()


def test_an_empty_account_prefills_nothing(tmp_path: Path) -> None:
    c = connect(tmp_path / "e.db")
    set_safety_buffer_cents(c, 25_000)
    p = prefill(c, ACCOUNT, [])
    assert (p.data_date, p.balance_cents, p.flows, p.rent, p.income) == (None, 0, (), None, None)
    assert p.buffer_cents == 25_000 and "balance" in p.missing
    c.close()


def test_the_usual_day_is_the_most_common_one_and_a_tie_goes_to_the_earlier() -> None:
    assert usual_day([date(2026, 1, 2), date(2026, 2, 2), date(2026, 3, 3)]) == 2
    assert usual_day([date(2026, 1, 5), date(2026, 2, 3)]) == 3
    assert usual_day([]) is None


# --- What is income and what is spending (changed after my real-data check) ---


def window(c: sqlite3.Connection) -> None:
    """90 days of history: a $1 purchase 89 days before the last day, and one on the last day."""
    insert(c, days_back(89), -100, "Opos First", flow="everyday")
    insert(c, LAST, -100, "Opos Last", flow="everyday")


def test_transfers_between_my_own_accounts_are_neither_income_nor_spending(
    tmp_path: Path,
) -> None:
    c = connect(tmp_path / "s.db")
    window(c)
    insert(c, days_back(10), 500_000, "Customer Transfer Cr. Savings", kind="customer transfer cr.",
           flow="transfer")  # fmt: skip
    insert(c, days_back(9), -400_000, "Customer Transfer Dr. Savings", kind="customer transfer dr.",
           flow="transfer")  # fmt: skip
    insert(c, days_back(8), 90_000, "Payroll Dep ", kind="deposit", flow="income")
    insert(c, days_back(7), 5_000, "Refund Opos Shop", flow="refund")
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert p.income is not None
    assert p.income.cents == round(90_000 * (365.25 / 12) / 90)  # the pay only
    assert p.daily_spend_cents == round(200 / 90)  # the transfer out is not spending
    assert "1 deposit counted over 90 days" in p.income_basis
    assert "own accounts" in p.income_basis and "own accounts" in p.spend_basis
    c.close()


def test_other_money_in_counts_as_income(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    window(c)
    insert(c, days_back(20), 30_000, "Interac E-Transfer From A Friend", kind="deposit",
           flow="everyday")  # fmt: skip
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert p.income is not None and p.income.cents == round(30_000 * (365.25 / 12) / 90)
    c.close()


def test_a_big_one_off_deposit_is_not_income(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    window(c)
    for k in (80, 50, 20):
        insert(c, days_back(k), 150_000, "Payroll Dep ", kind="deposit", flow="income")
    insert(c, days_back(30), 2_000_000, "Deposit Branch", kind="deposit", flow="income")
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert p.income is not None
    assert p.income.cents == round(450_000 * (365.25 / 12) / 90)
    assert "3 deposits counted over 90 days" in p.income_basis
    assert "1 one-off deposit left out" in p.income_basis
    c.close()


def test_the_daily_spend_is_the_average_day_not_the_median(tmp_path: Path) -> None:
    """A skewed made-up 90 days: nothing on 80 days, $90 on 10 days. The median day is $0;
    the average day is $10."""
    c = connect(tmp_path / "s.db")
    for k, back in enumerate((0, 10, 20, 30, 40, 50, 60, 70, 80, 89)):
        insert(c, days_back(back), -9_000, f"Opos Shop{k}", flow="everyday")
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert p.daily_spend_cents == 1_000
    assert "average day over 90 days" in p.spend_basis and "10 payments" in p.spend_basis
    c.close()


# --- Scenario v2 (spec docs/specs/2026-11-10-scenario-v2.md) ---


def history(c: sqlite3.Connection, with_transfers: bool) -> None:
    """January to June: pay on the 25th and a $20 purchase every 10th day; with transfers, $500
    comes in from savings on the 10th and $300 goes back on the 20th of each month."""
    for m in range(1, 7):
        insert(c, date(2026, m, 25), 150_000, "Payroll Dep ", kind="deposit", flow="income")
        for d in (5, 15, 28):
            insert(c, date(2026, m, d), -2_000, f"Opos Shop{d}", flow="everyday")
        if with_transfers:
            insert(c, date(2026, m, 10), 50_000, "Customer Transfer Cr. MB-Transfer From 1234",
                   kind="customer transfer cr.", flow="transfer")  # fmt: skip
            insert(c, date(2026, m, 20), -30_000, "Customer Transfer Dr. MB-Transfer To 1234",
                   kind="customer transfer dr.", flow="transfer")  # fmt: skip
    insert(c, LAST, -1_000, "Opos Last", flow="everyday", balance=321_000)


def confirm_all(c: sqlite3.Connection) -> None:
    for s in stored(c):
        confirm(c, ACCOUNT, s, LONG_AGO)


def test_transfers_between_my_accounts_change_neither_income_nor_spending(
    tmp_path: Path,
) -> None:
    """Definition of done 2: the same history with and without transfers to and from savings
    gives the same income and daily spend."""
    plain, moved = connect(tmp_path / "a.db"), connect(tmp_path / "b.db")
    history(plain, with_transfers=False)
    history(moved, with_transfers=True)
    a, b = prefill(plain, ACCOUNT, stored(plain)), prefill(moved, ACCOUNT, stored(moved))
    assert a.income is not None and b.income is not None
    assert (b.income.cents, b.daily_spend_cents) == (a.income.cents, a.daily_spend_cents)
    plain.close()
    moved.close()


def test_a_confirmed_series_of_own_account_transfers_cancels_out_in_the_combined_view(
    tmp_path: Path,
) -> None:
    """The monthly top-up from savings, once confirmed, is kept apart: counted in the
    chequing-only view, left out when chequing and savings are shown together."""
    c = connect(tmp_path / "s.db")
    history(c, with_transfers=True)
    confirm_all(c)
    p = prefill(c, ACCOUNT, stored(c))
    assert sorted(f.cents for f in p.own_account_flows) == [-30_000, 50_000]
    assert all(f.cents not in (-30_000, 50_000) for f in p.flows)
    chequing, both = starting_point(p, None), starting_point(p, 1_000_000)
    assert not chequing.combined and set(p.own_account_flows) <= set(chequing.flows)
    assert both.combined and not set(p.own_account_flows) & set(both.flows)
    assert both.balance_cents == 321_000 + 1_000_000 and chequing.balance_cents == 321_000
    c.close()


def test_a_savings_balance_of_zero_is_still_the_combined_view() -> None:
    p = Prefill(LAST, 321_000, (), None, None, 0, 20_000, ())
    assert starting_point(p, 0).combined and not starting_point(p, None).combined


def test_the_buffer_marker_follows_the_balance_the_chart_shows() -> None:
    """Definition of done 9: chequing alone drops below the $200 buffer on day 3; with $500 of
    savings the combined total does so only on day 8."""
    p = Prefill(LAST, 40_000, (), None, None, 10_000, 20_000, ())
    end = LAST + timedelta(days=30)

    def first_below(savings: int | None) -> date | None:
        view = starting_point(p, savings)
        return run(Inputs(view.balance_cents, LAST, end, view.flows, 10_000,
                          buffer_cents=p.buffer_cents)).first_below  # fmt: skip

    assert first_below(None) == LAST + timedelta(days=3)  # 400 - 3 x 100 = 100 < 200
    assert first_below(50_000) == LAST + timedelta(days=8)  # 900 - 8 x 100 = 100 < 200


def test_regular_family_transfers_count_and_a_big_lump_sum_is_left_out(tmp_path: Path) -> None:
    """Definition of done 6: incoming e-Transfers from family are income; a big one-off by the
    shared one-time money rule is not averaged in, and the note says so."""
    c = connect(tmp_path / "s.db")
    window(c)
    for k in (80, 50, 20):
        insert(c, days_back(k), 40_000, "Interac E-Transfer From Family", kind="deposit",
               flow="income")  # fmt: skip
    insert(c, days_back(35), 600_000, "Interac E-Transfer From Family", kind="deposit",
           flow="income")  # fmt: skip
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert p.income is not None
    assert p.income.cents == round(120_000 * (365.25 / 12) / 90)
    assert "3 deposits counted over 90 days" in p.income_basis
    assert "1 one-off deposit left out" in p.income_basis
    c.close()


def test_the_notes_are_never_blank(tmp_path: Path) -> None:
    """Definition of done 7: with no income at all, and with too little history, both notes still
    say what happened."""
    c = connect(tmp_path / "none.db")
    window(c)  # 90 days, spending only
    p = prefill(c, ACCOUNT, refresh(c, ACCOUNT))
    assert p.income is None and "No deposits counted as income" in p.income_basis
    assert p.spend_basis
    short = connect(tmp_path / "short.db")
    insert(short, LAST, -1_000, "Opos Shop", flow="everyday", balance=5_000)
    q = prefill(short, ACCOUNT, refresh(short, ACCOUNT))
    assert "not estimated" in q.income_basis and "not estimated" in q.spend_basis
    c.close()
    short.close()


def test_the_savings_balance_is_remembered_with_its_day_and_can_be_cleared(
    tmp_path: Path,
) -> None:
    """Definition of done 4, the storage side (the page side is in test_scenario_page.py)."""
    c = connect(tmp_path / "s.db")
    assert savings_balance(c) is None
    set_savings_balance(c, 1_234_500, date(2026, 11, 10))
    c.close()
    c = connect(tmp_path / "s.db")  # a new connection: it is stored, not kept in memory
    assert savings_balance(c) == (1_234_500, date(2026, 11, 10))
    set_savings_balance(c, None, date(2026, 11, 11))
    assert savings_balance(c) is None
    with c:
        c.execute("INSERT INTO app_settings (key, value) VALUES ('scenario_savings', 'garbled')")
    assert savings_balance(c) is None  # a damaged row means "not entered"
    with pytest.raises(ValueError):
        set_savings_balance(c, -1, date(2026, 11, 11))
    c.close()


def monthly(c: sqlite3.Connection, day: int, cents: int, text: str, kind: str, flow: str) -> None:
    for m in range(1, 7):
        insert(c, date(2026, m, day), cents, text, kind=kind, flow=flow)


def kept_apart(c: sqlite3.Connection) -> tuple[list[int], list[int]]:
    """(cents of the series counted in the combined view, cents of those left out of it)."""
    insert(c, LAST, -1_000, "Opos Last", flow="everyday", balance=321_000)
    confirm_all(c)
    p = prefill(c, ACCOUNT, stored(c))
    return sorted(f.cents for f in p.flows), sorted(f.cents for f in p.own_account_flows)


def test_an_e_transfer_series_to_someone_else_is_not_own_account(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    monthly(c, 12, -40_000, "Free Interac E-Transfer ", "withdrawal", "transfer")
    assert kept_apart(c) == ([-40_000], [])
    c.close()


def test_a_payment_to_my_own_card_does_not_cancel_out(tmp_path: Path) -> None:
    """A bill payment to a payee I marked as my own card leaves chequing and savings for good:
    it stays counted in the combined view (the reviewer's case, 2026-11-10)."""
    c = connect(tmp_path / "s.db")
    monthly(c, 18, -25_000, "Visa Card Payment", "bill payment", "transfer")
    mark_own_account(c, "Visa Card", when=LONG_AGO)  # the merchant name of the payee
    assert kept_apart(c) == ([-25_000], [])
    c.close()


def test_a_row_i_typed_as_something_else_is_not_own_account(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    monthly(c, 10, 50_000, "Customer Transfer Cr. MB-Transfer From 1234", "customer transfer cr.",
            "income")  # fmt: skip
    assert kept_apart(c) == ([50_000], [])
    c.close()


def test_a_confirmed_top_up_from_savings_is_not_an_income_series(tmp_path: Path) -> None:
    """With no other deposits, the income note says nothing was counted instead of pointing at a
    series the combined view leaves out."""
    c = connect(tmp_path / "s.db")
    window(c)
    monthly(c, 10, 50_000, "Customer Transfer Cr. MB-Transfer From 1234", "customer transfer cr.",
            "transfer")  # fmt: skip
    confirm_all(c)
    p = prefill(c, ACCOUNT, stored(c))
    assert p.income is None and "income" in p.missing
    assert "No deposits counted as income" in p.income_basis
    c.close()


def test_the_daily_spend_counts_what_is_not_counted_elsewhere(tmp_path: Path) -> None:
    """My decision of 2026-11-10: card payments outside a series, e-Transfers to other people,
    withdrawals and the payments of a series that is only detected are spending; rent (a series
    with a rent rule), a confirmed series and transfers between my own accounts are not."""
    c = connect(tmp_path / "s.db")
    window(c)  # $1 on the first and on the last day of 90
    # my card, but not a series: spending ($400: below the $500 floor of a big one-off payment)
    insert(c, days_back(60), -40_000, "Visa Card Payment", kind="bill payment", flow="transfer")
    mark_own_account(c, "Visa Card", when=LONG_AGO)
    insert(c, days_back(40), -9_000, "Free Interac E-Transfer ", kind="withdrawal", flow="transfer")
    insert(c, days_back(30), -20_000, "Withdrawal", kind="withdrawal", flow="transfer")
    insert(c, days_back(20), -300_000, "Customer Transfer Dr. MB-Transfer To 1234",
           kind="customer transfer dr.", flow="transfer")  # fmt: skip
    for m in range(1, 7):  # rent with a rule; a gym series that stays only detected
        insert(c, date(2026, m, 2), -90_000, "Free Interac E-Transfer ", kind="withdrawal",
               flow="transfer")  # fmt: skip
        insert(c, date(2026, m, 12), -4_000, "Opos Gym Club", flow="everyday")
    insert(c, LAST, -100, "Opos End", flow="everyday", balance=100_000)
    rent = next(s for s in stored(c) if s.kind == "e-transfer" and s.direction == "out")
    set_rent_rule(c, ACCOUNT, rent, RentRule((90_000,), 1, 5), LONG_AGO)
    confirm(c, ACCOUNT, next(s for s in stored(c) if s.key == rent.key), LONG_AGO)
    series = stored(c)
    assert any(s.name == "Gym Club" and s.status == "detected" for s in series), series
    p = prefill(c, ACCOUNT, series)
    assert p.rent is not None
    gym = sum(4_000 for m in range(4, 7))  # the gym payments inside the 90 days (Apr to Jun)
    counted = 100 + 100 + 100 + 40_000 + 9_000 + 20_000 + gym
    assert p.daily_spend_cents == round(counted / 90)
    assert "card payments, e-Transfers to other people and withdrawals included" in p.spend_basis
    c.close()


def test_a_listed_top_up_and_the_top_ups_field_warn_of_counting_twice() -> None:
    top_up = Flow("Customer Transfer", 50_000, "monthly", date(2024, 1, 10))
    with_series = Prefill(LAST, 0, (), None, None, 0, 20_000, (), own_account_flows=(top_up,))
    without = Prefill(LAST, 0, (), None, None, 0, 20_000, ())
    assert counts_top_up_twice(with_series, 30_000)
    assert not counts_top_up_twice(with_series, 0)
    assert not counts_top_up_twice(without, 30_000)


def test_rent_with_a_rule_on_a_series_not_yet_confirmed_is_in_the_rent_field(
    tmp_path: Path,
) -> None:
    """Rent left out of the daily spend must be carried by the rent field, confirmed or not."""
    c = connect(tmp_path / "s.db")
    window(c)
    for m in range(1, 7):
        insert(c, date(2026, m, 2), -90_000, "Free Interac E-Transfer ", kind="withdrawal",
               flow="transfer")  # fmt: skip
    rent = next(s for s in stored(c) if s.kind == "e-transfer" and s.direction == "out")
    set_rent_rule(c, ACCOUNT, rent, RentRule((90_000,), 1, 5), LONG_AGO)
    series = stored(c)
    assert next(s for s in series if s.key == rent.key).status == "detected"
    p = prefill(c, ACCOUNT, series)
    assert p.rent is not None and p.rent.cents == -90_000
    assert p.daily_spend_cents == round(200 / 90)  # the two $1 purchases only
    c.close()


def test_confirmed_series_are_left_out_of_the_daily_spend_even_once_ended(tmp_path: Path) -> None:
    """A confirmed gym (still going) and a confirmed streaming plan that ended in April: neither
    counts as daily spend; the ended one is not listed either, so it is not carried on."""
    c = connect(tmp_path / "s.db")
    window(c)
    for m in range(1, 7):
        insert(c, date(2026, m, 12), -4_000, "Opos Gym Club", flow="everyday")
    for m in range(1, 5):
        insert(c, date(2026, m, 8), -1_500, "Opos Stream Plus", flow="everyday")
    for s in stored(c):
        confirm(c, ACCOUNT, s, LONG_AGO)
    series = stored(c)
    stream = next(s for s in series if s.name == "Stream Plus")
    assert stream.status == "confirmed" and not stream.active
    p = prefill(c, ACCOUNT, series)
    assert [f.cents for f in p.flows] == [-4_000]  # the gym is listed, the ended plan is not
    assert p.daily_spend_cents == round(200 / 90)  # neither series is daily spend
    c.close()


# --- The two open points of 2026-11-10, my decisions of 2026-11-17 ---


def moved(c: sqlite3.Connection) -> tuple[str, str]:
    """Rent by e-Transfer to an old landlord January to April ($900, ended), then to a new one from
    March 30 ($1,200, still going); both with a rent rule. Returns (old key, new key)."""
    window(c)
    for m in range(1, 5):
        insert(c, date(2026, m, 2), -90_000, "Free Interac E-Transfer ", kind="withdrawal",
               flow="transfer")  # fmt: skip
    for d in (date(2026, 3, 30), date(2026, 4, 30), date(2026, 5, 30), date(2026, 6, 29)):
        insert(c, d, -120_000, "Free Interac E-Transfer ", kind="withdrawal", flow="transfer")
    rents = [s for s in stored(c) if s.kind == "e-transfer" and s.direction == "out"]
    old = next(s for s in rents if s.expected_cents == -90_000)
    new = next(s for s in rents if s.expected_cents == -120_000)
    set_rent_rule(c, ACCOUNT, old, RentRule((90_000,), 1, 5), LONG_AGO)
    set_rent_rule(c, ACCOUNT, new, RentRule((120_000,), 28, 3), LONG_AGO)
    return old.key, new.key


def test_an_ended_rent_series_is_dropped_when_another_rent_series_is_active(
    tmp_path: Path,
) -> None:
    """My decision (1) of 2026-11-17: after a move, the old rent is neither the rent field nor a
    listed flow; its payments stay out of the daily spend."""
    c = connect(tmp_path / "s.db")
    old_key, new_key = moved(c)
    series = stored(c)
    by_key = {s.key: s for s in series}
    assert not by_key[old_key].active and by_key[new_key].active
    p = prefill(c, ACCOUNT, series)
    assert p.rent is not None and (p.rent.cents, p.rent.key) == (-120_000, new_key)
    assert all(f.key != old_key for f in p.flows)
    assert p.daily_spend_cents == round(200 / 90)  # the two $1 purchases only
    c.close()


def test_an_ended_rent_series_stays_the_rent_when_no_rent_series_is_active(
    tmp_path: Path,
) -> None:
    """With no active rent series, the most recently paid one still fills the rent field, as
    before (the page lets me switch it off)."""
    c = connect(tmp_path / "s.db")
    window(c)
    for m in range(1, 5):
        insert(c, date(2026, m, 2), -90_000, "Free Interac E-Transfer ", kind="withdrawal",
               flow="transfer")  # fmt: skip
    rent = next(s for s in stored(c) if s.kind == "e-transfer" and s.direction == "out")
    set_rent_rule(c, ACCOUNT, rent, RentRule((90_000,), 1, 5), LONG_AGO)
    series = stored(c)
    assert not next(s for s in series if s.key == rent.key).active
    p = prefill(c, ACCOUNT, series)
    assert p.rent is not None and p.rent.cents == -90_000
    c.close()


def test_an_ended_confirmed_pay_series_is_left_out_of_income(tmp_path: Path) -> None:
    """My decision (2) of 2026-11-17, same as spending: the deposits of a confirmed pay series that
    has ended are not income per month, and it is not listed either."""
    c = connect(tmp_path / "s.db")
    window(c)
    day = date(2026, 1, 2)
    while day <= date(2026, 5, 8):  # pay every two weeks to May 8 (three inside the 90 days)
        insert(c, day, 150_000, "Payroll Dep ", kind="deposit", flow="income")
        day += timedelta(days=14)
    confirm_all(c)
    series = stored(c)
    pay = next(s for s in series if s.direction == "in")
    assert pay.status == "confirmed" and not pay.active
    p = prefill(c, ACCOUNT, series)
    assert p.income is None and "income" in p.missing
    assert "No deposits counted as income" in p.income_basis
    assert all(f.cents < 0 for f in p.flows)
    c.close()


def test_the_notes_count_each_bucket_of_the_window(tmp_path: Path) -> None:
    """The counts shown under Daily spend and Income (my real-data check of 2026-11-17)."""
    c = connect(tmp_path / "s.db")
    window(c)  # two $1 purchases
    insert(c, days_back(60), -9_000, "Free Interac E-Transfer ", kind="withdrawal", flow="transfer")
    insert(c, days_back(20), -300_000, "Customer Transfer Dr. MB-Transfer To 1234",
           kind="customer transfer dr.", flow="transfer")  # fmt: skip
    insert(c, days_back(19), 50_000, "Customer Transfer Cr. MB-Transfer From 1234",
           kind="customer transfer cr.", flow="transfer")  # fmt: skip
    insert(c, days_back(10), 20_000, "Free Interac E-Transfer ", kind="deposit", flow="income")
    p = prefill(c, ACCOUNT, stored(c))
    spend = dict(p.spend_counts)
    assert p.window_days == 90
    assert spend["payments counted"] == 3  # the two $1 purchases and the e-Transfer
    assert spend["transfers between your own accounts"] == 1
    assert spend["rent"] == spend["refunded pairs"] == spend["confirmed recurring payments"] == 0
    income = dict(p.income_counts)
    assert income["deposits counted"] == 1
    assert income["transfers between your own accounts"] == 1
    c.close()


def test_a_big_one_off_payment_is_left_out_of_the_daily_spend_and_counted_in_the_note(
    tmp_path: Path,
) -> None:
    """My real-data check of 2026-11-17: one bill payment typed recurring outflow, in no series,
    was 72% of the counted daily spend. By the shared one-time money rule it is left out, and
    the note counts it."""
    c = connect(tmp_path / "s.db")
    window(c)
    for k in range(2, 80, 3):  # small purchases every third day
        insert(c, days_back(k), -2_500, "Opos Grocer", flow="everyday")
    insert(c, days_back(45), -480_000, "Tax Office", kind="bill payment",
           flow="recurring_outflow")  # fmt: skip
    p = prefill(c, ACCOUNT, stored(c))
    small = 2 * 100 + len(range(2, 80, 3)) * 2_500
    assert p.daily_spend_cents == round(small / 90)
    assert dict(p.spend_counts)["big one-off payments"] == 1
    assert "big one-off payment" in p.spend_basis
    c.close()


def test_a_monthly_bill_is_not_a_one_off_payment(conn: sqlite3.Connection) -> None:
    """A payment in a detected series (here rent by e-Transfer and the phone bill, neither
    confirmed) is not set aside: it stays counted as before."""
    p = prefill(conn, ACCOUNT, stored(conn))
    assert dict(p.spend_counts)["big one-off payments"] == 0


def test_the_spend_note_gives_the_average_without_the_10_biggest_payments(tmp_path: Path) -> None:
    """My second real-data check of 2026-11-17: a line to judge the daily spend by; the default
    stays the full average."""
    c = connect(tmp_path / "s.db")
    window(c)  # two $1 purchases
    for k in range(2, 80, 3):  # 26 purchases of $25
        insert(c, days_back(k), -2_500, "Opos Grocer", flow="everyday")
    for k in range(1, 11):  # 10 purchases of $300, under the big one-off floor
        insert(c, days_back(5 * k), -30_000, "Opos Store", flow="everyday")
    p = prefill(c, ACCOUNT, stored(c))
    assert p.daily_spend_cents == round((200 + 26 * 2_500 + 10 * 30_000) / 90)
    assert p.spend_without_largest_cents == round((200 + 26 * 2_500) / 90)
    c.close()


def test_with_10_or_fewer_payments_there_is_no_line_without_the_biggest(tmp_path: Path) -> None:
    c = connect(tmp_path / "s.db")
    window(c)  # two $1 purchases only
    assert prefill(c, ACCOUNT, stored(c)).spend_without_largest_cents is None
    c.close()


def test_top_ups_from_savings_do_not_hide_a_lump_sum_from_the_one_off_rule(tmp_path: Path) -> None:
    """My nav-rework check of 2026-11-17: income per month about 3.3k where about 130 was right.
    Irregular top-ups from savings (never income) raised the typical deposit of the shared rule,
    so a family lump sum was no longer 5 times it and was counted as income."""
    c = connect(tmp_path / "s.db")
    window(c)
    for k in (80, 50, 20):  # small family e-Transfers
        insert(c, days_back(k), 15_000, "Free Interac E-Transfer Mom", kind="deposit",
               flow="income")  # fmt: skip
    insert(c, days_back(30), 800_000, "Free Interac E-Transfer Mom", kind="deposit", flow="income")
    gaps = (2, 9, 13, 24, 31, 33, 45, 52, 61, 66, 74, 85)  # irregular: no series is detected
    sizes = (180_000, 250_000, 120_000, 300_000, 90_000, 220_000, 160_000, 275_000, 140_000,
             210_000, 190_000, 230_000)  # fmt: skip
    for k, cents in zip(gaps, sizes, strict=True):
        insert(c, days_back(k), cents, "Customer Transfer Cr. MB-Transfer From 1234",
               kind="customer transfer cr.", flow="transfer")  # fmt: skip
    p = prefill(c, ACCOUNT, stored(c))
    assert dict(p.income_counts)["one-off deposits (big lump sums)"] == 1
    assert p.income is not None and p.income.cents == round(3 * 15_000 * (365.25 / 12) / 90)
    c.close()
