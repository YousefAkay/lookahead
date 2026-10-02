"""Tests for step 3a-i: calendar-aware detection of recurring payments. Made-up rows only.

Written before the detector. Dates are 2026 and 2027; holidays come from calendar_rules.
"""

from datetime import date, timedelta

import pytest

from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.flows import Txn
from lookahead.recurring import (
    Series,
    business_days_apart,
    detect,
    expected_dates,
    flow_overrides,
)

CARD = "Opos {:<20}toron "
KEY = bytes.fromhex("ab" * 32)


class Rows:
    """Builds made-up transactions with increasing ids."""

    def __init__(self) -> None:
        self.txns: list[Txn] = []

    def add(self, day: date, cents: int, kind: str, text: str) -> int:
        self.txns.append(Txn(len(self.txns) + 1, day, cents, kind, text))
        return len(self.txns)

    def card(self, day: date, cents: int, name: str) -> int:
        return self.add(day, cents, "pos purchase", CARD.format(name))

    def bill(self, day: date, cents: int, payee: str) -> int:
        return self.add(day, cents, "bill payment", f"{payee} ")

    def etransfer(self, day: date, cents: int, to: str = "") -> int:
        return self.add(day, cents, "withdrawal", f"Free Interac E-Transfer {to}".rstrip() + " ")


def monthly(rows: Rows, name: str, day: int, months: range, cents: int = -1599, year: int = 2026,
            bill: bool = False) -> list[int]:  # fmt: skip
    ids = []
    for m in months:
        due = monthly_date(year, m, day)
        if bill:
            ids.append(rows.bill(shift_to_business_day(due, "later"), cents, name))
        else:
            ids.append(rows.card(shift_to_business_day(due, "later"), cents, name))
    return ids


def only(series: list[Series]) -> Series:
    assert len(series) == 1, series
    return series[0]


# --- Calendar helpers ---


def test_business_days_apart_ignores_weekends_and_holidays() -> None:
    friday, monday = date(2026, 6, 5), date(2026, 6, 8)
    assert business_days_apart(friday, monday) == 1
    assert business_days_apart(monday, friday) == 1
    assert business_days_apart(friday, date(2026, 6, 6)) == 0  # a Saturday card posting
    assert business_days_apart(date(2026, 6, 30), date(2026, 7, 2)) == 1  # Canada Day between


def test_expected_monthly_dates_clamp_to_short_months_and_shift() -> None:
    got = expected_dates("monthly", date(2026, 1, 31), date(2026, 1, 1), date(2026, 5, 31), "out")
    # Jan 31 is a Saturday -> Monday Feb 2; Feb 28 is a Saturday -> Monday Mar 2;
    # Mar 31 a Tuesday; Apr 30 a Thursday; May 31 a Sunday -> Monday Jun 1.
    assert got == [date(2026, 2, 2), date(2026, 3, 2), date(2026, 3, 31), date(2026, 4, 30),
                   date(2026, 6, 1)]  # fmt: skip


def test_expected_income_dates_shift_earlier() -> None:
    got = expected_dates("biweekly", date(2026, 6, 26), date(2026, 6, 1), date(2026, 7, 31), "in")
    assert got == [date(2026, 6, 12), date(2026, 6, 26), date(2026, 7, 10), date(2026, 7, 24)]
    yearly = expected_dates("yearly", date(2026, 7, 1), date(2026, 1, 1), date(2027, 12, 31), "in")
    assert yearly == [date(2026, 6, 30), date(2027, 6, 30)]  # Canada Day -> the day before


# --- Cadences ---


def test_a_monthly_subscription_is_found_with_its_day_and_amount() -> None:
    rows = Rows()
    ids = monthly(rows, "Netflix.Com", 12, range(1, 7))
    s = only(detect(rows.txns, as_of=date(2026, 6, 20)))
    assert (s.cadence, s.anchor_day, s.direction) == ("monthly", 12, "out")
    assert s.amount_type == "fixed" and s.expected_cents == -1599
    assert s.rows == tuple(ids) and s.active and s.kind == "merchant"
    assert s.next_expected == date(2026, 7, 13)  # July 12 is a Sunday


def test_a_bill_on_the_31st_survives_short_months() -> None:
    rows = Rows()
    monthly(rows, "Hydro-Quebec", 31, range(1, 7), bill=True)
    s = only(detect(rows.txns, as_of=date(2026, 7, 5)))
    assert (s.cadence, s.anchor_day) == ("monthly", 31) and len(s.rows) == 6


