#!/usr/bin/env python3
"""Regenerate docs/screenshots/*.png — every image the README embeds.

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

# The hero crop: the device list without the app header, at half the page width.
HERO_WIDTH, HERO_HEIGHT, HERO_MARGIN = 1120, 520, 20
# Breathing room under the last panel row, so it is not flush with the frame.
PANEL_TAIL = 28
# Enough of the table under the change summary to show the badged rows.
CHANGES_ROWS = 13


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
        with self.app._lock:
            self.app._devices_cache = None
            self.app._devices_cache_loaded = True
        self.app.device_cache.clear(self.app.DEVICE_CACHE_FILE,
                                    self.app.DEVICE_CACHE_KEY_FILE)

    def close(self):
        self._server.shutdown()
        self._thread.join(5)


# --- the shots ---------------------------------------------------------------
# Each takes the page and the fixture, leaves the UI in the state to capture,
# and returns the keyword arguments to screenshot it with.

def open_list(page, fx):
    """Log in and land on the device table, local keys revealed."""
    fx.log_in()
    fx.serve(demo_fleet.fleet())
    fx.forget_devices()
    page.goto(fx.url, wait_until="networkidle")
    page.wait_for_selector("#rows tr")
    page.click("[data-key-toggle]")


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
    open_list(page, fx)
    box = page.locator(".toolbar").bounding_box()
    return {"clip": {"x": box["x"] - HERO_MARGIN, "y": box["y"] - HERO_MARGIN,
                     "width": HERO_WIDTH, "height": HERO_HEIGHT}}


def shot_filter(page, fx):
    open_list(page, fx)
    page.fill("#filter", "plug")
    page.wait_for_function("document.querySelectorAll('#rows tr').length === 10")
    return {}


def shot_details(page, fx):
    open_list(page, fx)
    # The name cell, not the row: a click on the key column is a copy, not a row click.
    page.locator("#rows tr", has_text="Bedroom Plug 02").locator("td").first.click()
    page.wait_for_function("document.body.classList.contains('panel-open')")
    # The panel is a fixed, scrolling column: grow the window to fit it whole.
    page.set_viewport_size({"width": WIDTH, "height": PANEL_TAIL + page.evaluate(
        "Math.ceil(document.querySelector('.panel-head').offsetHeight"
        " + document.getElementById('panelBody').scrollHeight)")})
    page.wait_for_timeout(300)      # the panel slides in
    return {}


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
    "details": shot_details,
    "filter": shot_filter,
}


def capture(browser, fx, name, scheme):
    context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT},
                                  timezone_id=TIMEZONE, color_scheme=scheme)
    try:
        page = context.new_page()
        options = SHOTS[name](page, fx)
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
