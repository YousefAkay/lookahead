"""Tests for the look of the app (the light design in docs/design/target): tokens, contrast, the
font, offline use, and the small helpers (money, dates, tags, callouts, the top bar, tables and the
chart template). Presentation only: nothing here touches a forecast."""

import tomllib
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest

from app import theme
from app.theme import (
    COLORS,
    callout_html,
    fmt_date,
    fmt_day,
    humanize_dates,
    money,
    prepare_table,
    style_forecast_chart,
    tag_html,
    top_bar_html,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- Tokens ---

TEXT_ON_BACKGROUND = [  # (text token, background token): every pair used for text on screen
    ("text", "bg"), ("text", "card"), ("text", "panel_blue"),
    ("muted", "bg"), ("muted", "card"), ("muted", "ended_bg"), ("muted", "grey_tag"),
    ("muted", "panel_blue"),
    ("faint", "bg"), ("faint", "card"),
    ("white", "topbar"), ("topbar_text", "topbar"), ("topbar", "white"),
    ("green_dark", "card"), ("green_dark", "bg"), ("green_dark", "soft_green"),
    ("green", "card"),
    ("blue", "soft_blue"), ("blue", "panel_blue"), ("blue", "card"),
    ("amber", "amber_bg"), ("amber", "card"), ("amber_dark", "amber_bg"),
    ("amber_link", "amber_bg"),
    ("white", "amber"), ("white", "green_dark"),
]  # fmt: skip


@pytest.mark.parametrize(("fg", "bg"), TEXT_ON_BACKGROUND)
def test_every_text_and_background_pair_meets_wcag_aa(fg: str, bg: str) -> None:
    assert contrast(getattr(COLORS, fg), getattr(COLORS, bg)) >= 4.5, (fg, bg)


def test_chart_lines_and_markers_can_be_seen_against_the_card() -> None:
    for graphic in (COLORS.text, COLORS.green, COLORS.blue, COLORS.amber_line, COLORS.amber):
        assert contrast(graphic, COLORS.card) >= 3.0, graphic  # WCAG 1.4.11 for graphics


def test_the_tokens_are_the_designs() -> None:
    assert COLORS.topbar == "#17603E" and COLORS.bg == "#F6F8F6" and COLORS.card == "#FFFFFF"
    assert COLORS.border == "#E2E8E4" and COLORS.text == "#14201A" and COLORS.muted == "#55665C"
    assert COLORS.green == "#1F7A4F" and COLORS.soft_green == "#E6F3EC"
    assert COLORS.blue == "#2E5F9A" and COLORS.soft_blue == "#EAF0F8"
    assert COLORS.panel_blue == "#F3F7FC"
    assert COLORS.amber == "#8A5300" and COLORS.amber_bg == "#FDF3E1"


def test_the_tokens_in_the_code_are_the_ones_in_the_streamlit_theme() -> None:
    t = CONFIG["theme"]
    assert t["base"] == "light"
    assert t["primaryColor"] == COLORS.green_dark
    assert t["backgroundColor"] == COLORS.bg
    assert t["secondaryBackgroundColor"] == COLORS.card
    assert t["textColor"] == COLORS.text
    assert t["borderColor"] == COLORS.border
    assert t["linkColor"] == COLORS.green_dark
    assert (t["greenColor"], t["greenBackgroundColor"]) == (COLORS.green, COLORS.soft_green)
    assert (t["blueColor"], t["blueBackgroundColor"]) == (COLORS.blue, COLORS.soft_blue)
    assert (t["orangeTextColor"], t["orangeBackgroundColor"]) == (COLORS.amber, COLORS.amber_bg)
    assert t["grayColor"] == COLORS.muted


def test_every_colour_is_a_valid_hex() -> None:
    for field in COLORS.__dataclass_fields__:
        value = getattr(COLORS, field)
        assert value.startswith("#") and len(value) == 7, field


# --- Font and offline use ---


def test_the_font_is_a_file_in_the_app_and_licensed() -> None:
    (face,) = CONFIG["theme"]["fontFaces"]
    assert face["family"] == "Manrope" and face["url"].startswith("app/static/")
    assert (ROOT / face["url"]).stat().st_size > 10_000
    assert (ROOT / "app" / "static" / "fonts" / "Manrope-LICENSE.txt").exists()
    assert CONFIG["server"]["enableStaticServing"] is True
    assert CONFIG["theme"]["font"].startswith("Manrope")


def test_the_first_attempts_fonts_and_logo_are_gone() -> None:
    fonts = {p.name for p in (ROOT / "app" / "static" / "fonts").iterdir()}
    assert fonts == {"Manrope-latin.woff2", "Manrope-LICENSE.txt"}
    assert not (ROOT / "app" / "static" / "wordmark.svg").exists()


def test_nothing_in_the_look_calls_out_to_the_internet() -> None:
    text = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    code = (ROOT / "app" / "theme.py").read_text(encoding="utf-8")
    for source in (text, code):
        assert "googleapis" not in source and "gstatic" not in source and "cdn." not in source
    assert "http://" not in theme.CSS and "https://" not in theme.CSS and "@import" not in theme.CSS
    assert "url(" not in theme.CSS  # no fetched images or fonts: the font comes from the config
    assert "material/" not in code  # a Material icon shortcode would be fetched from Google


def test_streamlits_own_navigation_is_off_because_the_top_bar_replaces_it() -> None:
    assert CONFIG["client"]["showSidebarNavigation"] is False


# --- Money and dates ---


def test_money_is_dollars_with_thousands_separators_and_a_real_minus_sign() -> None:
    assert money(123456) == "$1,234.56"
    assert money(5) == "$0.05" and money(0) == "$0.00"
    assert money(-3500) == "−$35.00"  # the design's minus, not a hyphen
    assert money(1865900) == "$18,659.00"
    assert money(79520, signed=True) == "+$795.20"
    assert money(-3500, signed=True) == "−$35.00"


def test_money_for_markdown_escapes_the_dollar_sign() -> None:
    assert money(123456, markdown=True) == "\\$1,234.56"
    assert money(-5000, markdown=True) == "−\\$50.00"


def test_dates_read_like_oct_2_2026() -> None:
    assert fmt_date(date(2026, 10, 2)) == "Oct 2, 2026"
    assert fmt_date(date(2027, 1, 15)) == "Jan 15, 2027"
    assert fmt_day(date(2026, 10, 2)) == "Oct 2" and fmt_day(date(2026, 12, 31)) == "Dec 31"


def test_iso_dates_inside_a_sentence_become_readable() -> None:
    assert humanize_dates("2026-09-03 to 2026-09-09") == "Sep 3, 2026 to Sep 9, 2026"
    assert humanize_dates("Large deposit (2026-09-12, not forecast)") == (
        "Large deposit (Sep 12, 2026, not forecast)"
    )
    assert humanize_dates("nothing here") == "nothing here" and humanize_dates("") == ""
    assert humanize_dates("Order 2026-13-45 is not a date") == "Order 2026-13-45 is not a date"


# --- Tags, callouts and the top bar ---


def test_a_tag_has_the_designs_colours_for_its_kind() -> None:
    detected = tag_html("Detected", "detected")
    assert COLORS.soft_blue in detected and COLORS.blue in detected and ">Detected<" in detected
    confirmed = tag_html("Confirmed", "confirmed")
    assert COLORS.soft_green in confirmed and COLORS.green_dark in confirmed
    ended = tag_html("Ended", "ended")
    assert COLORS.grey_tag in ended and COLORS.muted in ended
    assert "border-radius: 999px" in detected


def test_tag_text_is_escaped() -> None:
    assert "<script>" not in tag_html("<script>alert(1)</script>", "detected")


def test_the_warning_callout_is_amber_with_a_tag_a_sentence_and_a_link() -> None:
    html = callout_html("warning", "Possible shortfall", "<strong>22% chance</strong> of a dip.",
                        "See why →", "#evidence")  # fmt: skip
    assert COLORS.amber_bg in html and COLORS.amber_border in html and COLORS.amber in html
    assert "Possible shortfall" in html and "text-transform: uppercase" in html
    assert "<strong>22% chance</strong>" in html and 'href="#evidence"' in html


def test_the_on_track_callout_is_green_and_needs_no_link() -> None:
    html = callout_html("ok", "On track", "No simulated future dips below your buffer.")
    assert COLORS.soft_green in html and COLORS.green in html and "href" not in html


PAGES = [("Home", "/"), ("Transactions", "/Transactions"), ("Recurring", "/Recurring"),
         ("Forecast", "/Forecast")]  # fmt: skip


def test_the_top_bar_has_the_wordmark_links_the_data_date_and_the_source() -> None:
    html = top_bar_html("Forecast", PAGES, date(2026, 10, 1), "Synthetic demo")
    assert "Lookahead" in html and "<svg" in html
    for label, path in PAGES:
        assert f">{label}<" in html and f'href="{path}"' in html
    assert "Data to Oct 1, 2026" in html and "Synthetic demo" in html
    assert html.count('aria-current="page"') == 1
    marked = html[html.index('aria-current="page"') :]
    assert marked.index("Forecast") < marked.index("</a>")  # the current page is the marked one


def test_the_top_bar_without_data_says_so() -> None:
    assert "No data yet" in top_bar_html("Forecast", PAGES, None, "My data")


# --- Tables ---


def test_a_table_gets_plain_headers_real_dates_and_money_in_marked() -> None:
    raw = pd.DataFrame({
        "Date": ["2026-10-05", "2026-10-15"],
        "Name": ["Phone Co", "Family"],
        "Amount ($)": [-65.0, 1200.0],
        "Based on": ["detected", "entered"],
    })  # fmt: skip
    view = prepare_table(raw, money=["Amount ($)"], dates=["Date"], money_in=["Amount ($)"])
    assert list(view.frame.columns) == list(raw.columns)  # the data keeps its own column names
    assert list(view.frame["Date"]) == [date(2026, 10, 5), date(2026, 10, 15)]
    assert list(view.frame["Amount ($)"]) == [-65.0, 1200.0]  # still numbers
    assert view.labels == {
        "Date": "Date", "Name": "Name", "Amount ($)": "Amount", "Based on": "Based on",
    }  # fmt: skip
    assert view.positive_cells == {"Amount ($)": [False, True]}  # money in is green, out is plain
    assert list(raw["Date"]) == ["2026-10-05", "2026-10-15"]  # the original is not changed


def test_a_column_that_is_not_money_in_gets_no_green() -> None:
    raw = pd.DataFrame({"Type": ["Card"], "Per week": [12.5]})
    assert prepare_table(raw, money=["Per week"], dates=[]).positive_cells == {}


def test_an_empty_table_is_fine() -> None:
    empty = pd.DataFrame(columns=["Date", "Amount ($)"])
    view = prepare_table(empty, ["Amount ($)"], ["Date"], ["Amount ($)"])
    assert view.frame.empty and view.positive_cells == {"Amount ($)": []}


def test_underscores_never_reach_a_header() -> None:
    raw = pd.DataFrame({"first_day": [1], "Amount ($)": [2.0]})
    assert "_" not in " ".join(prepare_table(raw, ["Amount ($)"], []).labels.values())


# --- The chart ---


def sample_forecast():  # type: ignore[no-untyped-def]
    from lookahead.forecast.path import Forecast, ForecastDay
    from lookahead.forecast.simulate import Simulation
    from lookahead.forecast.start import ForecastStart

    first = date(2026, 10, 2)
    start = ForecastStart("a", date(2026, 10, 1), 100000, first, date(2026, 10, 4))
    days = [ForecastDay(date(2026, 10, 2 + k), 100000 - 100 * k, 0, 100, 0) for k in range(3)]
    sim = Simulation(
        tuple(d.day for d in days), 500, (90000, 89000, 88000), (99000, 98500, 98000),
        (101000, 100500, 100000), (98000, 97000, 96000), 0.12, 0.31, 0.01, 0.02, 95000, 90000, 12,
    )  # fmt: skip
    return Forecast(start, "test", days, [], False, simulation=sim), sim


def styled(low: bool = True):  # type: ignore[no-untyped-def]
    from lookahead.ui import forecast_chart

    forecast, sim = sample_forecast()
    recent = pd.DataFrame(
        {"date": pd.to_datetime(["2026-09-30", "2026-10-01"]), "balance ($)": [900.0, 1000.0]}
    )
    figure = forecast_chart(recent, forecast, 20000)
    lowest = (sim.days[2], sim.p50[2]) if low else None
    return style_forecast_chart(
        figure, forecast.start.data_last_updated, forecast.start.last_day, lowest
    )


def by_name(figure: go.Figure) -> dict[str, go.Scatter]:
    return {t.name: t for t in figure.data if t.name}


def test_the_chart_has_a_transparent_background_and_no_plotly_legend() -> None:
    layout = styled().layout
    assert layout.paper_bgcolor == "rgba(0,0,0,0)" and layout.plot_bgcolor == "rgba(0,0,0,0)"
    assert layout.showlegend is False  # the legend is written above the chart, as in the design
    assert layout.font.family.startswith("Manrope")


def test_the_actual_line_is_dark_the_middle_future_green_and_the_band_translucent_blue() -> None:
    figure = styled()
    traces = by_name(figure)
    assert traces["Recent balance"].line.color == COLORS.text
    assert traces["Middle future (median), with irregular deposits"].line.color == COLORS.green
    band = next(t for t in figure.data if t.fill == "tonexty")
    assert band.fillcolor == "rgba(46, 95, 154, 0.18)"


def test_the_buffer_is_dashed_amber_and_there_is_no_zero_line() -> None:
    shapes = {s.name: s for s in styled().layout.shapes if s.name}
    assert shapes["buffer"].line.color == COLORS.amber_line and shapes["buffer"].line.dash == "dash"
    assert "zero" not in shapes


def test_the_forecast_area_is_lightly_shaded_and_labelled() -> None:
    figure = styled()
    area = next(s for s in figure.layout.shapes if s.name == "forecast area")
    assert area.fillcolor == COLORS.panel_blue and area.layer == "below"
    assert str(area.x0)[:10] == "2026-10-01" and str(area.x1)[:10] == "2026-10-04"
    assert any(
        a.text == "FORECAST" and a.font.color == COLORS.blue for a in figure.layout.annotations
    )
    assert any(
        s.name == "forecast start" and s.line.color == COLORS.divider for s in figure.layout.shapes
    )


def test_the_low_point_is_marked_and_labelled() -> None:
    figure = styled()
    marker = by_name(figure)["Lowest expected balance"]
    assert marker.mode == "markers" and marker.marker.line.color == COLORS.amber
    assert marker.marker.color == COLORS.card
    label = next(a for a in figure.layout.annotations if a.text.startswith("Low "))
    assert label.text == "Low $980.00 · Oct 4" and label.bgcolor == COLORS.amber
    assert label.font.color == COLORS.white


def test_the_other_lines_are_hidden_not_removed() -> None:
    traces = by_name(styled())
    assert traces["Middle future (median), without irregular deposits"].visible is False
    assert traces["Forecast with no randomness (an estimate)"].visible is False


def test_axes_use_short_dollars_and_short_dates_with_faint_gridlines() -> None:
    layout = styled().layout
    assert layout.yaxis.tickformat == "$~s" and layout.yaxis.hoverformat == "$,.2f"
    assert layout.xaxis.tickformat == "%b %-d" and layout.xaxis.hoverformat == "%b %-d"
    assert layout.yaxis.gridcolor == COLORS.hairline and layout.xaxis.showgrid is False


def test_hover_labels_show_dollars_and_the_series_name() -> None:
    middle = by_name(styled())["Middle future (median), with irregular deposits"]
    assert "%{y:$,.2f}" in middle.hovertemplate and "%{fullData.name}" in middle.hovertemplate


def test_the_chart_without_a_low_point_still_works() -> None:
    figure = styled(low=False)
    assert not any(a.text.startswith("Low ") for a in figure.layout.annotations)
    assert "Lowest expected balance" not in by_name(figure)


def test_the_scenario_chart_is_styled_too() -> None:
    from dataclasses import replace

    from app.theme import style_scenario_chart
    from lookahead.ui import scenario_chart

    forecast, sim = sample_forecast()
    figure = style_scenario_chart(
        scenario_chart(forecast, sim, replace(sim, p50=tuple(c + 500 for c in sim.p50)), 20000)
    )
    medians = {t.name: t for t in figure.data if t.name and t.name.endswith("middle future")}
    assert medians["With the changes: middle future"].line.color == COLORS.green
    original, changed = (
        medians["Original: middle future"],
        medians["With the changes: middle future"],
    )
    assert original.line.dash != changed.line.dash  # told apart by dash, not colour alone
    assert figure.layout.paper_bgcolor == "rgba(0,0,0,0)"


# --- The legend, the what-if panel and the "Coming up" list ---


def test_the_legend_names_the_four_things_the_chart_shows() -> None:
    from app.theme import chart_legend_html

    html = chart_legend_html(20000)
    for words in ("Actual", "Middle future", "80% of futures", "Buffer $200"):
        assert words in html
    assert COLORS.green in html and COLORS.amber_line in html and "rgba(46, 95, 154, 0.18)" in html
    assert "stroke-dasharray" in html  # the buffer is dashed in the legend too


def test_the_result_panel_shows_before_and_after_and_colours_the_verdict() -> None:
    from app.theme import result_panel_html

    html = result_panel_html([
        ("Chance below buffer", "22%", "8%", "better"),
        ("Lowest expected balance", "$624.00", "$500.00", "worse"),
        ("Chance below $0", "6%", "6%", "same"),
    ])  # fmt: skip
    assert "22%" in html and "8%" in html and "Chance below buffer" in html
    assert html.count("Before") == 3 and html.count("After") == 3  # stacked, never side by side
    assert html.count("white-space: nowrap") >= 6  # every number stays on one line
    assert f"color: {COLORS.green_dark}" in html and f"color: {COLORS.amber}" in html
    assert COLORS.panel_blue in html and "repeat(2, minmax(0, 1fr))" in html


def test_the_result_panel_escapes_what_it_is_given() -> None:
    from app.theme import result_panel_html

    assert "<b>" not in result_panel_html([("<b>x</b>", "1", "2", "same")])


def test_the_coming_up_list_makes_money_in_green_and_bold() -> None:
    from app.theme import coming_up_html

    html = coming_up_html([
        ("Oct 9", "Boutique", "Income · detected", "+$795.20", True),
        ("Oct 13", "Phone", "Payment · confirmed", "−$35.00", False),
    ])  # fmt: skip
    assert "Oct 9" in html and "Boutique" in html and "Income · detected" in html
    first, second = html.split("Oct 13")
    assert f"font-weight: 800; color: {COLORS.green_dark}" in first
    assert f"font-weight: 700; color: {COLORS.text}" in second
    assert first.count("border-bottom") == 1 and "border-bottom" not in second  # no line after last


def test_the_coming_up_list_escapes_names() -> None:
    from app.theme import coming_up_html

    assert "<script>" not in coming_up_html([("Oct 9", "<script>x</script>", "s", "$1.00", False)])


def test_the_y_axis_fits_the_forecast_not_a_distant_peak_in_the_history() -> None:
    from app.theme import fit_y_range

    figure = go.Figure()
    fit_y_range(figure, [100.0, 600.0, 400.0, 0.0, 200.0])
    low, high = figure.layout.yaxis.range
    assert low < 0 and high > 600  # padded on both sides
    assert high - 600 == pytest.approx(0 - low, rel=0.01)  # evenly
    fit_y_range(figure, [500.0, 500.0])  # a flat line still gets a visible range
    assert figure.layout.yaxis.range[1] - figure.layout.yaxis.range[0] >= 100
    untouched = go.Figure()
    fit_y_range(untouched, [])  # nothing to fit: left alone
    assert untouched.layout.yaxis.range is None


def test_every_top_bar_link_carries_the_data_and_account_choice() -> None:
    html = top_bar_html(
        "Forecast", PAGES, date(2026, 10, 1), "Synthetic demo",
        "data=Synthetic+demo&account=synthetic-C&text=1",
    )  # fmt: skip
    for _, path in PAGES:
        assert f'href="{path}?data=Synthetic+demo&amp;account=synthetic-C&amp;text=1"' in html
    assert 'href="/?data=' in html  # the wordmark too
    assert 'href="/Forecast"' not in html  # no link without the choice


def test_the_choice_is_written_in_the_address_form() -> None:
    from urllib.parse import parse_qs, urlencode

    query = urlencode([("data", "My data"), ("account", "chequing 1"), ("text", "0")])
    assert parse_qs(query) == {"data": ["My data"], "account": ["chequing 1"], "text": ["0"]}


class FakeStreamlit:
    """Just enough of `st` for the choice helpers: a dict for the session, one for the address."""

    def __init__(self, address: dict[str, str] | None = None) -> None:
        self.session_state: dict[str, object] = {}
        self.query_params: dict[str, str] = dict(address or {})


def test_a_choice_in_the_address_fills_a_widget_that_has_no_value_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeStreamlit({"data": "Synthetic demo", "account": "synthetic-C", "text": "0"})
    monkeypatch.setattr(theme, "st", fake)
    theme.seed_choice("data_source", ["Synthetic demo", "My data"])
    theme.seed_choice("pseudonymize")
    theme.seed_choice("account", ["synthetic-A", "synthetic-C"])
    assert fake.session_state == {
        "data_source": "Synthetic demo", "pseudonymize": False, "account": "synthetic-C",
    }  # fmt: skip


def test_an_address_choice_that_does_not_fit_is_ignored_and_the_toggle_starts_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeStreamlit({"account": "gone", "data": "nonsense"})
    monkeypatch.setattr(theme, "st", fake)
    theme.seed_choice("account", ["synthetic-A"])
    theme.seed_choice("data_source", ["Synthetic demo", "My data"])
    theme.seed_choice("pseudonymize")
    assert fake.session_state == {"pseudonymize": True}


def test_a_held_value_that_no_longer_fits_the_options_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeStreamlit({"account": "synthetic-B"})
    fake.session_state["account"] = "private-account"  # held from the other data source
    monkeypatch.setattr(theme, "st", fake)
    theme.seed_choice("account", ["synthetic-A", "synthetic-B"])
    assert fake.session_state == {"account": "synthetic-B"}  # the address supplies a valid one


def test_a_value_the_widget_already_has_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeStreamlit({"account": "synthetic-B"})
    fake.session_state["account"] = "synthetic-A"
    monkeypatch.setattr(theme, "st", fake)
    theme.seed_choice("account", ["synthetic-A", "synthetic-B"])
    assert fake.session_state["account"] == "synthetic-A"


def test_choices_are_written_to_the_address_and_into_the_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeStreamlit()
    fake.session_state.update(data_source="My data", account="chequing 1", pseudonymize=False)
    monkeypatch.setattr(theme, "st", fake)
    theme.save_choices()
    assert fake.query_params == {"data": "My data", "account": "chequing 1", "text": "0"}
    assert theme.choice_query() == "data=My+data&account=chequing+1&text=0"


def test_the_comparison_legend_names_both_versions_and_the_buffer() -> None:
    from app.theme import scenario_legend_html

    html = scenario_legend_html(20000)
    assert "Original" in html and "With your change" in html and "Buffer $200" in html
    assert (
        COLORS.muted in html and COLORS.green in html
    )  # grey for the original, green for the change


def test_the_original_is_grey_and_the_changed_future_is_green_with_a_blue_band() -> None:
    from app.theme import ROLES

    assert ROLES["original_median"]["line"]["color"] == COLORS.muted
    assert ROLES["modified_median"]["line"]["color"] == COLORS.green
    assert "46, 95, 154" in ROLES["modified_band"]["fillcolor"]  # translucent blue
    assert "46, 95, 154" not in ROLES["original_band"]["fillcolor"]  # grey, not blue
    assert ROLES["original_median"]["line"]["dash"] != ROLES["modified_median"]["line"]["dash"]
