"""The look of Lookahead: light, a green top bar, white cards, Manrope. Presentation only.

The target was a design mockup (a Forecast page and a Recurring page, with made-up data).
Everything visual lives here so that every page uses the same pieces:
- `COLORS`: the tokens (the same values are in .streamlit/config.toml; a test keeps them in step
  and checks that every text/background pair meets WCAG AA);
- `CSS`: the custom CSS Streamlit's own theme cannot express (the top bar, cards, number cards,
  pills, expanders), in one place;
- HTML pieces: `top_bar_html`, `tag_html`, `callout_html`;
- `money`, `fmt_date`, `fmt_day`, `humanize_dates`;
- tables: `prepare_table` and `show_table`;
- charts: the Plotly template and `style_forecast_chart` / `style_scenario_chart`. The chart
  builders in lookahead/ui.py tag each trace with a role; this module decides how a role looks.

No external calls: Manrope is a file in app/static/fonts, named in .streamlit/config.toml.
"""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from app.navigation import section_links, section_of, tabs_of
from lookahead.demo_mode import BANNER, enable_if_demo, is_demo
from lookahead.recurring_store import Stored
from lookahead.safe_to_spend import SafeToSpend, exact_figure, safe_to_spend, sentence

STATIC = Path(__file__).resolve().parent / "static"
FAVICON = STATIC / "favicon.png"


@dataclass(frozen=True)
class Palette:
    white: str = "#FFFFFF"
    topbar: str = "#17603E"  # the green bar
    topbar_text: str = "#D3EBDD"  # page links and the data date on the bar
    brand_light: str = "#BFE7CF"  # the line in the wordmark
    bg: str = "#F6F8F6"  # page background
    card: str = "#FFFFFF"
    border: str = "#E2E8E4"  # card outline
    border_input: str = "#D5DED8"  # button and pill outline
    hairline: str = "#EDF1EE"  # dividers inside a card, chart gridlines
    text: str = "#14201A"
    muted: str = "#55665C"
    faint: str = "#66756C"  # the design's #7A8A80, a little darker so it is AA on white
    green: str = "#1F7A4F"  # money in, good news
    green_dark: str = "#17603E"  # primary buttons, links
    soft_green: str = "#E6F3EC"
    ok_border: str = "#CFE7D9"
    blue: str = "#2E5F9A"  # the forecast band, "detected", scenario results
    soft_blue: str = "#EAF0F8"
    panel_blue: str = "#F3F7FC"
    amber: str = "#8A5300"  # a possible shortfall
    amber_line: str = "#B26B00"  # the buffer line
    amber_bg: str = "#FDF3E1"
    amber_border: str = "#F1D9A8"
    amber_dark: str = "#3D2A08"
    amber_link: str = "#6B4000"
    ended_bg: str = "#FAFBFA"
    grey_tag: str = "#EEF1EF"
    add_border: str = "#9CC7AF"
    divider: str = "#9AAEA2"


COLORS = Palette()
FONT = "Manrope, system-ui, sans-serif"

# --- Money and dates ---


def money(cents: int, *, signed: bool = False, markdown: bool = False) -> str:
    """Integer cents as $1,234.56 (a real minus sign, −, in front of a negative; `signed` also puts
    a plus in front of a positive). `markdown` escapes the $ so Streamlit does not read it as a
    formula."""
    sign = "−" if cents < 0 else ("+" if signed else "")
    dollar = "\\$" if markdown else "$"
    return f"{sign}{dollar}{abs(cents) / 100:,.2f}"


def money_short(cents: int) -> str:
    """$200 for a whole number of dollars, $200.50 otherwise (for a buffer in a subtitle)."""
    return f"${cents // 100:,}" if cents % 100 == 0 and cents >= 0 else money(cents)


def fmt_date(day: date) -> str:
    """Oct 2, 2026."""
    return f"{day:%b} {day.day}, {day.year}"


def fmt_day(day: date) -> str:
    """Oct 2 (no year: for a day close to now)."""
    return f"{day:%b} {day.day}"


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def humanize_dates(text: str) -> str:
    """Turn ISO dates inside a sentence into Oct 2, 2026. Something that is not a date stays."""

    def one(match: re.Match[str]) -> str:
        try:
            return fmt_date(date(int(match[1]), int(match[2]), int(match[3])))
        except ValueError:
            return match[0]

    return _ISO_DATE.sub(one, text)


# --- Small HTML pieces ---

_TAGS = {
    "detected": (COLORS.soft_blue, COLORS.blue),
    "confirmed": (COLORS.soft_green, COLORS.green_dark),
    "ended": (COLORS.grey_tag, COLORS.muted),
    "neutral": (COLORS.grey_tag, COLORS.muted),
}


def tag_html(text: str, kind: str = "neutral") -> str:
    """A small status pill: Detected (blue), Confirmed (green), Ended (grey)."""
    background, colour = _TAGS[kind]
    return (
        f'<span style="font-size: 12px; font-weight: 700; background: {background}; '
        f'color: {colour}; padding: 4px 10px; border-radius: 999px">{html.escape(text)}</span>'
    )


def callout_html(
    kind: str, tag: str, sentence_html: str, link_text: str | None = None,
    link_href: str | None = None,
) -> str:  # fmt: skip
    """The one-sentence callout: amber for a possible shortfall, green for on track. The sentence
    is HTML that the page built from numbers (it may use <strong>); the tag and link are escaped."""
    if kind == "warning":
        box = f"background: {COLORS.amber_bg}; border: 1px solid {COLORS.amber_border}"
        pill = COLORS.amber
        words, link = COLORS.amber_dark, COLORS.amber_link
    else:
        box = f"background: {COLORS.soft_green}; border: 1px solid {COLORS.ok_border}"
        pill = COLORS.green
        words, link = COLORS.text, COLORS.green_dark
    anchor = ""
    if link_text and link_href:
        anchor = (
            f'<a href="{html.escape(link_href)}" style="font-weight: 700; font-size: 15px; '
            f'color: {link}">{html.escape(link_text)}</a>'
        )
    return (
        f'<div class="la-callout" style="{box}; border-radius: 14px; padding: 18px 20px; '
        f'display: flex; flex-wrap: wrap; gap: 14px 20px; align-items: center">'
        f'<span style="font-size: 12px; font-weight: 800; letter-spacing: 0.04em; '
        f"text-transform: uppercase; background: {pill}; color: {COLORS.white}; padding: 5px 10px; "
        f'border-radius: 999px">{html.escape(tag)}</span>'
        f'<p style="margin: 0; flex: 1 1 420px; font-size: 16px; color: {words}; '
        f'line-height: 1.5">{sentence_html}</p>{anchor}</div>'
    )