def test_biweekly_pay_shifted_earlier_for_a_holiday() -> None:
    rows = Rows()
    day = date(2026, 5, 22)
    ids = []
    while day <= date(2026, 9, 30):
        ids.append(rows.add(shift_to_business_day(day, "earlier"), 80000, "deposit", "Acme Inc "))
        day += timedelta(days=14)
    s = only(detect(rows.txns, as_of=date(2026, 9, 30)))
    assert (s.cadence, s.direction, len(s.rows)) == ("biweekly", "in", len(ids))
    assert s.next_expected == date(2026, 10, 9)


def test_weekly_and_semimonthly_and_yearly() -> None:
    rows = Rows()
    for k in range(8):
        rows.card(date(2026, 3, 4) + timedelta(weeks=k), -1200, "Yoga Studio")
    for m in range(1, 5):
        for day in (15, 31):
            due = shift_to_business_day(monthly_date(2026, m, day), "earlier")
            rows.add(due, 150000, "deposit", "Globex Corp ")
    rows.card(date(2025, 9, 10), -9900, "Domain Registrar")
    rows.card(date(2026, 9, 10), -9900, "Domain Registrar")
    found = {s.merchant: s for s in detect(rows.txns, as_of=date(2026, 9, 30))}
    assert found["Yoga Studio"].cadence == "weekly"
    assert found["Globex Corp"].cadence == "semimonthly" and len(found["Globex Corp"].rows) == 8
    assert found["Domain Registrar"].cadence == "yearly"
    assert found["Domain Registrar"].next_expected == date(2027, 9, 10)


def test_fewer_than_three_payments_is_not_a_series_yet() -> None:
    rows = Rows()
    monthly(rows, "Netflix.Com", 12, range(1, 3))
    assert detect(rows.txns, as_of=date(2026, 3, 1)) == []
    monthly(rows, "Netflix.Com", 12, range(3, 4))
    assert len(detect(rows.txns, as_of=date(2026, 3, 20))) == 1


def test_one_skipped_month_does_not_break_a_series() -> None:
    rows = Rows()
    monthly(rows, "Gym Club", 5, [1, 2, 3, 5, 6, 7])
    s = only(detect(rows.txns, as_of=date(2026, 7, 20)))
    assert s.cadence == "monthly" and len(s.rows) == 6


def test_a_payment_a_few_business_days_late_still_matches() -> None:
    rows = Rows()
    ids = monthly(rows, "Phone Co", 10, range(1, 6), cents=-6500, bill=True)
    late = rows.bill(date(2026, 6, 15), -6500, "Phone Co")  # due Wed Jun 10, 3 business days late
    s = only(detect(rows.txns, as_of=date(2026, 6, 30)))
    assert s.rows == (*ids, late)
    way_late = rows.bill(date(2026, 7, 24), -6500, "Phone Co")  # 10 business days late
    assert way_late not in only(detect(rows.txns, as_of=date(2026, 7, 30))).rows


# --- What is not a series ---


def test_everyday_purchases_at_one_merchant_are_not_a_series() -> None:
    rows = Rows()
    day = date(2026, 3, 2)
    while day <= date(2026, 6, 30):  # the same coffee most weekdays
        if day.weekday() < 5 and day.day % 4:
            rows.card(day, -325, "Cafe Velo")
        day += timedelta(days=1)
    assert detect(rows.txns, as_of=date(2026, 6, 30)) == []


def test_a_subscription_among_other_purchases_at_the_same_merchant() -> None:
    rows = Rows()
    prime = monthly(rows, "Amazon.Ca", 8, range(1, 8), cents=-999)
    for k, cents in enumerate([-2350, -4710, -1288, -6040, -3115]):
        rows.card(date(2026, 2, 3) + timedelta(days=23 * k), cents, "Amazon.Ca")
    s = only(detect(rows.txns, as_of=date(2026, 7, 20)))
    assert s.rows == tuple(prime) and s.expected_cents == -999


