"""The look of Lookahead: deep green with navy as the second colour. Presentation only.

Everything visual lives here so that every page uses the same pieces:
- `COLORS`: the tokens (the same values are in .streamlit/config.toml; a test keeps them in step
  and checks that every text/background pair meets WCAG AA);
- `TEMPLATE` and `style_figure`: one Plotly template for every chart, plus role-based styling
  (the chart builders tag each trace with a role; this module decides how a role looks);
- `CSS`: the small amount of custom CSS Streamlit's own theme cannot express;
- helpers: `apply`, `sidebar_brand`, `side_label`, `page_header`, `money`, `fmt_date`, `show_table`.

No external calls: the fonts are files in app/static/fonts, named in the config.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

DISCLAIMER = "Estimates for personal budgeting. Not financial advice."


@dataclass(frozen=True)
class Palette:
    bg: str = "#07120D"  # page: near-black green
    surface: str = "#0E1F17"  # cards, expanders, inputs: slightly lighter green
    raised: str = "#142B20"  # table headers, hover
    border: str = "#1F3D2E"  # hairlines (decorative)
    border_strong: str = "#4F7F67"  # an input's outline (3:1 or more)
    text: str = "#E9F2EC"  # off-white
    muted: str = "#9FB8AA"  # grey-green secondary text
    primary: str = "#34D399"  # emerald: buttons, links, the main forecast line
    primary_soft: str = "#A7F3D0"  # lighter green for second and third lines
    on_primary: str = "#04140C"  # text on a primary button
    link: str = "#6EE7B7"
    navy: str = "#0F2347"  # secondary panels and info callouts
    navy_accent: str = "#5B86E5"  # navy blue for chart graphics (the band)
    navy_text: str = "#BFD4FF"  # text on navy
    amber: str = "#F5B942"  # warnings, the safety buffer
    amber_bg: str = "#2B2410"
    amber_text: str = "#F8DC9B"
    red: str = "#F28B82"  # soft red: shortfalls, negative amounts
    red_bg: str = "#33161A"
    red_text: str = "#F8B4AE"
    green_bg: str = "#0F3324"  # "fine" callouts
    green_text: str = "#9AF0CB"


COLORS = Palette()
FONT = "Inter, system-ui, sans-serif"
MONO = "JetBrains Mono, ui-monospace, monospace"


def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


GRID = _rgba(COLORS.muted, 0.14)

# --- The Plotly template: one for every chart ---

TEMPLATE = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": FONT, "color": COLORS.text, "size": 13},
        colorway=[COLORS.primary, COLORS.navy_accent, COLORS.amber, COLORS.red,
                  COLORS.primary_soft],
        margin={"l": 8, "r": 12, "t": 48, "b": 8},
        title={"x": 0, "xanchor": "left", "font": {"size": 16, "color": COLORS.text}},
        hovermode="x unified",
        hoverlabel={
            "bgcolor": COLORS.surface, "bordercolor": COLORS.border_strong,
            "font": {"family": FONT, "color": COLORS.text, "size": 13},
        },
        legend={
            "orientation": "h", "x": 0, "xanchor": "left", "y": -0.16, "yanchor": "top",
            "bgcolor": "rgba(0,0,0,0)", "font": {"color": COLORS.muted, "size": 12},
        },
        xaxis={
            "gridcolor": GRID, "linecolor": GRID, "zeroline": False, "automargin": True,
            "tickformat": "%b %-d", "hoverformat": "%b %-d",
            "tickfont": {"color": COLORS.muted}, "title": {"text": ""},
        },
        yaxis={
            "gridcolor": GRID, "linecolor": GRID, "zeroline": False, "automargin": True,
            "tickprefix": "$", "tickformat": ",.0f", "hoverformat": "$,.2f",
            "tickfont": {"color": COLORS.muted}, "title": {"text": ""},
        },
    )
)  # fmt: skip
pio.templates["lookahead"] = TEMPLATE

# How a role looks. The chart builders in lookahead/ui.py tag each trace with one of these.
# Lines differ by dash style and width as well as colour, so they stay apart without colour.
_BAND = _rgba(COLORS.navy_accent, 0.3)
_BAND_GREY = _rgba(COLORS.muted, 0.2)
ROLES: dict[str, dict] = {  # type: ignore[type-arg]
    "recent": {"line": {"color": COLORS.text, "width": 2, "dash": "solid"}},
    "band_edge": {"line": {"width": 0}},
    "band": {"line": {"width": 0}, "fillcolor": _BAND},
    "median": {"line": {"color": COLORS.primary, "width": 3, "dash": "solid"}},
    "median_without": {"line": {"color": COLORS.primary_soft, "width": 2.5, "dash": "dot"}},
    "estimate": {"line": {"color": COLORS.primary_soft, "width": 2, "dash": "dash"}},
    "original_band": {"line": {"width": 0}, "fillcolor": _BAND_GREY},
    "original_median": {"line": {"color": COLORS.muted, "width": 2, "dash": "dashdot"}},
    "modified_band": {"line": {"width": 0}, "fillcolor": _BAND},
    "modified_median": {"line": {"color": COLORS.primary, "width": 3, "dash": "solid"}},
}
_HOVER = "%{y:$,.2f}<extra>%{fullData.name}</extra>"


def style_figure(figure: go.Figure) -> go.Figure:
    """Apply the shared template and the role styles to a figure and return it."""
    figure.update_layout(template=TEMPLATE)
    figure.update_layout(TEMPLATE.layout)  # also set explicitly, so the figure carries it
    for trace in figure.data:
        style = ROLES.get(trace.meta if isinstance(trace.meta, str) else "")
        if style is not None:
            trace.update(**style)
            if trace.meta != "band_edge":
                trace.update(hovertemplate=_HOVER)
    figure.update_shapes(selector={"name": "buffer"},
                         line={"color": COLORS.amber, "dash": "dash", "width": 1.5})  # fmt: skip
    figure.update_shapes(selector={"name": "zero"},
                         line={"color": COLORS.red, "dash": "dot", "width": 1})  # fmt: skip
    figure.update_annotations(font={"color": COLORS.amber, "size": 12})
    return figure


# --- The little custom CSS (Streamlit's own theme does the rest) ---

CSS = f"""
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stToolbar"] {{
  display: none !important; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.4rem; max-width: 1240px; }}
