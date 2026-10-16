#!/usr/bin/env python3
"""Regenerate the README and documentation screenshots in docs/screenshots/.

    python -m pip install -r requirements-dev.txt
    python -m playwright install chromium        # or have Edge/Chrome installed

    python tools/screenshots.py                  # all of them
    python tools/screenshots.py filter details   # just these two

Runs the real app against the fake account in tools/demo_fleet.py, drives it
with Playwright, and writes a light and a dark PNG per shot. Both themes come
from the same run, so the pair always shows the same state.

Nothing here reaches the network and no real session is touched: the session
file lives in a temp directory and the two Tuya calls the UI can make are
stubbed. Re-running only changes an image when the UI changed.
"""

import argparse
import copy
import importlib
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]

import demo_fleet                                                    # noqa: E402
from playwright.sync_api import Error as PlaywrightError             # noqa: E402
from playwright.sync_api import sync_playwright                      # noqa: E402
from werkzeug.serving import make_server                             # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "screenshots")
WIDTH, HEIGHT = 1440, 1000
TIMEZONE = "Asia/Kolkata"           # fixed, so the rendered times never move
BROWSER_CHANNELS = (None, "msedge", "chrome")   # None = playwright's own Chromium

DEMO_USER_CODE = "abcdef123456"
DEMO_QR_TOKEN = "demo0000-0000-4000-8000-000000000000"

# The hero crop: the header and the top of the device list, in a narrower window.
HERO_WIDTH, HERO_HEIGHT = 1120, 560
# Breathing room under the last panel row, so it is not flush with the frame.
PANEL_TAIL = 28
# Enough of the table under the change summary to show the badged rows.
CHANGES_ROWS = 13
# The same for the scan: the summary, the scan box and the first rows under them.
SCAN_ROWS = 8


def changed_fleet():
    """What a later refresh returns: one key rotated, one of each other change.

    The new device is spliced in near the top rather than appended, so its badge
    lands in frame; Tuya does not promise an order anyway.
    """
    devices = copy.deepcopy(demo_fleet.fleet())
    devices[1].local_key = "3vps@n2FwxR8?df%"     # Bedroom Plug 02, re-paired
    devices[2].name = "Pantry Strip 03"           # was Kitchen Strip 03
    devices.pop(8)                                # Guest Room Strip 09, unpaired
    arrived = demo_fleet.device(61)
    arrived.name = "Study Desk Lamp 61"
    arrived.update_time = demo_fleet.UPDATED + demo_fleet.UPDATE_STEP
    devices.insert(4, arrived)
    return devices


class Fixture:
    """The app under a real HTTP server, with the levers a shot needs."""

    def __init__(self, tmp):
        self.session_file = os.path.join(tmp, "session.json")
        os.environ.update(SESSION_FILE=self.session_file,
                          HASS_OPTIONS_FILE=os.path.join(tmp, "no-options.json"))
        for name in ("AUTH_USERNAME", "AUTH_PASSWORD"):
            os.environ.pop(name, None)

        import app
        self.app = importlib.reload(app)     # picks up the env above
        self.serve(demo_fleet.fleet())
        self.app.core.devices_from_session = lambda session, path: self._devices
        self.app.core.mint_qr_token = lambda user_code: DEMO_QR_TOKEN
        self.app.core.poll_login = lambda token, user_code: None   # stays pending
        self.app.lan_scan.scan = self._scan

        self._server = make_server("127.0.0.1", 0, self.app.app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_port}/"

    def serve(self, devices):
        self._devices = devices

    def log_in(self):
        with open(self.session_file, "w") as fh:
            json.dump({"client_id": "demo", "user_code": DEMO_USER_CODE,
                       "terminal_id": "terminal", "endpoint": "https://example.test",
                       "token_info": {"access_token": "demo"}}, fh)

    def log_out(self):
        try:
            os.remove(self.session_file)
        except OSError:
            pass

    def forget_devices(self):
        """Drop both caches, so the next list is a first list with no summary."""
        with self.app._devices_fetch_lock:
            with self.app._devices_cache_lock:
                self.app._devices_cache = None
                self.app._devices_cache_loaded = True
        self.app._clear_lan()
        self.app.device_cache.clear(self.app.DEVICE_CACHE_FILE,
                                    self.app.DEVICE_CACHE_KEY_FILE,
                                    self.app.LAN_CACHE_FILE)

    def remember_scan(self):
        """Store a finished scan, as if one ran earlier: results, no notice."""
        with open(self.session_file) as fh:
            key = self.app._session_cache_key(json.load(fh))
        with self.app._lan_lock:
            self.app._save_lan(key, {
                "results": demo_fleet.lan_results(), "summary": None,
                "targets": demo_fleet.LAN_SUBNET, "changes": None, "changes_at": None,
            })

    def _scan(self, targets, devices, known, progress=None, cancel=None, only=None, routers=None):
        results = demo_fleet.lan_results()
        return {"results": results, "summary": demo_fleet.lan_summary(results)}

    def close(self):
        self._server.shutdown()
        self._thread.join(5)