def test_fees_refunds_and_netted_rows_are_left_out() -> None:
    rows = Rows()
    for m in range(1, 7):  # a fee that happens to post every month: tied to events, not dates
        rows.add(date(2026, m, 26), -1500, "service charge", " ")
        rows.add(date(2026, m, 26), -4, "overdraft interest", " ")
    for m in range(1, 5):  # a hold and its same-day correction, every month: nets to zero
        rows.card(date(2026, m, 9), -2000, "Uber Canada/Ubertrip")
        rows.add(date(2026, m, 9), 2000, "correction", CARD.format("Uber Canada/Ubertrip"))
    assert detect(rows.txns, as_of=date(2026, 7, 1)) == []


# --- Amounts ---


def test_small_differences_are_still_a_fixed_amount() -> None:
    rows = Rows()
    for m, cents in zip(range(1, 7), [-5000, -5000, -5100, -5000, -4950, -5000], strict=True):
        rows.bill(shift_to_business_day(date(2026, m, 6), "later"), cents, "Internet Co")
    s = only(detect(rows.txns, as_of=date(2026, 6, 30)))
    assert s.amount_type == "fixed" and s.expected_cents == -5000  # within 3% of the median
    assert s.range_cents == (-5000, -5000)


def test_a_variable_bill_gets_a_median_and_a_normal_range() -> None:
    rows = Rows()
    amounts = [-6200, -7400, -9100, -8300, -5600, -6900, -7700, -8800]
    for m, cents in zip(range(1, 9), amounts, strict=True):
        rows.bill(shift_to_business_day(date(2026, m, 20), "later"), cents, "Hydro-Quebec")
    s = only(detect(rows.txns, as_of=date(2026, 8, 31)))
    assert s.amount_type == "variable" and s.expected_cents == -7550  # the median
    low, high = s.range_cents  # 10th to 90th percentile of the size, as signed amounts
    assert low == -8890 and high == -6020


def test_a_variable_range_is_at_least_ten_percent_with_few_payments() -> None:
    rows = Rows()
    for m, cents in zip(range(1, 5), [-7000, -7600, -7100, -7500], strict=True):
        rows.bill(shift_to_business_day(date(2026, m, 20), "later"), cents, "Hydro-Quebec")
    s = only(detect(rows.txns, as_of=date(2026, 4, 30)))
    assert s.amount_type == "variable" and s.expected_cents == -7300
    assert s.range_cents == (-8030, -6570)  # median +/- 10%: wider than the 4 values seen


# --- e-Transfers: found by amount and date, never rent on their own ---


def test_same_amount_monthly_etransfers_are_a_candidate() -> None:
    rows = Rows()
    rent = [rows.etransfer(shift_to_business_day(date(2026, m, 1), "later"), -75000)
            for m in range(1, 7)]  # fmt: skip
    for k, cents in enumerate([-2000, -4500, -1500, -6000]):
        rows.etransfer(date(2026, 1, 14) + timedelta(days=31 * k), cents)
    s = only(detect(rows.txns, as_of=date(2026, 6, 20)))
    assert s.kind == "e-transfer" and s.merchant is None and s.rows == tuple(rent)
    assert (s.cadence, s.anchor_day, s.expected_cents) == ("monthly", 1, -75000)


def test_etransfers_to_different_recipients_are_separate_groups() -> None:
    rows = Rows()
    for m in range(1, 7):  # alternating recipients: neither has a monthly series of its own
        to = "Alex Roy" if m % 2 else "Sam Lee"
        rows.etransfer(shift_to_business_day(date(2026, m, 1), "later"), -75000, to)
    assert detect(rows.txns, as_of=date(2026, 6, 20), key=KEY) == []
    for m in range(1, 7):
        rows.etransfer(shift_to_business_day(date(2026, m, 15), "later"), -30000, "Sam Lee")
    s = only(detect(rows.txns, as_of=date(2026, 6, 20), key=KEY))
    assert s.kind == "e-transfer" and s.expected_cents == -30000


def test_a_recipient_appears_in_a_key_only_as_the_keyed_token() -> None:
    import hashlib

    from lookahead.pseudonymize import token

    rows = Rows()
    for m in range(1, 7):
        rows.etransfer(shift_to_business_day(date(2026, m, 15), "later"), -30000, "Sam Lee")
    s = only(detect(rows.txns, as_of=date(2026, 6, 20), key=KEY))
    assert s.key == f"e-transfer|out|{token('PERSON', 'sam lee', KEY)}|30000"
    assert "sam" not in s.key.casefold() and "lee" not in s.key.casefold()
    for plain in ("sam lee", "Sam Lee"):  # no unkeyed hash of the name either
        assert hashlib.sha256(plain.encode()).hexdigest()[:12] not in s.key
    other = only(detect(rows.txns, as_of=date(2026, 6, 20), key=bytes(range(32))))
    assert other.key != s.key and other.rows == s.rows  # another key, another token
    # without the key, a named recipient is not grouped at all (never under a plain hash)
    assert detect(rows.txns, as_of=date(2026, 6, 20)) == []


