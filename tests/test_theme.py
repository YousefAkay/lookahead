"""Tests for the look of the app: tokens, contrast, fonts, the Plotly template and the table and
money helpers. Presentation only: nothing here touches a forecast."""

import tomllib
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app import theme
from app.theme import COLORS, fmt_date, money, prepare_table, style_figure

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
    ("text", "bg"), ("text", "surface"), ("text", "raised"), ("text", "navy"),
    ("text", "amber_bg"), ("text", "red_bg"), ("text", "green_bg"),
    ("muted", "bg"), ("muted", "surface"), ("muted", "raised"), ("muted", "navy"),
    ("primary", "bg"), ("primary", "surface"), ("primary", "green_bg"),
    ("link", "surface"), ("link", "bg"),
    ("on_primary", "primary"),
    ("navy_text", "navy"),
    ("amber", "bg"), ("amber", "surface"), ("amber", "amber_bg"), ("amber_text", "amber_bg"),
    ("red", "bg"), ("red", "surface"), ("red", "red_bg"), ("red_text", "red_bg"),
    ("green_text", "green_bg"),
]  # fmt: skip


@pytest.mark.parametrize(("fg", "bg"), TEXT_ON_BACKGROUND)
def test_every_text_and_background_pair_meets_wcag_aa(fg: str, bg: str) -> None:
    assert contrast(getattr(COLORS, fg), getattr(COLORS, bg)) >= 4.5, (fg, bg)


def test_chart_graphics_and_input_outlines_have_enough_contrast_to_be_seen() -> None:
    for graphic in (
        COLORS.navy_accent,
        COLORS.primary,
        COLORS.primary_soft,
        COLORS.amber,
        COLORS.red,
    ):
        assert contrast(graphic, COLORS.bg) >= 3.0, graphic  # WCAG 1.4.11 for graphics
    assert contrast(COLORS.border_strong, COLORS.surface) >= 3.0  # an input's outline


def test_the_tokens_in_the_code_are_the_ones_in_the_streamlit_theme() -> None:
    t = CONFIG["theme"]
    assert t["primaryColor"] == COLORS.primary
    assert t["backgroundColor"] == COLORS.bg
    assert t["secondaryBackgroundColor"] == COLORS.surface
    assert t["textColor"] == COLORS.text
    assert t["linkColor"] == COLORS.link
    assert t["borderColor"] == COLORS.border
    assert (t["greenBackgroundColor"], t["greenTextColor"]) == (COLORS.green_bg, COLORS.green_text)
    assert (t["blueColor"], t["blueBackgroundColor"], t["blueTextColor"]) == (
        COLORS.navy_accent, COLORS.navy, COLORS.navy_text)  # fmt: skip
    assert (t["orangeColor"], t["orangeBackgroundColor"], t["orangeTextColor"]) == (
        COLORS.amber, COLORS.amber_bg, COLORS.amber_text)  # fmt: skip
    assert (t["redColor"], t["redBackgroundColor"], t["redTextColor"]) == (
        COLORS.red, COLORS.red_bg, COLORS.red_text)  # fmt: skip
    assert t["grayColor"] == COLORS.muted and t["base"] == "dark"


def test_the_theme_colours_are_valid_hex() -> None:
    for name, value in vars(COLORS).items() if hasattr(COLORS, "__dict__") else []:
        assert value.startswith("#") and len(value) == 7, name
    for field in COLORS.__dataclass_fields__:
        value = getattr(COLORS, field)
        assert value.startswith("#") and len(value) == 7, field


# --- Fonts and offline use ---


def test_the_fonts_are_files_in_the_app_and_licensed() -> None:
    faces = CONFIG["theme"]["fontFaces"]
    assert {f["family"] for f in faces} == {"Inter", "JetBrains Mono"}
    for face in faces:
        assert face["url"].startswith("app/static/")  # served by the app itself
        assert (
            ROOT / "app" / "static" / Path(face["url"]).relative_to("app/static")
        ).stat().st_size > 10_000
    assert (ROOT / "app" / "static" / "fonts" / "Inter-LICENSE.txt").exists()
    assert (ROOT / "app" / "static" / "fonts" / "JetBrainsMono-LICENSE.txt").exists()
    assert CONFIG["server"]["enableStaticServing"] is True
    assert CONFIG["theme"]["font"].startswith("Inter") and CONFIG["theme"]["codeFont"].startswith(
        "JetBrains"
    )


def test_nothing_in_the_look_calls_out_to_the_internet() -> None:
    text = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    code = (ROOT / "app" / "theme.py").read_text(encoding="utf-8")
    for source in (text, code):
        assert "googleapis" not in source and "gstatic" not in source and "cdn." not in source
    assert "http://" not in theme.CSS and "https://" not in theme.CSS and "@import" not in theme.CSS
    assert "url(" not in theme.CSS  # no fetched images or fonts: fonts come from the config


# --- Money and dates ---


def test_money_is_dollars_with_thousands_separators_and_a_leading_minus() -> None:
    assert money(123456) == "$1,234.56"
    assert money(5) == "$0.05"
    assert money(0) == "$0.00"
    assert money(-54000) == "-$540.00"
    assert money(-1) == "-$0.01"
    assert money(1865900) == "$18,659.00"
    assert money(123456, signed=True) == "+$1,234.56"
    assert money(-123456, signed=True) == "-$1,234.56"


def test_money_for_markdown_escapes_the_dollar_sign() -> None:
    assert money(123456, markdown=True) == "\\$1,234.56"
    assert money(-5000, markdown=True) == "-\\$50.00"