# --- the shots ---------------------------------------------------------------
# Each takes the page and the fixture, leaves the UI in the state to capture,
# and returns the keyword arguments to screenshot it with.

def open_list(page, fx, scanned=True):
    """Log in and land on the device table, local keys revealed."""
    fx.log_in()
    fx.serve(demo_fleet.fleet())
    fx.forget_devices()
    if scanned:
        fx.remember_scan()
    page.goto(fx.url, wait_until="networkidle")
    page.wait_for_selector("#rows tr")
    page.click("#thead [data-key-toggle]")


def shot_login(page, fx):
    fx.log_out()
    page.goto(fx.url, wait_until="networkidle")
    page.wait_for_selector("#login:not(.hidden)")
    return {}


def shot_qr_login(page, fx):
    fx.log_out()
    page.goto(fx.url, wait_until="networkidle")
    page.fill("#usercode", DEMO_USER_CODE)
    page.click("#startBtn")
    page.wait_for_selector("#qr:not(.hidden)")
    page.wait_for_function("document.getElementById('qrImg').complete")
    return {}


def shot_devices(page, fx):
    open_list(page, fx)
    return {"full_page": True}


def shot_header_devices(page, fx):
    # A window the width of the crop, so the header's actions are in frame.
    page.set_viewport_size({"width": HERO_WIDTH, "height": HEIGHT})
    open_list(page, fx)
    return {"clip": {"x": 0, "y": 0, "width": HERO_WIDTH, "height": HERO_HEIGHT}}


def shot_filter(page, fx):
    open_list(page, fx)
    page.fill("#filter", "plug")
    page.wait_for_function("document.querySelectorAll('#rows tr').length === 10")
    return {}


def open_device_panel(page):
    # The name cell, not the row: a click on the key column is a copy, not a row click.
    page.locator("#rows tr", has_text="Bedroom Plug 02").locator("td").first.click()
    page.wait_for_function("""() => {
        const panel = document.getElementById('panel');
        return panel.getAttribute('aria-hidden') === 'false' &&
            panel.getBoundingClientRect().right <= innerWidth + 0.5;
    }""")


def fit_panel(page, body_id):
    # The panel is a fixed, scrolling column: grow the window to fit it whole.
    height = page.evaluate("""id => {
        const body = document.getElementById(id);
        return Math.ceil(body.lastElementChild.getBoundingClientRect().bottom +
            body.scrollTop + parseFloat(getComputedStyle(body).paddingBottom));
    }
    """, body_id) + PANEL_TAIL
    page.set_viewport_size({"width": WIDTH, "height": height})
    page.wait_for_timeout(300)      # panel and body width transitions settle
    body = page.locator(f"#{body_id}")
    assert body.evaluate("el => el.scrollHeight <= el.clientHeight"), "panel content must fit in the shot"
    assert body.evaluate("el => el.scrollWidth <= el.clientWidth"), "panel content must not overflow"


def panel_clip(page):
    box = page.locator("#panel").bounding_box()
    return {"clip": {key: round(value) for key, value in box.items()}}


def shot_details(page, fx):
    open_list(page, fx)
    open_device_panel(page)
    fit_panel(page, "panelBody")
    return {}