_MARK = (
    f'<svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">'
    f'<circle cx="13" cy="13" r="12" stroke="{COLORS.white}" stroke-width="2"></circle>'
    f'<path d="M6 16 L11 11 L15 14 L20 8" stroke="{COLORS.brand_light}" stroke-width="2.2" '
    f'stroke-linecap="round" stroke-linejoin="round"></path></svg>'
)

PAGES = section_links()  # the five top-bar links (app/navigation.py)


def top_bar_html(
    active: str, pages: list[tuple[str, str]], data_to: date | None, source: str, query: str = "",
    until: str | None = None,
) -> str:  # fmt: skip
    """The green bar: wordmark, page links (the current page marked), the date the data runs to,
    and which data is shown (Synthetic demo or My data). `query` ("data=...&account=...") is added
    to every link: a link opens a new browser session, and the address is what carries the
    data and account choice over."""
    suffix = f"?{query}" if query else ""
    section = section_of(active)
    links = []
    for label, path in pages:
        current = ' aria-current="page"' if label == section else ""
        href = html.escape(path + suffix)
        links.append(f'<a href="{href}" target="_self"{current}>{html.escape(label)}</a>')
    if until is None:  # a page that is not about one account's data says so in `until`
        until = f"Data to {fmt_date(data_to)}" if data_to is not None else "No data yet"
        if is_demo():  # the public demo: no data-date chip (my own data keeps it)
            until = ""
    return (
        '<header class="la-bar"><div class="la-bar-inner">'
        f'<a href="{html.escape("/" + suffix)}" target="_self" class="la-brand">'
        f"{_MARK}Lookahead</a>"
        f'<nav aria-label="Main" class="la-nav">{"".join(links)}</nav>'
        f'<div class="la-bar-right">'
        + (f'<span class="la-until">{html.escape(until)}</span>' if until else "")
        + f'<span class="la-source">{html.escape(source)}</span></div></div>'
        f"{section_tabs_html(active, query)}</header>"
    )


def section_tabs_html(active: str, query: str = "") -> str:
    """The row of tab links under the bar for a section with more than one page (Transactions,
    Forecast, How it works), the current page marked; "" for the others."""
    tabs = tabs_of(active)
    if not tabs:
        return ""
    suffix = f"?{query}" if query else ""
    links = []
    for label, path, current in tabs:
        mark = ' aria-current="page"' if current else ""
        href = html.escape(path + suffix)
        links.append(f'<a href="{href}" target="_self"{mark}>{html.escape(label)}</a>')
    section = html.escape(section_of(active))
    return f'<nav aria-label="{section}" class="la-tabs">{"".join(links)}</nav>'


# --- Recurring: series cards, the payments table, monthly totals ---

EVERY = {"weekly": "every week", "biweekly": "every 2 weeks", "semimonthly": "twice a month",
         "monthly": "every month", "yearly": "every year"}  # fmt: skip
PER = {"weekly": "week", "biweekly": "2 weeks", "semimonthly": "half-month", "monthly": "month",
       "yearly": "year"}  # fmt: skip
_PER_MONTH = {"weekly": 52 / 12, "biweekly": 26 / 12, "semimonthly": 2.0, "monthly": 1.0,
              "yearly": 1 / 12}  # fmt: skip


def monthly_totals(series: Sequence[Stored]) -> tuple[int, int]:
    """About what the active series take out and bring in each month, in cents, each rounded
    to the nearest $10. Ended and rejected series are left out."""
    out = money_in = 0.0
    for s in series:
        if not s.active or s.status == "rejected":
            continue
        month = abs(s.expected_cents) * _PER_MONTH[s.cadence]
        if s.direction == "in":
            money_in += month
        else:
            out += month
    return round(out / 1000) * 1000, round(money_in / 1000) * 1000


def series_summary(s: Stored) -> str:
    """The line under a series' name: what kind of payment it is and how steady the amount is."""
    if not s.active:
        return f"Last seen {fmt_day(s.last_paid)}" if s.last_paid else "No payment seen lately"
    low, high = (abs(c) for c in sorted(s.range_cents, key=abs))
    how = (f"same amount {EVERY[s.cadence]}" if s.amount_type == "fixed"
           else f"varies {money_short(low)}–{money_short(high)}")  # fmt: skip
    if s.kind == "e-transfer":
        by_hand = s.loose or bool(s.allowed_cents)
        return "Interac e-Transfer, " + ("sent by hand, the day varies" if by_hand else how)
    if s.kind == "own transfer":
        return "Transfer to my own account, " + how
    return how[0].upper() + how[1:]


def series_card_html(s: Stored, name: str, category: str | None, selected: bool) -> str:
    """One card of the Recurring grid: status tag and category, name, what it is, the amount per
    period and the next date. An ended series is faded and dashed; the selected one has a thick
    green border. (The page lays a see-through button over it, so the whole card is a click.)"""
    ended = not s.active
    if ended:
        tag, kind = "Ended", "ended"
    elif s.status == "confirmed":
        tag, kind = "Confirmed", "confirmed"
    elif s.status == "user-added":
        tag, kind = "Added by me", "neutral"
    elif s.status == "rejected":
        tag, kind = "Rejected", "neutral"
    else:
        tag, kind = "Detected", "detected"
    if selected:
        box = f"background: {COLORS.card}; border: 2px solid {COLORS.green}"
    elif ended:
        box = f"background: {COLORS.ended_bg}; border: 1px dashed {COLORS.border_input}"
    else:
        box = f"background: {COLORS.card}; border: 1px solid {COLORS.border}"
    faded = ended or s.status == "rejected"
    ink = COLORS.muted if faded else COLORS.text
    money_in = s.direction == "in" and not faded
    amount = ("+" if money_in else "") + money_short(abs(s.expected_cents))
    amount_colour = COLORS.green_dark if money_in else ink
    until = "Not expected" if ended else f"Next {fmt_day(s.next_expected)}"
    row = "display: flex; justify-content: space-between; gap: 8px"
    return (
        f'<div class="la-series" style="{box}; border-radius: 14px; padding: 18px; height: 100%; '
        f'box-sizing: border-box; display: flex; flex-direction: column; gap: 12px">'
        f'<div style="{row}; align-items: center">{tag_html(tag, kind)}'
        f'<span style="font-size: 12px; font-weight: 600; color: {COLORS.muted}">'
        f"{html.escape(category or 'No category')}</span></div>"
        f'<div style="display: flex; flex-direction: column; gap: 2px">'
        f'<span style="font-size: 17px; font-weight: 800; color: {ink}">{html.escape(name)}</span>'
        f'<span style="font-size: 13px; color: {COLORS.muted}">'
        f"{html.escape(series_summary(s))}</span></div>"
        f'<div style="{row}; align-items: baseline">'
        f'<span style="font-size: 22px; font-weight: 800; color: {amount_colour}; '
        f'white-space: nowrap">'
        f"{html.escape(amount)}"
        f'<span style="font-size: 14px; font-weight: 600; color: {COLORS.muted}"> '
        f"/ {PER[s.cadence]}</span></span>"
        f'<span style="font-size: 13px; color: {COLORS.muted}">{html.escape(until)}</span></div>'
        "</div>"
    )


