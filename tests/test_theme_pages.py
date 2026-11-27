"""Pieces for Home and Transactions: link buttons, the status card, the transactions table."""

from app.theme import COLORS, link_button_html, status_card_html, transactions_table_html


def test_a_link_button_is_a_link_that_carries_the_choice() -> None:
    html = link_button_html("Forecast", "/Forecast", "data=Synthetic+demo&account=a", primary=True)
    assert 'href="Forecast?data=Synthetic+demo&amp;account=a"' in html  # relative: see in_app
    assert ">Forecast<" in html and 'target="_self"' in html
    assert COLORS.topbar in html  # the primary button is the deep green


def test_a_plain_link_button_is_white_with_an_outline_and_needs_no_query() -> None:
    html = link_button_html("Recurring", "/Recurring")
    assert 'href="Recurring"' in html and "?" not in html
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


def test_the_balance_chart_is_the_actual_line_in_the_themes_ink_with_no_titles() -> None:
    import pandas as pd

    from app.theme import style_balance_chart
    from lookahead.ui import balance_chart

    daily = pd.DataFrame({"date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
                          "balance ($)": [100.0, 80.0]})  # fmt: skip
    figure = style_balance_chart(balance_chart(daily))
    assert figure.data[0].line.color == COLORS.text  # the "recent" role
    assert not [s for s in figure.layout.shapes if s.name == "zero"]
    assert not figure.layout.title.text
    assert not figure.layout.xaxis.title.text and not figure.layout.yaxis.title.text


def test_the_alert_list_shows_each_episode_with_a_status_tag() -> None:
    from app.theme import alert_episodes_html

    html = alert_episodes_html([
        ("Oct 2 – Oct 8", "Low about $126.32 around Oct 8 · 97% chance", "Open"),
        ("Sep 1 – Sep 6", "Low about $90.00 around Sep 5 · 40% chance", "Dismissed"),
        ("Aug 1 – Aug 3", "Low about $150.00 around Aug 2 · 25% chance", "Closed"),
    ])  # fmt: skip
    for part in ("Oct 2 – Oct 8", "$126.32", "Open", "Dismissed", "Closed"):
        assert part in html
    assert COLORS.amber_bg in html  # the open one is amber
    assert html.count("border-bottom") == 2  # a line between rows, none after the last


def test_the_alert_list_escapes_and_has_a_sentence_when_empty() -> None:
    from app.theme import alert_episodes_html

    assert "<b>" not in alert_episodes_html([("<b>x</b>", "<i>y</i>", "Open")])
    assert "No earlier alerts" in alert_episodes_html([])


def test_the_demo_intro_is_two_plain_lines_with_the_source_link() -> None:
    """The slim banner says the data is made up; Home's intro says what the app does, that a
    visitor's changes stay in their own copy, and where the code is. No all caps."""
    from app.theme import REPO, demo_intro_html

    page = demo_intro_html()
    assert page.count("<p") == 2
    assert "MADE-UP" not in page and "font-size: 22px" not in page
    assert "forecasts a bank balance 30 days ahead" in page
    assert "own copy" in page and "Start the demo again" in page
    assert f'href="{REPO}"' in page and 'rel="noopener"' in page
    for word in ("guarantee", "fraud"):
        assert word not in page.lower()
