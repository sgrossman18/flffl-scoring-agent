"""Diagnose a login that works on the Mac but not on GitHub Actions. Prints what
the login and the next page requests actually do: response codes, redirects,
cookie NAMES (never values), whether the logged-in marker shows up, and the
runner's outbound IP. Run via the workflow's "debug" input. Prints nothing
personal: no page text, no credentials."""

import os
import sys
import urllib.request
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flffl_scorer import config  # noqa: E402

MARKER = "text=Manage Roster"


def egress_ip() -> str:
    try:
        return urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
    except Exception as e:  # noqa: BLE001
        return f"? ({type(e).__name__})"


def state(page, label: str) -> None:
    print(f"[{label}] path={urlparse(page.url).path!r} title={page.title()!r} "
          f"loginbutton={page.locator('#loginbutton').count()} marker={page.locator(MARKER).count()} "
          f"tables={page.locator('table').count()} cookies={sorted(c['name'] for c in page.context.cookies())}")


def main() -> None:
    print("egress IPs:", [egress_ip() for _ in range(3)])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        print("user agent:", page.evaluate("navigator.userAgent"))

        def on_response(r):
            if r.request.resource_type == "document" or r.request.method == "POST":
                h = r.headers
                print(f"  {r.request.method} {urlparse(r.url).path} -> {r.status}"
                      f"{' location=' + urlparse(h['location']).path if 'location' in h else ''}"
                      f"{' set-cookie' if 'set-cookie' in h else ''}")

        page.on("response", on_response)
        page.goto(f"{config.FLFFL_BASE_URL}/index.php")
        state(page, "login page")
        page.fill("#UserName", os.environ["FLFFL_USERNAME"])
        page.fill("#Password", os.environ["FLFFL_PASSWORD"])
        page.click("#loginbutton")
        state(page, "right after click")
        page.wait_for_load_state("networkidle")
        state(page, "after networkidle")
        try:
            page.wait_for_selector(MARKER, timeout=15000)
            print("logged-in marker appeared")
        except Exception:  # noqa: BLE001
            print("logged-in marker NEVER appeared in 15s")
        state(page, "after waiting for marker")
        for i in (1, 2):
            page.goto(config.SCORING_ASSIGNMENTS_URL)
            page.wait_for_load_state("networkidle")
            state(page, f"assignments visit {i}")
        browser.close()


if __name__ == "__main__":
    main()