def add_card_html() -> str:
    """The "Add one I pay" card: dashed green, a plus and three words."""
    return (
        f'<div class="la-series" style="border: 1.5px dashed {COLORS.add_border}; '
        f"border-radius: 14px; padding: 18px; height: 100%; box-sizing: border-box; "
        f"display: flex; align-items: center; justify-content: center; gap: 8px; "
        f'font-weight: 700; min-height: 140px; color: {COLORS.green_dark}">'
        '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">'
        '<path d="M9 3v12M3 9h12" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
        "</path></svg>Add one I pay</div>"
    )


def payments_table_html(rows: Sequence[tuple[str, str, int, int | None]]) -> str:
    """The payments of one series: Date, Description, Amount, Balance after. A row is (date text,
    description, signed cents, balance cents or None). Descriptions are untrusted text and are
    escaped; money in is green and bold."""
    if not rows:
        return '<p class="la-note">No payments in the data yet for this series.</p>'
    head = "".join(
        f'<th style="text-align: {align}; padding: 12px 16px; font-size: 12px; font-weight: 700; '
        f'color: {COLORS.muted}; letter-spacing: 0.03em; text-transform: uppercase">{label}</th>'
        for label, align in (("Date", "left"), ("Description", "left"), ("Amount", "right"),
                             ("Balance after", "right"))
    )  # fmt: skip
    body = []
    for day, text, cents, balance in rows:
        colour = COLORS.green_dark if cents > 0 else COLORS.text
        sign = "+" if cents > 0 else ""
        shown_balance = "" if balance is None else money(balance)
        body.append(
            f'<tr style="border-top: 1px solid {COLORS.hairline}">'
            f'<td style="padding: 12px 16px">{html.escape(day)}</td>'
            f'<td style="padding: 12px 16px; color: {COLORS.muted}">{html.escape(text)}</td>'
            f'<td style="padding: 12px 16px; text-align: right; font-weight: 700; '
            f'color: {colour}">{sign}{money(cents)}</td>'
            f'<td style="padding: 12px 16px; text-align: right; color: {COLORS.muted}">'
            f"{shown_balance}</td></tr>"
        )
    return (
        f'<div style="overflow-x: auto; border: 1px solid {COLORS.hairline}; border-radius: 12px">'
        f'<table style="width: 100%; min-width: 480px; border-collapse: collapse; margin: 0; '
        f'font-size: 14px"><thead><tr style="background: {COLORS.bg}">{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


# --- Home and Transactions ---


def alert_episodes_html(rows: Sequence[tuple[str, str, str]]) -> str:
    """The list of alert episodes. A row is (period, one line about it, status): "Open" is
    amber, "Dismissed" and "Closed" are grey."""
    if not rows:
        return '<p class="la-note">No earlier alerts.</p>'
    out = []
    for i, (period, summary, status) in enumerate(rows):
        line = "" if i == len(rows) - 1 else f"border-bottom: 1px solid {COLORS.hairline}"
        if status == "Open":
            tag = (
                f'<span style="font-size: 12px; font-weight: 700; background: {COLORS.amber_bg}; '
                f'color: {COLORS.amber}; padding: 4px 10px; border-radius: 999px">'
                f"{html.escape(status)}</span>"
            )
        else:
            tag = tag_html(status, "neutral")
        out.append(
            f'<div style="display: grid; grid-template-columns: 150px minmax(0, 1fr) auto; '
            f'gap: 12px; align-items: center; padding: 12px 0; {line}">'
            f'<span style="font-size: 13px; font-weight: 700; color: {COLORS.muted}">'
            f"{html.escape(period)}</span>"
            f'<span style="font-size: 14px">{html.escape(summary)}</span>{tag}</div>'
        )
    return f'<div style="display: flex; flex-direction: column">{"".join(out)}</div>'


def link_button_html(label: str, path: str, query: str = "", primary: bool = False) -> str:
    """A link that looks like a button (the deep green one is the main action). `query` is the
    data and account choice, so the next page opens on the same ones."""
    href = html.escape(path + (f"?{query}" if query else ""))
    if primary:
        look = (f"background: {COLORS.topbar}; color: {COLORS.white}; "
                f"border: 1px solid {COLORS.topbar}")  # fmt: skip
    else:
        look = (f"background: {COLORS.white}; color: {COLORS.text}; "
                f"border: 1px solid {COLORS.border_input}")  # fmt: skip
    return (
        f'<a class="la-linkbutton" href="{href}" target="_self" style="{look}; font-weight: 700; '
        f"font-size: 14px; padding: 10px 18px; border-radius: 10px; min-height: 44px; "
        f"box-sizing: border-box; display: inline-flex; align-items: center; "
        f'text-decoration: none">{html.escape(label)}</a>'
    )


def status_card_html(items: Sequence[tuple[str, str]]) -> str:
    """The Home page's status card: a few labelled facts side by side."""
    cells = "".join(
        f'<div style="display: flex; flex-direction: column; gap: 4px; min-width: 0">'
        f'<span style="font-size: 13px; color: {COLORS.muted}; font-weight: 600">'
        f"{html.escape(label)}</span>"
        f'<span style="font-size: 22px; font-weight: 800; overflow-wrap: anywhere">'
        f"{html.escape(value)}</span></div>"
        for label, value in items
    )
    return (
        f'<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); '
        f'gap: 20px">{cells}</div>'
    )


def transactions_table_html(
    rows: Sequence[tuple[str, str, str | None, int, int | None, bool]],
) -> str:
    """The trimmed transactions table: Date, Merchant, Category (a tag), Amount, Balance. A row is
    (date text, merchant, category or None, signed cents, balance cents or None, to check?).
    Merchant text is untrusted and is escaped; money in is green and bold."""
    if not rows:
        return '<p class="la-note">No transactions match.</p>'
    head = "".join(
        f'<th style="text-align: {align}; padding: 12px 16px; font-size: 12px; font-weight: 700; '
        f'color: {COLORS.muted}; letter-spacing: 0.03em; text-transform: uppercase">{label}</th>'
        for label, align in (("Date", "left"), ("Merchant", "left"), ("Category", "left"),
                             ("Amount", "right"), ("Balance", "right"))
    )  # fmt: skip
    dash = "\u2014"
    check_tag = (
        f' <span style="font-size: 12px; font-weight: 700; background: {COLORS.amber_bg}; '
        f'color: {COLORS.amber}; padding: 4px 10px; border-radius: 999px">to check</span>'
    )
    body = []
    for day, merchant, category, cents, balance, check in rows:
        colour = COLORS.green_dark if cents > 0 else COLORS.text
        sign = "+" if cents > 0 else ""
        tags = (tag_html(category, "neutral") if category else dash) + (check_tag if check else "")
        body.append(
            f'<tr style="border-top: 1px solid {COLORS.hairline}">'
            f'<td style="padding: 10px 16px; white-space: nowrap">{html.escape(day)}</td>'
            f'<td style="padding: 10px 16px">{html.escape(merchant) or dash}</td>'
            f'<td style="padding: 10px 16px">{tags}</td>'
            f'<td style="padding: 10px 16px; text-align: right; font-weight: 700; '
            f'color: {colour}; white-space: nowrap">{sign}{money(cents)}</td>'
            f'<td style="padding: 10px 16px; text-align: right; color: {COLORS.muted}; '
            f'white-space: nowrap">{dash if balance is None else money(balance)}</td></tr>'
        )
    return (
        f'<div style="overflow-x: auto; border: 1px solid {COLORS.hairline}; border-radius: 12px">'
        f'<table style="width: 100%; min-width: 560px; border-collapse: collapse; margin: 0; '
        f'font-size: 14px"><thead><tr style="background: {COLORS.bg}">{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


# --- The custom CSS (Streamlit's own theme does the rest) ---

CSS = f"""
#MainMenu, footer, [data-testid="stDecoration"] {{ display: none !important; }}
html, body, [data-testid="stApp"] {{ background: {COLORS.bg}; color: {COLORS.text};
  font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }}
[data-testid="stMainBlockContainer"] {{ max-width: 1200px; padding: 0 24px 64px; }}
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {{ gap: 24px; }}
[data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {{ margin: 0 !important; padding: 0 !important; }}
h1 {{ font-size: 34px !important; font-weight: 800 !important; letter-spacing: -0.02em;
  line-height: 1.2; }}
h2, h3 {{ font-size: 20px !important; font-weight: 800 !important; letter-spacing: 0; }}
[data-testid="stMarkdownContainer"] p.la-sub {{ margin: 6px 0 0; color: {COLORS.muted};
  font-size: 15px; }}
[data-testid="stMarkdownContainer"] p.la-note {{ margin: 0; color: {COLORS.muted};
  font-size: 13px; }}
[data-testid="stMarkdownContainer"] p.la-foot {{ margin: 0; color: {COLORS.faint}; font-size: 12px;
  text-align: center; }}

/* a jump link (See why) lands below the sticky bar, not under it */
[id="alerts"], [id="evidence"] {{ scroll-margin-top: 96px; }}

/* the block that carries this stylesheet takes no room (it would add a gap above the bar) */
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style) {{ display: none; }}

/* the green top bar: the first block of the page, sticky at the top, so it takes its own height
   (one row or three) and can never cover the title. A huge spread shadow, clipped above and
   below, paints it across the full width while its content stays in the 1200px column. */
[data-testid="stLayoutWrapper"]:has(> .st-key-topbar) {{ position: sticky; top: 0; z-index: 100;
  margin-bottom: 8px; }}
.st-key-topbar {{ background: {COLORS.topbar}; padding: 0; gap: 0;
  box-shadow: 0 0 0 100vmax {COLORS.topbar}; clip-path: inset(0 -100vmax); }}
.la-bar {{ background: {COLORS.topbar}; color: {COLORS.white}; }}
.la-bar-inner {{ margin: 0; padding: 0; display: flex;
  flex-wrap: wrap; align-items: center; gap: 12px 28px; min-height: 64px; }}
.la-brand {{ display: flex; align-items: center; gap: 10px; color: {COLORS.white} !important;
  text-decoration: none !important; font-weight: 800; font-size: 20px; letter-spacing: -0.02em; }}
.la-nav {{ display: flex; flex-wrap: wrap; gap: 4px; flex: 1 1 auto; }}
.la-nav a {{ color: {COLORS.topbar_text} !important; text-decoration: none !important;
  font-weight: 600; font-size: 15px; padding: 8px 12px; border-radius: 8px; }}
.la-nav a:hover {{ background: rgba(255, 255, 255, 0.10); }}
.la-nav a[aria-current="page"] {{ color: {COLORS.white} !important;
  background: rgba(255, 255, 255, 0.14); font-weight: 700; }}
.la-bar-right {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-right: 60px; }}
/* the tab row of a section with several pages (Transactions, Forecast, How it works) */
.la-tabs {{ display: flex; gap: 4px; padding: 0 0 10px; overflow-x: auto; scrollbar-width: none; }}
.la-tabs a {{ color: {COLORS.topbar_text} !important; text-decoration: none !important;
  font-weight: 600; font-size: 14px; padding: 6px 14px; border-radius: 999px;
  white-space: nowrap; }}
.la-tabs a:hover {{ background: rgba(255, 255, 255, 0.10); }}
.la-tabs a[aria-current="page"] {{ background: {COLORS.white}; color: {COLORS.topbar} !important;
  font-weight: 700; }}
.la-until {{ font-size: 13px; color: {COLORS.topbar_text}; }}
.la-source {{ font-size: 12px; font-weight: 700; background: {COLORS.white};
  color: {COLORS.topbar}; padding: 4px 10px; border-radius: 999px; }}

/* the header holds only the settings button, drawn as a gear: it sits at the right end of the
   green bar. The gear is a text character (U+2699, text style): no image, nothing fetched. */
[data-testid="stHeader"] {{ position: fixed; top: 0; z-index: 101;
  right: max(24px, calc((100vw - 1200px) / 2 + 24px));
  left: auto; width: auto; height: 64px; background: transparent; display: flex;
  align-items: center; }}
[data-testid="stToolbar"] {{ position: static; padding: 0; }}
[data-testid="stExpandSidebarButton"] {{ width: 44px; height: 44px; padding: 0;
  background: rgba(255, 255, 255, 0.14); border-radius: 10px; display: flex;
  align-items: center; justify-content: center; }}
[data-testid="stExpandSidebarButton"] span {{ display: none; }}
[data-testid="stExpandSidebarButton"]::after {{ content: "\\2699\\FE0E"; color: {COLORS.white};
  font-size: 22px; line-height: 1; }}

/* Streamlit's markdown block pulls the next block up by 1rem; an HTML block needs no such pull */
[data-testid="stMarkdownContainer"] {{ margin-bottom: 0 !important; }}
[data-testid="stMarkdownContainer"] > p:last-child {{ margin-bottom: 0; }}

/* cards: a bordered container with a key starting "card-" (the border is the block itself) */
[class*="st-key-card-"][data-testid="stVerticalBlock"] {{ background: {COLORS.card};
  border: 1px solid {COLORS.border}; border-radius: 16px; padding: 24px; gap: 16px; }}
[data-testid="stColumn"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] {{
  flex: 1 1 auto; }}
[data-testid="stLayoutWrapper"] > [class*="st-key-card-"] {{ height: 100%; }}
[data-testid="stMetric"] {{ background: {COLORS.card}; border: 1px solid {COLORS.border};
  border-radius: 14px; padding: 20px; display: flex; flex-direction: column; gap: 6px;
  min-height: 132px; }}
[data-testid="stMetricLabel"] {{ color: {COLORS.muted}; font-weight: 600; }}
[data-testid="stMetricLabel"] * {{ font-size: 13px !important; white-space: normal !important;
  overflow: visible !important; text-overflow: clip !important; }}
[data-testid="stMetricValue"] * {{ font-size: 30px !important; font-weight: 800 !important;
  letter-spacing: -0.02em; line-height: 1.2; }}
[data-testid="stMetricDelta"] {{ background: transparent !important; padding: 0 !important;
  color: {COLORS.muted} !important; font-size: 13px !important; font-weight: 400; }}
[data-testid="stMetricDelta"] * {{ color: {COLORS.muted} !important; font-size: 13px !important;
  font-weight: 400; }}
.st-key-tone-warn [data-testid="stMetricValue"] * {{ color: {COLORS.amber} !important; }}
.st-key-evidence {{ background: {COLORS.card}; border: 1px solid {COLORS.border};
  border-radius: 16px; padding: 8px 24px; gap: 0; }}
.st-key-evidence [data-testid="stExpander"] details {{ background: transparent; border: 0;
  border-bottom: 1px solid {COLORS.hairline}; border-radius: 0; padding: 4px 0; }}
.st-key-evidence [data-testid="stExpander"]:last-child details {{ border-bottom: 0; }}
.st-key-evidence summary {{ padding: 10px 0; }}
.st-key-evidence summary p {{ font-size: 16px !important; font-weight: 700 !important; }}

/* Recurring: a grid of series cards. Each card is a keyed block ("seriescard-...") holding the
   card's HTML and a see-through button laid over all of it, so the whole card is one click. */
.st-key-seriesgrid {{ display: grid !important; gap: 16px; align-items: stretch !important;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }}
[class*="st-key-seriescard-"][data-testid="stVerticalBlock"] {{ position: relative; gap: 0;
  height: 100%; }}
[class*="st-key-seriescard-"] > [data-testid="stElementContainer"]:not(:has(button)) {{
  height: 100%; }}
[class*="st-key-seriescard-"] [data-testid="stMarkdown"],
[class*="st-key-seriescard-"] [data-testid="stMarkdown"] > div,
[class*="st-key-seriescard-"] [data-testid="stMarkdownContainer"] {{ height: 100%; }}
[class*="st-key-seriescard-"] > [data-testid="stElementContainer"]:has(button) {{
  position: absolute;
  inset: 0; z-index: 2; }}
[class*="st-key-seriescard-"] button {{ width: 100%; height: 100%; opacity: 0; cursor: pointer;
  min-height: 0; }}
[class*="st-key-seriescard-"]:has(button:focus-visible) {{ outline: 3px solid {COLORS.green};
  outline-offset: 2px; border-radius: 14px; }}
[class*="st-key-seriescard-"]:hover .la-series {{ border-color: {COLORS.green}; }}
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-seriescard-"]) {{ width: auto; }}

/* pills: the what-if choices */
button[data-variant="pills"] {{ border-radius: 999px; border: 1px solid {COLORS.border_input};
  background: {COLORS.white}; color: {COLORS.text}; font-weight: 600; min-height: 44px;
  padding: 10px 14px; }}
button[data-variant="pills"][aria-pressed="true"] {{ border: 1.5px solid {COLORS.green};
  background: {COLORS.soft_green}; color: {COLORS.green_dark}; font-weight: 700; }}
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {{ min-height: 44px;
  font-weight: 700; }}

/* the settings panel (Streamlit's sidebar), kept small and plain */
[data-testid="stSidebar"] {{ border-right: 1px solid {COLORS.border}; }}
[data-testid="stSidebar"][aria-expanded="true"] {{ width: 380px !important;
  min-width: 380px !important; max-width: 100vw; }}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] label * {{ overflow-wrap: normal;
  word-break: normal; white-space: normal; }}
[data-testid="stSidebar"] [data-testid="stMetric"] {{ min-height: 0; padding: 14px 16px; }}
[data-testid="stSidebar"] [data-testid="stMetricValue"] * {{ font-size: 26px !important;
  white-space: nowrap !important; overflow: visible !important; text-overflow: clip !important; }}
[data-testid="stAlert"] {{ border-radius: 14px; }}

@media (max-width: 760px) {{
  [data-testid="stMainBlockContainer"] {{ padding: 0 16px 48px; }}
  /* the five links stay on one row of their own and scroll sideways; the gear stays top right */
  .la-nav {{ flex: 1 1 100%; order: 3; flex-wrap: nowrap; overflow-x: auto;
    scrollbar-width: none; }}
  .la-nav a {{ white-space: nowrap; }}
  .la-bar-right {{ padding-right: 56px; }}
  .la-bar-inner {{ padding: 12px 0; gap: 8px 16px; }}
  [data-testid="stHeader"] {{ top: 10px; right: 16px; height: auto; }}
  [data-testid="stMetric"] {{ min-height: 0; padding: 16px; }}
  h1 {{ font-size: 28px !important; }}
}}
@media (max-width: 480px) {{
  /* the wordmark row is as tall as the gear beside it, so the gear never covers a link; the data
     date and source move under the links */
  .la-brand {{ min-height: 56px; }}
  .la-bar-right {{ flex: 1 1 100%; order: 4; padding-right: 0; }}
}}
"""


def apply(title: str) -> None:
    """Page settings and the custom CSS. Call first on every page. The tab icon is a local file: a
    Material icon shortcode would be fetched from Google."""
    st.set_page_config(
        page_title=f"{title} · Lookahead", page_icon=str(FAVICON), layout="wide",
        initial_sidebar_state="collapsed",
    )  # fmt: skip
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
    pio.templates.default = "lookahead"
    if not enable_if_demo() and not served_locally():
        st.error(
            "Lookahead with your own data runs on this computer only. Start it with "
            "`streamlit run app/Home.py --server.address localhost`, or put "
            '`address = "localhost"` under `[server]` in `~/.streamlit/config.toml`.'
        )
        st.stop()  # nothing is read before this point


LOOPBACK = ("localhost", "127.0.0.1", "::1")


def served_locally() -> bool:
    """True when the server listens on this computer only (DECISIONS.md, 2026-10-11): outside
    demo mode the pages refuse to run otherwise, whatever the config file or a flag says."""
    return st.get_option("server.address") in LOOPBACK


def demo_banner_html() -> str:
    """The banner of demo mode, at the top of every page."""
    return (
        f'<div class="la-demo-banner" role="note" style="background: {COLORS.amber_bg}; border: '
        f"1px solid {COLORS.amber_border}; color: {COLORS.amber_dark}; border-radius: 10px; "
        f'padding: 8px 14px; margin-top: 10px; font-size: 15px"><strong>{BANNER}</strong> · '
        "every account and transaction here is generated for the demo. Importing bank files and "
        "the optional LLM feature are turned off.</div>"
    )


def top_bar(slot: "st.delta_generator.DeltaGenerator", active: str, data_to: date | None,
            source: str, until: str | None = None) -> None:  # fmt: skip
    """Draw the green bar into the container `slot` (made first, filled once the data is known)."""
    with slot:
        st.markdown(
            top_bar_html(active, PAGES, data_to, source, choice_query(), until),
            unsafe_allow_html=True,
        )
        if is_demo():  # every page draws the bar, so every page says it is the demo
            st.markdown(demo_banner_html(), unsafe_allow_html=True)


# --- The data and account choice survives a click on the top bar ---
# Streamlit keeps a widget's value only inside one browser session, and a plain link starts a new
# one. So the three choices live in the address (`?data=...&account=...&text=...`): written after
# the widgets are drawn, read before they are drawn on the next page.

CHOICES = {"data_source": "data", "account": "account", "pseudonymize": "text"}


def seed_choice(key: str, options: list[str] | None = None) -> None:
    """Call before drawing the widget with this key. If the widget has no value yet and the
    address carries one that fits `options` (or, for the on/off toggle, "1" or "0"), use it; the
    toggle starts on. The widget itself is then drawn with no default of its own. A
    value held from before that no longer fits (another data source has other accounts) goes."""
    if options is not None and st.session_state.get(key) not in (*options, None):
        del st.session_state[key]
    if key in st.session_state:
        return
    wanted = st.query_params.get(CHOICES[key])
    if options is None:  # an on/off toggle, on unless the address says "0"
        st.session_state[key] = wanted != "0"
    elif wanted in options:
        st.session_state[key] = wanted


def _choice_pairs() -> list[tuple[str, str]]:
    pairs = []
    for key, name in CHOICES.items():
        if key in st.session_state:
            value = st.session_state[key]
            pairs.append((name, ("1" if value else "0") if isinstance(value, bool) else value))
    return pairs


def choice_query() -> str:
    """The current choices as an address query string ("" if none are set yet)."""
    return urlencode(_choice_pairs())


def save_choices() -> None:
    """Write the current choices into the address, so a reload or a copied link keeps them."""
    for name, value in _choice_pairs():
        st.query_params[name] = value


SOURCES = ["Synthetic demo", "My data"]


def data_sources() -> list[str]:
    """The data choices: synthetic only in demo mode."""
    return SOURCES[:1] if is_demo() else list(SOURCES)


def data_settings() -> tuple[str, bool]:
    """The settings panel's first two choices, the same on every page: which data (Synthetic demo
    or My data) and whether text is pseudonymized. Returns (source, pseudonymize)."""
    side_label("Data")
    sources = data_sources()
    seed_choice("data_source", sources)
    source = st.sidebar.radio("Data", sources, label_visibility="collapsed", key="data_source")
    seed_choice("pseudonymize")
    pseudonymize = st.sidebar.toggle(
        "Pseudonymize text", key="pseudonymize",
        help="Hides people's names and reference numbers; business names stay readable.",
    )  # fmt: skip
    return source, pseudonymize


def account_setting(names: list[str]) -> str:
    """The account choice, the same on every page; it is written into the address."""
    side_label("Account")
    seed_choice("account", names)
    account = st.sidebar.selectbox("Account", names, label_visibility="collapsed", key="account")
    save_choices()
    return account


def side_label(text: str) -> None:
    """A small section label in the settings panel."""
    st.sidebar.markdown(f'<p class="la-note"><strong>{html.escape(text)}</strong></p>',
                        unsafe_allow_html=True)  # fmt: skip


def chart_legend_html(buffer_cents: int) -> str:
    """The legend written above the chart, as in the design: Actual, Middle future, 80% of futures,
    and the buffer."""

    def line(colour: str, width: float, dash: str = "") -> str:
        return (
            f'<svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" '
            f'stroke="{colour}" stroke-width="{width}"{dash}></line></svg>'
        )

    band = (
        f'<span style="width: 22px; height: 12px; background: {_rgba(COLORS.blue, 0.18)}; '
        f'border-radius: 3px"></span>'
    )
    items = [
        (line(COLORS.text, 2.5), "Actual"),
        (line(COLORS.green, 3), "Middle future"),
        (band, "80% of futures"),
        (
            line(COLORS.amber_line, 2, ' stroke-dasharray="4 3"'),
            f"Buffer {money_short(buffer_cents)}",
        ),
    ]
    parts = "".join(
        f'<span style="display: flex; align-items: center; gap: 6px">'
        f"{mark}{html.escape(text)}</span>"
        for mark, text in items
    )
    return (
        f'<div class="la-legend" style="display: flex; flex-wrap: wrap; gap: 8px 16px; '
        f'font-size: 13px; color: {COLORS.muted}; align-items: center; justify-content: flex-end">'
        f"{parts}</div>"
    )


def result_panel_html(rows: list[tuple[str, str, str, str]]) -> str:
    """The what-if result: a blue panel, two columns, each `before → after`. A row is (label,
    before, after, verdict) with verdict "better" (green), "worse" (amber) or "same"."""
    colour = {"better": COLORS.green_dark, "worse": COLORS.amber, "same": COLORS.text}
    small = f"font-size: 12px; font-weight: 600; color: {COLORS.muted}"
    line = "display: flex; justify-content: space-between; align-items: baseline; gap: 8px"
    cells = "".join(
        f'<div style="display: flex; flex-direction: column; gap: 4px; min-width: 0">'
        f'<span style="font-size: 13px; color: {COLORS.blue}; font-weight: 700">'
        f"{html.escape(label)}</span>"
        f'<span style="{line}"><span style="{small}">Before</span>'
        f'<span style="font-size: 16px; font-weight: 700; white-space: nowrap">'
        f"{html.escape(before)}</span></span>"
        f'<span style="{line}"><span style="{small}">After</span>'
        f'<span style="font-size: 20px; font-weight: 800; white-space: nowrap; '
        f'color: {colour[verdict]}">{html.escape(after)}</span></span></div>'
        for label, before, after, verdict in rows
    )
    return (
        f'<div class="la-panel" style="background: {COLORS.panel_blue}; border-radius: 12px; '
        f"padding: 16px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); "
        f'gap: 16px">{cells}</div>'
    )


def scenario_legend_html(buffer_cents: int) -> str:
    """The legend above the comparison chart: the original (grey) and with your change (green and
    blue), each with its band, and the buffer."""

    def swatch(line_colour: str, band: str, dash: str = "") -> str:
        return (
            f'<svg width="26" height="14" aria-hidden="true"><rect x="0" y="1" width="26" '
            f'height="12" rx="2" fill="{band}"></rect><line x1="0" y1="7" x2="26" y2="7" '
            f'stroke="{line_colour}" stroke-width="2.5"{dash}></line></svg>'
        )

    items = [
        (
            swatch(COLORS.muted, _rgba(COLORS.muted, 0.15), ' stroke-dasharray="6 3 1 3"'),
            "Original",
        ),
        (swatch(COLORS.green, _BLUE_BAND), "With your change"),
        (
            f'<svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" '
            f'stroke="{COLORS.amber_line}" stroke-width="2" stroke-dasharray="4 3"></line></svg>',
            f"Buffer {money_short(buffer_cents)}",
        ),
    ]
    parts = "".join(
        f'<span style="display: flex; align-items: center; gap: 6px">'
        f"{mark}{html.escape(text)}</span>"
        for mark, text in items
    )
    return (
        f'<div class="la-legend" style="display: flex; flex-wrap: wrap; gap: 8px 16px; '
        f'font-size: 13px; color: {COLORS.muted}; align-items: center; justify-content: flex-end">'
        f"{parts}</div>"
    )


def coming_up_html(rows: list[tuple[str, str, str, str, bool]]) -> str:
    """The "Coming up" list. A row is (date, name, what it is, amount, is money in); money in is
    green and bold, other amounts are plain."""
    out = []
    for i, (day, name, sub, amount, money_in) in enumerate(rows):
        line = "" if i == len(rows) - 1 else f"border-bottom: 1px solid {COLORS.hairline}"
        weight, colour = ("800", COLORS.green_dark) if money_in else ("700", COLORS.text)
        out.append(
            f'<div style="display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; '
            f"gap: 12px; "
            f'align-items: center; padding: 12px 0; {line}">'
            f'<span style="font-size: 13px; font-weight: 700; color: {COLORS.muted}">'
            f"{html.escape(day)}</span>"
            f'<div style="display: flex; flex-direction: column; gap: 2px; min-width: 0">'
            f'<span style="font-weight: 700">{html.escape(name)}</span>'
            f'<span style="font-size: 12px; color: {COLORS.muted}">{html.escape(sub)}</span></div>'
            f'<span style="font-weight: {weight}; color: {colour}">'
            f"{html.escape(amount)}</span></div>"
        )
    return f'<div style="display: flex; flex-direction: column">{"".join(out)}</div>'


def footer(text: str) -> None:
    st.markdown(f'<p class="la-foot">{html.escape(text)}</p>', unsafe_allow_html=True)


# --- Tables ---


@dataclass(frozen=True)
class TableView:
    frame: pd.DataFrame  # the data, with real dates; column names unchanged
    labels: dict[str, str]  # column name -> the header shown
    positive_cells: dict[str, list[bool]]  # money-in column -> which cells are positive


def _label(name: str) -> str:
    return name.replace(" ($)", "").replace("_", " ")


def prepare_table(
    df: pd.DataFrame, money: list[str], dates: list[str], money_in: list[str] | None = None
) -> TableView:  # fmt: skip
    """Get a table ready to show: ISO dates become real dates (they sort properly and show as
    Oct 2, 2026), headers lose "($)" and underscores, and in the `money_in` columns the positive
    cells are noted so they can be shown in green (money in). The data keeps its own column names
    and numbers."""
    frame = df.copy()
    for column in dates:
        frame[column] = pd.to_datetime(frame[column]).dt.date
    labels = {c: _label(c) for c in frame.columns}
    positives = {c: [bool(v > 0) for v in frame[c]] for c in (money_in or [])}
    return TableView(frame, labels, positives)


def show_table(
    df: pd.DataFrame, money: list[str] = (), dates: list[str] = (),  # type: ignore[assignment]
    money_in: list[str] = (), widths: dict[str, str | int] | None = None, whole: bool = False,  # type: ignore[assignment]
) -> None:  # fmt: skip
    """A read-only table: no index, amounts as $1,234.56 (money in green where `money_in` says
    so), dates as Oct 2, 2026, plain headers. `widths` maps a column to "small", "medium", "large"
    or pixels. `whole` shows every row (for a short table) instead of scrolling inside it."""
    view = prepare_table(df, list(money), list(dates), list(money_in))
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
    if money_in:
        green = f"color: {COLORS.green_dark}; font-weight: 700"
        shown = shown.map(lambda v: green if v > 0 else "", subset=list(money_in))
    st.dataframe(shown, hide_index=True, width="stretch", column_config=config,
                 height="content" if whole else "auto")  # fmt: skip


# --- Charts ---


def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


TEMPLATE = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": FONT, "color": COLORS.text, "size": 12},
        margin={"l": 8, "r": 12, "t": 16, "b": 8},
        showlegend=False,
        hovermode="x unified",
        hoverlabel={
            "bgcolor": COLORS.card, "bordercolor": COLORS.border_input,
            "font": {"family": FONT, "color": COLORS.text, "size": 13},
        },
        xaxis={
            "showgrid": False, "linecolor": COLORS.hairline, "zeroline": False, "automargin": True,
            "tickformat": "%b %-d", "hoverformat": "%b %-d",
            "tickfont": {"color": COLORS.faint, "size": 11}, "title": {"text": ""},
        },
        yaxis={
            "gridcolor": COLORS.hairline, "zeroline": False, "automargin": True,
            "tickformat": "$~s", "hoverformat": "$,.2f",
            "tickfont": {"color": COLORS.faint, "size": 11}, "title": {"text": ""},
        },
    )
)  # fmt: skip
pio.templates["lookahead"] = TEMPLATE