.la-brand {{ display: flex; align-items: center; gap: .65rem; margin: .2rem 0 .6rem;
  font-weight: 700; font-size: 1.4rem; letter-spacing: -.01em; color: {COLORS.text}; }}
.la-brand svg {{ width: 30px; height: 30px; flex: none; }}
.la-side-label {{ text-transform: uppercase; letter-spacing: .09em; font-size: .72rem;
  font-weight: 600; color: {COLORS.muted}; margin: 1.3rem 0 .3rem; }}
.la-chip {{ display: inline-block; padding: .22rem .75rem; border-radius: 999px;
  background: {COLORS.surface}; border: 1px solid {COLORS.border}; color: {COLORS.muted};
  font-size: .82rem; font-variant-numeric: tabular-nums; }}
.la-lede {{ color: {COLORS.muted}; font-size: 1.02rem; margin: -.4rem 0 .7rem; max-width: 62ch; }}
[data-testid="stMetric"] {{ background: {COLORS.surface}; border: 1px solid {COLORS.border};
  border-radius: 14px; padding: .9rem 1.1rem; }}
[data-testid="stMetricLabel"] {{ color: {COLORS.muted}; }}
[data-testid="stMetricValue"], [data-testid="stMetricDelta"] {{
  font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }}
[data-testid="stExpander"] details {{ background: {COLORS.surface};
  border: 1px solid {COLORS.border}; border-radius: 14px; }}
