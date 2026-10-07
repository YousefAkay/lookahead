"""Pieces for Home and Transactions: link buttons, the status card, the transactions table."""

from app.theme import COLORS, link_button_html, status_card_html, transactions_table_html


def test_a_link_button_is_a_link_that_carries_the_choice() -> None:
    html = link_button_html("Forecast", "/Forecast", "data=Synthetic+demo&account=a", primary=True)
    assert 'href="/Forecast?data=Synthetic+demo&amp;account=a"' in html
    assert ">Forecast<" in html and 'target="_self"' in html
    assert COLORS.topbar in html  # the primary button is the deep green


def test_a_plain_link_button_is_white_with_an_outline_and_needs_no_query() -> None:
    html = link_button_html("Recurring", "/Recurring")
    assert 'href="/Recurring"' in html and "?" not in html
    assert COLORS.border_input in html and COLORS.white in html


def test_a_link_button_escapes_its_text() -> None:
    assert "&lt;b&gt;" in link_button_html("<b>", "/x")


def test_the_status_card_shows_each_label_and_value() -> None:
    html = status_card_html([
        ("Data to", "Oct 1, 2026"), ("Account", "synthetic-B"), ("Transactions", "1,234"),
        ("Confirmed series", "3 of 6"),
    ])  # fmt: skip
    for part in ("Data to", "Oct 1, 2026", "Account", "synthetic-B", "Transactions", "1,234",
                 "Confirmed series", "3 of 6"):  # fmt: skip
        assert part in html


def test_the_status_card_escapes_values() -> None:
    assert "&lt;script&gt;" in status_card_html([("Account", "<script>")])


ROWS = [
    ("Oct 1, 2026", "Uber", "Transport", -1250, 60000, False),
    ("Sep 30, 2026", "Payroll <b>", "Income", 190000, 61250, True),
    ("Sep 29, 2026", "", None, -500, None, False),
]


def test_the_transactions_table_has_five_headers_and_signed_money() -> None:
    html = transactions_table_html(ROWS)
    for header in ("Date", "Merchant", "Category", "Amount", "Balance"):
        assert f">{header}<" in html
    assert "−$12.50" in html and "+$1,900.00" in html and "$600.00" in html
    assert COLORS.green_dark in html  # money in is green
    assert html.count("<tr") == 4


def test_a_category_is_a_tag_and_a_row_to_check_says_so() -> None:
    html = transactions_table_html(ROWS)
    assert "Transport" in html and "Income" in html
    assert html.count("to check") == 1  # only the flagged row
    assert COLORS.amber_bg in html


def test_merchant_text_is_escaped_and_missing_values_are_dashes() -> None:
    html = transactions_table_html(ROWS)
    assert "<b>" not in html and "Payroll &lt;b&gt;" in html
    assert "—" in html  # a row without merchant, category or balance


def test_an_empty_transactions_table_is_a_sentence() -> None:
    assert "No transactions match" in transactions_table_html([])