_BLUE_BAND = _rgba(COLORS.blue, 0.18)
BUFFER_LINE = {"color": COLORS.amber_line, "dash": "dash", "width": 1.6}
ROLES: dict[str, dict] = {  # type: ignore[type-arg]
    "recent": {"line": {"color": COLORS.text, "width": 2.2, "dash": "solid"}},
    "band_edge": {"line": {"width": 0}},
    "band": {"line": {"width": 0}, "fillcolor": _BLUE_BAND},
    "median": {"line": {"color": COLORS.green, "width": 3, "dash": "solid"}},
    "median_without": {"visible": False},  # the design shows one middle line; the data stays
    "estimate": {"visible": False},
    "realized": {"line": {"color": COLORS.muted, "width": 2, "dash": "dot"}},  # Time machine
    "original_band": {"line": {"width": 0}, "fillcolor": _rgba(COLORS.muted, 0.15)},
    "original_median": {"line": {"color": COLORS.muted, "width": 2, "dash": "dashdot"}},
    "modified_band": {"line": {"width": 0}, "fillcolor": _BLUE_BAND},
    "modified_median": {"line": {"color": COLORS.green, "width": 3, "dash": "solid"}},
}
_HOVER = "%{y:$,.2f}<extra>%{fullData.name}</extra>"


