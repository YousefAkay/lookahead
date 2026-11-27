"""Smoke test of the public demo in a real browser (Playwright, Chromium). Run by hand, not by the
merge gate (it needs a browser): before a deploy on a local demo it starts itself, and after a
deploy on the live link.

    python -m tools.demo_smoke                       # starts a local demo on a fresh temp database
    python -m tools.demo_smoke --url https://....streamlit.app
    python -m tools.demo_smoke --screenshots docs/img   # also saves the three README pictures

It only ever looks at the synthetic demo. The local server is started with LOOKAHEAD_DEMO=1 (the
app then cannot open anything under data/private) and a demo database in a temporary folder. For
--url, the first page must show the "Demo with made-up data" banner, or it stops before anything
else.

Checks: every page opens with the banner, no error box and no browser console error (except the
two health probes Streamlit's own front end sends when a page is opened by its own address, and,
on the live link, the requests of Community Cloud's own page around the app); Home
leads with MADE-UP DATA and opens on synthetic-C; each visitor gets their own visit id, kept across
a top-bar click; Ask answers a question with its steps and rows; the Scenario chart's title is
inside the chart and its legend is one row; on Recurring, every card's "Next <date>" stays on one
line, for each demo account at four window widths; nothing in the green top bar sits under the
header's buttons (the gear, and Community Cloud's own toolbar on the live site) at four widths.
On Community Cloud the app runs in a frame inside Cloud's page; every check looks in that frame.
Exit code 0 when every check passes.
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode, urlparse

from playwright.sync_api import Browser, Frame, Page, sync_playwright

from app.navigation import APP_PAGES
from lookahead.forecast.simulate import N_FUTURES

ROOT = Path(__file__).resolve().parents[1]
BANNER = "Demo with made-up data"
ACCOUNTS = ("synthetic-A", "synthetic-B", "synthetic-C")
WIDTHS = (1280, 1000, 768, 390)
QUESTION = "How much did I spend on groceries last month?"
SCENARIO_DAILY_SPEND = "25"  # the README what-if: spend $25 a day
# Streamlit's front end, opened at /<Page>, first asks /<Page>/_stcore/health and /host-config
# (404) before it finds the server at the root. Not an error of the app: counted, not failed.
PROBE = re.compile(r"/[A-Za-z_]+/_stcore/(health|host-config)$")


def cloud_own(url: str, base: str) -> bool:
    """On the live link only: a request made by Community Cloud's page around the app (its
    analytics, its /api/ calls for a signed-in user), not by the app. The app loads nothing from
    another host and has no /api/ path."""
    where = urlparse(url)
    if not where.netloc:  # no address: cannot tell whose, so it counts
        return False
    return where.netloc != urlparse(base).netloc or where.path.startswith("/api/")
WAKE = "get this app back up"  # Community Cloud's page for an app that went to sleep
WAIT_MS = 180_000
NO_ACCOUNT = {"Model report", "Time machine"}  # pages not about one demo account


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""),
              flush=True)  # fmt: skip

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.rows)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_local(folder: Path) -> tuple[subprocess.Popen[bytes], str]:
    """A local demo server: demo mode on, its database in `folder`, nothing private set."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LOOKAHEAD_", "PSEUDONYM"))}
    env.pop("ANTHROPIC_API_KEY", None)
    env.update(LOOKAHEAD_DEMO="1", LOOKAHEAD_DEMO_DB=str(folder / "demo.db"))
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app/Home.py", "--server.port", str(port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    url = f"http://localhost:{port}"
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as r:
                if r.status == 200:
                    return server, url
        except OSError:
            time.sleep(0.5)
    server.kill()
    raise SystemExit("the local demo did not start")


def app(page: Page) -> Frame:
    """Where the app's own page is: on Community Cloud the app runs in a frame at /~/+/ inside
    Cloud's page; served locally it is the page itself."""
    for frame in page.frames:
        if "/~/+/" in frame.url:
            return frame
    return page.main_frame


def settle(page: Page) -> None:
    """Wait until the page shows the banner and Streamlit has finished running the script."""
    deadline = time.monotonic() + WAIT_MS / 1000
    while not app(page).locator(".la-demo-banner").count():  # Cloud's frame may come later
        if time.monotonic() > deadline:
            raise TimeoutError("no demo banner after 180 s")
        page.wait_for_timeout(300)
    app(page).wait_for_function(
        "() => document.querySelector('[data-testid=\"stApp\"]')"
        "?.getAttribute('data-test-script-state') === 'notRunning'",
        timeout=WAIT_MS,
    )
    page.wait_for_timeout(700)  # charts draw just after the script ends