[data-testid="stExpander"] summary {{ font-weight: 600; }}
.stMarkdown, [data-testid="stCaptionContainer"] {{ font-variant-numeric: tabular-nums; }}
[data-testid="stAlert"] {{ border-radius: 14px; }}
@media (max-width: 640px) {{
  .block-container {{ padding: 1.4rem .9rem 3rem; }}
  [data-testid="stMetric"] {{ padding: .7rem .85rem; }}
}}
"""

_MARK = (
    f'<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" '
    f'fill="{COLORS.navy}"/><path d="M5 22 L12 15 L17 19 L27 8" fill="none" '
    f'stroke="{COLORS.primary}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
    f'<circle cx="27" cy="8" r="2.8" fill="{COLORS.primary}"/></svg>'
)


def apply(title: str, icon: str = ":material/show_chart:") -> None:
    """Page settings, the custom CSS and the shared chart template. Call first on every page."""
    st.set_page_config(page_title=f"{title} · Lookahead", page_icon=icon, layout="wide")
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
    pio.templates.default = "lookahead"


def sidebar_brand() -> None:
    """The wordmark at the top of the sidebar."""
    st.sidebar.markdown(f'<div class="la-brand">{_MARK}<span>Lookahead</span></div>',
                        unsafe_allow_html=True)  # fmt: skip


def side_label(text: str) -> None:
    """A small section label in the sidebar (Data, Account, Forecast settings)."""
    st.sidebar.markdown(f'<div class="la-side-label">{text}</div>', unsafe_allow_html=True)


def page_header(title: str, description: str, data_updated: date | None = None) -> None:
    """The same header on every page: title, a one-line description and a status chip."""
    st.title(title)
    st.markdown(f'<p class="la-lede">{description}</p>', unsafe_allow_html=True)
    if data_updated is not None:
        st.markdown(f'<span class="la-chip">Data last updated: {fmt_date(data_updated)}</span>',
                    unsafe_allow_html=True)  # fmt: skip


# --- Money and dates ---


def money(cents: int, *, signed: bool = False, markdown: bool = False) -> str:
    """Integer cents as $1,234.56 (a minus sign in front for a negative; `signed` also puts a plus
    in front of a positive). `markdown` escapes the $ so Streamlit does not read it as a formula."""
    sign = "-" if cents < 0 else ("+" if signed else "")
    dollar = "\\$" if markdown else "$"
    return f"{sign}{dollar}{abs(cents) / 100:,.2f}"


def fmt_date(day: date) -> str:
    """Oct 2, 2026."""
    return f"{day:%b} {day.day}, {day.year}"


# --- Tables ---


@dataclass(frozen=True)
class TableView:
    frame: pd.DataFrame  # the data, with real dates; column names unchanged
    labels: dict[str, str]  # column name -> the header shown
    negative_cells: dict[str, list[bool]]  # money column -> which cells are negative


def _label(name: str) -> str:
    return name.replace(" ($)", "").replace("_", " ")


def prepare_table(df: pd.DataFrame, money: list[str], dates: list[str]) -> TableView:
    """Get a table ready to show: ISO dates become real dates (they sort properly and show as
    Oct 2, 2026), headers lose "($)" and underscores, and negative amounts are noted so they can
    be shown in soft red. The data keeps its own column names and numbers."""
    frame = df.copy()
    for column in dates:
        frame[column] = pd.to_datetime(frame[column]).dt.date
    labels = {c: _label(c) for c in frame.columns}
    negatives = {c: [bool(v < 0) for v in frame[c]] for c in money}
    return TableView(frame, labels, negatives)


def show_table(df: pd.DataFrame, money: list[str] = (), dates: list[str] = (),  # type: ignore[assignment]
               widths: dict[str, str] | None = None) -> None:  # fmt: skip
    """A read-only table: no index, amounts as $1,234.56 (negatives in soft red), dates as
    Oct 2, 2026, plain headers. `widths` maps a column to "small", "medium" or "large"."""
    view = prepare_table(df, list(money), list(dates))
    widths = widths or {}
    config: dict = {}  # type: ignore[type-arg]
    for column, label in view.labels.items():
        if column in money:
            config[column] = st.column_config.NumberColumn(label, format="dollar",
                                                           width=widths.get(column))  # fmt: skip
        elif column in dates:
            config[column] = st.column_config.DateColumn(label, format="MMM D, YYYY",
                                                         width=widths.get(column))  # fmt: skip
        else:
            config[column] = st.column_config.TextColumn(label, width=widths.get(column))
    shown = view.frame.style
    if money:
        shown = shown.map(lambda v: f"color: {COLORS.red}" if v < 0 else "", subset=list(money))
    st.dataframe(shown, hide_index=True, width="stretch", column_config=config)