# --- Active or ended, and the next expected date ---


def test_a_series_ends_after_two_expected_dates_without_a_payment() -> None:
    rows = Rows()
    monthly(rows, "Gym Club", 5, range(1, 5))
    assert only(detect(rows.txns, as_of=date(2026, 5, 20))).active  # one missed so far
    s = only(detect(rows.txns, as_of=date(2026, 6, 20)))  # May and June both missed
    assert not s.active and s.next_expected == date(2026, 5, 5)


def test_a_payment_still_inside_its_window_is_not_counted_as_missing() -> None:
    rows = Rows()
    monthly(rows, "Gym Club", 5, range(1, 5))
    s = only(detect(rows.txns, as_of=date(2026, 5, 6)))  # due May 5, tolerance not over
    assert s.active and s.next_expected == date(2026, 5, 5)


# --- Rule zero ---


def test_detection_at_a_date_uses_only_rows_posted_by_then() -> None:
    rows = Rows()
    monthly(rows, "Netflix.Com", 12, range(1, 10))
    monthly(rows, "Gym Club", 5, range(5, 10))  # starts later
    cut = date(2026, 6, 20)
    past = [t for t in rows.txns if t.posted_date <= cut]
    assert detect(rows.txns, as_of=cut) == detect(past, as_of=cut)
    # the merchant is the resolved group: the alias list turns "Netflix.Com" into "Netflix"
    assert {s.merchant for s in detect(rows.txns, as_of=cut)} == {"Netflix"}
    assert {s.merchant for s in detect(rows.txns, as_of=date(2026, 9, 30))} == {
        "Netflix", "Gym Club",
    }  # fmt: skip


# --- How detection changes flow types (carried over from week 2) ---


def test_a_card_subscription_becomes_a_recurring_outflow() -> None:
    rows = Rows()
    ids = monthly(rows, "Netflix.Com", 12, range(1, 7))
    other = rows.card(date(2026, 3, 3), -2500, "Bookshop")
    got = flow_overrides(rows.txns, detect(rows.txns, as_of=date(2026, 6, 30)))
    assert {i: got[i].flow_type for i in ids} == dict.fromkeys(ids, "recurring_outflow")
    assert other not in got


def test_a_bill_payment_that_never_repeats_is_a_one_off() -> None:
    rows = Rows()
    monthly(rows, "Phone Co", 10, range(1, 7), bill=True)
    single = rows.bill(date(2026, 4, 17), -120000, "City Of Montreal")
    twice = [rows.bill(date(2026, 5, 4), -9000, "Dentist"),
             rows.bill(date(2026, 6, 4), -9000, "Dentist")]  # fmt: skip
    got = flow_overrides(rows.txns, detect(rows.txns, as_of=date(2026, 6, 30)))
    assert got[single].one_off and got[single].flow_type == "everyday"
    assert twice[0] not in got and twice[1] not in got  # two payments: not decided yet


def test_etransfer_candidates_change_nothing_until_confirmed() -> None:
    rows = Rows()
    rent = [rows.etransfer(shift_to_business_day(date(2026, m, 1), "later"), -75000)
            for m in range(1, 7)]  # fmt: skip
    series = detect(rows.txns, as_of=date(2026, 6, 20))
    assert not set(rent) & set(flow_overrides(rows.txns, series))
    confirmed = flow_overrides(rows.txns, series, confirmed={series[0].key})
    assert {confirmed[i].flow_type for i in rent} == {"recurring_outflow"}


def test_unknown_cadence_is_refused() -> None:
    with pytest.raises(ValueError, match="cadence"):
        expected_dates("daily", date(2026, 1, 1), date(2026, 1, 1), date(2026, 2, 1), "out")


# --- Habits are not schedules (found on the synthetic personas: false series) ---


def test_a_weekly_shopping_habit_with_changing_amounts_is_not_a_series() -> None:
    rows = Rows()
    amounts = [-4210, -6830, -3975, -8120, -5540, -7260, -4480, -6015, -9050, -3690]
    for k, cents in enumerate(amounts):  # every Saturday, a different basket
        rows.card(date(2026, 3, 7) + timedelta(weeks=k), cents, "Maxi")
    assert detect(rows.txns, as_of=date(2026, 5, 31)) == []


