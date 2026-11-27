"""The Recurring page's pieces: series cards, the payments table, the monthly totals.
Presentation only: made-up series, no database."""

from datetime import date

from app.theme import (
    COLORS,
    monthly_totals,
    payments_table_html,
    series_card_html,
    series_summary,
)
from lookahead.recurring_store import Stored


def make(**changes: object) -> Stored:
    fields: dict[str, object] = {
        "key": "k1", "name": "Spotify", "kind": "merchant", "direction": "out",
        "cadence": "monthly", "anchor": date(2026, 9, 20), "anchor_day": 20,
        "amount_type": "fixed", "expected_cents": -1299, "range_cents": (-1299, -1299),
        "next_expected": date(2026, 10, 20), "status": "detected", "active": True,
        "last_paid": date(2026, 9, 20),
    }  # fmt: skip
    return Stored(**{**fields, **changes})  # type: ignore[arg-type]


# --- Monthly totals ("about $1,090 out and $1,590 in each month") ---


def test_monthly_totals_turn_every_cadence_into_a_month_and_round_to_ten_dollars() -> None:
    series = [
        make(cadence="monthly", expected_cents=-75000),
        make(cadence="weekly", expected_cents=-828),  # 828 * 52 / 12 = 3588 a month
        make(cadence="biweekly", direction="in", expected_cents=79500),  # 79500 * 26 / 12
        make(cadence="semimonthly", direction="in", expected_cents=10000),
    ]
    out, money_in = monthly_totals(series)
    assert out == 79000  # 75000 + 3588 = 78588 cents, to the nearest $10
    assert money_in == 192000  # 79500 * 26 / 12 + 2 * 10000 = 192250 cents


def test_monthly_totals_leave_out_ended_and_rejected_series() -> None:
    series = [
        make(expected_cents=-5000),
        make(expected_cents=-9000, active=False),
        make(expected_cents=-7000, status="rejected"),
    ]
    assert monthly_totals(series) == (5000, 0)


# --- The line under a series' name ---


def test_a_fixed_merchant_series_says_same_amount() -> None:
    assert series_summary(make()) == "Same amount every month"
    assert series_summary(make(cadence="biweekly")) == "Same amount every 2 weeks"
    assert series_summary(make(cadence="semimonthly")) == "Same amount twice a month"


def test_a_variable_series_gives_its_usual_range() -> None:
    s = make(amount_type="variable", direction="in", range_cents=(76600, 83100),
             expected_cents=79500)  # fmt: skip
    assert series_summary(s) == "Varies $766–$831"


def test_an_e_transfer_series_says_so_and_a_hand_sent_one_says_by_hand() -> None:
    assert series_summary(make(kind="e-transfer")) == "Interac e-Transfer, same amount every month"
    assert series_summary(make(kind="e-transfer", loose=True)) == (
        "Interac e-Transfer, sent by hand, the day varies"
    )
    rent = make(kind="e-transfer", allowed_cents=(75000,), window=(30, 2))
    assert series_summary(rent) == "Interac e-Transfer, sent by hand, the day varies"


def test_an_ended_series_says_when_it_was_last_seen() -> None:
    assert series_summary(make(active=False, last_paid=date(2026, 8, 17))) == "Last seen Aug 17"
    assert series_summary(make(active=False, last_paid=None)) == "No payment seen lately"


# --- The card ---


def card(**changes: object) -> str:
    return series_card_html(make(**changes), "Spotify", "Subscriptions", selected=False)


def test_a_card_has_tag_category_name_summary_amount_period_and_next_date() -> None:
    html = card()
    for part in ("Detected", "Subscriptions", "Spotify", "Same amount every month", "$12.99",
                 "/ month", "Next Oct 20"):  # fmt: skip
        assert part in html
    assert COLORS.soft_blue in html  # the "Detected" tag


def test_a_confirmed_card_has_the_green_tag_and_money_in_is_green_with_a_plus() -> None:
    assert COLORS.soft_green in card(status="confirmed") and "Confirmed" in card(status="confirmed")
    income = card(direction="in", expected_cents=79500, cadence="biweekly")
    assert "+$795" in income and "/ 2 weeks" in income and COLORS.green_dark in income


def test_an_ended_card_is_faded_dashed_and_not_expected() -> None:
    html = card(active=False, last_paid=date(2026, 8, 17))
    assert "Ended" in html and "Not expected" in html and "Last seen Aug 17" in html
    assert "dashed" in html and COLORS.ended_bg in html
    assert "Next Oct 20" not in html


def test_a_selected_card_has_a_thick_green_border() -> None:
    html = series_card_html(make(), "Spotify", None, selected=True)
    assert f"2px solid {COLORS.green}" in html
    assert f"1px solid {COLORS.border}" in card()


def test_a_card_without_a_category_says_so_and_a_rejected_one_is_marked() -> None:
    assert "No category" in series_card_html(make(), "Spotify", None, selected=False)
    assert "Rejected" in card(status="rejected")
    assert "Added by me" in card(status="user-added")


def test_card_text_is_escaped() -> None:
    html = series_card_html(make(), "<script>alert(1)</script>", "R&D", selected=False)
    assert "<script>" not in html and "&lt;script&gt;" in html and "R&amp;D" in html


# --- The payments table ---


def test_the_payments_table_has_four_headers_and_signed_money() -> None:
    html = payments_table_html([
        ("Oct 1, 2026", "Interac e-Transfer", -75000, 15013),
        ("Sep 1, 2026", "Pay <b>day</b>", 79500, None),
    ])  # fmt: skip
    for header in ("Date", "Description", "Amount", "Balance after"):
        assert header in html
    assert "−$750.00" in html and "$150.13" in html and "+$795.00" in html
    assert COLORS.green_dark in html  # money in is green
    assert "<b>day</b>" not in html and "&lt;b&gt;" in html  # untrusted text is escaped
    assert html.count("<tr") == 3  # the header and two payments


def test_an_empty_payments_table_is_a_sentence() -> None:
    assert "No payments in the data yet" in payments_table_html([])


def test_a_series_label_is_just_its_name_and_the_note_is_in_the_grey_line() -> None:
    from lookahead.ui import series_label

    rent = make(name="Rent", kind="e-transfer", loose=True)
    assert series_label(rent) == "Rent"  # no "(sent by hand, the day varies)" after the name
    assert series_summary(rent) == "Interac e-Transfer, sent by hand, the day varies"
    assert series_summary(make(kind="e-transfer", allowed_cents=(75000,), window=(30, 2))) == (
        "Interac e-Transfer, sent by hand, the day varies"
    )


def test_the_next_date_never_breaks_inside_and_moves_down_whole_on_a_narrow_card() -> None:
    card = series_card_html(make(next_expected=date(2026, 10, 9)), "Boutique", "Income", False)
    span = card.split('class="la-next"', 1)[1].split(">", 1)[0]
    assert "white-space: nowrap" in span
    assert "Next Oct 9</span>" in card
    assert "flex-wrap: wrap" in card  # the row it sits in may wrap instead