def errors_on(page: Page) -> int:
    boxes = '[data-testid="stException"], [data-testid="stAlertContentError"]'
    return app(page).locator(boxes).count()


def address(base: str, path: str, **query: str) -> str:
    return base.rstrip("/") + path + ("?" + urlencode(query) if query else "")


def visit_of(page: Page) -> str | None:
    found = re.search(r"[?&]visit=([A-Za-z0-9_-]+)", app(page).url)
    return found.group(1) if found else None


def first_visit(page: Page, base: str, result: Result) -> bool:
    """The first page: wakes a sleeping app, times it, and makes sure this is the demo."""
    start = time.monotonic()
    page.goto(base, timeout=WAIT_MS)
    saw_building = False
    for _ in range(int(WAIT_MS / 500)):
        body = app(page).locator("body").inner_text(timeout=WAIT_MS)
        if WAKE in body:
            app(page).get_by_role("button", name=re.compile(WAKE, re.I)).click()
        if "Building the demo" in body:
            saw_building = True
        if app(page).locator(".la-demo-banner").count():
            break
        page.wait_for_timeout(500)
    if not app(page).locator(".la-demo-banner").count():
        result.check("the first page is the demo (banner shown)", False, "no banner: stopped")
        return False
    settle(page)
    seconds = time.monotonic() - start
    built = "yes" if saw_building else "no"
    result.check("the first page is the demo (banner shown)", True,
                 f"ready in {seconds:.0f} s; build message seen: {built}")  # fmt: skip
    return True


def check_home(page: Page, result: Result) -> None:
    intro = app(page).locator(".la-demo-intro")
    body = app(page).locator("body").inner_text()
    once = app(page).locator(".la-demo-banner").count() == 1 and "MADE-UP DATA" not in body
    result.check("Home: the made-up data said once (the banner), then two plain intro lines",
                 once and intro.count() == 1 and "your own copy" in intro.inner_text())  # fmt: skip
    account = app(page).get_by_role("combobox", name="Account")
    shown = account.input_value() if account.count() else ""
    if not shown:  # the settings panel may be closed: read the choice from the links
        shown = "synthetic-C" if "account=synthetic-C" in app(page).url else app(page).url
    result.check("the demo opens on synthetic-C", shown == "synthetic-C", shown)


def _current_section(page: Page) -> str:
    try:
        marked = app(page).locator('.la-nav a[aria-current="page"]')
        return marked.first.inner_text(timeout=1000).strip() if marked.count() else ""
    except Exception:  # noqa: BLE001 - the page is being replaced
        return ""


def check_visits(browser: Browser, base: str, first: Page, result: Result) -> None:
    mine = visit_of(first)
    other = browser.new_context().new_page()
    other.goto(base, timeout=WAIT_MS)
    settle(other)
    theirs = visit_of(other)
    other.context.close()
    result.check("each visitor gets their own visit id", bool(mine and theirs and mine != theirs))
    kept, stuck, nested, unsynced = True, [], [], []
    for link in ("Forecast", "How it works", "Home"):  # How it works reads no demo database
        app(first).get_by_role("link", name=link).first.click()
        deadline = time.monotonic() + 60
        while _current_section(first) != link and time.monotonic() < deadline:
            first.wait_for_timeout(300)  # the click must really open that page
        if _current_section(first) != link:
            stuck.append(link)
            continue
        settle(first)
        kept = kept and visit_of(first) == mine
        if sum("/~/+/" in f.url for f in first.frames) > 1:  # Cloud's page loaded again inside
            nested.append(link)
        inner = urlparse(app(first).url).path.replace("/~/+/", "/")
        if urlparse(first.url).path != inner:  # the address bar must show the page, for a reload
            unsynced.append(link)  # or a shared link
    result.check("the visit id is kept across top-bar clicks (incl. How it works)", kept)
    result.check("each top-bar click opens its page", not stuck, ", ".join(stuck))
    result.check("a top-bar link never loads Cloud's page inside the app's frame", not nested,
                 ", ".join(nested))  # fmt: skip
    result.check("the address bar shows the page after each top-bar click", not unsynced,
                 ", ".join(unsynced))  # fmt: skip


def check_pages(page: Page, base: str, visit: str, result: Result) -> None:
    for p in APP_PAGES:
        page.goto(address(base, "/" + p.url_path, visit=visit), timeout=WAIT_MS)
        settle(page)
        n = errors_on(page)
        result.check(f"{p.name}: opens with the banner and no error box", n == 0,
                     f"{n} error boxes" if n else "")  # fmt: skip
        if p.name not in NO_ACCOUNT:
            result.check(f"{p.name}: opens on synthetic-C", "account=synthetic-C" in app(page).url,
                         app(page).url.split("?", 1)[-1])  # fmt: skip