def test_three_same_price_purchases_that_line_up_are_not_a_series() -> None:
    rows = Rows()
    for day in (date(2026, 3, 3), date(2026, 3, 17), date(2026, 3, 31)):  # by chance biweekly
        rows.card(day, -475, "Cafe Velo")
    for k, cents in enumerate([-890, -1250, -640, -1525, -710, -980, -1130, -560]):
        rows.card(date(2026, 3, 5) + timedelta(days=9 * k), cents, "Cafe Velo")
    assert detect(rows.txns, as_of=date(2026, 5, 31)) == []


def test_a_series_among_other_rows_needs_four_payments() -> None:
    rows = Rows()
    monthly(rows, "Amazon.Ca", 8, range(1, 4), cents=-999)
    for k, cents in enumerate([-2350, -4710, -1288, -6040]):
        rows.card(date(2026, 1, 20) + timedelta(days=17 * k), cents, "Amazon.Ca")
    assert detect(rows.txns, as_of=date(2026, 3, 31)) == []
    monthly(rows, "Amazon.Ca", 8, range(4, 5), cents=-999)
    assert only(detect(rows.txns, as_of=date(2026, 4, 30))).expected_cents == -999


def test_a_foreign_currency_subscription_may_vary_a_little() -> None:
    rows = Rows()
    for m, cents in zip(range(1, 7), [-2840, -2700, -2990, -2760, -2880, -3010], strict=True):
        rows.card(shift_to_business_day(date(2026, m, 18), "later"), cents, "Cloud Tools")
    s = only(detect(rows.txns, as_of=date(2026, 6, 30)))  # the exchange rate moves the amount
    assert s.cadence == "monthly" and s.amount_type == "variable"


def test_a_card_charge_with_loose_timing_is_not_a_series() -> None:
    rows = Rows()
    for day in (date(2026, 1, 12), date(2026, 2, 9), date(2026, 3, 16), date(2026, 4, 14),
                date(2026, 5, 8), date(2026, 6, 15)):  # fmt: skip
        rows.card(day, -2500, "Barber Shop")  # about monthly, same price, but when I feel like it
    assert detect(rows.txns, as_of=date(2026, 6, 30)) == []


def test_a_regular_transfer_to_my_own_savings_is_found_by_amount() -> None:
    rows = Rows()
    saving = [rows.add(shift_to_business_day(date(2026, m, 3), "later"), -20000,
                       "customer transfer dr.", "Mb-Transfer ") for m in range(1, 6)]  # fmt: skip
    rows.add(date(2026, 2, 19), -5000, "customer transfer dr.", "Mb-Transfer ")
    rows.add(date(2026, 4, 22), -12500, "customer transfer dr.", "Mb-Transfer ")
    s = only(detect(rows.txns, as_of=date(2026, 5, 31)))
    assert s.kind == "own transfer" and s.rows == tuple(saving) and s.merchant is None
    assert (s.cadence, s.anchor_day, s.expected_cents) == ("monthly", 3, -20000)
    # it already is a transfer: detection does not change its flow type
    assert not set(saving) & set(flow_overrides(rows.txns, [s]))


# --- One price change keeps the series (so "amount changed" can fire) ---


def test_a_subscription_with_one_price_increase_stays_one_series() -> None:
    rows = Rows()
    old = monthly(rows, "Netflix.Com", 12, range(1, 9), cents=-1599)
    new = monthly(rows, "Netflix.Com", 12, range(9, 12), cents=-1839)  # +15% from September
    s = only(detect(rows.txns, as_of=date(2026, 11, 30)))
    assert s.rows == (*old, *new) and s.amount_type == "fixed"
    assert (s.expected_cents, s.previous_cents, s.changed_row) == (-1839, -1599, new[0])
    assert s.range_cents == (-1839, -1839)


def test_the_price_change_is_visible_from_its_first_payment() -> None:
    rows = Rows()
    old = monthly(rows, "Netflix.Com", 12, range(1, 9), cents=-1599)
    new = monthly(rows, "Netflix.Com", 12, range(9, 10), cents=-1839)
    s = only(detect(rows.txns, as_of=date(2026, 9, 30)))
    assert s.rows == (*old, *new)
    assert (s.expected_cents, s.previous_cents, s.changed_row) == (-1839, -1599, new[0])
    before = only(detect(rows.txns, as_of=date(2026, 8, 31)))  # rule zero: not known earlier
    assert (before.expected_cents, before.previous_cents, before.changed_row) == (-1599, None, None)