def _apply_roles(figure: go.Figure) -> None:
    figure.update_layout(template=TEMPLATE)
    figure.update_layout(TEMPLATE.layout)  # also set explicitly, so the figure carries it
    for trace in figure.data:
        style = ROLES.get(trace.meta if isinstance(trace.meta, str) else "")
        if style is not None:
            trace.update(**style)
            if trace.meta not in ("band_edge", "median_without", "estimate"):
                trace.update(hovertemplate=_HOVER)


def style_forecast_chart(
    figure: go.Figure, forecast_start: date, forecast_end: date, low: tuple[date, int] | None
) -> go.Figure:  # fmt: skip
    """The design's chart: the actual line dark, the middle future green, the 80% band translucent
    blue, the buffer dashed amber, the forecast area lightly shaded with its start marked, and the
    low point marked and labelled. `low` is (day, cents) of the lowest expected balance."""
    _apply_roles(figure)
    figure.update_layout(title={"text": ""})  # the heading is written above the chart
    figure.layout.shapes = [s for s in figure.layout.shapes if s.name != "zero"]
    figure.layout.annotations = [a for a in figure.layout.annotations if a.text != "safety buffer"]
    figure.update_shapes(selector={"name": "buffer"}, line=BUFFER_LINE)
    figure.add_shape(type="rect", xref="x", yref="paper", x0=forecast_start, x1=forecast_end,
                     y0=0, y1=1, fillcolor=COLORS.panel_blue, line={"width": 0}, layer="below",
                     name="forecast area")  # fmt: skip
    figure.add_shape(type="line", xref="x", yref="paper", x0=forecast_start, x1=forecast_start,
                     y0=0, y1=1, line={"color": COLORS.divider, "width": 1},
                     name="forecast start")  # fmt: skip
    figure.add_annotation(x=forecast_start, y=1, xref="x", yref="paper", text="FORECAST",
                          showarrow=False, xanchor="left", yanchor="top", xshift=8, yshift=-4,
                          font={"color": COLORS.blue, "size": 11, "weight": 700})  # fmt: skip
    if low is not None:
        day, cents = low
        figure.add_scatter(
            x=[day], y=[cents / 100], mode="markers", name="Lowest expected balance",
            marker={"size": 12, "color": COLORS.card,
                    "line": {"color": COLORS.amber, "width": 2.5}},
            hovertemplate=_HOVER, meta="low",
        )  # fmt: skip
        figure.add_annotation(
            x=day, y=cents / 100, xref="x", yref="y", showarrow=False, yanchor="top", yshift=-14,
            text=f"Low {money(cents)} · {fmt_day(day)}", bgcolor=COLORS.amber, borderpad=5,
            font={"color": COLORS.white, "size": 12, "weight": 700},
        )  # fmt: skip
    return figure