def check_forecast_wait(page: Page, base: str, visit: str, result: Result) -> None:
    """Forecast takes seconds to simulate its futures: within 3 s it shows the waiting message or
    the forecast itself (its figure cards), never a page with only the bar."""
    start = time.monotonic()
    page.goto(address(base, "/Forecast", visit=visit), timeout=WAIT_MS)
    first = None
    while time.monotonic() - start < 60:
        body = app(page).locator("body").inner_text()
        if f"Simulating {N_FUTURES} futures" in body or "Chance below buffer" in body:
            first = time.monotonic() - start
            break
        page.wait_for_timeout(100)
    settle(page)
    done = time.monotonic() - start
    shown = f"{first:.1f} s" if first is not None else "never"
    result.check("Forecast: shows the waiting message or the page within 3 s",
                 first is not None and first <= 3,
                 f"first shown at {shown}, ready at {done:.1f} s")  # fmt: skip


def check_ask(page: Page, base: str, visit: str, result: Result) -> None:
    page.goto(address(base, "/Ask", visit=visit), timeout=WAIT_MS)
    settle(page)
    box = app(page).get_by_role("textbox", name="Your question")
    box.fill(QUESTION)
    box.press("Enter")
    app(page).get_by_text("Rows behind this").wait_for(timeout=WAIT_MS)
    settle(page)
    text = app(page).locator("body").inner_text()
    result.check("Ask answers with its steps and rows (offline, LLM off)",
                 "You spent $" in text and "How I worked it out" in text)  # fmt: skip


def check_scenario(page: Page, base: str, visit: str, result: Result) -> None:
    page.goto(address(base, "/Scenario", visit=visit), timeout=WAIT_MS)
    settle(page)
    chart = app(page).locator(".js-plotly-plot").first
    chart.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    shape = app(page).evaluate(
        """() => { const p = document.querySelector('.js-plotly-plot');
        const box = p.getBoundingClientRect(); const t = p.querySelector('.gtitle');
        const tb = t ? t.getBoundingClientRect() : null;
        const ys = [...p.querySelectorAll('.legend .traces')]
          .map(e => Math.round(e.getBoundingClientRect().top));
        return {inside: !!tb && tb.top >= box.top && tb.left >= box.left
                  && tb.bottom <= box.bottom && tb.right <= box.right,
                title: t ? t.textContent : '', rows: new Set(ys).size, items: ys.length}; }"""
    )
    result.check("Scenario chart: title inside the chart", shape["inside"], shape["title"])
    result.check("Scenario chart: legend on one row", shape["items"] >= 2 and shape["rows"] == 1,
                 f"{shape['items']} items, {shape['rows']} rows")  # fmt: skip


def check_recurring(page: Page, base: str, visit: str, result: Result) -> None:
    worst: list[str] = []
    seen = 0
    for account in ACCOUNTS:
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(address(base, "/Recurring", visit=visit, data="Synthetic demo",
                          account=account), timeout=WAIT_MS)  # fmt: skip
        settle(page)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(400)
            spans = app(page).evaluate(
                """() => [...document.querySelectorAll('.la-series span')]
                .filter(s => /^Next /.test(s.textContent.trim()))
                .map(s => { const lh = parseFloat(getComputedStyle(s).lineHeight)
                            || parseFloat(getComputedStyle(s).fontSize) * 1.6;
                            const r = s.getBoundingClientRect();
                            const card = s.closest('.la-series').getBoundingClientRect();
                            const out = r.right > card.right + 0.5 ? 9 : 0;
                            return [s.textContent.trim(), r.height / lh + out]; })"""
            )
            seen += len(spans)
            worst += [f"{account} {width}px '{t}'" for t, lines in spans if lines > 1.5]
    page.set_viewport_size({"width": 1280, "height": 800})
    detail = "; ".join(worst[:5]) or f"{seen} dates measured"
    result.check("Recurring: every 'Next <date>' on one line, inside its card "
                 "(3 accounts x 4 widths)",
                 seen > 0 and not worst, detail)  # fmt: skip