def test_amounts_that_keep_moving_are_not_a_price_change() -> None:
    rows = Rows()
    for m, cents in zip(range(1, 9), [-1500, -1900, -1450, -2100, -1600, -2300, -1700, -1950],
                        strict=True):  # fmt: skip
        rows.card(shift_to_business_day(date(2026, m, 12), "later"), cents, "Corner Store")
    assert detect(rows.txns, as_of=date(2026, 8, 31)) == []  # a card habit, not a bill


def test_a_fixed_bill_with_one_price_change_is_still_fixed() -> None:
    rows = Rows()
    monthly(rows, "Phone Co", 10, range(1, 7), cents=-6500, bill=True)
    monthly(rows, "Phone Co", 10, range(7, 10), cents=-7200, bill=True)
    s = only(detect(rows.txns, as_of=date(2026, 9, 30)))
    assert s.amount_type == "fixed" and (s.expected_cents, s.previous_cents) == (-7200, -6500)


# --- Card rows post Monday to Saturday (found by the synthetic evaluation) ---


def test_a_card_subscription_due_on_a_saturday_posts_that_saturday() -> None:
    from lookahead.calendar_rules import next_card_posting_day

    rows = Rows()
    for m in range(6, 10):  # due the 20th: a Saturday in June, a Sunday in September
        rows.card(next_card_posting_day(date(2026, m, 20)), -1299, "Music Box")
    s = only(detect(rows.txns, as_of=date(2026, 9, 30)))
    assert (s.cadence, s.anchor_day) == ("monthly", 20)  # not the 19th
    assert s.next_expected == date(2026, 10, 20)
    card = expected_dates("monthly", date(2026, 6, 20), date(2026, 6, 1), date(2026, 9, 30),
                          "out", card=True)  # fmt: skip
    assert card == [date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20), date(2026, 9, 21)]


# --- Evaluation against the generator's schedules, and the counts-only report ---


def test_evaluation_counts_planted_series_from_the_generator_not_from_the_rules() -> None:
    from eval.recurring import evaluate

    r = evaluate("B", months=4)
    assert (r.planted, r.planted_fixed) == (6, 6)
    assert r.found_fixed == 6 and r.cadence_right == 6 and r.true <= r.detected
    assert r.rows_right <= r.rows_detected and r.rows_right <= r.rows_planted_fixed
    c = evaluate("C", months=4)
    assert (c.planted, c.planted_fixed) == (5, 3)
    assert c.missed == {"irregular (not targeted)": 2}  # by choice: I add those by hand


def test_evaluation_command_prints_the_caveat(capsys: pytest.CaptureFixture[str]) -> None:
    from eval.recurring import main as eval_main

    assert eval_main(["--months", "4"]) == 0
    out = capsys.readouterr().out
    assert "PIPELINE CHECK, NOT EVIDENCE" in out and "all 3 personas" in out
    assert "precision" in out and "recall" in out