def style_balance_chart(figure: go.Figure) -> go.Figure:
    """The Transactions page's end-of-day balance: the actual line in the theme's ink, no zero
    line, no titles (the heading is written above the chart)."""
    _apply_roles(figure)
    figure.layout.shapes = [s for s in figure.layout.shapes if s.name != "zero"]
    figure.update_layout(title={"text": ""})
    figure.update_xaxes(title_text="")
    figure.update_yaxes(title_text="")
    return figure


def style_scenario_chart(figure: go.Figure) -> go.Figure:
    """The original and the changed futures, in the same palette."""
    _apply_roles(figure)
    figure.layout.shapes = [s for s in figure.layout.shapes if s.name != "zero"]
    figure.layout.annotations = [a for a in figure.layout.annotations if a.text != "safety buffer"]
    figure.update_shapes(selector={"name": "buffer"}, line=BUFFER_LINE)
    return figure


def fit_y_range(figure: go.Figure, dollars: list[float], pad: float = 0.1) -> go.Figure:
    """Set the y-axis to the values that matter (the forecast, the buffer and the last few days),
    padded evenly, so one distant peak in the history cannot squash the forecast. Lines outside
    the range are cut off at the edge of the chart."""
    if not dollars:
        return figure
    low, high = min(dollars), max(dollars)
    margin = max((high - low) * pad, 50.0)  # at least $50 of room, so a flat line is visible
    figure.update_yaxes(range=[low - margin, high + margin])
    return figure


