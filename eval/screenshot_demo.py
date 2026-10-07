"""Development only: screenshots of a page in the Synthetic demo, for design review.

Never run on real data: the "My data" database path is pointed at a file that does not exist, so
real data cannot load even if something clicks it, and the script checks that the Synthetic demo
radio is selected before every screenshot. It also records every request the page makes and
fails if any goes anywhere but this computer (the app must work offline).

Needs the dev requirements (Playwright) and the Edge or Chrome already installed.

    python -m eval.screenshot_demo Forecast            # one page, three widths, plus opened
    python -m eval.screenshot_demo Forecast --account synthetic-C

Pictures go to docs/design/. The page name is the one in the sidebar (Forecast, Recurring, ...).
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "design"
PORT = 8599
WIDTHS = {"desktop": (1440, 900), "laptop": (1024, 768), "phone": (390, 844)}


def start_server(private_guard: Path) -> subprocess.Popen[bytes]:
    env = {**os.environ, "LOOKAHEAD_PRIVATE_DB": str(private_guard)}  # a file that will not exist
    cmd = [sys.executable, "-m", "streamlit", "run", "app/Home.py", "--server.port", str(PORT),
           "--server.headless", "true", "--browser.gatherUsageStats", "false"]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)  # fmt: skip
    for _ in range(90):
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/_stcore/health", timeout=1)
            return proc
        except OSError:
            time.sleep(1)
    proc.terminate()
    raise RuntimeError("the app did not start")


def main() -> None:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(prog="python -m eval.screenshot_demo")
    parser.add_argument("page", help="the page as named in the sidebar, e.g. Forecast")
    parser.add_argument("--account", default="synthetic-B")
    parser.add_argument("--tag", default="", help="added to the file names, e.g. calm")
    parser.add_argument("--wait-for", default="Projected balance")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stem = args.page.lower() + (f"-{args.tag}" if args.tag else "")
    guard = Path(tempfile.mkdtemp()) / "no-such-private.db"
    server = start_server(guard)
    external: set[str] = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": WIDTHS["desktop"][0],
                                              "height": WIDTHS["desktop"][1]})  # fmt: skip
            page.on("request", lambda r: external.add(r.url)
                    if urlparse(r.url).hostname not in ("localhost", "127.0.0.1", None)
                    and not r.url.startswith(("data:", "blob:")) else None)  # fmt: skip
            page.goto(f"http://localhost:{PORT}/{args.page}", wait_until="networkidle")
            page.get_by_text("Synthetic demo", exact=True).first.wait_for(timeout=60000)
            sidebar = page.locator('[data-testid="stSidebar"]')
            opener = page.locator('[data-testid="stExpandSidebarButton"]')
            if opener.count():  # the settings panel starts closed: open it to choose the account
                opener.first.click()
                time.sleep(1)
            account = sidebar.get_by_role("combobox").first
            if args.account and account.count():
                account.click()
                option = page.get_by_role("option", name=args.account, exact=True)
                option.first.wait_for(state="visible", timeout=15000)
                option.first.click()
                page.get_by_role("option").first.wait_for(state="hidden", timeout=15000)
                shown = sidebar.get_by_role("combobox").first.input_value()
                assert shown == args.account, f"picked {shown!r}, wanted {args.account!r}"
                closer = page.locator('[data-testid="stSidebarCollapseButton"] button')
                if closer.count():  # close the panel again, as a reader would see the page
                    closer.first.click()
                    time.sleep(1)
            page.get_by_text(args.wait_for, exact=False).first.wait_for(timeout=90000)
            for name, (width, height) in WIDTHS.items():  # same page and session, resized
                page.set_viewport_size({"width": width, "height": height})
                time.sleep(3)  # let the chart and tables redraw
                overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                fonts = page.evaluate("[...document.fonts].filter(f => f.status === 'loaded')"
                                      ".map(f => f.family)")  # fmt: skip
                print(
                    f"{name} {width}px: sideways overflow {overflow}px; fonts {sorted(set(fonts))}"
                )
                tall = page.evaluate(
                    "document.querySelector('[data-testid=\"stMain\"]').scrollHeight"
                )
                page.set_viewport_size(
                    {"width": width, "height": min(max(height, tall + 40), 9000)}
                )
                time.sleep(2)  # the chart redraws at the new size
                page.screenshot(path=str(OUT / f"{stem}-{name}.png"))
                page.set_viewport_size({"width": width, "height": height})
            page.set_viewport_size({"width": WIDTHS["desktop"][0], "height": WIDTHS["desktop"][1]})
            time.sleep(2)
            closed = page.locator("details:not([open]) > summary")  # open the closed ones
            for _ in range(20):  # always the first one left: the list shrinks as they open
                if not closed.count():
                    break
                closed.first.click()
                time.sleep(0.4)
            time.sleep(3)
            tall = page.evaluate("document.querySelector('[data-testid=\"stMain\"]').scrollHeight")
            page.set_viewport_size({"width": WIDTHS["desktop"][0], "height": min(tall + 40, 12000)})
            time.sleep(2)
            page.screenshot(path=str(OUT / f"{stem}-desktop-open.png"))
            browser.close()
    finally:
        server.terminate()
    print("requests that left this computer:", sorted(external) or "none")
    if external:
        raise SystemExit("the app called out to the internet")


if __name__ == "__main__":
    main()