def test_report_prints_counts_only(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime

    from lookahead.db import connect
    from lookahead.generator.personas import PERSONAS
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports
    from lookahead.recurring import main

    export, _ = write_persona(PERSONAS["B"], tmp_path / "s", 42, date(2026, 6, 1),
                              date(2026, 9, 30))  # fmt: skip
    conn = connect(tmp_path / "t.db")
    store_exports(conn, "scotia-chequing", [(export.name, parse_export(export))],
                  datetime(2026, 10, 2))  # fmt: skip
    conn.commit()
    conn.close()
    args = ["report", "--db", str(tmp_path / "t.db"), "--env", str(tmp_path / "none.env")]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "series detected: 6" in out and "semimonthly 1" in out and "own transfer 1" in out
    assert "$" not in out
    for text in ("Netflix", "Videotron", "Hydro", "Globex", "1150", "115000", "PERSON_"):
        assert text not in out  # no names, no amounts, no tokens
    assert main(["report", "--db", str(tmp_path / "missing.db")]) == 2


# --- Bill payments: why a payee is or is not a series (counts only) ---


def test_bill_payment_payees_are_explained_without_names_or_amounts() -> None:
    from lookahead.recurring import BillGroup, bill_payment_groups

    rows = Rows()
    monthly(rows, "Phone Co", 10, range(1, 8), cents=-6500, bill=True)  # a series
    rows.bill(date(2026, 4, 17), -120000, "City Of Montreal")  # once
    rows.bill(date(2026, 5, 4), -9000, "Dentist")
    rows.bill(date(2026, 6, 4), -9000, "Dentist")  # twice
    for day in (date(2026, 2, 3), date(2026, 5, 20), date(2026, 9, 8), date(2026, 9, 25)):
        rows.bill(day, -250000, "University")  # four payments, no calendar pattern
    rows.card(date(2026, 3, 3), -2500, "Bookshop")  # not a bill payment
    got = bill_payment_groups(rows.txns, as_of=date(2026, 9, 30))
    assert got == [
        BillGroup(7, "is a series", "20-45 days"),
        BillGroup(4, "irregular timing", "46-120 days"),
        BillGroup(2, "too few payments", "20-45 days"),
        BillGroup(1, "too few payments", "one payment"),
    ]
    assert all("Phone" not in repr(g) and "65" not in repr(g) for g in got)


# --- Found on real data: a bill-payment series was counted as "card purchases upgraded" ---


def test_only_card_purchases_are_upgraded_a_bill_series_is_left_as_it_is() -> None:
    from lookahead.recurring import count_recurring

    rows = Rows()
    card = monthly(rows, "Music Box", 12, range(1, 7))
    bill = monthly(rows, "Phone Co", 10, range(1, 7), cents=-6500, bill=True)
    series = detect(rows.txns, as_of=date(2026, 6, 30))
    assert {s.merchant for s in series} == {"Music Box", "Phone Co"}
    got = flow_overrides(rows.txns, series)
    assert set(got) == set(card)  # the bill payments already are recurring outflows
    assert not set(bill) & set(got)
    c = count_recurring(rows.txns)
    assert c.series == 2 and c.rows_in_series == 12 and c.upgraded_rows == 6
    assert c.by_payment == {"card": 1, "bill payment": 1}
    assert c.by_kind == {"merchant": 2}  # "merchant" covers card merchants and bill payees


# --- A bill payment to my own card or credit line is a transfer, not spending ---


def test_a_bill_payment_to_my_own_account_is_a_transfer() -> None:
    from lookahead.categories import category_by_rules
    from lookahead.flows import flow_types
    from lookahead.resolve import UserDecisions, name_key

    rows = Rows()
    card_payments = [rows.bill(day, cents, "Big Bank Visa") for day, cents in (
        (date(2026, 2, 3), -41200), (date(2026, 3, 19), -8850), (date(2026, 5, 6), -63010),
        (date(2026, 6, 24), -12000))]  # fmt: skip
    phone = monthly(rows, "Phone Co", 10, range(1, 7), cents=-6500, bill=True)
    before = flow_types(rows.txns)
    assert {before[i].flow_type for i in card_payments} == {"recurring_outflow"}

    mine = UserDecisions(own_payees={name_key("Big Bank Visa")})
    flows = flow_types(rows.txns, mine)
    assert {(flows[i].flow_type, flows[i].reason) for i in card_payments} == {
        ("transfer", "payment to my own account")
    }
    assert {flows[i].flow_type for i in phone} == {"recurring_outflow"}  # other payees: as before
    decided = category_by_rules(rows.txns, mine)
    assert {decided[i].category for i in card_payments} == {"Transfers"}
    # irregular, so no series; and never "upgraded" to a recurring outflow or a one-off
    series = detect(rows.txns, mine, as_of=date(2026, 6, 30))
    assert {s.merchant for s in series} == {"Phone Co"}
    assert not set(card_payments) & set(flow_overrides(rows.txns, series, user=mine))


def test_marking_an_own_account_is_dated_and_can_be_undone(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from lookahead.db import connect
    from lookahead.resolve import load_user_decisions, mark_own_account, name_key

    conn = connect(tmp_path / "t.db")
    key = mark_own_account(conn, "Big Bank  VISA", True, date(2026, 6, 10))
    assert key == name_key("Big Bank Visa")
    assert load_user_decisions(conn).own_payees == {key}
    assert load_user_decisions(conn, as_of=date(2026, 6, 9)).own_payees == set()  # not yet
    mark_own_account(conn, "Big Bank Visa", False, date(2026, 6, 20))
    assert load_user_decisions(conn).own_payees == set()
    assert load_user_decisions(conn, as_of=date(2026, 6, 15)).own_payees == {key}
    with pytest.raises(ValueError):
        mark_own_account(conn, "  ")


def test_own_account_command_prints_counts_only(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:  # type: ignore[no-untyped-def]
    from lookahead.db import connect
    from lookahead.flows import main

    conn = connect(tmp_path / "t.db")
    for n, (day, cents) in enumerate([("2026-02-03", -41200), ("2026-03-19", -8850)]):
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES ('scotia-chequing', ?, ?, 'bill payment', 'Big Bank Visa ', 0, ?, 'f', 'x')",
            (day, cents, f"fp{n}"),
        )
    conn.commit()
    conn.close()
    db = str(tmp_path / "t.db")
    assert main(["own-account", "--db", db, "--payee", "Big Bank Visa"]) == 0
    out = capsys.readouterr().out
    assert "0 rows before, 2 now" in out and "Big Bank" not in out and "412" not in out
    assert main(["own-account", "--db", db, "--payee", "No Such Payee"]) == 1
    assert "nothing changed" in capsys.readouterr().out
    assert main(["own-account", "--db", db, "--payee", "Big Bank Visa", "--undo"]) == 0
    assert "2 rows before, 0 now" in capsys.readouterr().out


# --- Why a regular payment was missed (counts only; found on real data: rent) ---


def test_missed_regular_payments_are_explained_without_names_or_amounts() -> None:
    from lookahead.recurring import AmountGroup, explain_amount_groups

    rows = Rows()
    for m in range(1, 7):  # a clean monthly e-Transfer: a series
        rows.etransfer(shift_to_business_day(date(2026, m, 1), "later"), -75000)
    for day in (date(2026, 1, 28), date(2026, 3, 6), date(2026, 3, 30), date(2026, 5, 7),
                date(2026, 5, 28), date(2026, 7, 8)):  # fmt: skip
        rows.etransfer(day, -42000)  # about monthly, but up to a week off the 1st
    for m in (1, 2, 6, 9, 10, 11):  # the 15th, with long stretches away
        rows.etransfer(shift_to_business_day(date(2026, m, 15), "later"), -30000)
    for m in range(1, 4):  # only three so far
        rows.etransfer(shift_to_business_day(date(2026, m, 20), "later"), -9900)
    for m, cents in zip(range(1, 9), [-60000] * 3 + [-64000] * 3 + [-68500] * 2, strict=True):
        rows.bill(shift_to_business_day(date(2026, m, 25), "later"), cents, "Landlord Inc")
    rows.etransfer(date(2026, 4, 9), -1500)  # a one-off: not listed
    got = explain_amount_groups(rows.txns, as_of=date(2026, 11, 30))
    why = {(g.flow, g.payments): g.why for g in got}
    assert why[("outgoing e-Transfers", 6)] in {
        "is a series", "timing: payments more than 3 business days from any pattern",
        "gaps: too many expected dates without a payment",
    }  # fmt: skip
    assert sorted(g.why for g in got if g.flow == "outgoing e-Transfers") == sorted([
        "is a series",
        "timing: payments more than 3 business days from any pattern",
        "gaps: too many expected dates without a payment",
        "fewer than 4 payments of one amount",
    ])  # fmt: skip
    assert all(isinstance(g, AmountGroup) for g in got)
    assert not any(ch.isdigit() for g in got for ch in g.why.replace("3 business", "")
                   .replace("4 payments", "").replace("10%", ""))  # fmt: skip
    for text in ("Landlord", "750", "420", "300"):
        assert text not in repr(got)


def test_a_named_recipient_without_the_key_is_reported_as_such() -> None:
    from lookahead.recurring import explain_amount_groups

    rows = Rows()
    for m in range(1, 7):
        rows.etransfer(shift_to_business_day(date(2026, m, 1), "later"), -75000, "Alex Roy")
    got = explain_amount_groups(rows.txns, as_of=date(2026, 6, 30))
    assert [g.why for g in got] == ["named recipient, no pseudonym key"]
    with_key = explain_amount_groups(rows.txns, key=KEY, as_of=date(2026, 6, 30))
    assert [g.why for g in with_key] == ["is a series"]
    assert "alex" not in repr(got).casefold()