# --- Safe to spend (Forecast page and Home) ---


@st.cache_data(show_spinner="Working out a safe amount to spend...", max_entries=32)
def cached_safe_to_spend(db_path: str, version: object, account: str, key: bytes | None
                         ) -> SafeToSpend | None:  # fmt: skip
    """Safe-to-spend for the account as the data stands. The search runs a few dozen what-ifs, so
    the answer is kept until `version` changes (what the forecast rests on: the page passes the
    data date, the buffer and the placed payments)."""
    from lookahead.db import connect_readonly

    conn = connect_readonly(Path(db_path))
    try:
        return safe_to_spend(conn, account, "weekday", None, key)
    finally:
        conn.close()


def safe_to_spend_card(s: SafeToSpend | None) -> None:
    """The safe-to-spend card: the amount a day (or "No safe amount"), the usual everyday spending
    beside it, and the sentence. Always labelled an estimate."""
    if s is None:
        return
    with st.container(border=True, key="card-safe"):
        st.markdown(
            '<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">'
            f"<h2>Safe to spend</h2>{tag_html('estimate', 'neutral')}</div>",
            unsafe_allow_html=True,
        )
        left, right = st.columns(2)
        window = (f"until {fmt_day(s.income_day)}, the next expected income"
                  if s.income_day is not None else f"for the next {s.days} days")  # fmt: skip
        with left:
            if s.cents_per_day is None:
                st.metric("Everyday spending you can afford", "No safe amount", window,
                          delta_color="off", delta_arrow="off")  # fmt: skip
            elif s.plenty:
                st.metric("Everyday spending you can afford", "Plenty of room",
                          f"well above your usual, {window}", delta_color="off",
                          delta_arrow="off")  # fmt: skip
            else:
                more = "+" if s.capped else ""
                st.metric("Everyday spending you can afford",
                          f"${s.dollars_per_day:,}{more}/day", f"{window}, 80% confidence",
                          delta_color="off", delta_arrow="off")  # fmt: skip
        with right:
            st.metric("Your usual everyday spending", f"${s.usual_cents_per_day / 100:,.0f}/day",
                      "the forecast's estimate, same days", delta_color="off",
                      delta_arrow="off")  # fmt: skip
        small = f'<p class="la-note">{html.escape(exact_figure(s))}</p>' if s.plenty else ""
        st.markdown(
            f'<p style="margin: 0">{html.escape(sentence(s)).replace("$", "&#36;")}</p>{small}'
            '<p class="la-note">At least 80 of every 100 simulated futures stay at or above your '
            "buffer on every day of that period at this level, with recurring payments and fees as "
            "forecast. It is worked out from your own past weeks: an estimate, not a promise.</p>",
            unsafe_allow_html=True,
        )