def test_dates_read_like_oct_2_2026() -> None:
    assert fmt_date(date(2026, 10, 2)) == "Oct 2, 2026"
    assert fmt_date(date(2027, 1, 15)) == "Jan 15, 2027"
    assert fmt_date(date(2026, 12, 31)) == "Dec 31, 2026"


# --- Tables ---


def test_a_table_gets_plain_headers_real_dates_and_negative_amounts_marked() -> None:
    raw = pd.DataFrame({
        "Date": ["2026-10-05", "2026-10-15"],
        "Name": ["Phone Co", "Family"],
        "Amount ($)": [-65.0, 1200.0],
        "Based on": ["detected", "entered"],
    })  # fmt: skip
    view = prepare_table(raw, money=["Amount ($)"], dates=["Date"])
    assert list(view.frame.columns) == list(raw.columns)  # the data keeps its own column names
    assert list(view.frame["Date"]) == [date(2026, 10, 5), date(2026, 10, 15)]
    assert list(view.frame["Amount ($)"]) == [-65.0, 1200.0]  # still numbers
    assert view.labels == {
        "Date": "Date",
        "Name": "Name",
        "Amount ($)": "Amount",
        "Based on": "Based on",
    }
    assert view.negative_cells == {"Amount ($)": [True, False]}
    assert list(raw["Date"]) == ["2026-10-05", "2026-10-15"]  # the original is not changed


def test_an_empty_table_is_fine() -> None:
    view = prepare_table(
        pd.DataFrame(columns=["Date", "Amount ($)"]), money=["Amount ($)"], dates=["Date"]
    )
    assert view.frame.empty and view.negative_cells == {"Amount ($)": []}


def test_underscores_never_reach_a_header() -> None:
    raw = pd.DataFrame({"first_day": [1], "Amount ($)": [2.0]})
    assert "_" not in " ".join(prepare_table(raw, money=["Amount ($)"], dates=[]).labels.values())


# --- Charts ---


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


def test_the_forecast_chart_uses_the_shared_template_and_the_palette() -> None:
    from lookahead.ui import forecast_chart

    forecast, _ = sample_forecast()
    figure = style_figure(forecast_chart(pd.DataFrame(), forecast, 20000))
    layout = figure.layout
    assert layout.paper_bgcolor == "rgba(0,0,0,0)" and layout.plot_bgcolor == "rgba(0,0,0,0)"
    assert layout.legend.orientation == "h" and layout.legend.y < 0  # below the chart
    assert layout.xaxis.hoverformat == "%b %-d" and layout.xaxis.tickformat == "%b %-d"
    assert layout.yaxis.tickprefix == "$" and layout.yaxis.hoverformat == "$,.2f"
    lines = {t.name: t for t in figure.data if t.name and t.mode == "lines" and t.line.width}
    middle = next(
        t for n, t in lines.items() if n.startswith("Middle future") and "without" not in n
    )
    assert middle.line.color == COLORS.primary


def test_lines_differ_by_dash_style_and_not_by_colour_alone() -> None:
    from lookahead.ui import forecast_chart

    forecast, _ = sample_forecast()
    figure = style_figure(forecast_chart(pd.DataFrame(), forecast, 20000))
    drawn = [t for t in figure.data if t.name and t.line.width]
    dashes = {t.name: t.line.dash for t in drawn}
    wanted = ("Middle future", "Forecast with no")
    forecast_lines = [d for n, d in dashes.items() if n.startswith(wanted)]
    assert len(forecast_lines) == 3 and len(set(forecast_lines)) == 3  # solid, dotted, dashed


def test_the_buffer_is_amber_and_dashed_and_zero_is_a_soft_red_dotted_line() -> None:
    from lookahead.ui import forecast_chart

    forecast, _ = sample_forecast()
    figure = style_figure(forecast_chart(pd.DataFrame(), forecast, 20000))
    shapes = {s.name: s for s in figure.layout.shapes}
    assert shapes["buffer"].line.color == COLORS.amber and shapes["buffer"].line.dash == "dash"
    assert shapes["zero"].line.color == COLORS.red and shapes["zero"].line.dash == "dot"


def test_the_band_is_translucent_navy_blue() -> None:
    from lookahead.ui import forecast_chart

    forecast, _ = sample_forecast()
    figure = style_figure(forecast_chart(pd.DataFrame(), forecast, 20000))
    band = next(t for t in figure.data if t.fill == "tonexty")
    assert band.fillcolor.startswith("rgba(91, 134, 229") and band.fillcolor.endswith("0.3)")


def test_the_scenario_chart_is_styled_too() -> None:
    from lookahead.ui import scenario_chart

    forecast, sim = sample_forecast()
    figure = style_figure(
        scenario_chart(forecast, sim, replace(sim, p50=tuple(c + 500 for c in sim.p50)), 20000)
    )
    medians = {t.name: t for t in figure.data if t.name and t.name.endswith("middle future")}
    assert medians["With the changes: middle future"].line.color == COLORS.primary
    assert (
        medians["Original: middle future"].line.dash
        != medians["With the changes: middle future"].line.dash
    )


def test_hover_labels_show_dollars_and_the_series_name() -> None:
    from lookahead.ui import forecast_chart

    forecast, _ = sample_forecast()
    figure = style_figure(forecast_chart(pd.DataFrame(), forecast, 20000))
    middle = next(
        t for t in figure.data if t.name == "Middle future (median), with irregular deposits"
    )
    assert "%{y:$,.2f}" in middle.hovertemplate and "%{fullData.name}" in middle.hovertemplate