HEADER_WIDTHS = (1280, 768, 390)
LAYOUT = """() => {
  const vw = document.documentElement.clientWidth;
  const shown = e => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden'; };
  const box = e => e.getBoundingClientRect();
  const name = e => (e.textContent.trim() || e.getAttribute('aria-label') || e.className)
    .slice(0, 30);
  const theirs = [...document.querySelectorAll(
      '[data-testid="stHeader"] button, [data-testid="stHeader"] a')].filter(shown);
  const gear = [...document.querySelectorAll('.st-key-la-gear button')].filter(shown);
  const ours = [...document.querySelectorAll(
      '.la-brand, .la-nav a, .la-bar-right > *, .la-tabs a, .la-menu')].filter(shown);
  const hit = (a, b) => a.left < b.right && b.left < a.right
    && a.top < b.bottom && b.top < a.bottom;
  const out = [];
  for (const o of [...ours, ...gear]) {
    const r = box(o);
    if (r.left < -0.5 || r.right > vw + 0.5) out.push(`'${name(o)}' off-screen`);
    for (const t of theirs) if (hit(r, box(t))) out.push(`'${name(o)}' under Cloud's '${name(t)}'`);
  }
  for (const o of ours) for (const g of gear)
    if (hit(box(o), box(g))) out.push(`'${name(o)}' under the gear`);
  if (!gear.length) out.push('no gear');
  return [out, theirs.length, document.documentElement.scrollWidth > vw + 1]; }"""


def check_top_bar(page: Page, base: str, visit: str, result: Result) -> None:
    """Our header shares no space with Community Cloud's toolbar (Fork and GitHub for a visitor;
    Share, star, edit, Stop for the owner) nor with the gear, nothing in it is off-screen, and the
    page never scrolls sideways: Home and Forecast (with its tab row), at three widths. On a phone
    the menu lists every page on screen, and one of them opens."""
    hits: list[str] = []
    cloud = 0
    for width in HEADER_WIDTHS:
        page.set_viewport_size({"width": width, "height": 800})
        for path in ("/", "/Forecast"):
            page.goto(address(base, path, visit=visit), timeout=WAIT_MS)
            settle(page)
            found, cloud, sideways = app(page).evaluate(LAYOUT)
            hits += [f"{width}px {path}: {f}" for f in found]
            if sideways:
                hits.append(f"{width}px {path}: the page scrolls sideways")
    result.check("header: nothing overlaps Cloud's toolbar or the gear, nothing off-screen "
                 "(1280, 768, 390 px; Home and Forecast)", not hits,
                 "; ".join(hits[:5]) or f"Cloud toolbar: {cloud} buttons")  # fmt: skip
    # the phone menu, at 390 px on Forecast
    menu = app(page).locator(".la-menu")
    menu.click()
    page.wait_for_timeout(400)
    links = app(page).evaluate(
        """() => { const vw = document.documentElement.clientWidth;
        return [...document.querySelectorAll('.la-menu-list a')].map(a => {
          const r = a.getBoundingClientRect();
          return [a.textContent.trim(), r.width > 0 && r.left >= 0 && r.right <= vw + 0.5]; }); }"""
    )
    off = [name for name, ok in links if not ok]
    result.check("phone menu: every page listed and on screen (390 px)",
                 len(links) >= len(APP_PAGES) - 2 and not off,
                 f"{len(links)} links; off-screen: {', '.join(off) or 'none'}")  # fmt: skip
    app(page).locator(".la-menu-list a", has_text="Budgets").first.click()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and "Budgets" not in app(page).url:
        page.wait_for_timeout(300)
    settle(page)
    marked = app(page).locator('.la-menu-list a[aria-current="page"]')
    result.check("phone menu: a page in it opens (Budgets)",
                 marked.count() == 1 and marked.first.text_content().strip() == "Budgets")
    page.set_viewport_size({"width": 1280, "height": 800})


def check_settings(page: Page, base: str, visit: str, result: Result) -> None:
    """The gear opens the settings panel and pressing it again closes it; on the demo there is no
    pseudonymize switch (nothing to hide in made-up data)."""
    page.goto(address(base, "/Forecast", visit=visit), timeout=WAIT_MS)
    settle(page)
    gear = app(page).get_by_role("button", name="Settings")
    body = app(page).locator('[data-testid="stPopoverBody"]')
    gear.click()
    page.wait_for_timeout(600)
    opened = body.count() > 0 and body.first.is_visible()
    account = app(page).get_by_role("combobox", name="Account").count() > 0
    pseudo = app(page).get_by_text("Pseudonymize text").count()
    gear.click()
    page.wait_for_timeout(600)
    closed = body.count() == 0 or not body.first.is_visible()
    result.check("the gear opens the settings and closes them again", opened and account and closed,
                 f"opened {opened}, account choice {account}, closed {closed}")  # fmt: skip
    result.check("no pseudonymize switch on the demo", pseudo == 0)