def setup_panel(page, fx, tool):
    shot_scan(page, fx)
    open_device_panel(page)
    page.click("#panelSetupOpen")
    page.click(f"#setupBody [data-setup-tool='{tool}']")
    # Show the demo key explicitly; the panel's eye is separate from the table's.
    page.click("#setupBody [data-key-toggle]")
    if tool == "tinytuya":
        page.wait_for_selector("#setupBody [data-setup-snippet]")
    assert not page.locator("#setupBody [data-setup-readiness]").count()
    fit_panel(page, "setupBody")
    return panel_clip(page)


def shot_setup_tuya_local(page, fx):
    return setup_panel(page, fx, "tuya-local")


def shot_setup_localtuya(page, fx):
    return setup_panel(page, fx, "localtuya")


def shot_setup_tinytuya(page, fx):
    return setup_panel(page, fx, "tinytuya")


def shot_local_network(page, fx):
    # Exercise the scan flow so current-key verification comes from a check,
    # rather than presenting an older saved result as newly verified.
    shot_scan(page, fx)
    open_device_panel(page)
    page.click("#panelNetworkOpen")
    page.wait_for_selector("#networkBody [data-network-summary] h3:text-is('Connection checked')")
    fit_panel(page, "networkBody")
    return panel_clip(page)


def shot_scan(page, fx):
    open_list(page, fx, scanned=False)
    page.click("#scanBtn")
    page.fill("#scanTargets", demo_fleet.LAN_SUBNET)
    page.click("#scanStart")
    page.wait_for_selector("#lanSummaryNotice")
    page.wait_for_function("!lanPollTimer")
    height = page.evaluate(
        "n => Math.round(document.querySelectorAll('#rows tr')[n - 1]"
        ".getBoundingClientRect().bottom)", SCAN_ROWS)
    return {"clip": {"x": 0, "y": 0, "width": WIDTH, "height": height}}


def shot_changes(page, fx):
    open_list(page, fx)
    fx.serve(changed_fleet())
    page.click("#refreshBtn")
    page.wait_for_selector("#changesNotice")
    # Cut on a row boundary, so the table runs off the frame cleanly.
    height = page.evaluate(
        "n => Math.round(document.querySelectorAll('#rows tr')[n - 1]"
        ".getBoundingClientRect().bottom)", CHANGES_ROWS)
    return {"clip": {"x": 0, "y": 0, "width": WIDTH, "height": height}}


SHOTS = {
    "login": shot_login,
    "qr-login": shot_qr_login,
    "devices": shot_devices,
    "header-devices": shot_header_devices,
    "changes": shot_changes,
    "scan": shot_scan,
    "details": shot_details,
    "setup-tuya-local": shot_setup_tuya_local,
    "setup-localtuya": shot_setup_localtuya,
    "setup-tinytuya": shot_setup_tinytuya,
    "local-network": shot_local_network,
    "filter": shot_filter,
}


def capture(browser, fx, name, scheme):
    context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT},
                                  timezone_id=TIMEZONE, color_scheme=scheme)
    try:
        page = context.new_page()
        options = SHOTS[name](page, fx)
        page.mouse.move(0, 0)
        page.evaluate("document.activeElement.blur()")
        path = os.path.join(OUT_DIR, f"{name}-{scheme}.png")
        page.screenshot(path=path, **options)
        return path
    finally:
        context.close()


def launch(playwright):
    for channel in BROWSER_CHANNELS:
        try:
            return playwright.chromium.launch(channel=channel)
        except PlaywrightError:
            continue
    raise SystemExit("no Chromium-family browser: run `python -m playwright install chromium`")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("shots", nargs="*", metavar="SHOT",
                        help=f"shots to regenerate (default: all of {', '.join(SHOTS)})")
    names = parser.parse_args().shots or list(SHOTS)
    unknown = [name for name in names if name not in SHOTS]
    if unknown:
        parser.error(f"no such shot: {', '.join(unknown)}")

    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tuya-screenshots-")
    fx = Fixture(tmp)
    try:
        with sync_playwright() as playwright:
            browser = launch(playwright)
            try:
                for name in names:
                    for scheme in ("light", "dark"):
                        path = capture(browser, fx, name, scheme)
                        print(os.path.relpath(path, ROOT))
            finally:
                browser.close()
    finally:
        fx.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