def screenshots(browser: Browser, base: str, folder: Path) -> list[Path]:
    """The three README pictures, from the synthetic demo, at 1280 px wide (2x pixels)."""
    folder.mkdir(parents=True, exist_ok=True)
    page = browser.new_context(viewport={"width": 1280, "height": 820},
                               device_scale_factor=2).new_page()  # fmt: skip
    page.goto(base, timeout=WAIT_MS)
    settle(page)
    visit = visit_of(page) or ""
    out = []
    page.goto(address(base, "/Forecast", visit=visit), timeout=WAIT_MS)
    settle(page)
    out.append(folder / "forecast-warning.png")
    page.screenshot(path=out[-1])
    page.goto(address(base, "/Ask", visit=visit), timeout=WAIT_MS)
    settle(page)
    box = app(page).get_by_role("textbox", name="Your question")
    box.fill(QUESTION)
    box.press("Enter")
    app(page).get_by_text("Rows behind this").wait_for(timeout=WAIT_MS)
    settle(page)
    page.set_viewport_size({"width": 1280, "height": 1000})
    page.wait_for_timeout(500)
    out.append(folder / "ask-answer.png")
    page.screenshot(path=out[-1])
    page.set_viewport_size({"width": 1280, "height": 820})
    page.goto(address(base, "/Scenario", visit=visit), timeout=WAIT_MS)
    settle(page)
    spend = app(page).get_by_label("Daily spend ($)")  # a what-if, so the two lines part
    spend.fill(SCENARIO_DAILY_SPEND)
    spend.press("Enter")
    page.wait_for_timeout(500)
    settle(page)
    app(page).evaluate("() => document.querySelector('.js-plotly-plot')"
                  ".scrollIntoView({block: 'center'})")  # fmt: skip
    page.wait_for_timeout(800)
    out.append(folder / "scenario.png")
    page.screenshot(path=out[-1])
    page.context.close()
    return out


def run(base: str, shots: Path | None, hosted: bool = False) -> int:
    result = Result()
    console: list[str] = []
    probes = 0
    cloud: list[str] = []

    def on_console(msg) -> None:  # type: ignore[no-untyped-def]
        nonlocal probes
        if msg.type != "error":
            return
        if PROBE.search(msg.location.get("url", "")):
            probes += 1
        elif hosted and cloud_own(msg.location.get("url", ""), base):
            cloud.append(urlparse(msg.location.get("url", "")).netloc
                         + urlparse(msg.location.get("url", "")).path)  # fmt: skip
        else:
            console.append(f"{msg.text} @ {msg.location.get('url', '')}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.on("console", on_console)
        page.on("pageerror", lambda e: console.append(f"page error: {e}"))
        if not first_visit(page, base, result):
            browser.close()
            return 1
        check_home(page, result)
        visit = visit_of(page) or ""
        steps: list[Callable[[], None]] = [
            lambda: check_visits(browser, base, page, result),
            lambda: check_pages(page, base, visit, result),
            lambda: check_forecast_wait(page, base, visit, result),
            lambda: check_ask(page, base, visit, result),
            lambda: check_scenario(page, base, visit, result),
            lambda: check_recurring(page, base, visit, result),
            lambda: check_top_bar(page, base, visit, result),
            lambda: check_settings(page, base, visit, result),
        ]
        for step in steps:
            step()
        ignored = f"{probes} Streamlit page-address probes ignored"
        if hosted:
            where = ", ".join(sorted(set(cloud)))
            ignored += f"; {len(cloud)} from Cloud's own page ignored ({where})"
        detail = "; ".join(console[:5]) or ignored
        result.check("no browser console errors", not console, detail)
        if shots is not None:
            for f in screenshots(browser, base, shots):
                print(f"saved {f}")
        browser.close()
    failed = sum(not ok for _, ok, _ in result.rows)
    print(f"\n{len(result.rows) - failed} of {len(result.rows)} checks passed")
    return 0 if result.passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.demo_smoke")
    parser.add_argument("--url", help="the live demo (default: start a local one)")
    parser.add_argument("--screenshots", type=Path, help="save the README pictures here")
    args = parser.parse_args()
    if args.url:
        return run(args.url, args.screenshots, hosted=True)
    with tempfile.TemporaryDirectory(prefix="lookahead-demo-") as folder:
        server, base = start_local(Path(folder))
        try:
            return run(base, args.screenshots)
        finally:
            server.terminate()
            server.wait(timeout=30)


if __name__ == "__main__":
    sys.exit(main())
