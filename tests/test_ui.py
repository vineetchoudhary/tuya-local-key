"""Browser smoke tests for the frontend — the behaviour Python can't reach.

Needs playwright and a Chromium-family browser:

    python -m pip install -r requirements-dev.txt
    python -m playwright install chromium

Both are optional; this module skips itself when either is missing. The bundled
Chromium is preferred, falling back to a system Edge/Chrome install.
"""

import copy
import csv
import html
import importlib
import json
import os
import re
import shutil
import sys
import threading
import time
import types
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.middleware.dispatcher import DispatcherMiddleware  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import demo_devices  # noqa: E402
import support_report  # noqa: E402

pytestmark = pytest.mark.ui

DEVICES = demo_devices.devices()
PLUG = DEVICES[demo_devices.PLUG]
BROWSER_CHANNELS = (None, "msedge", "chrome")   # None = playwright's own Chromium
MASK = "•"
APP = None      # the reloaded app module the server runs, for tests that vary it
INGRESS_PREFIX = "/api/hassio_ingress/frontend-test"
CURRENT_VERSION = support_report.app_version()
_major, _minor, *_patch = CURRENT_VERSION.split(".")
NEXT_VERSION = f"{_major}.{int(_minor) + 1}"
NEXT_RELEASE_URL = f"https://github.com/vineetchoudhary/tuya-local-key/releases/tag/v{NEXT_VERSION}"

# Every test leaves a screenshot behind, gathered into a gallery you can open.
# Local convenience only: CI runners set CI, and skip the whole thing.
SHOTS_DIR = Path(os.environ.get(
    "UI_SHOTS_DIR", Path(__file__).resolve().parents[1] / "test-results" / "ui"))
SHOTS_ENABLED = not os.environ.get("CI")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """The real app, serving the fake device list over a real port."""
    global APP
    tmp = tmp_path_factory.mktemp("ui")
    session = tmp / "session.json"
    session.write_text(json.dumps({
        "client_id": "demo", "user_code": "demo", "terminal_id": "terminal",
        "endpoint": "https://example.test", "token_info": {"access_token": "demo"},
    }))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SESSION_FILE", str(session))
        mp.setenv("HASS_OPTIONS_FILE", str(tmp / "missing-options.json"))
        mp.setenv("CHECK_FOR_UPDATES", "true")
        mp.delenv("AUTH_USERNAME", raising=False)
        mp.delenv("AUTH_PASSWORD", raising=False)
        import app

        app = importlib.reload(app)
        APP = app
        mp.setattr(app.core, "devices_from_session", lambda session, path: DEVICES)
        mp.setattr(app._update_checker, "status", lambda current_version, **kwargs: {
            "current_version": current_version, "available": False, "check_after_seconds": 86400,
        })
        def ingress(environ, start_response):
            environ["HTTP_X_INGRESS_PATH"] = INGRESS_PREFIX
            return app.app(environ, start_response)

        mounted = DispatcherMiddleware(app.app, {INGRESS_PREFIX: ingress})
        httpd = make_server("127.0.0.1", 0, mounted, threaded=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}/"
        finally:
            httpd.shutdown()
            thread.join(5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        for channel in BROWSER_CHANNELS:
            try:
                launched = playwright.chromium.launch(channel=channel)
                break
            except PlaywrightError:
                continue
        else:
            pytest.skip("no Chromium-family browser: run `python -m playwright install chromium`")
        try:
            yield launched
        finally:
            launched.close()


GALLERY_CSS = """
  :root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --fg:#1c2530;
          --muted:#6b7684; --border:#e3e7ec; --ok:#1a9d51; --bad:#b03a3a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12161c; --card:#1b2129; --fg:#e6eaef; --muted:#9aa5b1;
            --border:#2b333d; --ok:#38c172; --bad:#e06666; }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:28px; background:var(--bg); color:var(--fg);
         font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size:19px; margin:0 0 4px; }
  .summary { color:var(--muted); font-size:13.5px; margin-bottom:22px; }
  .grid { display:grid; gap:18px; grid-template-columns:repeat(auto-fill, minmax(420px, 1fr)); }
  figure { margin:0; background:var(--card); border:1px solid var(--border); border-radius:12px;
           overflow:hidden; }
  figcaption { display:flex; align-items:center; gap:9px; padding:11px 13px;
               border-bottom:1px solid var(--border); font-size:13px; }
  .name { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap:anywhere; }
  .badge { flex:0 0 auto; font-size:11px; font-weight:700; letter-spacing:.04em;
           padding:2px 8px; border-radius:999px; text-transform:uppercase; }
  .pass { color:var(--ok); background:color-mix(in srgb, var(--ok) 14%, transparent); }
  .fail { color:var(--bad); background:color-mix(in srgb, var(--bad) 14%, transparent); }
  a.shot { display:block; }
  img { display:block; width:100%; height:auto; }
  figure.failed { border-color:var(--bad); }
"""


@pytest.fixture(scope="module")
def gallery(request):
    """Collects one screenshot per test and writes an index.html over them."""
    shots = []
    if SHOTS_ENABLED:
        shutil.rmtree(SHOTS_DIR, ignore_errors=True)   # stale shots are worse than none
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    yield shots
    if not shots:
        return
    failed = [s for s in shots if s["status"] == "fail"]
    cards = "\n".join(
        f'<figure class="{"failed" if s["status"] == "fail" else ""}">'
        f'<figcaption><span class="badge {s["status"]}">{s["status"]}</span>'
        f'<span class="name">{html.escape(s["name"])}</span></figcaption>'
        f'<a class="shot" href="{quote(s["file"], safe="")}" target="_blank">'
        f'<img src="{quote(s["file"], safe="")}" alt="{html.escape(s["name"])}" loading="lazy"></a>'
        f"</figure>"
        for s in shots
    )
    index = SHOTS_DIR / "index.html"
    index.write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>UI test screenshots</title>"
        f"<style>{GALLERY_CSS}</style></head><body>"
        f"<h1>UI test screenshots</h1>"
        f'<div class="summary">{len(shots)} test(s) · {len(shots) - len(failed)} passed · '
        f"{len(failed)} failed · newest run</div>"
        f'<div class="grid">{cards}</div></body></html>',
        encoding="utf-8",
    )
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter:
        reporter.write_line(f"UI screenshots: {index}")


@pytest.fixture()
def page(browser, server, gallery, request):
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000},
        timezone_id="Asia/Kolkata",     # fixed, so rendered local times are assertable
        color_scheme="light",
        accept_downloads=True,
    )
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=server)
    loaded = context.new_page()
    try:
        loaded.goto(server, wait_until="networkidle")
        loaded.wait_for_selector("#rows tr")
        yield loaded
    finally:
        try:
            if SHOTS_ENABLED:
                record_shot(loaded, gallery, request.node)
        finally:
            context.close()
            _forget_devices(APP)


def record_shot(page, gallery, node):
    """Screenshot the page as the test leaves it, pass or fail."""
    report = getattr(node, "rep_call", None)
    status = "fail" if report is None or report.failed else "pass"
    # Parameter IDs can contain API paths. Keep each screenshot in one file.
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", node.name)
    name = f"{len(gallery) + 1:02d}-{status}-{filename}.png"
    try:
        # A fixed drawer belongs to the viewport. A full-page capture places it
        # partway down a scrolled mobile page instead of showing the visible UI.
        panel_open = page.locator("#panel").get_attribute("aria-hidden") == "false"
        page.screenshot(path=SHOTS_DIR / name, full_page=not panel_open)
    except PlaywrightError:
        return          # a dead page must not mask the test's own failure
    gallery.append({"name": node.name, "status": status, "file": name})


def cell_texts(page, column):
    return page.locator(f"#rows tr td[data-column='{column}']").all_inner_texts()


def open_panel(page, name):
    page.click(f"#rows tr:has-text('{name}') td.name-col")
    # "visible", not just "attached": the panel is visibility:hidden until the
    # slide-in starts, and innerText reads empty while it is.
    page.wait_for_selector("#panel[aria-hidden='false']", state="visible")
    page.wait_for_function("document.getElementById('panel').getBoundingClientRect().right <= innerWidth + 0.5")


def open_network(page, name):
    open_panel(page, name)
    page.locator("#panelNetworkOpen").click()
    page.wait_for_selector("#networkBody", state="visible")


def open_information(page, name):
    open_panel(page, name)
    page.locator("#panelInfoOpen").click()
    expect(page.locator("#panelBody")).to_be_visible()


def open_raw(page, name):
    open_panel(page, name)
    page.locator("#panelRawOpen").click()
    expect(page.locator("#rawBody")).to_be_visible()


def field_value(page, key):
    """The panel's rendered value for a device field, addressed by its raw name."""
    return page.locator(f"#panelBody dt[title='{key}'] + dd:visible, #networkBody dt[title='{key}'] + dd:visible").inner_text()


def clipboard(page):
    return page.evaluate("navigator.clipboard.readText()")


def frontend_state(page, field):
    return page.evaluate("""async field => {
        const { appState } = await import(new URL('static/js/shared/state.js', document.baseURI).href);
        return appState[field];
    }""", field)


def repaint(page):
    page.evaluate("""async () => {
        const { render } = await import(new URL('static/js/app.js', document.baseURI).href);
        render();
    }""")


def wait_scan_idle(page):
    page.wait_for_function("""async () => {
        const { isWatchingScan } = await import(new URL('static/js/features/network.js', document.baseURI).href);
        return !isWatchingScan();
    }""")


@pytest.mark.parametrize("ingress,width,theme", [
    (False, 1440, "light"), (False, 390, "dark"),
    (True, 1440, "dark"), (True, 390, "light"),
])
def test_modular_frontend_starts_and_navigates_at_root_and_ingress(browser, server, tmp_path, ingress, width, theme):
    target = server + (INGRESS_PREFIX.lstrip("/") + "/" if ingress else "")
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme)
    loaded = context.new_page()
    errors, failed_assets, requests = [], [], []
    loaded.on("pageerror", lambda error: errors.append(str(error)))
    loaded.on("request", lambda request: requests.append(request.url))
    loaded.on("requestfailed", lambda request: failed_assets.append((request.url, request.failure)))

    def check_asset(response):
        if response.request.resource_type in ("script", "stylesheet") and not response.ok:
            failed_assets.append((response.url, response.status))

    loaded.on("response", check_asset)
    try:
        loaded.goto(target, wait_until="networkidle")
        loaded.wait_for_selector("#rows tr")
        expected_background = "rgb(246, 247, 249)" if theme == "light" else "rgb(18, 22, 28)"
        assert loaded.evaluate("getComputedStyle(document.body).backgroundColor") == expected_background
        # Reinitialization must not bind duplicate actions or restart boot requests.
        loaded.evaluate("""async () => {
            const { initApp } = await import(new URL('static/js/app.js', document.baseURI).href);
            initApp(); initApp();
        }""")
        open_panel(loaded, PLUG.name)
        loaded.locator("#panelSetupOpen").click()
        expect(loaded.locator(".setup-card")).to_be_visible()
        loaded.locator("[data-setup-action='network']").click()
        expect(loaded.locator(".network-card")).to_be_visible()
        loaded.locator("[data-network-action='fix']").click()
        expect(loaded.locator("#fixBody [data-fix-heading]")).to_have_text("Check the saved details")
        loaded.locator("#fixBody [data-fix-action='test']").click()
        expect(loaded.locator("#fixCheckIP")).to_be_focused()
        loaded.locator("#panelBack").click()
        expect(loaded.locator("#fixBody [data-fix-heading]")).to_have_text("Check the saved details")
        loaded.locator("#panelBack").click()
        expect(loaded.locator(".network-card")).to_be_visible()
        loaded.locator("#panelBack").click()
        expect(loaded.locator(".setup-card")).to_be_visible()
        assert loaded.evaluate("document.documentElement.scrollWidth") <= width
        assert requests.count(target + "api/state") == 1
        assert all(url.startswith(target) for url in requests)
        assert any("/static/css/" in url for url in requests)
        assert any("/static/js/shared/" in url for url in requests)
        assert errors == []
        assert failed_assets == []
        loaded.screenshot(path=tmp_path / f"frontend-{ingress}-{width}-{theme}.png")
    finally:
        context.close()


@pytest.mark.parametrize("ingress", [False, True])
def test_modular_login_confirms_qr_and_loads_devices(browser, server, ingress):
    target = server + (INGRESS_PREFIX.lstrip("/") + "/" if ingress else "")
    context = browser.new_context()
    loaded = context.new_page()
    starts, polls, errors = [], [], []
    loaded.on("pageerror", lambda error: errors.append(str(error)))
    loaded.route("**/api/state", lambda route: route.fulfill(json={"logged_in": False}))

    def start_login(route):
        starts.append(route.request.post_data_json)
        route.fulfill(json={"token": "frontend-test", "qr": (
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
            "AAAAC0lEQVR42mP8/x8AAwMCAO+a/ngAAAAASUVORK5CYII=")})

    def confirm_login(route):
        polls.append(route.request.post_data_json)
        route.fulfill(json={"status": "confirmed"})

    loaded.route("**/api/login/start", start_login)
    loaded.route("**/api/login/poll", confirm_login)
    try:
        loaded.goto(target, wait_until="networkidle")
        loaded.locator("#usercode").fill("demo-user-code")
        loaded.locator("#startBtn").click()
        expect(loaded.locator("#qr")).to_be_visible()
        loaded.wait_for_selector("#rows tr")
        assert starts == [{"user_code": "demo-user-code"}]
        assert polls == [{"token": "frontend-test"}]
        assert loaded.locator("#qr").is_hidden()
        assert errors == []
    finally:
        context.close()


def test_table_shows_the_scan_columns_only(page):
    headers = page.locator("#thead th").all_inner_texts()

    assert [h.strip() for h in headers] == [
        "Name", "Status", "ID", "Local Key / Node ID", "Protocol", "Product ID", "Product Name",
        "Update Time", "Details",
    ]
    assert page.locator("#rows tr").count() == len(DEVICES)
    assert page.locator("#selectAllDevices, #rows [data-device-select]").count() == 0
    # Fields that moved into the panel are not duplicated in the table.
    body = page.locator("#rows").inner_text()
    for moved in (PLUG.uuid, PLUG.category, PLUG.ip):
        assert moved not in body


def test_table_and_panel_local_key_visibility_are_independent(page):
    keys = page.locator("#rows .key")
    assert MASK in keys.first.inner_text()
    assert PLUG.local_key not in page.locator("#rows").inner_text()
    open_information(page, PLUG.name)
    panel_toggle = page.locator("#panelBody [data-key-toggle]")
    table_toggle = page.locator("#thead [data-key-toggle]")
    panel_key = page.locator("#panelBody .key").element_handle()
    page.click("#thead [data-key-toggle]")
    assert PLUG.local_key in page.locator("#rows").inner_text()
    assert MASK in field_value(page, "local_key")
    assert panel_toggle.get_attribute("aria-pressed") == "false"
    assert panel_key.evaluate("el => el.isConnected"), "table toggles leave the panel untouched"

    table_key = row(page, PLUG).locator(".key").element_handle()
    panel_toggle.press("Enter")
    assert PLUG.local_key in field_value(page, "local_key")
    assert PLUG.local_key in page.locator("#rows").inner_text()
    assert table_toggle.get_attribute("aria-pressed") == "true"
    assert panel_toggle.get_attribute("aria-label") == "Hide local key"
    assert table_key.evaluate("el => el.isConnected"), "panel toggles leave the table untouched"
    expect(panel_toggle).to_be_focused()

    table_toggle.click()
    assert PLUG.local_key not in page.locator("#rows").inner_text()
    assert PLUG.local_key in field_value(page, "local_key")
    assert panel_toggle.get_attribute("aria-pressed") == "true"

    panel_toggle.click()
    assert MASK in field_value(page, "local_key")
    assert PLUG.local_key not in page.locator("#rows").inner_text()
    assert table_toggle.get_attribute("aria-pressed") == "false"
    assert panel_toggle.get_attribute("aria-label") == "Show local key"


def test_panel_keys_start_hidden_when_reopened_or_another_device_is_opened(page):
    open_information(page, PLUG.name)
    page.locator("#panelBody [data-key-toggle]").click()
    assert PLUG.local_key in field_value(page, "local_key")
    page.locator("#panelClose").click()
    open_information(page, PLUG.name)
    assert MASK in field_value(page, "local_key")
    page.locator("#panelBody [data-key-toggle]").click()
    open_information(page, LAMP.name)
    assert MASK in field_value(page, "local_key")
    assert LAMP.local_key not in field_value(page, "local_key")
    assert page.locator("#thead [data-key-toggle]").get_attribute("aria-pressed") == "false"


def test_copying_a_masked_key_yields_the_real_value(page):
    pill = page.locator(f"#rows tr:has-text('{PLUG.name}') .key")
    assert MASK in pill.inner_text()

    pill.click()

    assert pill.inner_text() == "copied!"        # the failure path renders "copy failed"
    assert clipboard(page) == PLUG.local_key
    page.wait_for_timeout(900)
    assert MASK in pill.inner_text()             # and it goes back to being masked


def test_panel_shows_the_fields_the_table_leaves_out(page):
    open_information(page, PLUG.name)

    assert page.locator("#panelTitle").inner_text() == "Device information"
    assert PLUG.name in page.locator("#panelSub").inner_text()
    assert field_value(page, "uuid") == PLUG.uuid
    assert field_value(page, "category") == PLUG.category
    assert field_value(page, "ip") == PLUG.ip
    assert field_value(page, "model") == PLUG.model
    assert field_value(page, "support_local") == "Yes"
    assert field_value(page, "asset_id") == "-"          # empty string, not "None"
    assert page.locator("#rows tr.selected").count() == 1


def test_panel_surfaces_fields_the_sdk_does_not_document(page):
    open_information(page, "Balcony Door Sensor")

    assert "OTHER FIELDS" in page.locator("#panelBody").inner_text().upper()
    assert field_value(page, "firmware_channel") == "beta"
    assert field_value(page, "sub") == "Yes"
    assert field_value(page, "gateway_id") == "ebd8f1c0a1b2c3d4e5"


def test_panel_shows_local_time_utc_and_epoch(page):
    open_information(page, PLUG.name)

    updated = field_value(page, "update_time")

    # Asia/Kolkata puts this epoch on the next calendar day: a UTC-only render fails here.
    assert "2025-07-09 00:10:00" in updated
    assert "2025-07-08 18:40:00 UTC" in updated
    assert str(demo_devices.UPDATE_TIME) in updated
    # The table reads local, not UTC.
    assert "2025-07-09 00:10:00" in cell_texts(page, "update_time")[0]


def test_current_values_keeps_data_point_ids_values_and_definitions(page):
    open_values(page)
    power = page.locator("#valuesBody [data-values-dp='19']")
    expect(power.locator("th small")).to_have_text("DP 19")
    expect(power.locator("[data-cloud-value]")).to_contain_text("raw 812")
    expect(page.locator("#valuesBody [data-values-dp='1'] [data-cloud-value]")).to_have_text("true")
    page.locator("#valuesBody .values-specs summary").click()
    expect(page.locator("[data-values-definition='cur_power']")).to_have_text(
        "cur_power: Integer · read-only · 0–50000 W · scale 1 · minux")
    expect(page.locator("[data-values-definition='switch_1']")).to_have_text("switch_1: Boolean · read/write")
    page.keyboard.press("Escape")
    expect(page.locator("#panelValuesOpen")).to_be_focused()
    page.locator("#panelValuesOpen").click()
    expect(page.locator("#valuesBody .values-specs")).to_have_attribute("open", "")


def test_current_values_keeps_definitions_without_a_local_mapping(page):
    open_values(page, "Balcony Door Sensor")
    battery = page.locator("#valuesBody [data-values-dp='battery_percentage']")
    expect(battery.locator("th small")).to_have_text("No local mapping")
    expect(battery.locator("[data-cloud-value]")).to_have_text("84")
    page.locator("#valuesBody .values-specs summary").click()
    expect(page.locator("[data-values-definition='battery_percentage']")).to_have_text(
        "battery_percentage: Integer · read-only · 0–100 %")


def test_panel_degrades_for_a_device_with_no_specs_or_timestamps(page):
    open_information(page, "Unpaired Relay")

    body = page.locator("#panelBody").inner_text()
    assert "DATA POINTS" not in body.upper()
    assert "TIMELINE" not in body.upper()
    assert page.locator("#panelBody dt[title='update_time']").count() == 0
    assert field_value(page, "id") == "sparse000000000001"


def test_panel_closes_on_escape_and_on_the_close_button(page):
    open_panel(page, PLUG.name)
    page.keyboard.press("Escape")
    page.wait_for_selector("#panel[aria-hidden='true']", state="attached")
    assert page.locator("#rows tr.selected").count() == 0

    open_panel(page, PLUG.name)
    page.click("#panelClose")
    page.wait_for_selector("#panel[aria-hidden='true']", state="attached")


def test_filter_matches_names_specs_and_rendered_times(page):
    page.fill("#filter", "plug")
    assert cell_texts(page, "name") == [PLUG.name]

    page.fill("#filter", "cur_power")       # a data point code, only in the specs
    assert cell_texts(page, "name") == [PLUG.name]

    page.fill("#filter", "00:10:00")        # local time, rendered in the browser
    assert len(cell_texts(page, "name")) == 3    # every device that has timestamps

    page.fill("#filter", "no-such-device")
    assert page.locator("#rows tr").count() == 0

    page.fill("#filter", "")
    assert page.locator("#rows tr").count() == len(DEVICES)


def test_sorting_by_name_toggles_direction(page):
    page.click("#thead th[data-key='name']")
    ascending = cell_texts(page, "name")
    assert ascending == sorted(ascending, key=str.lower)

    page.click("#thead th[data-key='name']")
    assert cell_texts(page, "name") == list(reversed(ascending))


def test_csv_export_keeps_the_fields_dropped_from_the_table(page, tmp_path):
    with page.expect_download() as download:
        page.click("#csvBtn")
    path = tmp_path / "devices.csv"
    download.value.save_as(path)

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == len(DEVICES)
    columns = set(rows[0])
    assert {"name", "id", "uuid", "local_key", "category", "ip", "time_zone",
            "create_time", "active_time"} <= columns
    assert columns.isdisjoint({"status", "function", "status_range", "local_strategy", "epochs"})
    plug_row = next(r for r in rows if r["name"] == PLUG.name)
    assert plug_row["uuid"] == PLUG.uuid
    assert plug_row["local_key"] == PLUG.local_key      # export is never masked
    assert plug_row["update_time"] == "2025-07-08 18:40:00 UTC"


def test_raw_json_hides_the_key_but_copies_the_real_record(page):
    open_raw(page, PLUG.name)

    dumped = page.locator("#rawBody pre").inner_text()
    assert MASK in dumped
    assert PLUG.local_key not in dumped

    page.click("#rawBody .raw-actions > button")
    assert json.loads(clipboard(page))["local_key"] == PLUG.local_key


# --------------------------------------------------------------------------- #
# What changed, and the saved-snapshot fallback
# --------------------------------------------------------------------------- #
def _forget_devices(app):
    """Drop the cached list so the next load fetches DEVICES again."""
    # Closing the browser aborts its requests, but a server-side fetch can still
    # be finishing. Let it finish before clearing what the next test will see.
    with app._devices_fetch_lock:
        with app._devices_cache_lock:
            app._devices_cache = None
            app._devices_cache_loaded = True
        app._clear_lan()
        app.device_cache.clear(app.DEVICE_CACHE_FILE, app.DEVICE_CACHE_KEY_FILE, app.LAN_CACHE_FILE)
        # Visibility preferences intentionally outlive cache clears in the app.
        # Each browser test still needs its own account preference state.
        Path(app.DEVICE_PREFERENCES_FILE).unlink(missing_ok=True)


@pytest.fixture()
def running_app(server):
    """The app behind `server`; the page fixture resets it after closing the browser."""
    return APP


def _serves(app, monkeypatch, devices):
    monkeypatch.setattr(app.core, "devices_from_session", lambda session, path: devices)


def _raises(app, monkeypatch, error):
    def fail(session, path):
        raise error
    monkeypatch.setattr(app.core, "devices_from_session", fail)


def _with_rotated_key(value="ROTATED-KEY-9999"):
    devices = copy.deepcopy(DEVICES)
    devices[demo_devices.PLUG].local_key = value
    return devices


def refreshed(page, selector):
    with page.expect_response(lambda r: r.url.endswith("/api/devices?refresh=1")) as fetched:
        page.click("#refreshBtn")
    fetched.value.finished()
    page.wait_for_selector("#loading", state="hidden")
    page.wait_for_selector(selector)


def test_a_rotated_local_key_is_called_out_after_a_refresh(page, running_app, monkeypatch):
    _serves(running_app, monkeypatch, _with_rotated_key())

    refreshed(page, "#changesNotice")

    notice = page.locator("#changesNotice").inner_text()
    assert "1 local key changed" in notice
    assert PLUG.name in notice
    assert "will fail until you update it" in notice
    # The notice reports the rotation; it never carries key values.
    assert "ROTATED-KEY-9999" not in notice
    row = page.locator(f"#rows tr:has-text('{PLUG.name}')").inner_text()
    assert "key changed" in row.lower()


def test_added_removed_and_renamed_devices_are_listed(page, running_app, monkeypatch):
    devices = copy.deepcopy(DEVICES)
    was = devices[demo_devices.PLUG].name
    devices[demo_devices.PLUG].name = "Utility Room Plug"
    dropped = devices.pop()
    arrived = copy.deepcopy(DEVICES[demo_devices.LAMP])
    arrived.id, arrived.name = "new0000000000000001", "Hallway Sensor"
    devices.append(arrived)
    _serves(running_app, monkeypatch, devices)

    refreshed(page, "#changesNotice")

    notice = page.locator("#changesNotice").inner_text()
    assert f"1 device added: Hallway Sensor" in notice
    assert f"1 device removed: {dropped.name}" in notice
    assert "1 device renamed" in notice
    assert f"Utility Room Plug (was {was})" in notice
    assert "local key changed" not in notice


def test_an_unchanged_refresh_shows_no_notice(page, running_app):
    before = frontend_state(page, "devicesCachedAt")
    page.click("#refreshBtn")
    page.wait_for_function("""async prev => {
        const { appState } = await import(new URL('static/js/shared/state.js', document.baseURI).href);
        return appState.devicesCachedAt !== prev;
    }""", arg=before)

    assert page.locator("#changesNotice").count() == 0
    assert page.locator(".notice").count() == 0


def test_the_changes_notice_stays_dismissed_across_a_reload(page, running_app, monkeypatch):
    _serves(running_app, monkeypatch, _with_rotated_key())
    refreshed(page, "#changesNotice")

    page.click("#changesNotice [data-dismiss-changes]")
    assert page.locator("#changesNotice").count() == 0

    page.reload(wait_until="networkidle")
    page.wait_for_selector("#rows tr")

    assert page.locator("#changesNotice").count() == 0
    # Dismissed, not forgotten: the row still carries its badge.
    assert "key changed" in page.locator(f"#rows tr:has-text('{PLUG.name}')").inner_text().lower()


def test_a_changed_device_opens_its_panel_from_the_notice(page, running_app, monkeypatch):
    _serves(running_app, monkeypatch, _with_rotated_key())
    refreshed(page, "#changesNotice")

    page.click("#changesNotice [data-open-device]")
    page.wait_for_selector("#panel[aria-hidden='false']", state="visible")

    assert PLUG.name in page.locator("#panelTitle").inner_text()
    page.locator("#panelInfoOpen").click()
    page.click("#panelBody [data-key-toggle]")
    assert "ROTATED-KEY-9999" in field_value(page, "local_key")


def test_filtering_by_key_changed_narrows_to_the_rotated_device(page, running_app, monkeypatch):
    _serves(running_app, monkeypatch, _with_rotated_key())
    refreshed(page, "#changesNotice")

    page.fill("#filter", "key changed")

    assert page.locator("#rows tr").count() == 1
    assert PLUG.name in page.locator("#rows").inner_text()


def test_an_unreachable_tuya_shows_the_saved_list_as_a_snapshot(page, running_app, monkeypatch):
    _raises(running_app, monkeypatch, RuntimeError("offline"))

    refreshed(page, "[data-snapshot='fetch_failed']")

    assert "Could not reach Tuya" in page.locator(".notice.warn").inner_text()
    assert page.locator("#rows tr").count() == len(DEVICES)
    assert PLUG.id in page.locator("#rows").inner_text()


def test_an_expired_login_keeps_the_saved_list_and_offers_a_relogin(page, running_app, monkeypatch):
    class SessionExpired(Exception):
        error_code = "-9999999"
        error_message = "sign invalid"

    _raises(running_app, monkeypatch, SessionExpired())

    refreshed(page, "[data-snapshot='session_invalid']")

    notice = page.locator(".notice.warn").inner_text()
    assert "Your login expired" in notice
    assert page.locator("#rows tr").count() == len(DEVICES), "the saved keys are still good"

    page.click("[data-relogin]")
    page.wait_for_selector("#login:not(.hidden)")

    assert page.locator("#devices").is_hidden()


# --------------------------------------------------------------------------- #
# Scan network: local IPs and protocol versions
# --------------------------------------------------------------------------- #
LAMP = DEVICES[demo_devices.LAMP]
SENSOR = DEVICES[demo_devices.SENSOR]
BARE = DEVICES[demo_devices.BARE]


def _lan(status, ip=None, version=None, device22=False, checked_at=1_752_000_000, **extra):
    return dict(status=status, ip=ip, version=version, device22=device22,
                checked_at=checked_at, **extra)


FIRST_SCAN = {
    PLUG.id: _lan("ok", "192.168.1.61", "3.4"),
    LAMP.id: _lan("busy", "192.168.1.42", "3.3", device22=True),
    SENSOR.id: _lan("via_gateway", gateway_id=SENSOR.gateway_id),
    BARE.id: _lan("not_found"),
}


def _scans(app, monkeypatch, *rounds, **summary):
    """Each scan answers with the next of `rounds`: {device id: result}.
    `summary` adds to the summary every scan reports."""
    remaining = list(rounds)
    calls = []

    def scan(targets, devices, known, progress=None, cancel=None, only=None, routers=None):
        calls.append({"targets": targets, "devices": [d["id"] for d in devices], "only": only,
                      "routers": routers})
        if progress:
            progress({"phase": "probe", "addresses": len(targets), "open": 3, "devices": 3, "matched": 1})
        results = remaining.pop(0) if remaining else {}
        return {"results": results, "summary": {
            "addresses": len(targets), "open": 3, "devices": 3, "sub_devices": 1,
            "sub_devices_reached": sum(r["status"] == "via_gateway" and bool(r["ip"])
                                       for r in results.values()),
            "matched": sum(r["status"] == "ok" for r in results.values()),
            "refused": ["192.168.1.42"], "unmatched": ["192.168.1.77"], "out_of_budget": [],
            "routers": sorted(routers or ()),   # as if each likely router refused
            "cancelled": False, "duration": 1.2, "finished_at": 1_752_000_000 + len(calls),
            **summary,
        }}

    monkeypatch.setattr(app.lan_scan, "scan", scan)
    return calls


def _held_scan(app, monkeypatch):
    """A scan that keeps running until the test sets the returned event, the
    way one waits on the checks already under way after a cancel."""
    release = threading.Event()

    def scan(targets, devices, known, progress=None, cancel=None, only=None, routers=None):
        if progress:
            progress({"phase": "probe", "addresses": len(targets), "open": 1, "devices": 3, "matched": 0})
        release.wait(10)
        return {"results": {}, "summary": {
            "addresses": len(targets), "open": 1, "devices": 3, "sub_devices": 1,
            "sub_devices_reached": 0, "matched": 0, "refused": [], "unmatched": [],
            "out_of_budget": [], "routers": [], "cancelled": bool(cancel and cancel.is_set()),
            "duration": 1.0, "finished_at": 1_752_000_100,
        }}

    monkeypatch.setattr(app.lan_scan, "scan", scan)
    return release


def start_scan(page, targets="192.168.1.0/30"):
    if page.locator("#scanBox").is_hidden():
        page.click("#scanBtn")
    page.fill("#scanTargets", targets)
    page.click("#scanStart")
    page.wait_for_selector("#scanCancel")


def scanned(page, targets="192.168.1.0/24"):
    if page.locator("#scanBox").is_hidden():
        page.click("#scanBtn")
    page.fill("#scanTargets", targets)
    # An earlier summary can already be visible, and the poll timer is cleared
    # before the final results are loaded. Await this scan's response and render.
    with page.expect_response(lambda r: r.url.endswith("/api/lan") and
                              r.request.method == "GET") as results:
        with page.expect_response(lambda r: r.url.endswith("/api/lan/scan") and
                                  r.request.method == "POST") as started:
            page.click("#scanStart")
        assert started.value.status == 202
    outcome = results.value.json()
    assert outcome["job"]["id"] == started.value.json()["job"]["id"]
    page.wait_for_function("""async expected => {
        const [{ appState }, { isWatchingScan }] = await Promise.all([
            import(new URL('static/js/shared/state.js', document.baseURI).href),
            import(new URL('static/js/features/network.js', document.baseURI).href),
        ]);
        const { lanData } = appState;
        return !isWatchingScan() && lanData.job && lanData.job.id === expected.job.id &&
            lanData.job.state === 'done' &&
            JSON.stringify(lanData.summary) === JSON.stringify(expected.summary);
    }
    """, arg=outcome)
    page.wait_for_selector("#lanSummaryNotice")


def row(page, device):
    return page.locator(f"#rows tr:has-text('{device.name}')")


def test_a_scan_fills_the_protocol_column_and_the_status_badges(page, running_app, monkeypatch):
    calls = _scans(running_app, monkeypatch, FIRST_SCAN)

    scanned(page, "192.168.1.0/30")

    assert calls[0]["targets"] == ["192.168.1.1", "192.168.1.2"]
    assert calls[0]["routers"] == {"192.168.1.1"}, "scanned, but taken for the router"
    assert row(page, PLUG).locator("td[data-column='protocol_version']").inner_text() == "3.4"
    assert "3.3" in row(page, LAMP).locator("td[data-column='protocol_version']").inner_text()
    assert "device22" in row(page, LAMP).locator("td[data-column='protocol_version']").inner_text()
    assert row(page, PLUG).locator("td[data-column='online']").inner_text().split() == ["LAN", "reachable"]
    assert row(page, LAMP).locator("td[data-column='online']").inner_text().split() == ["LAN", "busy"]
    assert "via gateway" in row(page, SENSOR).inner_text()
    assert "not found" in row(page, BARE).inner_text()
    notice = page.locator("#lanSummaryNotice").inner_text()
    assert "Found 1 of 3 devices on the local network" in notice
    assert "1 address refused the connection on port 6668" in notice and "192.168.1.42" in notice
    assert "It may not be a Tuya device at all" in notice
    assert "192.168.1.77" in notice
    assert "router" not in notice, "the likely router (192.168.1.1) is left out without a word"


@pytest.mark.parametrize("delayed_path", ["/api/lan/scan", "/api/lan"])
def test_scanned_waits_for_the_new_results_when_a_summary_already_exists(
        page, running_app, monkeypatch, delayed_path):
    _scans(running_app, monkeypatch, FIRST_SCAN,
           {PLUG.id: _lan("ok", "192.168.2.62", "3.5")})
    scanned(page)
    page.evaluate("""path => {
        const originalFetch = window.fetch;
        window.fetch = async (...args) => {
            const response = await originalFetch(...args);
            if (new URL(args[0], document.baseURI).pathname === path) {
                await new Promise(resolve => setTimeout(resolve, 250));
            }
            return response;
        };
    }""", delayed_path)

    scanned(page)

    assert row(page, PLUG).locator("td[data-column='protocol_version']").inner_text() == "3.5"
    open_network(page, PLUG.name)
    assert field_value(page, "local_ip") == "192.168.2.62"


def test_a_devices_status_falls_back_to_the_cloud_flag_without_a_scan(page):
    assert row(page, PLUG).locator("td[data-column='online']").inner_text() == "online"
    assert row(page, PLUG).locator("td[data-column='protocol_version']").inner_text() == "-"


def test_the_status_column_sorts_lan_results_ahead_of_the_cloud_flag(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.1.61", "3.4"),
                                      BARE.id: _lan("busy", "192.168.1.9", "3.3")})
    scanned(page)

    page.click("#thead th[data-key='online']")

    names = [n.strip() for n in cell_texts(page, "name")]
    # reachable, then the cloud's online (the lamp), then busy, then the cloud's offline
    assert names == [PLUG.name, LAMP.name, BARE.name, SENSOR.name]
    assert "3 online" in page.locator("#count").inner_text()


def test_the_panel_shows_the_local_network_section(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, FIRST_SCAN)
    scanned(page)

    open_network(page, LAMP.name)

    assert field_value(page, "local_ip") == "192.168.1.42"
    assert "3.3" in field_value(page, "protocol_version")
    assert "3.22" in field_value(page, "protocol_version"), "tuya-local's name for the quirk"
    assert "busy" in field_value(page, "lan_status")
    assert "another local client" in field_value(page, "lan_status")
    assert page.locator("#networkBody [data-lan-check] input").input_value() == "192.168.1.42"


def test_a_sub_device_points_to_its_gateway_instead_of_a_check(page):
    open_network(page, SENSOR.name)

    body = page.locator("#networkBody").inner_text()
    assert "reached through its gateway" in body
    assert "check it at an IP address below" not in body, "there is no check below to point to"
    assert page.locator("#networkBody [data-lan-check]").count() == 0


def test_a_device_without_a_local_key_offers_no_check(page):
    open_network(page, BARE.name)   # Tuya returned no local_key for it

    body = page.locator("#networkBody").inner_text()
    assert "doesn't return a local key" in body
    assert "check it at an IP address below" not in body
    assert page.locator("#networkBody [data-lan-check]").count() == 0


def test_a_sub_device_whose_gateway_wasnt_found_is_greyed_out(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, FIRST_SCAN)   # the sensor's gateway: not reached
    scanned(page)

    badge = row(page, SENSOR).locator("td[data-column='online'] .badge")
    assert "muted" in badge.get_attribute("class")
    assert "reached through" not in page.locator("#lanSummaryNotice").inner_text()
    open_network(page, SENSOR.name)
    assert "which the last scan didn't find" in field_value(page, "lan_status")


def test_a_sub_device_reached_through_its_gateway_reads_as_reachable(page, running_app, monkeypatch):
    reached = dict(FIRST_SCAN, **{SENSOR.id: _lan("via_gateway", "192.168.1.61", "3.4",
                                                  gateway_id=SENSOR.gateway_id)})
    _scans(running_app, monkeypatch, reached)
    scanned(page)

    badge = row(page, SENSOR).locator("td[data-column='online'] .badge")
    assert "on" in badge.get_attribute("class").split()
    assert "plus 1 sub-device reached through its gateway" in page.locator("#lanSummaryNotice").inner_text()


@pytest.mark.parametrize("online, text, cls", [(True, "via gateway", "on"), (False, "offline", "off")])
def test_a_sub_device_reads_as_its_gateway_reports_it(page, running_app, monkeypatch, online, text, cls):
    reported = dict(FIRST_SCAN, **{SENSOR.id: _lan("via_gateway", "192.168.1.61", "3.4",
                                                   gateway_id=SENSOR.gateway_id, sub_online=online)})
    _scans(running_app, monkeypatch, reported, sub_devices_offline=int(not online))
    scanned(page)

    badge = row(page, SENSOR).locator("td[data-column='online'] .badge")
    assert badge.inner_text().split() == ["LAN", *text.split()]
    assert cls in badge.get_attribute("class").split()
    state = "online" if online else "offline"
    assert f"Its gateway reports it {state}." in badge.get_attribute("title")
    assert page.evaluate("""async id => {
        const [{ appState }, { isOnline }] = await Promise.all([
            import(new URL('static/js/shared/state.js', document.baseURI).href),
            import(new URL('static/js/shared/model.js', document.baseURI).href),
        ]);
        return isOnline(appState.deviceData.find(d => d.id === id));
    }""", SENSOR.id) is online
    notice = page.locator("#lanSummaryNotice").inner_text()
    assert ("Its gateway reports it offline." in notice) is not online
    open_network(page, SENSOR.name)
    assert f"which reports it {state}" in field_value(page, "lan_status")


def test_an_ip_being_typed_survives_a_repaint(page):
    open_network(page, LAMP.name)
    page.fill("#networkBody [data-lan-check] input", "192.168.1.5")

    repaint(page)   # what a finished scan or the key toggle does

    assert page.locator("#networkBody [data-lan-check] input").input_value() == "192.168.1.5"
    assert page.evaluate("document.activeElement.name") == "ip", "and it keeps the focus"


def test_cancel_says_so_until_the_scan_stops(page, running_app, monkeypatch):
    release = _held_scan(running_app, monkeypatch)
    try:
        start_scan(page)
        page.click("#scanCancel")
        page.wait_for_selector("#scanProgress :text('Cancelling')")
        page.wait_for_timeout(1500)   # a poll or two later: still cancelling, not cancellable again

        assert "Cancelling" in page.locator("#scanProgress").inner_text()
        assert page.locator("#scanCancel").is_disabled()
    finally:
        release.set()
    page.wait_for_selector("#scanProgress :text('Scan cancelled')")


def test_a_refresh_during_a_scan_keeps_a_closed_scan_box_closed(page, running_app, monkeypatch):
    release = _held_scan(running_app, monkeypatch)
    try:
        start_scan(page)
        page.click("#scanClose")

        page.click("#refreshBtn")
        page.wait_for_selector("#devices:not(.hidden)")
        page.wait_for_function("!document.getElementById('refreshBtn').disabled")

        assert page.locator("#scanBox").is_hidden()
    finally:
        release.set()
    wait_scan_idle(page)


def test_one_device_can_be_checked_from_the_panel(page, running_app, monkeypatch):
    calls = _scans(running_app, monkeypatch, {LAMP.id: _lan("ok", "192.168.1.50", "3.5")})
    open_network(page, LAMP.name)

    page.fill("#networkBody [data-lan-check] input", "192.168.1.50")
    page.click("#networkBody [data-lan-check] button")
    page.wait_for_selector("#networkBody :text('Found at 192.168.1.50')")

    assert calls[0]["targets"] == ["192.168.1.50"]
    assert calls[0]["only"] == [LAMP.id], "the whole list goes along, but only the lamp is looked for"
    assert row(page, LAMP).locator("td[data-column='protocol_version']").inner_text() == "3.5"
    assert page.locator("#lanSummaryNotice").count() == 0, "a single check is not a scan"


# Gateways as Tuya's sharing API lists them (issue #7): marked `sub`, with no
# local key, which sits on their sub-devices instead.
ZIGBEE_GATEWAY = demo_devices.CustomerDevice(
    id="gwzigbee0000000001", name="Zigbee Gateway", category="wg2", sub=True, node_id="0010",
    online=True)
BLE_GATEWAY = demo_devices.CustomerDevice(
    id="gwble000000000001", name="Mesh Gateway", category="wg2", sub=True, node_id="00d8",
    online=True)
VALVE = demo_devices.CustomerDevice(
    id="valve0000000000001", name="Garden Valve", local_key="Vv11Ww22Xx33Yy44", category="ggq",
    sub=True, node_id="a4c138ec2d57044b", ip="", online=True)
WATER_TIMER = demo_devices.CustomerDevice(
    id="timer0000000000001", name="Water Timer", local_key="Tt55Uu66Ss77Rr88", category="sfkzq",
    sub=True, node_id="74bb38f08dd9d7f5", ip="", online=True)


def _with_gateways(page, app, monkeypatch, *gateways_and_subs):
    _serves(app, monkeypatch, list(DEVICES) + list(gateways_and_subs))
    refreshed(page, "#changesNotice")


def _named_sub(device, gateway):
    sub = copy.deepcopy(device)
    sub.gateway_id = gateway.id
    return sub


def listed_ids(page):
    return page.locator("#rows tr").evaluate_all("rows => rows.map(row => row.dataset.id)")


def test_local_key_heading_tracks_the_full_account_not_the_visible_rows(page, running_app, monkeypatch):
    heading = page.locator("#thead th").filter(has=page.locator("[data-key-toggle]"))
    _serves(running_app, monkeypatch, [PLUG, ZIGBEE_GATEWAY, BLE_GATEWAY])
    refreshed(page, "#changesNotice")
    assert heading.inner_text() == "Local Key", "gateway node IDs do not change the heading"
    assert page.locator("#rows .node-id").count() == 0

    _serves(running_app, monkeypatch, [PLUG, ZIGBEE_GATEWAY, _named_sub(VALVE, ZIGBEE_GATEWAY)])
    refreshed(page, "#changesNotice")
    assert heading.inner_text() == "Local Key / Node ID"
    row(page, ZIGBEE_GATEWAY).locator("[data-gateway-toggle]").click()
    assert row(page, VALVE).is_hidden()
    assert heading.inner_text() == "Local Key / Node ID"
    page.fill("#filter", PLUG.name)
    assert page.locator("#rows tr").count() == 1
    assert heading.inner_text() == "Local Key / Node ID"
    page.fill("#filter", "no matching device")
    assert page.locator("#rows tr").count() == 0
    assert heading.inner_text() == "Local Key / Node ID"

    page.fill("#filter", "")
    _serves(running_app, monkeypatch, [PLUG])
    refreshed(page, "#changesNotice")
    assert heading.inner_text() == "Local Key"


def test_sub_device_node_ids_are_copyable_and_stay_visible_when_keys_are_hidden(
        page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    for sub in (VALVE, WATER_TIMER):
        node = row(page, sub).locator(".node-id")
        assert node.inner_text() == sub.node_id, "the gateway does not have to be identified first"
        assert "gateway's IP, protocol version and local key" in node.get_attribute("title")
        assert node.get_attribute("aria-label") == "Copy sub-device Node ID"
        assert row(page, sub).locator(".key").count() == 0
        node.click()
        assert clipboard(page) == sub.node_id
        assert page.locator("#panel").get_attribute("aria-hidden") == "true"

    for visible in (True, False):
        page.click("#thead [data-key-toggle]")
        assert (PLUG.local_key in row(page, PLUG).inner_text()) is visible
        for sub in (VALVE, WATER_TIMER):
            assert row(page, sub).locator(".node-id").inner_text() == sub.node_id
            assert sub.local_key not in row(page, sub).inner_text()


def test_a_missing_node_id_does_not_fall_back_to_the_uuid_or_local_key(page, running_app, monkeypatch):
    sub = copy.deepcopy(VALVE)
    del sub.node_id
    sub.uuid = "sub-device-uuid"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    page.click("#thead [data-key-toggle]")

    cell = row(page, sub).locator("td[data-column='local_key']")
    assert cell.inner_text() == "-"
    assert cell.locator(".dash").get_attribute("title") == \
        "Tuya did not return a Node ID for this sub-device."
    assert cell.locator("[data-copy]").count() == 0
    assert sub.uuid not in row(page, sub).inner_text()
    assert sub.local_key not in row(page, sub).inner_text()


def test_sub_device_details_and_csv_keep_the_key_node_id_and_uuid_separate(
        page, running_app, monkeypatch, tmp_path):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    sub.uuid = "sub-device-uuid"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    open_information(page, sub.name)

    labels = page.locator("#panelBody dl.fields dt").all_inner_texts()
    assert "Gateway local key" in labels and "Node ID" in labels and "UUID" in labels
    assert "Local Key / Node ID" not in labels
    assert MASK in field_value(page, "local_key")
    assert field_value(page, "node_id") == sub.node_id
    assert field_value(page, "uuid") == sub.uuid
    page.locator("#panelBody dt[title='local_key'] + dd .key").click()
    assert clipboard(page) == sub.local_key, "the returned gateway key is available before discovery"
    page.click("#panelBody [data-key-toggle]")
    assert sub.local_key in field_value(page, "local_key")
    assert sub.local_key not in row(page, sub).inner_text()
    assert sub.uuid not in row(page, sub).inner_text()

    with page.expect_download() as download:
        page.click("#csvBtn")
    path = tmp_path / "devices.csv"
    download.value.save_as(path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    assert rows[sub.id]["local_key"] == sub.local_key
    assert rows[sub.id]["node_id"] == sub.node_id
    assert rows[sub.id]["uuid"] == sub.uuid


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("width, scheme", [
    (1440, "light"), (1440, "dark"), (390, "light"), (390, "dark"),
])
def test_regular_device_names_align_with_the_header_and_only_children_are_indented(
        page, running_app, monkeypatch, grouped, width, scheme):
    if grouped:
        _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY,
                       _named_sub(VALVE, ZIGBEE_GATEWAY))
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme=scheme)
    positions = page.evaluate("""() => {
        const heading = document.querySelector('#thead th[data-key="name"]');
        const text = document.createRange();
        text.selectNodeContents(heading.firstChild);
        const regular = [...document.querySelectorAll('#rows tr:not(.sub-device-row)')]
            .filter(row => !row.querySelector('[data-gateway-toggle]'));
        return {
            heading: text.getBoundingClientRect().left,
            names: regular.map(row => row.querySelector('.device-name').getBoundingClientRect().left),
            placeholders: regular.filter(row => row.querySelector('.gateway-spacer')).length,
        };
    }""")
    assert positions["names"]
    assert positions["names"] == pytest.approx([positions["heading"]] * len(positions["names"]), abs=0.5)
    assert positions["placeholders"] == 0
    if grouped:
        gateway = row(page, ZIGBEE_GATEWAY)
        child = row(page, VALVE)
        assert gateway.locator("[data-gateway-toggle]").is_visible()
        assert child.locator(".gateway-spacer").is_visible()
        assert child.locator(".device-name").bounding_box()["x"] > gateway.locator(".device-name").bounding_box()["x"]


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (1440, "dark"), (390, "light")])
def test_known_sub_devices_start_expanded_below_the_gateway(page, running_app, monkeypatch,
                                                            width, scheme):
    _with_gateways(page, running_app, monkeypatch, _named_sub(VALVE, ZIGBEE_GATEWAY),
                   ZIGBEE_GATEWAY, _named_sub(WATER_TIMER, ZIGBEE_GATEWAY))
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme=scheme)

    assert listed_ids(page) == [d.id for d in DEVICES] + [ZIGBEE_GATEWAY.id, VALVE.id, WATER_TIMER.id]
    toggle = row(page, ZIGBEE_GATEWAY).locator("[data-gateway-toggle]")
    assert toggle.get_attribute("aria-expanded") == "true"
    count = row(page, ZIGBEE_GATEWAY).locator(".gateway-count")
    assert count.inner_text() == "2"
    assert count.get_attribute("title") == "2 sub-devices connected to this gateway"
    for sub in (VALVE, WATER_TIMER):
        child = row(page, sub)
        assert child.is_visible()
        assert child.get_attribute("data-gateway-id") == ZIGBEE_GATEWAY.id
        assert "sub-device-row" in child.get_attribute("class")
        assert child.get_attribute("id") in toggle.get_attribute("aria-controls").split()
        assert child.locator("td[data-column='protocol_version']").inner_text() == "-"
    assert row(page, SENSOR).get_attribute("data-gateway-id") is None, "its gateway is not in the list"
    if width < 600:
        node = row(page, VALVE).locator(".node-id")
        node.scroll_into_view_if_needed()
        bounds = node.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_collapsing_a_gateway_keeps_counts_and_survives_sort_and_refresh(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, _named_sub(VALVE, ZIGBEE_GATEWAY),
                   ZIGBEE_GATEWAY, _named_sub(WATER_TIMER, ZIGBEE_GATEWAY))
    toggle = row(page, ZIGBEE_GATEWAY).locator("[data-gateway-toggle]")
    toggle.focus()
    toggle.press("Enter")

    assert toggle.get_attribute("aria-expanded") == "false"
    assert toggle.evaluate("el => el === document.activeElement"), "keyboard focus survives the repaint"
    assert row(page, VALVE).is_hidden() and row(page, WATER_TIMER).is_hidden()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true", "the toggle does not open details"
    assert page.locator("#count").inner_text().startswith("7 devices")
    assert "2 sub-devices hidden" in page.locator("#count").inner_text()

    page.click("#thead th[data-key='name']")
    assert toggle.get_attribute("aria-expanded") == "false"
    page.click("#refreshBtn")
    page.wait_for_selector("#devices:not(.hidden)")
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.press("Space")
    assert row(page, VALVE).is_visible() and row(page, WATER_TIMER).is_visible()
    open_panel(page, VALVE.name)
    assert page.locator("#panelTitle").inner_text() == VALVE.name
    page.locator("#panelNetworkOpen").click()
    assert page.locator(f"#networkBody .lan-note [data-open-device='{ZIGBEE_GATEWAY.id}']").is_visible()


def test_filter_reveals_matching_children_with_gateway_context_and_exports_only_matches(
        page, running_app, monkeypatch, tmp_path):
    _with_gateways(page, running_app, monkeypatch, _named_sub(VALVE, ZIGBEE_GATEWAY),
                   ZIGBEE_GATEWAY, _named_sub(WATER_TIMER, ZIGBEE_GATEWAY))
    toggle = row(page, ZIGBEE_GATEWAY).locator("[data-gateway-toggle]")
    toggle.click()
    page.fill("#filter", VALVE.name)

    assert listed_ids(page) == [ZIGBEE_GATEWAY.id, VALVE.id]
    assert row(page, VALVE).is_visible(), "search reveals a match in a collapsed group"
    assert page.locator("#count").inner_text().startswith("1 device ")
    assert "1 gateway shown for context" in page.locator("#count").inner_text()
    toggle.click()
    assert row(page, VALVE).is_hidden()
    with page.expect_download() as download:
        page.click("#csvBtn")
    path = tmp_path / "filtered-devices.csv"
    download.value.save_as(path)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["id"] for r in rows] == [VALVE.id], "a context row and collapse do not change the export"
    assert rows[0]["local_key"] == VALVE.local_key

    page.fill("#filter", "")
    assert toggle.get_attribute("aria-expanded") == "false", "the original collapse state is restored"
    page.fill("#filter", "no-such-device")
    assert page.locator("#rows tr").count() == 0


def test_sort_orders_gateways_and_children_without_splitting_groups(page, running_app, monkeypatch):
    backyard = _named_sub(VALVE, ZIGBEE_GATEWAY)
    backyard.id, backyard.name = "backyard000000001", "Backyard Valve"
    _with_gateways(page, running_app, monkeypatch, _named_sub(VALVE, ZIGBEE_GATEWAY),
                   ZIGBEE_GATEWAY, backyard, _named_sub(WATER_TIMER, BLE_GATEWAY), BLE_GATEWAY)

    page.click("#thead th[data-key='name']")
    assert listed_ids(page) == [SENSOR.id, PLUG.id, LAMP.id, BLE_GATEWAY.id, WATER_TIMER.id,
                                BARE.id, ZIGBEE_GATEWAY.id, backyard.id, VALVE.id]
    page.click("#thead th[data-key='name']")
    assert listed_ids(page) == [ZIGBEE_GATEWAY.id, VALVE.id, backyard.id, BARE.id, BLE_GATEWAY.id,
                                WATER_TIMER.id, LAMP.id, PLUG.id, SENSOR.id]


def test_a_keyless_gateway_shows_the_key_it_answered_to(page, running_app, monkeypatch, tmp_path):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, VALVE)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)

    page.click("#thead [data-key-toggle]")
    key_cell = row(page, ZIGBEE_GATEWAY).locator("td[data-column='local_key']")
    assert VALVE.local_key in key_cell.inner_text()
    assert key_cell.locator(".key-mark").get_attribute("title") == \
        "Tuya lists this key on its sub-device Garden Valve, not on the gateway."
    gateway, plug = (row(page, d).bounding_box()["height"] for d in (ZIGBEE_GATEWAY, PLUG))
    assert abs(gateway - plug) < 0.5, "one line, like every other row"
    footnote = page.locator("#keyMarkNote")
    assert footnote.inner_text() == "* Tuya lists this key on the gateway's sub-devices, not on the gateway."
    page.fill("#filter", VALVE.name)
    assert listed_ids(page) == [ZIGBEE_GATEWAY.id, VALVE.id]
    assert footnote.is_visible(), "the gateway's borrowed key is explained when it is shown for context"
    page.fill("#filter", PLUG.name)
    assert footnote.is_hidden(), "only while a key it explains is listed"
    page.fill("#filter", "")
    open_information(page, ZIGBEE_GATEWAY.name)
    assert VALVE.local_key not in field_value(page, "local_key")
    page.locator("#panelBody [data-key-toggle]").click()
    assert VALVE.local_key in field_value(page, "local_key")
    assert "Tuya lists this key on its sub-device Garden Valve" in field_value(page, "local_key")
    with page.expect_download() as download:
        page.click("#csvBtn")
    path = tmp_path / "devices.csv"
    download.value.save_as(path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    assert rows[ZIGBEE_GATEWAY.id]["local_key"] == VALVE.local_key
    page.fill("#filter", VALVE.local_key)
    assert page.locator("#rows tr").count() == 2, "the gateway is found by the key it shows"


def test_a_sub_device_shows_its_gateways_version_only_as_the_gateways(page, running_app, monkeypatch,
                                                                      tmp_path):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, VALVE)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)

    protocol = row(page, VALVE).locator("td[data-column='protocol_version']")
    assert protocol.inner_text() == "-", "a sub-device has no protocol version of its own"
    assert protocol.locator(".dash").get_attribute("title") == \
        "Reached through its gateway, which uses protocol 3.4"
    assert row(page, ZIGBEE_GATEWAY).locator("td[data-column='protocol_version']").inner_text() == "3.4"
    page.click("#thead th[data-key='protocol_version']")
    names = cell_texts(page, "name")
    assert names[-2].startswith(ZIGBEE_GATEWAY.name)
    assert names[-1].startswith(VALVE.name), "a sub-device stays under its gateway when sorting by protocol"

    open_information(page, VALVE.name)
    assert field_value(page, "node_id") == VALVE.node_id
    page.locator("#panelBack").click()
    page.locator("#panelNetworkOpen").click()
    labels = page.locator("#networkBody dl.fields dt").all_inner_texts()
    assert "Gateway IP" in labels and "Gateway protocol" in labels
    assert "Local IP" not in labels and "Protocol version" not in labels
    assert field_value(page, "local_ip") == "192.168.2.8"
    assert field_value(page, "protocol_version") == "3.4"

    row(page, ZIGBEE_GATEWAY).locator("[data-gateway-toggle]").click()
    assert row(page, VALVE).is_hidden()
    with page.expect_download() as download:
        page.click("#csvBtn")
    path = tmp_path / "devices.csv"
    download.value.save_as(path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    assert rows[VALVE.id]["protocol_version"] == "3.4", "scripts still get the gateway's version"
    assert rows[VALVE.id]["local_ip"] == "192.168.2.8"
    assert rows[VALVE.id]["lan_gateway_id"] == ZIGBEE_GATEWAY.id
    assert rows[ZIGBEE_GATEWAY.id]["lan_gateway_id"] == ""


def test_a_check_that_moves_a_gateways_key_says_so(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    _scans(running_app, monkeypatch, {   # as left by a check typed with the wrong IP
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.9", "3.4", key_from=WATER_TIMER.id),
        BLE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
    }, {   # a recheck makes the stale, wrong address editable again
        ZIGBEE_GATEWAY.id: _lan("busy", "192.168.2.9", "3.4", key_from=WATER_TIMER.id),
    }, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
    })
    scanned(page)

    open_network(page, ZIGBEE_GATEWAY.name)
    page.locator("#networkBody [data-network-action='recheck']").click()
    expect(page.locator("#networkBody [data-lan-check] input")).to_be_visible()
    page.fill("#networkBody [data-lan-check] input", "192.168.2.8")
    page.click("#networkBody [data-lan-check] button")
    page.wait_for_selector("#networkBody :text('Found at 192.168.2.8')")

    note = page.locator("#networkBody .lan-note").last.inner_text()
    assert "It answered to the local key Tuya lists on Garden Valve." in note
    assert "Before, it had the one on Water Timer." in note
    assert "Mesh Gateway had that key, so check it again at its own IP." in note


def test_a_keyless_gateway_says_how_to_find_its_key(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    _scans(running_app, monkeypatch, {
        BLE_GATEWAY.id: _lan("not_found", "192.168.2.7"),
    })

    open_network(page, BLE_GATEWAY.name)
    body = page.locator("#networkBody").inner_text()
    assert "Tuya lists this gateway's local key on its sub-devices" in body
    assert "Use Scan network, or check it at its IP" in body
    page.fill("#networkBody [data-lan-check] input", "192.168.2.7")
    page.click("#networkBody [data-lan-check] button")

    page.wait_for_selector("#networkBody :text('none of the keys Tuya lists on sub-devices answered there')")


def test_a_sub_device_links_to_the_gateway_the_scan_found_it_through(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, VALVE)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)

    open_network(page, VALVE.name)
    gateway_link = page.locator(f"#networkBody .lan-note [data-open-device='{ZIGBEE_GATEWAY.id}']")
    assert gateway_link.inner_text() == ZIGBEE_GATEWAY.name
    page.click(f"#networkBody .lan-note [data-open-device='{ZIGBEE_GATEWAY.id}']")

    expected_title = "Local network"
    page.wait_for_function("expected => document.querySelector('#panelTitle').innerText === expected", arg=expected_title)


def test_one_click_checks_a_gateway_and_the_one_left_is_checked_too(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    calls = _scans(running_app, monkeypatch, {
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=None),
        WATER_TIMER.id: _lan("via_gateway", "192.168.2.9", "3.4", gateway_id=None),
    }, {   # the check of the Mesh Gateway
        BLE_GATEWAY.id: _lan("ok", "192.168.2.9", "3.4", key_from=WATER_TIMER.id),
        WATER_TIMER.id: _lan("via_gateway", "192.168.2.9", "3.4", gateway_id=BLE_GATEWAY.id),
    }, {   # the gateway left, checked without a click
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    }, unnamed_gateways=[
        {"ip": "192.168.2.8", "version": "3.4", "sub_devices": [VALVE.id]},
        {"ip": "192.168.2.9", "version": "3.4", "sub_devices": [WATER_TIMER.id]},
    ])
    scanned(page)

    for sub in (VALVE, WATER_TIMER):
        assert row(page, sub).get_attribute("data-gateway-id") is None, "the gateway is still unknown"
    assert page.locator("#rows [data-gateway-toggle]").count() == 0, "shared keys alone do not form a group"
    found = "192.168.2.8 (Garden Valve), 192.168.2.9 (Water Timer)"
    notice = page.locator("#lanSummaryNotice").inner_text()
    assert f"2 gateways answered to local keys Tuya lists on sub-devices: {found}." in notice
    for gateway in (ZIGBEE_GATEWAY, BLE_GATEWAY):
        assert row(page, gateway).locator("td[data-column='online']").inner_text().split() == ["LAN", "check", "needed"]
    open_network(page, VALVE.name)
    assert "which answered at 192.168.2.8 to this device's key" in page.locator("#networkBody").inner_text()
    open_network(page, BLE_GATEWAY.name)
    body = page.locator("#networkBody").inner_text()
    assert "Tuya lists this gateway's local key on its sub-devices" in body
    assert "When that leaves one gateway, it's checked too." in body

    page.click("#networkBody [data-check-ip='192.168.2.9']")
    page.wait_for_selector("#networkBody :text('Found at 192.168.2.9')")

    assert (calls[1]["targets"], calls[1]["only"]) == (["192.168.2.9"], [BLE_GATEWAY.id])
    assert (calls[2]["targets"], calls[2]["only"]) == (["192.168.2.8"], [ZIGBEE_GATEWAY.id])
    message = page.locator("#networkBody .lan-note").last.inner_text()
    assert "It answered to the local key Tuya lists on Water Timer." in message
    assert "That left one gateway, so Zigbee Gateway was checked too: found at 192.168.2.8." in message
    page.click("#thead [data-key-toggle]")
    assert WATER_TIMER.local_key in row(page, BLE_GATEWAY).inner_text()
    assert VALVE.local_key in row(page, ZIGBEE_GATEWAY).inner_text()
    for gateway, sub in ((ZIGBEE_GATEWAY, VALVE), (BLE_GATEWAY, WATER_TIMER)):
        assert row(page, sub).get_attribute("data-gateway-id") == gateway.id
        assert row(page, gateway).evaluate("tr => tr.nextElementSibling.dataset.id") == sub.id
        assert row(page, gateway).locator("[data-gateway-toggle]").get_attribute("aria-expanded") == "true"
    assert "gateway answered to local keys" not in page.locator("#lanSummaryNotice").inner_text()


def test_bad_targets_are_explained_in_the_scan_box(page, running_app):
    page.click("#scanBtn")
    page.fill("#scanTargets", "8.8.8.8")
    page.click("#scanStart")

    page.wait_for_selector("#scanError:not(.hidden)")
    assert "not a private network" in page.locator("#scanError").inner_text()


def test_the_last_targets_prefill_the_next_scan(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, FIRST_SCAN)
    scanned(page, "192.168.2.0/24, 192.168.3.0/24")

    page.reload(wait_until="networkidle")
    page.wait_for_selector("#rows tr")
    page.click("#scanBtn")

    assert page.locator("#scanTargets").input_value() == "192.168.2.0/24, 192.168.3.0/24"
    assert row(page, PLUG).locator("td[data-column='protocol_version']").inner_text() == "3.4", "results come back on load"


def test_a_version_change_is_called_out_and_dismissed_on_its_own(page, running_app, monkeypatch):
    upgraded = dict(FIRST_SCAN, **{PLUG.id: _lan("ok", "192.168.1.61", "3.5")})
    _scans(running_app, monkeypatch, FIRST_SCAN, upgraded)
    scanned(page)
    assert page.locator("#changesNotice").count() == 0
    page.click("#lanSummaryNotice [data-dismiss-lan-summary]")

    scanned(page)

    notice = page.locator("#changesNotice").inner_text()
    assert "What changed since the previous scan" in notice
    assert "1 protocol version changed" in notice
    assert f"{PLUG.name} (3.4 → 3.5)" in notice
    assert "version changed" in row(page, PLUG).inner_text().lower()
    page.click("#changesNotice [data-dismiss-changes]")
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#rows tr")
    assert page.locator("#changesNotice").count() == 0
    assert "version changed" in row(page, PLUG).inner_text().lower()


def test_filtering_matches_the_lan_badges(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, FIRST_SCAN)
    scanned(page)

    page.fill("#filter", "busy")

    assert page.locator("#rows tr").count() == 1
    assert LAMP.name in page.locator("#rows").inner_text()


def test_csv_export_includes_the_lan_fields(page, running_app, monkeypatch, tmp_path):
    _scans(running_app, monkeypatch, FIRST_SCAN)
    scanned(page)

    with page.expect_download() as download:
        page.click("#csvBtn")
    path = tmp_path / "devices.csv"
    download.value.save_as(path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}

    assert rows[PLUG.id]["protocol_version"] == "3.4"
    assert rows[PLUG.id]["local_ip"] == "192.168.1.61"
    assert rows[PLUG.id]["lan_status"] == "ok"
    assert rows[PLUG.id]["online"] == "true", "the cloud flag stays as it was"
    assert rows[LAMP.id]["device22"] == "true"
    assert rows[PLUG.id]["lan_checked_at"] == "2025-07-08 18:40:00 UTC"


# --------------------------------------------------------------------------- #
# Header actions: what doesn't fit on the header's line goes in the ⋮ menu
# --------------------------------------------------------------------------- #
ACTIONS = ["csvBtn", "scanBtn", "refreshBtn", "logoutBtn"]


def in_menu(page):
    return [a for a in ACTIONS if page.locator(f"#moreMenu #{a}").count()]


@pytest.mark.parametrize("target", [".app-icon", "h1"])
@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("selected", [False, True])
def test_brand_exits_selection_without_changing_home_search_or_exclusions(page, target, width, selected):
    page.set_viewport_size({"width": width, "height": 1000})
    page.fill("#filter", "plug")
    if selected:
        select_devices(page, PLUG)
    else:
        enter_selection(page)
    table = page.locator("#table").element_handle()
    cached_at, url = frontend_state(page, "devicesCachedAt"), page.url
    saved = page.request.get(page.url + "api/device-exclusions").json()

    page.locator(f"#homeLink {target}").click()

    assert page.url == url
    assert table.evaluate("el => el.isConnected"), "Home clicks keep the existing table"
    assert frontend_state(page, "devicesCachedAt") == cached_at
    assert frontend_state(page, "deviceView") == "active"
    expect(page.locator("#filter")).to_have_value("plug")
    assert frontend_state(page, "selectionMode") is False
    expect(page.locator("#bulkActions")).to_be_hidden()
    expect(page.locator("#rows [data-device-select], #selectAllDevices")).to_have_count(0)
    expect(page.locator("#selectionCount")).to_have_text("0 selected")
    expect(page.locator("#homeLink")).to_be_focused()
    expect(row(page, PLUG)).to_be_visible()
    assert page.request.get(page.url + "api/device-exclusions").json() == saved


@pytest.mark.parametrize("target", [".app-icon", "h1"])
def test_brand_closes_each_device_panel_page_on_home(page, target):
    page.fill("#filter", "plug")
    cached_at, url = frontend_state(page, "devicesCachedAt"), page.url
    entries = {"menu": None, "details": "panelInfoOpen", "raw": "panelRawOpen",
               "setup": "panelSetupOpen", "network": "panelNetworkOpen", "values": "panelValuesOpen"}
    for view, entry in entries.items():
        open_panel(page, PLUG.name)
        if entry:
            page.locator(f"#{entry}").click()
        page.locator(f"#homeLink {target}").click()

        expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")
        expect(page.locator("#homeLink")).to_be_focused()
        expect(page.locator("#filter")).to_have_value("plug")
        assert page.url == url
        assert frontend_state(page, "devicesCachedAt") == cached_at
        assert frontend_state(page, "selectedId") is None
        assert frontend_state(page, "panelHistory") == []


@pytest.mark.parametrize("target", [".app-icon", "h1"])
@pytest.mark.parametrize("width", [1440, 390])
def test_brand_returns_from_excluded_without_restoring_devices(page, target, width):
    page.set_viewport_size({"width": width, "height": 1000})
    page.fill("#filter", "plug")
    select_devices(page, PLUG)
    saved = save_selection(page)
    switch_device_view(page)
    expect(row(page, PLUG)).to_be_visible()
    if width == 1440:
        open_panel(page, PLUG.name)
    select_devices(page, PLUG)
    cached_at, url = frontend_state(page, "devicesCachedAt"), page.url

    page.locator(f"#homeLink {target}").click()

    assert page.url == url
    assert frontend_state(page, "deviceView") == "active"
    assert frontend_state(page, "devicesCachedAt") == cached_at
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")
    expect(page.locator("#homeLink")).to_be_focused()
    expect(page.locator("#backToDevicesBtn")).to_be_hidden()
    expect(page.locator("#bulkActions")).to_be_hidden()
    expect(page.locator("#rows [data-device-select], #selectAllDevices")).to_have_count(0)
    assert frontend_state(page, "selectionMode") is False
    expect(page.locator("#filter")).to_have_value("plug")
    expect(row(page, PLUG)).to_have_count(0)
    assert page.request.get(page.url + "api/device-exclusions").json() == saved


def test_brand_home_action_supports_keyboard_activation(page):
    open_panel(page, PLUG.name)
    home = page.get_by_role("link", name="Tuya Local Key home")
    home.focus()
    home.press("Enter")
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")
    expect(home).to_be_focused()
    select_devices(page, PLUG)
    home.focus()
    home.press("Enter")
    expect(page.locator("#bulkActions")).to_be_hidden()
    expect(page.locator("#rows [data-device-select], #selectAllDevices")).to_have_count(0)
    expect(home).to_be_focused()


def test_every_action_fits_in_a_wide_header(page):
    assert in_menu(page) == []
    assert page.locator("#moreWrap").is_hidden()
    assert page.locator("#moreMenu").is_hidden()


def test_a_narrow_header_moves_actions_into_the_menu_on_one_line(page):
    one_line = page.locator("header").bounding_box()["height"]

    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!!document.querySelector('#moreMenu #logoutBtn')")

    assert page.locator("header").bounding_box()["height"] == one_line
    assert "refreshBtn" not in in_menu(page), "Refresh is the last to go"
    assert "logoutBtn" in in_menu(page) and "csvBtn" in in_menu(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    assert page.locator("#moreMenu").is_hidden()

    page.click("#moreBtn")
    assert page.locator("#moreMenu").is_visible()
    expect(page.locator("#moreMenu button:visible").first).to_be_focused()
    page.click("#moreMenu #scanBtn")

    assert page.locator("#moreMenu").is_hidden()
    assert page.locator("#scanBox").is_visible()

    page.set_viewport_size({"width": 1440, "height": 1000})
    page.wait_for_function("!document.querySelector('#moreMenu #logoutBtn')")
    assert in_menu(page) == []


def test_the_menu_closes_on_escape_and_on_an_outside_click(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!!document.querySelector('#moreMenu #logoutBtn')")

    page.click("#moreBtn")
    page.keyboard.press("Escape")
    assert page.locator("#moreMenu").is_hidden()
    assert page.evaluate("document.activeElement.id") == "moreBtn"

    page.click("#moreBtn")
    page.click("#count")
    assert page.locator("#moreMenu").is_hidden()


def test_opening_the_panel_moves_actions_out_of_its_way(page):
    page.set_viewport_size({"width": 1000, "height": 800})
    page.wait_for_timeout(100)
    assert in_menu(page) == []

    open_panel(page, PLUG.name)
    page.wait_for_function("!!document.querySelector('#moreMenu #logoutBtn')")

    assert "logoutBtn" in in_menu(page)


def test_the_menu_button_is_as_tall_as_the_buttons_beside_it(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!!document.querySelector('#moreMenu #logoutBtn')")

    more = page.locator("#moreBtn").bounding_box()
    refresh = page.locator("#refreshBtn").bounding_box()
    assert more["height"] == refresh["height"]
    assert more["y"] == refresh["y"]


def test_ui_text_uses_no_semicolons_or_em_dashes(page, running_app, monkeypatch):
    # Every notice at once: a rotated key, a scan summary, a version change.
    _scans(running_app, monkeypatch, FIRST_SCAN,
           dict(FIRST_SCAN, **{PLUG.id: _lan("ok", "192.168.1.61", "3.5")}))
    scanned(page)
    scanned(page)
    _serves(running_app, monkeypatch, _with_rotated_key())
    refreshed(page, "#changesNotice")
    open_panel(page, LAMP.name)

    # innerText is only what shows: add closed dialogs, hidden boxes, tooltips,
    # placeholders and labels, which are UI text too.
    text = page.evaluate("""() => {
      const parts = [document.body.innerText];
      for (const el of document.querySelectorAll("dialog, #scanBox, .hint")) parts.push(el.textContent);
      for (const el of document.querySelectorAll("[title], [placeholder], [aria-label]")) {
        for (const name of ["title", "placeholder", "aria-label"]) {
          if (el.hasAttribute(name)) parts.push(el.getAttribute(name));
        }
      }
      return parts.join("\\n");
    }""")
    assert "Log out?" in text, "the closed dialog is checked too"
    assert "—" not in text
    assert ";" not in text.replace(PLUG.local_key, "").replace(LAMP.local_key, "")


# --------------------------------------------------------------------------- #
# Log out asks first
# --------------------------------------------------------------------------- #
@pytest.fixture()
def restores_session(running_app):
    """For a test that really logs out: put the shared session back afterwards."""
    path = Path(running_app.SESSION_FILE)
    saved = path.read_text()
    yield running_app
    path.write_text(saved)


def test_log_out_asks_first_and_cancel_keeps_everything(page, running_app):
    page.click("#logoutBtn")

    dialog = page.locator("#logoutDialog")
    assert dialog.is_visible()
    assert "network scan results" in dialog.inner_text()
    assert page.evaluate("document.activeElement.id") == "logoutCancel", "the safe choice has focus"

    page.click("#logoutCancel")

    assert dialog.is_hidden()
    assert page.locator("#devices").is_visible()
    assert os.path.exists(running_app.SESSION_FILE)
    page.wait_for_function("document.activeElement.id === 'logoutBtn'")


def test_escape_and_the_backdrop_cancel_without_closing_the_panel(page, running_app):
    open_panel(page, PLUG.name)

    page.click("#logoutBtn")
    page.keyboard.press("Escape")
    assert page.locator("#logoutDialog").is_hidden()
    assert page.locator("#panel").get_attribute("aria-hidden") == "false"

    page.click("#logoutBtn")
    page.mouse.click(5, 5)
    assert page.locator("#logoutDialog").is_hidden()
    assert os.path.exists(running_app.SESSION_FILE)


def test_confirming_logs_out(page, restores_session):
    page.click("#logoutBtn")
    page.click("#logoutConfirm")

    page.wait_for_selector("#login:not(.hidden)")
    assert not os.path.exists(restores_session.SESSION_FILE)
    assert page.locator("#headerActions").is_hidden()


def test_log_out_from_the_menu_asks_too(page, running_app):
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!!document.querySelector('#moreMenu #logoutBtn')")

    page.click("#moreBtn")
    page.click("#moreMenu #logoutBtn")
    assert page.locator("#logoutDialog").is_visible()
    assert page.locator("#moreMenu").is_hidden()

    page.click("#logoutCancel")
    # The dialog's close event, which moves focus, fires just after the click.
    page.wait_for_function("document.activeElement.id === 'moreBtn'")
    assert os.path.exists(running_app.SESSION_FILE)


def test_an_unavailable_scanner_is_explained_and_the_list_stays(page, running_app, monkeypatch):
    def unavailable():
        raise running_app.lan_scan.ScannerUnavailable("tinytuya could not be loaded (ImportError)")

    monkeypatch.setattr(running_app.lan_scan, "require_scanner", unavailable)
    page.click("#scanBtn")
    page.fill("#scanTargets", "192.168.2.0/24")
    page.click("#scanStart")

    page.wait_for_selector("#scanError:not(.hidden)")
    assert "tinytuya library couldn't be loaded" in page.locator("#scanError").inner_text()
    assert "device list and local keys still work" in page.locator("#scanError").inner_text()
    assert page.locator("#rows tr").count() == len(DEVICES)


def open_setup(page, name):
    open_panel(page, name)
    page.locator("#panelSetupOpen").click()
    page.wait_for_selector("#setupBody", state="visible")


def setup_field(page, key):
    return page.locator(f"#setupBody [data-setup-field='{key}']")


def setup_value(page, key):
    return setup_field(page, key).locator(".setup-value").inner_text()


def setup_tool(page, tool):
    page.locator(f"[data-setup-tool='{tool}']").click()


def copied_setup_snippet(page):
    page.locator("[data-setup-copy-snippet]").click()
    return clipboard(page)


def execute_setup_snippet(code, monkeypatch):
    """Use the real constructors, but status requests never touch the network."""
    import tinytuya

    devices = []

    class OfflineDevice(tinytuya.Device):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            devices.append(self)

        def status(self):
            self.status_requested = True
            return {"dps": {"1": True}}

        def subdev_query(self):
            self.subdevices_requested = True
            return {"online": ["test-node"]}

    monkeypatch.setitem(sys.modules, "tinytuya", types.SimpleNamespace(Device=OfflineDevice))
    exec(compile(code, "copied-setup.py", "exec"), {})
    return devices


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_setup_view_has_its_own_content_and_returns_to_details(page, width, scheme):
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme=scheme)
    open_panel(page, PLUG.name)
    close_box = page.locator("#panelClose").bounding_box()
    expect(page.locator("#menuBody")).to_be_visible()
    assert page.locator("#menuBody [data-panel-page]").count() == 5
    assert page.locator("#menuBody [data-support-report]").count() == 1
    assert page.locator("#menuBody dl.fields, #menuBody ul.dps, #menuBody pre").count() == 0
    assert page.locator("#panelSetupEntry").count() == 0
    assert page.locator("#panelBody").is_hidden()
    assert page.locator("#panelBack").is_hidden()
    page.locator("#panelSetupOpen").press("Enter")
    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    assert page.locator("#panelSub").inner_text() == PLUG.name
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert page.locator("#menuBody").is_hidden()
    assert page.locator("#setupBody").is_visible()
    assert page.locator("#setupBody dl.fields, #setupBody details.raw").count() == 0
    expect(page.locator("[data-setup-tool='tuya-local']")).to_be_focused()
    page.keyboard.press("Escape")
    assert page.locator("#panelTitle").inner_text() == PLUG.name
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert page.locator("#menuBody").is_visible()
    assert page.locator("#setupBody").is_hidden()
    expect(page.locator("#panelSetupOpen")).to_be_focused()
    page.keyboard.press("Escape")
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    open_setup(page, PLUG.name)
    page.locator("#panelClose").click()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    open_panel(page, PLUG.name)
    assert page.locator("#panelTitle").inner_text() == PLUG.name
    assert page.locator("#setupBody").is_hidden()


def test_setup_tuya_local_has_all_manual_fields_and_never_uses_the_cloud_ip(page):
    open_setup(page, PLUG.name)
    assert page.locator(".setup-card [data-setup-field]").evaluate_all(
        "fields => fields.map(field => field.dataset.setupField)") == [
        "host", "device_id", "local_key", "protocol_version", "poll_only", "device_cid",
    ]
    assert setup_value(page, "host") == "Not available"
    assert setup_field(page, "host").locator("button.setup-copy").is_disabled()
    assert setup_value(page, "device_id") == PLUG.id
    assert setup_value(page, "protocol_version") == "auto"
    assert setup_value(page, "poll_only") == "false"
    assert setup_value(page, "device_cid") == "Leave empty"
    assert MASK in setup_value(page, "local_key")
    assert PLUG.local_key not in page.locator(".setup-card").inner_text()
    setup_field(page, "local_key").locator("button.setup-copy").click()
    assert clipboard(page) == PLUG.local_key
    setup_field(page, "poll_only").locator("button.setup-copy").click()
    assert clipboard(page) == "false"


@pytest.mark.parametrize("version, tuya_version", [("3.3", "3.22"), ("3.4", "3.42"), ("3.5", "3.52")])
def test_setup_device22_uses_each_tools_protocol_names(page, running_app, monkeypatch,
                                                     version, tuya_version):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", version, device22=True)})
    scanned(page)
    open_setup(page, PLUG.name)
    assert setup_value(page, "host") == "192.168.2.61"
    assert setup_value(page, "protocol_version") == tuya_version
    setup_field(page, "protocol_version").locator("button.setup-copy").click()
    assert clipboard(page) == tuya_version

    setup_tool(page, "localtuya")
    assert setup_value(page, "protocol_version") == version
    assert setup_field(page, "dev_type").count() == 0

    setup_tool(page, "tinytuya")
    assert setup_value(page, "version") == version
    assert setup_value(page, "dev_type") == "device22"
    assert "dev_type" in setup_field(page, "dev_type").locator(".setup-label").inner_text()
    assert PLUG.local_key not in page.locator("[data-setup-snippet]").inner_text()
    devices = execute_setup_snippet(copied_setup_snippet(page), monkeypatch)
    assert len(devices) == 1
    assert devices[0].version == float(version)
    assert devices[0].dev_type == "device22"
    assert devices[0].address == "192.168.2.61"
    assert devices[0].real_local_key == PLUG.local_key.encode("latin1")
    assert devices[0].status_requested


@pytest.mark.parametrize("gateway, child", [(ZIGBEE_GATEWAY, VALVE), (BLE_GATEWAY, WATER_TIMER)])
def test_setup_sub_device_maps_gateway_and_child_arguments(page, running_app, monkeypatch, gateway, child):
    sub = _named_sub(child, gateway)
    _with_gateways(page, running_app, monkeypatch, gateway, sub)
    _scans(running_app, monkeypatch, {
        gateway.id: _lan("ok", "192.168.2.8", "3.4", key_from=sub.id),
        sub.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=gateway.id),
    })
    scanned(page)
    open_setup(page, sub.name)
    assert setup_value(page, "host") == "192.168.2.8"
    assert setup_value(page, "device_id") == gateway.id
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == sub.local_key
    assert setup_value(page, "device_cid") == sub.node_id

    setup_tool(page, "localtuya")
    assert setup_value(page, "device_id") == sub.id
    assert setup_value(page, "gateway_id") == gateway.id
    assert setup_value(page, "node_id") == sub.node_id

    setup_tool(page, "tinytuya")
    assert setup_value(page, "dev_id") == gateway.id
    assert setup_value(page, "sub_id") == sub.id
    assert setup_value(page, "cid") == sub.node_id
    assert setup_value(page, "parent") == "gateway"
    devices = execute_setup_snippet(copied_setup_snippet(page), monkeypatch)
    assert len(devices) == 2
    parent, child_device = devices
    assert parent.id == gateway.id and parent.address == "192.168.2.8"
    assert parent.real_local_key == sub.local_key.encode("latin1")
    assert child_device.id == sub.id and child_device.cid == sub.node_id
    assert child_device.parent is parent and child_device.version == parent.version == 3.4
    assert child_device.status_requested

    page.locator(f".setup-card [data-open-device='{gateway.id}']").click()
    assert setup_value(page, "dev_id") == gateway.id
    assert page.locator(".setup-children li").count() == 1
    devices = execute_setup_snippet(copied_setup_snippet(page), monkeypatch)
    assert devices[0].subdevices_requested
    setup_tool(page, "tuya-local")
    assert setup_value(page, "device_cid") == "Leave empty", "a gateway's own node ID is not a child CID"
    page.locator(".setup-children").scroll_into_view_if_needed()


def test_setup_gateway_children_use_the_full_list_and_open_their_panels(page, running_app, monkeypatch):
    subs = [_named_sub(VALVE, ZIGBEE_GATEWAY), _named_sub(WATER_TIMER, ZIGBEE_GATEWAY)]
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, *subs)
    open_setup(page, ZIGBEE_GATEWAY.name)
    page.fill("#filter", PLUG.name)
    assert page.locator("#rows tr").count() == 1
    assert page.locator(".setup-children li").count() == 2
    assert "Sub-devices (2)" in page.locator(".setup-children").inner_text()
    page.locator(f".setup-children [data-copy='{VALVE.node_id}']").click()
    assert clipboard(page) == VALVE.node_id
    page.locator(f".setup-children [data-open-device='{VALVE.id}']").click()
    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    assert page.locator("#panelSub").inner_text() == VALVE.name
    assert setup_value(page, "device_cid") == VALVE.node_id
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    expect(page.locator(f".setup-children [data-open-device='{VALVE.id}']")).to_be_focused()
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == ZIGBEE_GATEWAY.name
    page.locator("#panelInfoOpen").click()
    assert field_value(page, "id") == ZIGBEE_GATEWAY.id
    page.locator("#panelBack").click()
    assert page.locator("#setupBody").is_hidden()
    page.fill("#filter", "")
    page.locator("#panelSetupOpen").click()
    open_panel(page, PLUG.name)
    assert page.locator("#panelTitle").inner_text() == PLUG.name
    assert page.locator("#panelBack").is_hidden()


def test_setup_uuid_fallback_is_only_used_for_tuya_local(page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    del sub.node_id
    sub.uuid = "actual-sub-device-uuid"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=sub.id),
        sub.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    open_setup(page, sub.name)
    assert setup_value(page, "device_cid") == sub.uuid
    assert setup_field(page, "device_cid").locator(".setup-label").inner_text() == "Sub-device UUID"
    setup_tool(page, "localtuya")
    assert setup_value(page, "node_id") == "Not available"
    assert setup_field(page, "node_id").locator("button.setup-copy").is_disabled()
    setup_tool(page, "tinytuya")
    assert page.locator("[data-setup-copy-snippet]").is_disabled()
    assert "sub-device node ID" in setup_notice(page).inner_text()
    assert sub.uuid not in page.locator(".setup-card").inner_text()


def test_setup_unknown_gateway_is_not_guessed_from_a_shared_key(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    _scans(running_app, monkeypatch, {
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4"),
        WATER_TIMER.id: _lan("via_gateway", "192.168.2.9", "3.4"),
    })
    scanned(page)
    open_setup(page, VALVE.name)
    assert setup_value(page, "host") == "192.168.2.8"
    assert setup_value(page, "device_id") == "Not available"
    setup_tool(page, "tinytuya")
    assert page.locator("[data-setup-copy-snippet]").is_disabled()
    assert "gateway ID" in setup_notice(page).inner_text()


def test_setup_explicit_gateway_wins_and_conflicting_scan_values_are_not_borrowed(
        page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    gateway = copy.deepcopy(ZIGBEE_GATEWAY)
    gateway.local_key = "actual-gateway-key"
    _with_gateways(page, running_app, monkeypatch, gateway, BLE_GATEWAY, sub)
    _scans(running_app, monkeypatch, {
        sub.id: _lan("via_gateway", "192.168.2.9", "3.5", gateway_id=BLE_GATEWAY.id),
    })
    scanned(page)
    open_setup(page, sub.name)
    assert setup_value(page, "device_id") == gateway.id
    assert setup_value(page, "host") == "Not available"
    assert setup_value(page, "protocol_version") == "auto"
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == gateway.local_key
    setup_tool(page, "tinytuya")
    assert page.locator("[data-setup-copy-snippet]").is_disabled()


def test_setup_localtuya_editions_have_their_actual_optional_fields(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.5")})
    scanned(page)
    open_setup(page, PLUG.name)
    setup_tool(page, "localtuya")
    assert setup_value(page, "node_id") == "Leave empty"
    repo = page.locator("#setup-tool-panel .setup-footnote a")
    assert repo.is_visible()
    assert repo.get_attribute("href") == "https://github.com/xZetsubou/hass-localtuya"
    page.locator("[data-setup-advanced] summary").click()
    assert setup_value(page, "enable_debug") == "false"
    for field in ("scan_interval", "manual_dps_strings", "reset_dpids", "device_sleep_time"):
        assert setup_value(page, field) == "Leave empty"
    page.select_option("[data-localtuya-edition]", "original")
    assert setup_value(page, "protocol_version") == "3.5 (unsupported)"
    assert setup_field(page, "protocol_version").locator("button.setup-copy").is_disabled()
    assert setup_field(page, "node_id").count() == 0
    assert setup_field(page, "device_sleep_time").count() == 0
    assert page.locator("[data-setup-advanced]").evaluate("el => el.open")
    assert repo.get_attribute("href") == "https://github.com/rospogrigio/localtuya"


def test_setup_advanced_state_survives_an_immediate_edition_change(page):
    open_setup(page, PLUG.name)
    setup_tool(page, "localtuya")
    for edition, expected_open in [("original", True), ("xzetsubou", False)]:
        # Native details queues its toggle event. A repaint in this same browser
        # turn must still preserve the user's just-changed open/closed state.
        actual_open = page.evaluate("""edition => {
            document.querySelector('[data-setup-advanced] summary').click();
            const select = document.querySelector('[data-localtuya-edition]');
            select.value = edition;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            return document.querySelector('[data-setup-advanced]').open;
        }""", edition)
        assert actual_open is expected_open


def test_setup_original_localtuya_explains_its_missing_sub_device_support(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, _named_sub(VALVE, ZIGBEE_GATEWAY))
    open_setup(page, VALVE.name)
    setup_tool(page, "localtuya")
    page.select_option("[data-localtuya-edition]", "original")
    assert "Sub-devices require" in setup_notice(page).inner_text()
    assert "xZetsubou" in setup_notice(page).inner_text()
    assert page.locator(".setup-card [data-setup-field]").count() == 0


@pytest.mark.parametrize("tool", ["tuya-local", "localtuya", "tinytuya"])
def test_panel_visibility_applies_to_details_setup_and_snippets_without_changing_the_table(
        page, running_app, monkeypatch, tool):
    _scans(running_app, monkeypatch, FIRST_SCAN)
    scanned(page)
    open_raw(page, PLUG.name)
    page.locator("#rawBody [data-key-toggle]").click()
    assert PLUG.local_key in page.locator("#rawBody pre").inner_text()
    page.locator("#panelBack").click()
    assert PLUG.local_key not in page.locator("#menuBody").inner_text()
    page.locator("#panelSetupOpen").click()
    setup_tool(page, tool)
    assert PLUG.local_key in setup_value(page, "local_key")
    if tool == "tinytuya":
        assert PLUG.local_key in page.locator("[data-setup-snippet]").inner_text()
    assert PLUG.local_key not in page.locator("#rows").inner_text()
    page.locator("#thead [data-key-toggle]").click()
    panel_toggle = setup_field(page, "local_key").locator("[data-key-toggle]")
    panel_toggle.click()
    assert MASK in setup_value(page, "local_key")
    assert PLUG.local_key in page.locator("#rows").inner_text()
    assert page.locator("#thead [data-key-toggle]").get_attribute("aria-pressed") == "true"
    expect(panel_toggle).to_be_focused()
    setup_field(page, "local_key").locator(".setup-copy").click()
    assert clipboard(page) == PLUG.local_key
    if tool == "tinytuya":
        assert PLUG.local_key not in page.locator("[data-setup-snippet]").inner_text()
        assert PLUG.local_key in copied_setup_snippet(page)
    page.locator("#panelBack").click()
    page.locator("#panelRawOpen").click()
    assert PLUG.local_key not in page.locator("#rawBody pre").inner_text()
    page.locator("#panelBack").click()
    page.locator("#panelInfoOpen").click()
    assert MASK in field_value(page, "local_key")
    assert PLUG.local_key in page.locator("#rows").inner_text()


def test_setup_snippet_preserves_quoted_keys_and_is_masked_until_revealed(page, running_app, monkeypatch):
    device = copy.deepcopy(PLUG)
    device.local_key = "q'\"\\\n\t<&>x;!@#%z"
    _serves(running_app, monkeypatch, [device])
    refreshed(page, "#changesNotice")
    _scans(running_app, monkeypatch, {device.id: _lan("ok", "192.168.2.61", "3.3")})
    scanned(page)
    open_setup(page, device.name)
    setup_tool(page, "tinytuya")
    masked = page.locator("[data-setup-snippet]").inner_text()
    assert MASK in masked and json.dumps(device.local_key) not in masked
    code = copied_setup_snippet(page)
    assert json.dumps(device.local_key) in code
    devices = execute_setup_snippet(code, monkeypatch)
    assert devices[0].real_local_key == device.local_key.encode("latin1")
    setup_field(page, "local_key").locator("[data-key-toggle]").click()
    assert page.locator("[data-setup-snippet]").inner_text() == code
    setup_field(page, "local_key").locator("[data-key-toggle]").click()
    assert page.locator("[data-setup-snippet]").inner_text() == masked


def test_setup_updates_copied_values_after_key_and_scan_changes(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, FIRST_SCAN, {PLUG.id: _lan("ok", "192.168.2.62", "3.5")})
    scanned(page)
    open_setup(page, PLUG.name)
    setup_tool(page, "tinytuya")
    before = copied_setup_snippet(page)
    _serves(running_app, monkeypatch, _with_rotated_key("NEW-KEY-12345678!"))
    refreshed(page, "#changesNotice")
    assert page.locator("[data-setup-tool='tinytuya']").get_attribute("aria-selected") == "true"
    scanned(page)
    after = copied_setup_snippet(page)
    assert "192.168.2.62" in after and "version=3.5" in after
    assert "NEW-KEY-12345678!" in after and PLUG.local_key not in after
    assert before != after


def test_setup_controls_keep_their_state_and_support_keyboard_tabs(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, FIRST_SCAN)
    scanned(page)
    open_setup(page, PLUG.name)
    page.locator("[data-setup-tool='tuya-local']").focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("[data-setup-tool='localtuya']")).to_be_focused()
    page.locator("[data-setup-advanced] summary").click()
    page.locator("#thead [data-key-toggle]").click()
    assert page.locator("[data-setup-tool='localtuya']").get_attribute("aria-selected") == "true"
    assert page.locator("[data-setup-advanced]").evaluate("el => el.open")
    page.locator("#panelBack").click()
    expect(page.locator("#menuBody")).to_be_visible()
    expect(page.locator("#panelSetupOpen")).to_be_focused()
    page.locator("#panelSetupOpen").click()
    assert page.locator("[data-setup-advanced]").evaluate("el => el.open")
    page.locator("[data-setup-tool='localtuya']").focus()
    page.keyboard.press("End")
    expect(page.locator("[data-setup-tool='tinytuya']")).to_be_focused()
    page.keyboard.press("Home")
    expect(page.locator("[data-setup-tool='tuya-local']")).to_be_focused()


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
@pytest.mark.parametrize("tool", ["tuya-local", "localtuya", "tinytuya"])
def test_setup_card_layouts(page, running_app, monkeypatch, width, scheme, tool):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    sub.name = "Garden irrigation valve beside the greenhouse and vegetable beds"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=sub.id),
        sub.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme=scheme)
    open_panel(page, sub.name)
    close_box = page.locator("#panelClose").bounding_box()
    page.locator("#panelSetupOpen").click()
    setup_tool(page, tool)
    title = page.locator("#panelTitle")
    assert title.text_content() == "Set up in…"
    assert title.get_attribute("title") == title.text_content()
    assert title.evaluate("el => el.clientHeight <= 2 * parseFloat(getComputedStyle(el).lineHeight) + 1")
    assert page.locator("#panelSub").inner_text() == sub.name
    assert page.locator("#panelClose").bounding_box()["width"] >= 28
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert page.locator(".setup-card").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("#setupBody").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("#panelBody").is_hidden()
    assert page.locator(".setup-card .setup-param, .setup-card .field-sub").count() == 0
    assert page.locator(".setup-card .setup-note").count() == 0
    assert MASK in setup_value(page, "local_key")
    assert page.locator("[data-setup-tool='localtuya']").inner_text() == "localtuya"
    repos = {
        "tuya-local": "make-all/tuya-local",
        "localtuya": "xZetsubou/hass-localtuya",
        "tinytuya": "jasonacox/tinytuya",
    }
    footnote = page.locator("#setup-tool-panel .setup-footnote")
    footnote.scroll_into_view_if_needed()
    assert footnote.is_visible()
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert footnote.locator("a").get_attribute("href") == f"https://github.com/{repos[tool]}"


# --------------------------------------------------------------------------- #
# Setup guidance stays inline; local network diagnostics have their own view.
# --------------------------------------------------------------------------- #
def setup_notice(page):
    return page.locator("#setupBody [data-setup-readiness]")


def setup_action(page, action):
    return page.locator(f"#setupBody [data-setup-action='{action}']")


def network_summary(page):
    return page.locator("#networkBody [data-network-summary] h3, #networkBody h3[data-network-summary]").inner_text()


def network_child_report(page):
    return page.locator("#networkBody [data-network-check='sub_device']")


def test_setup_missing_details_are_inline_and_link_to_the_network_view(page):
    open_setup(page, PLUG.name)
    notice = setup_notice(page)
    assert notice.is_visible()
    assert "ip" in notice.inner_text().lower()
    assert PLUG.ip not in notice.inner_text()
    assert PLUG.id not in notice.inner_text() and PLUG.local_key not in notice.inner_text()
    assert "protocol" not in notice.inner_text().lower(), "auto-detection needs no warning"
    assert page.locator("#panelReadinessOpen, #readinessBody").count() == 0

    setup_action(page, "network").click()

    assert page.locator("#networkBody").is_visible()
    assert page.locator("#setupBody").is_hidden()
    assert page.locator("#panelTitle").inner_text() == "Local network"
    assert page.locator("#networkBody [data-lan-check] input").is_editable()
    assert page.locator("#networkBody [data-setup-tool], #networkBody [role='tablist']").count() == 0


def test_setup_warning_protocol_rules_follow_the_selected_tool_and_edition(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("busy", "192.168.2.61")})
    scanned(page)
    open_setup(page, PLUG.name)
    assert "protocol" not in setup_notice(page).inner_text().lower()
    setup_tool(page, "localtuya")
    assert "protocol" not in setup_notice(page).inner_text().lower()
    page.select_option("#setupBody [data-localtuya-edition]", "original")
    assert "protocol" in setup_notice(page).inner_text().lower()
    assert setup_action(page, "network").is_visible()
    setup_tool(page, "tinytuya")
    assert "protocol" in setup_notice(page).inner_text().lower()


def test_setup_complete_checked_details_have_no_readiness_warning(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    scanned(page)
    open_setup(page, PLUG.name)
    assert not setup_notice(page).is_visible()
    assert page.locator("#setupBody [data-readiness-check]").count() == 0


def test_setup_child_uuid_only_satisfies_tuya_local_and_the_edition_action_is_useful(
        page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    del sub.node_id
    sub.uuid = "network-sub-device-uuid"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=sub.id),
        sub.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    open_setup(page, sub.name)
    assert not setup_notice(page).is_visible()
    setup_tool(page, "tinytuya")
    assert "node" in setup_notice(page).inner_text().lower()
    setup_tool(page, "localtuya")
    assert "node" in setup_notice(page).inner_text().lower()
    page.select_option("#setupBody [data-localtuya-edition]", "original")

    setup_action(page, "edition").click()

    assert page.locator("#setupBody [data-localtuya-edition]").input_value() == "xzetsubou"
    assert "node" in setup_notice(page).inner_text().lower()


def test_setup_does_not_guess_a_gateway_and_localtuya_can_use_the_child_id(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    _scans(running_app, monkeypatch, {
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4"),
        WATER_TIMER.id: _lan("via_gateway", "192.168.2.9", "3.4"),
    })
    scanned(page)
    open_setup(page, VALVE.name)
    assert "gateway" in setup_notice(page).inner_text().lower()
    setup_tool(page, "localtuya")
    assert setup_value(page, "device_id") == VALVE.id
    notice = setup_notice(page).inner_text().lower() if setup_notice(page).is_visible() else ""
    assert "gateway id" not in notice, "this edition uses the child ID"
    setup_tool(page, "tinytuya")
    assert "gateway" in setup_notice(page).inner_text().lower()


def test_setup_missing_local_key_offers_a_real_device_refresh(page, running_app, monkeypatch):
    device = copy.deepcopy(BARE)
    _serves(running_app, monkeypatch, [device])
    refreshed(page, "#changesNotice")
    open_setup(page, device.name)
    assert "key" in setup_notice(page).inner_text().lower()
    device.local_key = "NETWORK-NEW-KEY"
    _serves(running_app, monkeypatch, [device])

    with page.expect_response(lambda r: r.url.endswith("/api/devices?refresh=1")) as fetched:
        setup_action(page, "refresh").click()
    fetched.value.finished()
    page.wait_for_function("""() => document.querySelector(
        '#setupBody [data-setup-field="local_key"] button.setup-copy').dataset.copy === 'NETWORK-NEW-KEY'
    """)
    assert "local key" not in setup_notice(page).inner_text().lower()
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == device.local_key


@pytest.mark.parametrize("version, summary", [
    ("3.4", "Connection checked"), ("3.1", "Device found"),
])
def test_network_only_encrypted_current_key_results_count_as_checked(page, running_app, monkeypatch,
                                                                   version, summary):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", version)})
    scanned(page)
    open_network(page, PLUG.name)
    assert network_summary(page) == summary
    assert page.locator("#networkBody [data-lan-check], #networkBody [data-network-action='fix'], #networkBody [data-network-action='scan']").count() == 0
    expect(page.locator("#networkBody [data-network-action='recheck']")).to_have_text("Recheck connection")
    if version == "3.1":
        assert "do not verify the local key" in page.locator("#networkBody [data-network-summary]").inner_text()
    assert PLUG.local_key not in page.locator("#networkBody").inner_text()


def test_network_copies_its_ip_and_protocol_without_claiming_a_31_key_check(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, VALVE)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("busy", "192.168.2.8", "3.1"),
    }, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.1", key_from=VALVE.id),
    })
    scanned(page)
    open_network(page, ZIGBEE_GATEWAY.name)

    page.locator("#networkBody [data-lan-check] button").click()
    expect(page.locator("#networkBody [data-network-summary] h3")).to_have_text("Device found")

    assert network_summary(page) == "Device found"
    body = page.locator("#networkBody").inner_text()
    assert "do not verify the local key" in body
    assert "Answered on port 6668 to its local key" not in body
    assert "It answered to the local key Tuya lists on" not in body
    for key, expected in (("local_ip", "192.168.2.8"), ("protocol_version", "3.1")):
        button = page.locator(f"#networkBody dt[title='{key}'] + dd [data-copy]")
        button.click()
        expect(button).to_have_text("copied!")
        assert clipboard(page) == expected


def test_network_does_not_offer_other_gateways_when_the_named_gateway_is_not_listed(
        page, running_app, monkeypatch):
    sub = copy.deepcopy(VALVE)
    sub.gateway_id = "gateway-not-in-the-device-list"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, sub)
    open_network(page, sub.name)

    assert network_summary(page) == "Gateway not listed"
    assert page.locator("#networkBody [data-lan-check], #networkBody .network-gateways").count() == 0
    assert page.locator("#networkBody [data-network-action='refresh']").is_visible()


@pytest.mark.parametrize("failure", [None, "503", "aborted"])
def test_network_key_rotation_loses_proof_and_keeps_the_saved_address(page, running_app, monkeypatch, failure):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    scanned(page)
    open_network(page, PLUG.name)
    assert network_summary(page) == "Connection checked"
    _serves(running_app, monkeypatch, _with_rotated_key("ROTATED-NETWORK-KEY"))

    def fail_lan_reload(route):
        if failure == "503":
            route.fulfill(status=503, content_type="application/json", body="{}")
        else:
            route.abort()

    if failure:
        page.route("**/api/lan", fail_lan_reload)
    refreshed(page, "#changesNotice")

    assert page.locator("#networkBody").is_visible()
    assert network_summary(page) == "Check again"
    assert field_value(page, "local_ip") == "192.168.2.61"
    assert field_value(page, "protocol_version") == "3.4"
    assert "ROTATED-NETWORK-KEY" not in page.locator("#networkBody").inner_text()


def test_network_offline_child_is_separate_from_its_checked_gateway(page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=sub.id),
        sub.id: _lan("via_gateway", "192.168.2.8", "3.4",
                     gateway_id=ZIGBEE_GATEWAY.id, sub_online=False),
    })
    scanned(page)
    open_network(page, sub.name)
    assert network_summary(page) == "Gateway checked"
    assert network_child_report(page).get_attribute("data-state") == "attention"
    assert "offline" in network_child_report(page).inner_text().lower()


@pytest.mark.parametrize("changed_field", ["gateway_id", "node_id"])
def test_network_discards_an_offline_child_report_when_its_relationship_changes(page, running_app, monkeypatch,
                                                                              changed_field):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, sub)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=sub.id),
        sub.id: _lan("via_gateway", "192.168.2.8", "3.4",
                     gateway_id=ZIGBEE_GATEWAY.id, sub_online=False),
    })
    scanned(page)
    open_network(page, sub.name)
    assert network_child_report(page).get_attribute("data-state") == "attention"
    changed = copy.deepcopy(sub)
    setattr(changed, changed_field, BLE_GATEWAY.id if changed_field == "gateway_id" else "new-node-id")
    _serves(running_app, monkeypatch, list(DEVICES) + [ZIGBEE_GATEWAY, BLE_GATEWAY, changed])

    refreshed(page, "#networkBody")

    assert network_child_report(page).get_attribute("data-state") == "pending"
    assert "offline" not in network_child_report(page).inner_text().lower()
    assert network_summary(page) != "Gateway checked"


def test_network_a_new_successful_scan_supersedes_a_failed_manual_check(page, running_app, monkeypatch):
    later_success = {}
    _scans(running_app, monkeypatch, {
        PLUG.id: _lan("key_mismatch", "192.168.2.61", "3.4", checked_at=page.evaluate("Date.now() / 1000")),
    }, later_success)
    open_network(page, PLUG.name)
    page.fill("#networkBody [data-lan-check] input", "192.168.2.61")
    page.click("#networkBody [data-lan-check] button")
    page.wait_for_selector("#networkBody .lan-note.scan-error")
    wait_scan_idle(page)
    assert page.locator("#networkBody .lan-note.scan-error").is_visible()
    later_success[PLUG.id] = _lan("ok", "192.168.2.61", "3.4", checked_at=page.evaluate("Date.now() / 1000"))

    scanned(page)

    assert network_summary(page) == "Connection checked"


@pytest.mark.parametrize("child", [False, True])
def test_network_form_checks_the_device_or_gateway_and_keeps_the_view_open(page, running_app, monkeypatch, child):
    device = PLUG
    gateway = copy.deepcopy(ZIGBEE_GATEWAY)
    gateway.local_key = VALVE.local_key
    if child:
        device = _named_sub(VALVE, gateway)
        _with_gateways(page, running_app, monkeypatch, gateway, device)
    target = gateway if child else PLUG
    calls = _scans(running_app, monkeypatch, {
        target.id: _lan("busy", "192.168.2.61", "3.4"),
    }, {
        target.id: _lan("ok", "192.168.2.61", "3.4"),
    })
    scanned(page)
    open_network(page, device.name)
    form = page.locator("#networkBody [data-lan-check]")
    assert form.get_attribute("data-lan-check") == target.id
    assert form.locator("input").input_value() == "192.168.2.61"

    form.locator("button").click()

    assert page.locator("#networkBody").is_visible()
    assert page.locator("#panelBody").is_hidden()
    assert page.locator("#panelTitle").inner_text() == "Local network"
    expected = "Gateway checked" if child else "Connection checked"
    page.wait_for_function("""expected => {
        const summary = document.querySelector('#networkBody [data-network-summary]');
        return (summary.matches('h3') ? summary : summary.querySelector('h3')).innerText === expected;
    }""", arg=expected)
    assert calls[1]["targets"] == ["192.168.2.61"]
    assert calls[1]["only"] == [target.id]


def test_network_check_preserves_a_control_focused_during_a_slow_post(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, {
        PLUG.id: _lan("busy", "192.168.2.61", "3.4"),
    }, {
        PLUG.id: _lan("ok", "192.168.2.61", "3.4"),
    })
    scanned(page)
    open_network(page, PLUG.name)
    held_posts = []

    def delay_post(route):
        if route.request.method == "POST":
            held_posts.append(route)
        else:
            route.continue_()

    page.route("**/api/lan/scan", delay_post)
    page.locator("#networkBody [data-lan-check] button").click()
    chosen_control = page.locator("#panelClose")
    chosen_control.focus()
    expect(chosen_control).to_be_focused()
    assert len(held_posts) == 1

    with page.expect_response(lambda r: r.url.endswith("/api/lan/scan") and
                              r.request.method == "POST") as started:
        held_posts[0].continue_()
    assert started.value.status == 202
    expect(chosen_control).to_be_focused()
    page.wait_for_function("""() => {
        const summary = document.querySelector('#networkBody [data-network-summary]');
        return (summary.matches('h3') ? summary : summary.querySelector('h3')).innerText === 'Connection checked';
    }""")
    expect(chosen_control).to_be_focused()


def test_network_draft_ip_and_focus_survive_repaint_and_back_navigation(page):
    open_network(page, LAMP.name)
    ip_input = page.locator("#networkBody [data-lan-check] input")
    ip_input.fill("192.168.2.200")
    repaint(page)
    assert ip_input.input_value() == "192.168.2.200"
    expect(ip_input).to_be_focused()
    page.locator("#panelBack").click()
    expect(page.locator("#panelNetworkOpen")).to_be_focused()
    page.locator("#panelNetworkOpen").click()
    assert ip_input.input_value() == "192.168.2.200"
    expect(ip_input).to_be_focused()


def test_network_pristine_discovered_ip_keeps_its_selection_on_repaint_and_accepts_new_results(
        page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, {
        PLUG.id: _lan("busy", "192.168.2.61", "3.4"),
    }, {
        PLUG.id: _lan("ok", "192.168.2.62", "3.5"),
    })
    scanned(page)
    open_network(page, PLUG.name)
    ip_input = page.locator("#networkBody [data-lan-check] input")
    assert ip_input.evaluate("el => el.value === el.defaultValue")
    ip_input.evaluate("el => { el.focus(); el.setSelectionRange(2, 9); }")

    repaint(page)

    expect(ip_input).to_be_focused()
    assert ip_input.evaluate("el => [el.selectionStart, el.selectionEnd]") == [2, 9]
    scanned(page)
    assert ip_input.count() == 0
    assert field_value(page, "local_ip") == "192.168.2.62"
    assert field_value(page, "protocol_version") == "3.5"


def test_network_scan_action_opens_the_actual_network_scan_form(page):
    open_network(page, PLUG.name)
    page.locator("#networkBody [data-network-action='scan']").click()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    assert page.locator("#scanBox").is_visible()
    expect(page.locator("#scanTargets")).to_be_focused()


def test_setup_network_history_returns_to_setup_then_details_with_their_state(page):
    page.set_viewport_size({"width": 390, "height": 640})
    open_information(page, PLUG.name)
    details_scroll = page.locator("#panelBody").evaluate("el => { el.scrollTop = 350; return el.scrollTop; }")
    assert details_scroll > 0
    page.locator("#panelBack").click()
    page.locator("#panelSetupOpen").click()
    setup_tool(page, "localtuya")
    page.select_option("#setupBody [data-localtuya-edition]", "original")
    page.locator("#setupBody [data-setup-advanced] summary").click()
    manual_ip = setup_action(page, "network")
    assert manual_ip.inner_text() == "Enter IP manually"
    manual_ip.click()
    assert page.locator("#panelBack").inner_text() == "Back to set up in…"
    page.fill("#networkBody [data-lan-check] input", "192.168.2.245")
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    assert page.locator("#setupBody [data-setup-tool='localtuya']").get_attribute("aria-selected") == "true"
    assert page.locator("#setupBody [data-localtuya-edition]").input_value() == "original"
    assert page.locator("#setupBody [data-setup-advanced]").evaluate("el => el.open")
    expect(manual_ip).to_be_focused()
    assert page.locator("#panelBack").inner_text() == "Back to device"
    page.keyboard.press("Escape")
    assert page.locator("#panelTitle").inner_text() == PLUG.name
    expect(page.locator("#panelSetupOpen")).to_be_focused()
    page.locator("#panelInfoOpen").click()
    assert page.locator("#panelBody").evaluate("el => el.scrollTop") == pytest.approx(details_scroll, abs=1)
    page.keyboard.press("Escape")
    assert page.locator("#panelBack").is_hidden()
    page.keyboard.press("Escape")
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"


def test_setup_device_link_history_restores_previous_device_options_scroll_and_focus(page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    page.set_viewport_size({"width": 390, "height": 640})
    open_setup(page, ZIGBEE_GATEWAY.name)
    setup_tool(page, "localtuya")
    page.select_option("#setupBody [data-localtuya-edition]", "original")
    page.locator("#setupBody [data-setup-advanced] summary").click()
    child_link = page.locator(f"#setupBody .setup-children [data-open-device='{sub.id}']")
    child_link.focus()
    setup_scroll = page.locator("#setupBody").evaluate("el => el.scrollTop")
    assert setup_scroll > 0

    child_link.press("Enter")
    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    setup_tool(page, "tinytuya")
    page.locator("#panelBack").click()

    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    assert page.locator("#setupBody [data-setup-tool='localtuya']").get_attribute("aria-selected") == "true"
    assert page.locator("#setupBody [data-localtuya-edition]").input_value() == "original"
    assert page.locator("#setupBody [data-setup-advanced]").evaluate("el => el.open")
    assert page.locator("#setupBody").evaluate("el => el.scrollTop") == pytest.approx(setup_scroll, abs=1)
    expect(child_link).to_be_focused()
    page.keyboard.press("Escape")
    assert page.locator("#panelTitle").inner_text() == ZIGBEE_GATEWAY.name
    expect(page.locator("#panelSetupOpen")).to_be_focused()


def test_network_gateway_link_history_restores_the_child_draft_and_initiating_link(page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    open_network(page, sub.name)
    ip_input = page.locator("#networkBody [data-lan-check] input")
    ip_input.fill("192.168.2.245")
    gateway_link = page.locator(f"#networkBody .lan-note [data-open-device='{ZIGBEE_GATEWAY.id}']")

    gateway_link.click()
    assert page.locator("#panelTitle").inner_text() == "Local network"
    assert page.locator("#panelBack").inner_text() == "Back to local network"
    ip_input.fill("192.168.2.246")
    page.keyboard.press("Escape")

    assert page.locator("#panelTitle").inner_text() == "Local network"
    assert ip_input.input_value() == "192.168.2.245"
    expect(gateway_link).to_be_focused()
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == sub.name
    expect(page.locator("#panelNetworkOpen")).to_be_focused()


def test_network_close_and_table_reopen_start_a_fresh_navigation_history(page):
    open_setup(page, PLUG.name)
    setup_action(page, "network").click()
    page.fill("#networkBody [data-lan-check] input", "192.168.2.245")
    page.locator("#panelClose").click()
    open_panel(page, LAMP.name)

    assert page.locator("#panelTitle").inner_text() == LAMP.name
    assert page.locator("#panelBack").is_hidden()
    page.locator("#panelNetworkOpen").click()
    assert page.locator("#panelBack").inner_text() == "Back to device"
    page.keyboard.press("Escape")
    assert page.locator("#panelTitle").inner_text() == LAMP.name
    assert page.locator("#panelBack").is_hidden()
    page.keyboard.press("Escape")
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"


def test_setup_history_skips_a_previous_device_removed_by_refresh(page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    open_setup(page, ZIGBEE_GATEWAY.name)
    page.locator(f"#setupBody .setup-children [data-open-device='{sub.id}']").click()
    setup_action(page, "network").click()
    _serves(running_app, monkeypatch, list(DEVICES) + [sub])

    refreshed(page, "#changesNotice")
    page.locator("#panelBack").click()

    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    assert page.locator("#setupBody").is_visible()
    page.locator("#panelBack").click()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"


def test_setup_history_returns_to_previous_view_when_the_current_device_is_removed(
        page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    row(page, ZIGBEE_GATEWAY).locator("[data-open]").click()
    page.locator("#panelSetupOpen").click()
    page.locator(f"#setupBody .setup-children [data-open-device='{sub.id}']").click()
    _serves(running_app, monkeypatch, list(DEVICES) + [ZIGBEE_GATEWAY])

    refreshed(page, "#changesNotice")

    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    assert page.locator("#setupBody").is_visible()
    assert page.locator("#setupBody .setup-children li").count() == 0
    expect(page.locator("#setupBody [data-setup-tool='tuya-local']")).to_be_focused()
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == ZIGBEE_GATEWAY.name
    expect(page.locator("#panelSetupOpen")).to_be_focused()
    page.locator("#panelClose").click()
    expect(row(page, ZIGBEE_GATEWAY).locator("[data-open]")).to_be_focused()


def test_setup_history_supports_a_registered_extra_page_without_another_back_handler(page):
    page.set_viewport_size({"width": 390, "height": 640})
    open_setup(page, PLUG.name)
    setup_tool(page, "tinytuya")
    page.evaluate("""async () => {
        const [{ registerPanelPage, navigatePanel }, { appState }] = await Promise.all([
            import(new URL('static/js/features/panel.js', document.baseURI).href),
            import(new URL('static/js/shared/state.js', document.baseURI).href),
        ]);
        const body = document.createElement('div');
        body.id = 'panelExtraTestBody';
        body.className = 'panel-body hidden';
        body.tabIndex = -1;
        document.getElementById('panel').append(body);
        registerPanelPage('extra_test', {
            body: body.id,
            backLabel: 'Back to additional setup',
            title: d => `Additional setup for “${d.name}”`,
            render: () => '<details><summary>Additional options</summary>' +
                '<div style="height:900px">Extra preferences</div></details>' +
                '<button type="button" data-setup-action="network">Local network</button>',
            focus: 'summary',
        });
        const opener = document.querySelector('#setupBody [data-setup-action="network"]');
        opener.addEventListener('click', event => {
            event.stopPropagation();
            navigatePanel(appState.selectedId, 'extra_test', opener);
        }, { once: true });
    }""")

    setup_action(page, "network").click()
    extra_body = page.locator("#panelExtraTestBody")
    assert extra_body.is_visible()
    extra_body.locator("details summary").click()
    next_page = extra_body.locator("[data-setup-action='network']")
    next_page.focus()
    extra_scroll = extra_body.evaluate("el => el.scrollTop")
    assert extra_scroll > 0
    next_page.press("Enter")

    assert page.locator("#networkBody").is_visible()
    assert page.locator("#panelBack").inner_text() == "Back to additional setup"
    page.locator("#panelBack").click()

    assert page.locator("#panelTitle").inner_text() == f"Additional setup for “{PLUG.name}”"
    assert extra_body.locator("details").evaluate("el => el.open")
    assert extra_body.evaluate("el => el.scrollTop") == pytest.approx(extra_scroll, abs=1)
    expect(next_page).to_be_focused()
    page.keyboard.press("Escape")
    assert page.locator("#panelTitle").inner_text() == "Set up in…"
    assert page.locator("#setupBody [data-setup-tool='tinytuya']").get_attribute("aria-selected") == "true"
    expect(setup_action(page, "network")).to_be_focused()


@pytest.mark.parametrize("width, scheme", [
    (1440, "light"), (1440, "dark"), (390, "light"), (390, "dark"),
    (320, "light"), (320, "dark"),
])
def test_network_navigation_and_layout_at_each_screen_size(page, running_app, monkeypatch, width, scheme):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    sub.name = "Garden irrigation valve beside the greenhouse and vegetable beds"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme=scheme)
    open_panel(page, sub.name)
    close_box = page.locator("#panelClose").bounding_box()
    assert page.locator("#panelBody [data-lan-check], #panelBody dt[title='local_ip']").count() == 0
    page.locator("#panelNetworkOpen").press("Enter")

    title = page.locator("#panelTitle")
    assert title.inner_text() == "Local network"
    assert title.get_attribute("title") == title.text_content()
    assert title.evaluate("el => el.clientHeight <= 2 * parseFloat(getComputedStyle(el).lineHeight) + 1")
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert page.locator("#panelBody").is_hidden() and page.locator("#setupBody").is_hidden()
    assert page.locator("#networkBody [role='tablist'], #networkBody [data-setup-tool]").count() == 0
    assert page.locator("#networkBody").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert sub.local_key not in page.locator("#networkBody").inner_text()
    page.keyboard.press("Escape")

    assert page.locator("#panelTitle").inner_text() == sub.name
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    expect(page.locator("#panelNetworkOpen")).to_be_focused()
    page.locator("#panelNetworkOpen").click()
    page.locator("#panelBack").click()
    expect(page.locator("#panelNetworkOpen")).to_be_focused()
    page.locator("#panelNetworkOpen").click()
    page.locator("#panelClose").click()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    open_network(page, sub.name)


def test_network_short_mobile_viewport_keeps_header_fixed_and_actions_reachable(page, running_app, monkeypatch):
    sub = _named_sub(VALVE, ZIGBEE_GATEWAY)
    sub.name = "Garden irrigation valve beside the greenhouse and vegetable beds"
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, sub)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=sub.id),
        sub.id: _lan("via_gateway", "192.168.2.8", "3.4",
                     gateway_id=ZIGBEE_GATEWAY.id, sub_online=False),
    })
    scanned(page)
    page.set_viewport_size({"width": 320, "height": 640})
    page.emulate_media(color_scheme="dark")
    open_network(page, sub.name)
    body = page.locator("#networkBody")
    close_box = page.locator("#panelClose").bounding_box()
    title_box = page.locator("#panelTitle").bounding_box()
    scan_action = page.locator("#networkBody [data-network-action='scan']")
    assert body.evaluate("el => el.scrollHeight > el.clientHeight"), "long network results scroll in a short viewport"

    scan_action.scroll_into_view_if_needed()
    scan_action.focus()

    expect(scan_action).to_be_focused()
    assert body.evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("#panel").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert page.locator("#panelTitle").bounding_box() == pytest.approx(title_box, abs=0.5)
    box = scan_action.bounding_box()
    assert box["y"] >= title_box["y"] + title_box["height"]
    assert box["y"] + box["height"] <= 640
    assert box["x"] >= 0 and box["x"] + box["width"] <= 320


# --------------------------------------------------------------------------- #
# Persistent device exclusions (issue #9)
# --------------------------------------------------------------------------- #
def device_checkbox(page, device):
    identity = device if isinstance(device, str) else device.id
    return page.locator(f"#rows [data-device-select='{identity}']")


def select_devices(page, *devices):
    enter_selection(page)
    for device in devices:
        device_checkbox(page, device).check()


def enter_selection(page):
    expect(page.locator("#devices")).to_be_visible()
    if not page.locator("#selectAllDevices").count():
        open_filters(page)
        page.locator("#selectDevicesBtn").click()
    expect(page.locator("#selectAllDevices")).to_be_visible()


def switch_device_view(page):
    if page.locator("#backToDevicesBtn").is_visible():
        page.locator("#backToDevicesBtn").click()
    else:
        open_filters(page)
        page.locator("#excludedViewBtn").click()


def assert_no_selection(page):
    assert page.locator("#rows [data-device-select]:checked").count() == 0
    if page.locator("#bulkActions").is_visible():
        expect(page.locator("#selectionCount")).to_have_text("0 selected")
        expect(page.locator("#bulkActions button:not(.hidden)").filter(
            has_text="selected")).to_be_disabled()


def save_selection(page, *, restore=False):
    with page.expect_response(lambda response:
                              response.url.endswith("/api/device-exclusions") and
                              response.request.method == "PATCH") as saved:
        page.locator("#restoreSelected" if restore else "#excludeSelected").click()
    saved.value.finished()
    assert saved.value.status == 200
    expect(page.locator("#bulkActions")).to_be_hidden()
    return saved.value.json()


def export_rows(page, tmp_path, filename):
    with page.expect_download() as download:
        page.locator("#csvBtn").click()
    assert download.value.suggested_filename == filename
    path = tmp_path / filename
    download.value.save_as(path)
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_exclusions_selection_is_opt_in_and_done_clears_without_saving(page):
    original_headers = page.locator("#thead th").all_inner_texts()
    assert page.locator("#rows input[type='checkbox'], #thead input[type='checkbox']").count() == 0
    assert page.locator("#thead .select-col, #rows .select-col").count() == 0
    enter_selection(page)
    assert page.locator("#rows [data-device-select]").count() == len(DEVICES)
    assert page.locator("#selectAllDevices").get_attribute("aria-label")
    expect(page.locator("#excludeSelected")).to_be_disabled()
    select_devices(page, PLUG)
    page.locator("#selectionDoneBtn").click()

    assert page.locator("#rows input[type='checkbox'], #thead input[type='checkbox']").count() == 0
    assert page.locator("#thead .select-col, #rows .select-col").count() == 0
    assert page.locator("#thead th").all_inner_texts() == original_headers
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []
    select_devices(page, PLUG)
    save_selection(page)
    assert page.locator("#rows [data-device-select], #selectAllDevices").count() == 0
    switch_device_view(page)
    expect(row(page, PLUG)).to_be_visible()
    assert page.locator("#rows [data-device-select], #selectAllDevices").count() == 0
    open_panel(page, PLUG.name)
    assert page.locator("#panelTitle").inner_text() == PLUG.name


@pytest.mark.parametrize("width", [1440, 390])
def test_exclusions_whole_row_toggles_selection_without_opening_details(page, width):
    page.set_viewport_size({"width": width, "height": 1000})
    enter_selection(page)
    assert page.locator("#rows [data-open], #thead .details-col").count() == 0
    for column in ("name", "id", "online"):
        cell = row(page, PLUG).locator(f"td[data-column='{column}']")
        cell.click()
        expect(device_checkbox(page, PLUG)).to_be_checked()
        expect(page.locator("#selectionCount")).to_have_text("1 selected")
        assert page.locator("#panel").get_attribute("aria-hidden") == "true"
        cell.click()
        expect(device_checkbox(page, PLUG)).not_to_be_checked()
        expect(page.locator("#selectionCount")).to_have_text("0 selected")
    row(page, PLUG).locator("td.select-col").click(position={"x": 3, "y": 3})
    expect(device_checkbox(page, PLUG)).to_be_checked()
    device_checkbox(page, PLUG).click()
    expect(device_checkbox(page, PLUG)).not_to_be_checked()
    expect(page.locator("#selectionCount")).to_have_text("0 selected")
    key = row(page, PLUG).locator("[data-copy]").first
    key.click()
    expect(key).to_have_text("copied!")
    assert clipboard(page) == PLUG.local_key
    expect(device_checkbox(page, PLUG)).not_to_be_checked()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    page.locator("#selectionDoneBtn").click()
    open_panel(page, PLUG.name)
    assert page.locator("#panelTitle").inner_text() == PLUG.name


def test_exclusions_whole_row_restores_current_and_missing_devices_without_details(
        page, running_app, monkeypatch):
    select_devices(page, PLUG, LAMP)
    save_selection(page)
    _serves(running_app, monkeypatch, [LAMP, SENSOR, BARE])
    refreshed(page, "#rows tr")
    switch_device_view(page)
    enter_selection(page)
    for device in (PLUG, LAMP):
        page.locator(f"#rows tr[data-id='{device.id}'] td[data-column='name']").click()
        expect(device_checkbox(page, device)).to_be_checked()
    expect(page.locator("#selectionCount")).to_have_text("2 selected")
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    assert save_selection(page, restore=True)["excluded_ids"] == []
    switch_device_view(page)
    assert set(listed_ids(page)) == {LAMP.id, SENSOR.id, BARE.id}


def test_exclusions_bulk_round_trip_persists_across_reload_and_restores_without_refresh(page):
    select_devices(page, PLUG, LAMP)
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    expect(page.locator("#selectionCount")).to_contain_text("2 selected")
    assert page.locator("#selectAllDevices").evaluate("el => el.indeterminate")

    result = save_selection(page)

    assert set(result["excluded_ids"]) == {PLUG.id, LAMP.id}
    assert result["revision"] > 0 and result["account_id"]
    assert listed_ids(page) == [SENSOR.id, BARE.id]
    expect(page.locator("#excludedViewBtn")).to_contain_text("2")
    assert page.locator("#changesNotice").count() == 0
    page.reload(wait_until="networkidle")
    expect(row(page, PLUG)).to_have_count(0)
    assert listed_ids(page) == [SENSOR.id, BARE.id]

    switch_device_view(page)
    expect(row(page, PLUG)).to_be_visible()
    assert set(listed_ids(page)) == {PLUG.id, LAMP.id}
    assert PLUG.local_key not in row(page, PLUG).inner_text()
    select_devices(page, PLUG)
    save_selection(page, restore=True)
    expect(row(page, PLUG)).to_have_count(0)
    assert listed_ids(page) == [LAMP.id]
    page.locator("#restoreAll").click()
    expect(page.locator("#rows tr")).to_have_count(0)
    switch_device_view(page)
    expect(page.locator("#rows tr")).to_have_count(len(DEVICES))
    assert page.locator("#changesNotice").count() == 0


def test_exclusions_select_all_only_selects_visible_matches_not_collapsed_children_or_context(
        page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY,
                   _named_sub(VALVE, ZIGBEE_GATEWAY), _named_sub(WATER_TIMER, ZIGBEE_GATEWAY))
    row(page, ZIGBEE_GATEWAY).locator("[data-gateway-toggle]").click()
    enter_selection(page)
    page.locator("#selectAllDevices").check()
    assert page.locator("#rows [data-device-select]:checked").count() == len(DEVICES) + 1
    assert not device_checkbox(page, VALVE).is_checked()
    assert not device_checkbox(page, WATER_TIMER).is_checked()
    page.locator("#clearSelection").click()
    page.fill("#filter", VALVE.name)
    assert listed_ids(page) == [ZIGBEE_GATEWAY.id, VALVE.id]
    row(page, ZIGBEE_GATEWAY).locator("td[data-column='id']").click()
    assert_no_selection(page)
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"

    page.locator("#selectAllDevices").check()

    assert device_checkbox(page, VALVE).is_checked()
    assert device_checkbox(page, ZIGBEE_GATEWAY).is_disabled()
    expect(page.locator("#selectionCount")).to_contain_text("1 selected")
    result = save_selection(page)
    assert result["excluded_ids"] == [VALVE.id]
    expect(page.locator("#rows tr")).to_have_count(0)


def test_exclusions_selection_survives_sort_and_redraw_but_clears_on_search_and_view(page):
    select_devices(page, LAMP)
    save_selection(page)
    enter_selection(page)
    checkbox = device_checkbox(page, PLUG)
    checkbox.focus()
    checkbox.press("Space")
    expect(checkbox).to_be_checked()
    expect(checkbox).to_be_focused()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    page.locator("#thead th[data-key='name']").click()
    expect(checkbox).to_be_checked()
    page.locator("#thead [data-key-toggle]").click()
    expect(checkbox).to_be_checked()
    page.fill("#filter", "plug")
    expect(checkbox).not_to_be_checked()
    assert_no_selection(page)
    select_devices(page, PLUG)
    switch_device_view(page)
    expect(page.locator("#bulkActions")).to_be_hidden()
    switch_device_view(page)
    enter_selection(page)
    expect(device_checkbox(page, PLUG)).not_to_be_checked()


def test_exclusions_selection_prunes_collapsed_children_and_devices_removed_by_refresh(
        page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY,
                   _named_sub(VALVE, ZIGBEE_GATEWAY))
    select_devices(page, PLUG, VALVE)
    row(page, ZIGBEE_GATEWAY).locator("[data-gateway-toggle]").click()
    assert device_checkbox(page, PLUG).is_checked()
    assert not device_checkbox(page, VALVE).is_checked()
    expect(page.locator("#selectionCount")).to_contain_text("1 selected")
    _serves(running_app, monkeypatch, [LAMP, SENSOR, BARE])
    refreshed(page, "#changesNotice")
    assert_no_selection(page)


def test_exclusions_failed_write_keeps_rows_and_selection_and_can_be_retried(page):
    def fail_write(route):
        if route.request.method == "PATCH":
            route.fulfill(status=503, json={"error": "Could not save device exclusions. Try again."})
        else:
            route.continue_()

    page.route("**/api/device-exclusions", fail_write)
    select_devices(page, PLUG, LAMP)
    page.locator("#excludeSelected").click()
    expect(page.locator("#exclusionError")).to_be_visible()
    expect(page.locator("#filterCount")).to_be_hidden()
    expect(page.locator("#excludeSelected")).to_be_enabled()
    assert listed_ids(page) == [device.id for device in DEVICES]
    assert device_checkbox(page, PLUG).is_checked() and device_checkbox(page, LAMP).is_checked()
    assert page.locator("#selectionCount").inner_text().startswith("2 selected")
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []
    page.unroute("**/api/device-exclusions", fail_write)

    save_selection(page)

    expect(page.locator("#exclusionError")).to_be_hidden()
    expect(page.locator("#filterCount")).to_have_text("2")
    assert listed_ids(page) == [SENSOR.id, BARE.id]


def test_exclusions_failed_save_is_not_replayed_after_the_selection_context_changes(page):
    def fail_write(route):
        if route.request.method == "PATCH":
            route.fulfill(status=503, json={"error": "preference_unavailable"})
        else:
            route.continue_()

    page.route("**/api/device-exclusions", fail_write)
    select_devices(page, PLUG)
    page.locator("#excludeSelected").click()
    expect(page.locator("#exclusionError")).to_be_visible()
    page.fill("#filter", LAMP.name)
    expect(page.locator("#exclusionError")).to_be_hidden()
    assert_no_selection(page)
    page.unroute("**/api/device-exclusions", fail_write)
    select_devices(page, LAMP)

    assert save_selection(page)["excluded_ids"] == [LAMP.id]

    page.fill("#filter", "")
    expect(row(page, PLUG)).to_be_visible()
    expect(row(page, LAMP)).to_have_count(0)


def test_exclusions_initial_load_failure_hides_table_until_a_successful_retry(page):
    def fail_read(route):
        route.fulfill(status=503, json={"error": "Device preferences could not be read. Try again."})

    page.route("**/api/device-exclusions", fail_read)
    page.reload(wait_until="networkidle")
    expect(page.locator("#retryExclusions")).to_be_visible()
    expect(page.locator("#rows tr")).to_have_count(0)
    assert page.locator("#exclusionError").get_attribute("role") == "alert"
    page.unroute("**/api/device-exclusions", fail_read)

    page.locator("#retryExclusions").click()

    expect(page.locator("#rows tr")).to_have_count(len(DEVICES))
    expect(page.locator("#exclusionError")).to_be_hidden()


def test_exclusions_failed_background_read_preserves_the_last_saved_view(page):
    select_devices(page, PLUG)
    save_selection(page)

    def fail_read(route):
        route.fulfill(status=503, json={"error": "preference_unavailable"})

    page.route("**/api/device-exclusions", fail_read)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.locator("#exclusionError")).to_be_visible()
    assert listed_ids(page) == [LAMP.id, SENSOR.id, BARE.id]
    page.unroute("**/api/device-exclusions", fail_read)
    page.locator("#retryExclusions").click()
    expect(page.locator("#exclusionError")).to_be_hidden()
    assert listed_ids(page) == [LAMP.id, SENSOR.id, BARE.id]


def test_exclusions_late_older_read_cannot_undo_a_successful_save(page):
    previous = page.request.get(page.url + "api/device-exclusions").json()
    held = []

    def delay_read(route):
        if route.request.method == "GET":
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/device-exclusions", delay_read)
    with page.expect_request(lambda request: request.url.endswith("/api/device-exclusions") and
                             request.method == "GET"):
        page.evaluate("window.dispatchEvent(new Event('focus'))")
    select_devices(page, PLUG)
    saved = save_selection(page)
    assert saved["revision"] > previous["revision"]
    assert len(held) == 1

    with page.expect_response(lambda response: response.url.endswith("/api/device-exclusions") and
                              response.request.method == "GET") as response:
        held[0].fulfill(status=200, json=previous)
    response.value.finished()
    page.wait_for_timeout(100)

    expect(row(page, PLUG)).to_have_count(0)
    expect(page.locator("#excludedViewBtn")).to_contain_text("1")
    assert frontend_state(page, "exclusionRevision") == saved["revision"]
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == [PLUG.id]


def test_exclusions_mismatched_account_responses_do_not_render_a_mixed_device_list(page):
    def different_account(route):
        route.fulfill(status=200, json={"account_id": "another-account", "revision": 0,
                                        "excluded_ids": []})

    page.route("**/api/device-exclusions", different_account)
    page.reload(wait_until="networkidle")

    expect(page.locator("#retryExclusions")).to_be_visible()
    expect(page.locator("#rows tr")).to_have_count(0)
    expect(page.locator("#filter")).to_be_disabled()
    assert PLUG.local_key not in page.locator("#devices").inner_text()
    page.unroute("**/api/device-exclusions", different_account)
    page.locator("#retryExclusions").click()
    expect(page.locator("#rows tr")).to_have_count(len(DEVICES))
    expect(page.locator("#exclusionError")).to_be_hidden()


def test_exclusions_account_changed_during_save_requires_reload_and_never_replays_selection(
        page, restores_session):
    select_devices(page, PLUG, LAMP)
    path = Path(restores_session.SESSION_FILE)
    session = json.loads(path.read_text())
    session["user_code"] = "a-different-account"
    path.write_text(json.dumps(session))
    patches = []
    page.on("request", lambda request: patches.append(request.post_data_json)
            if request.url.endswith("/api/device-exclusions") and request.method == "PATCH" else None)

    with page.expect_response(lambda response: response.url.endswith("/api/device-exclusions") and
                              response.request.method == "PATCH") as rejected:
        page.locator("#excludeSelected").click()
    assert rejected.value.status == 409
    expect(page.locator("#exclusionError")).to_be_visible()
    expect(page.locator("#retryExclusions")).to_be_visible()
    page.locator("#retryExclusions").click()

    expect(page.locator("#exclusionError")).to_be_hidden()
    expect(page.locator("#rows tr")).to_have_count(len(DEVICES))
    expect(page.locator("#bulkActions")).to_be_hidden()
    enter_selection(page)
    expect(device_checkbox(page, PLUG)).not_to_be_checked()
    assert len(patches) == 1
    assert set(patches[0]["exclude"]) == {PLUG.id, LAMP.id}
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []


def test_exclusions_missing_ids_can_be_restored_but_never_exported(page, running_app, monkeypatch,
                                                                 tmp_path):
    select_devices(page, PLUG, LAMP)
    save_selection(page)
    _serves(running_app, monkeypatch, [LAMP, SENSOR, BARE])
    refreshed(page, "#rows tr")
    active_csv = export_rows(page, tmp_path, "tuya-local-key.csv")
    assert {device["id"] for device in active_csv} == {SENSOR.id, BARE.id}
    switch_device_view(page)
    missing = page.locator(f"#rows tr[data-id='{PLUG.id}'][data-missing]")
    expect(missing).to_be_visible()
    assert "Not in the current device list" in missing.inner_text()
    assert missing.locator("[data-open], .key, [data-copy]").count() == 0
    excluded_csv = export_rows(page, tmp_path, "tuya-local-key-excluded.csv")
    assert [device["id"] for device in excluded_csv] == [LAMP.id]
    page.fill("#filter", LAMP.name)
    assert [device["id"] for device in export_rows(page, tmp_path, "tuya-local-key-excluded.csv")] == [LAMP.id]
    page.fill("#filter", PLUG.id)
    select_devices(page, PLUG)
    assert save_selection(page, restore=True)["excluded_ids"] == [LAMP.id]
    expect(missing).to_have_count(0)
    page.locator("#restoreAll").click()
    expect(page.locator("#restoreAll")).to_be_hidden()
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []


def test_exclusions_restore_all_includes_ids_hidden_by_search(page):
    enter_selection(page)
    page.locator("#selectAllDevices").check()
    save_selection(page)
    expect(page.locator("#rows tr")).to_have_count(0)
    assert "excluded" in page.locator("#devices").inner_text().lower()
    switch_device_view(page)
    page.fill("#filter", PLUG.name)
    assert listed_ids(page) == [PLUG.id]

    page.locator("#restoreAll").click()

    expect(page.locator("#rows tr")).to_have_count(0)
    switch_device_view(page)
    assert listed_ids(page) == [PLUG.id]
    page.fill("#filter", "")
    expect(page.locator("#rows tr")).to_have_count(len(DEVICES))


def test_exclusions_change_notices_follow_the_view_without_acknowledging_hidden_changes(
        page, running_app, monkeypatch):
    select_devices(page, PLUG)
    save_selection(page)
    _serves(running_app, monkeypatch, _with_rotated_key())
    refreshed(page, "#rows tr")
    assert page.locator("#changesNotice").count() == 0
    switch_device_view(page)
    expect(page.locator("#changesNotice")).to_contain_text("1 local key changed")
    assert PLUG.name in page.locator("#changesNotice").inner_text()
    assert "ROTATED-KEY-9999" not in page.locator("#changesNotice").inner_text()
    select_devices(page, PLUG)
    save_selection(page, restore=True)
    assert page.locator("#changesNotice").count() == 0
    switch_device_view(page)
    expect(page.locator("#changesNotice")).to_contain_text("1 local key changed")
    assert "key changed" in row(page, PLUG).inner_text().lower()


@pytest.mark.parametrize("hide_gateway, hide_child", [(True, False), (False, True), (True, True)])
def test_exclusions_gateway_visibility_keeps_account_wide_scan_and_child_setup_evidence(
        page, running_app, monkeypatch, hide_gateway, hide_child):
    child = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, child)
    hidden = [device for device, excluded in ((ZIGBEE_GATEWAY, hide_gateway), (child, hide_child))
              if excluded]
    select_devices(page, *hidden)
    save_selection(page)
    expected_ids = {device.id for device in DEVICES} | {
        device.id for device in (ZIGBEE_GATEWAY, child) if device not in hidden}
    assert set(listed_ids(page)) == expected_ids, "group context must respect each device's visibility"
    calls = _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=child.id),
        child.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })

    scanned(page)

    assert {ZIGBEE_GATEWAY.id, child.id} <= set(calls[0]["devices"])
    assert set(listed_ids(page)) == expected_ids
    assert "all account devices" in page.locator("#lanSummaryNotice").inner_text()
    if hide_child:
        if not hide_gateway:
            open_setup(page, ZIGBEE_GATEWAY.name)
            assert page.locator(f".setup-children [data-open-device='{child.id}']").count() == 0
            assert "excluded sub-device" in page.locator("#setupBody").inner_text().lower()
            page.locator("#panelClose").click()
        switch_device_view(page)
    open_setup(page, child.name)
    assert setup_value(page, "device_id") == ZIGBEE_GATEWAY.id + (" Excluded" if hide_gateway else "")
    assert setup_field(page, "device_id").locator("button.setup-copy").get_attribute("data-copy") == ZIGBEE_GATEWAY.id
    assert setup_value(page, "host") == "192.168.2.8"
    assert setup_value(page, "protocol_version") == "3.4"
    assert setup_value(page, "device_cid") == child.node_id
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == child.local_key
    setup_tool(page, "tinytuya")
    snippets = execute_setup_snippet(copied_setup_snippet(page), monkeypatch)
    assert snippets[0].id == ZIGBEE_GATEWAY.id
    assert snippets[0].real_local_key == child.local_key.encode("latin1")
    assert snippets[1].id == child.id and snippets[1].parent is snippets[0]
    if hide_gateway and not hide_child:
        page.locator(f".setup-card [data-open-device='{ZIGBEE_GATEWAY.id}']").click()
        expect(page.locator("#panel [data-excluded-note]")).to_contain_text("Excluded")
        assert setup_value(page, "dev_id") == ZIGBEE_GATEWAY.id
        with page.expect_response(lambda response: response.url.endswith("/api/device-exclusions") and
                                  response.request.method == "PATCH") as restored:
            page.locator(f"#panel [data-restore-device='{ZIGBEE_GATEWAY.id}']").click()
        assert restored.value.status == 200
        expect(row(page, ZIGBEE_GATEWAY)).to_be_visible()
        expect(page.locator("#panel [data-excluded-note]")).to_have_count(0)
        page.locator("#panelBack").click()
        assert page.locator("#panelTitle").inner_text() == "Set up in…"


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (1440, "dark"),
                                         (390, "light"), (390, "dark")])
def test_exclusions_controls_fit_and_keyboard_focus_survives_selected_row_removal(page, width, scheme):
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme=scheme)
    if width >= 900:
        open_panel(page, LAMP.name)
    enter_selection(page)
    checkbox = device_checkbox(page, PLUG)
    assert PLUG.name in checkbox.get_attribute("aria-label")
    checkbox.focus()
    checkbox.press("Space")
    expect(checkbox).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth") <= width

    action = page.locator("#excludeSelected")
    action.scroll_into_view_if_needed()
    box = action.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    save_selection(page)

    if width >= 900:
        assert page.locator("#panelTitle").inner_text() == LAMP.name
        assert page.locator("#panel").get_attribute("aria-hidden") == "false"
    assert page.evaluate("document.activeElement !== document.body && document.activeElement.isConnected")
    if width >= 900:
        page.locator("#panelClose").click()
    switch_device_view(page)
    expect(row(page, PLUG)).to_be_visible()
    select_devices(page, PLUG)
    expect(page.locator("#restoreSelected")).to_be_visible()
    restore_box = page.locator("#restoreSelected").bounding_box()
    assert restore_box["x"] >= 0 and restore_box["x"] + restore_box["width"] <= width
    assert page.evaluate("document.documentElement.scrollWidth") <= width


# --------------------------------------------------------------------------- #
# SDK home membership filters the presentation while setup/scans keep all data.
# --------------------------------------------------------------------------- #
HOMES = [
    {"id": "home-a", "name": "Home"},
    {"id": "home-b", "name": "Home"},
    {"id": "home-empty", "name": "Holiday home"},
]
HOME_MEMBERSHIP = {
    PLUG.id: ["home-a", "home-b"],   # shared: both authoritative memberships survive
    LAMP.id: ["home-b"],
    BARE.id: ["home-a"],
    SENSOR.id: [],
}


def serves_homes(page, app, monkeypatch, *, devices=DEVICES, homes=HOMES,
                 membership=HOME_MEMBERSHIP):
    _serves(app, monkeypatch, app.core.Devices(devices, homes=homes, home_ids=membership))
    refreshed(page, "#filtersBtn")


def open_filters(page):
    if page.locator("#filtersPopover").is_hidden():
        page.locator("#filtersBtn").click()
    expect(page.locator("#filtersPopover")).to_be_visible()


def select_home(page, home_id=""):
    open_filters(page)
    value = "unknown" if home_id == "unknown" else "home:" + home_id if home_id else ""
    page.locator("#homeFilter").select_option(value)


def test_homes_use_authoritative_ids_preserve_shared_membership_and_show_empty_unknown_groups(
        page, running_app, monkeypatch, tmp_path):
    serves_homes(page, running_app, monkeypatch)
    assert page.locator("#homeFilter option[value='home:home-a']").inner_text() == "Home (home-a)"
    assert page.locator("#homeFilter option[value='home:home-b']").inner_text() == "Home (home-b)"
    assert page.locator("#homeFilter option[value='home:home-empty']").inner_text() == "Holiday home"
    select_home(page, "home-a")
    assert set(listed_ids(page)) == {PLUG.id, BARE.id}
    assert page.locator("#count").inner_text().startswith("2 devices")
    assert {device["id"] for device in export_rows(page, tmp_path, "tuya-local-key.csv")} == {PLUG.id, BARE.id}
    page.fill("#filter", "kitchen")
    assert listed_ids(page) == [PLUG.id]
    select_home(page, "home-b")
    assert listed_ids(page) == [PLUG.id], "a shared device belongs to either home by ID"
    page.fill("#filter", "")
    assert set(listed_ids(page)) == {PLUG.id, LAMP.id}
    select_home(page, "home-empty")
    assert listed_ids(page) == []
    expect(page.locator("#emptyDevices")).to_be_visible()
    select_home(page, "unknown")
    assert listed_ids(page) == [SENSOR.id]
    select_home(page)
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    assert page.locator("#rows [data-device-select], #selectAllDevices").count() == 0


def test_homes_filter_is_remembered_on_reload_and_missing_home_returns_to_all(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    select_home(page, "home-b")
    page.reload(wait_until="networkidle")
    expect(page.locator("#homeFilter")).to_have_value("home:home-b")
    assert set(listed_ids(page)) == {PLUG.id, LAMP.id}
    serves_homes(page, running_app, monkeypatch, homes=[HOMES[0]],
                 membership={PLUG.id: ["home-a"], BARE.id: ["home-a"]})
    expect(page.locator("#homeFilter")).to_have_value("")
    assert set(listed_ids(page)) == {device.id for device in DEVICES}


def test_homes_remembered_filter_is_scoped_to_the_logged_in_account(
        page, restores_session, monkeypatch):
    serves_homes(page, restores_session, monkeypatch)
    select_home(page, "home-a")
    path = Path(restores_session.SESSION_FILE)
    original_session = json.loads(path.read_text())
    another_session = dict(original_session, user_code="different-home-account")
    path.write_text(json.dumps(another_session))
    refreshed(page, "#filtersBtn")
    expect(page.locator("#homeFilter")).to_have_value("")
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    select_home(page, "home-b")
    path.write_text(json.dumps(original_session))
    refreshed(page, "#filtersBtn")
    expect(page.locator("#homeFilter")).to_have_value("home:home-a")
    assert set(listed_ids(page)) == {PLUG.id, BARE.id}


def test_homes_remembered_filter_survives_a_snapshot_without_home_metadata(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    select_home(page, "home-b")
    _serves(running_app, monkeypatch, DEVICES)
    refreshed(page, "#filtersBtn")
    open_filters(page)
    expect(page.locator("#homeFilter")).to_have_value("")
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    page.reload(wait_until="networkidle")
    open_filters(page)
    expect(page.locator("#homeHint [data-refresh-homes]")).to_be_visible()
    serves_homes(page, running_app, monkeypatch)
    expect(page.locator("#homeFilter")).to_have_value("home:home-b")
    assert set(listed_ids(page)) == {PLUG.id, LAMP.id}


def test_homes_filter_still_works_when_browser_storage_is_unavailable(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("""(() => {
      for (const method of ["getItem", "setItem"]) {
        Object.defineProperty(Storage.prototype, method, {
          configurable: true, value: () => { throw new Error("Storage unavailable"); }
        });
      }
    })();""")
    page.reload(wait_until="networkidle")
    select_home(page, "home-a")
    assert set(listed_ids(page)) == {PLUG.id, BARE.id}
    page.locator("#thead th[data-key='name']").click()
    assert set(listed_ids(page)) == {PLUG.id, BARE.id}
    page.reload(wait_until="networkidle")
    expect(page.locator("#homeFilter")).to_have_value("")
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    assert errors == []


def test_homes_old_snapshot_requires_explicit_refresh_and_never_infers_membership(
        page, running_app, monkeypatch):
    assert page.locator("#homeFilter option").all_inner_texts() == ["All homes"]
    open_filters(page)
    expect(page.locator("#homeHint [data-refresh-homes]")).to_be_visible()
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    refreshed_requests = []
    page.on("request", lambda request: refreshed_requests.append(request.url)
            if "/api/devices?refresh=1" in request.url else None)
    _serves(running_app, monkeypatch,
            running_app.core.Devices(DEVICES, homes=HOMES, home_ids=HOME_MEMBERSHIP))
    page.reload(wait_until="networkidle")
    open_filters(page)
    expect(page.locator("#homeHint [data-refresh-homes]")).to_be_visible()
    assert refreshed_requests == [], "loading a metadata-free snapshot must not trigger extra cloud queries"
    with page.expect_response(lambda response: response.url.endswith("/api/devices?refresh=1")):
        page.locator("#homeHint [data-refresh-homes]").click()
    expect(page.locator("#homeFilter option[value='home:home-a']")).to_have_count(1)
    expect(page.locator("#homeHint")).to_be_hidden()
    select_home(page, "home-a")
    assert set(listed_ids(page)) == {PLUG.id, BARE.id}


def test_homes_known_empty_metadata_has_no_refresh_warning(page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch, homes=[], membership={})
    expect(page.locator("#homeHint")).to_be_hidden()
    assert "All homes" in page.locator("#homeFilter option").all_inner_texts()
    assert page.locator("#homeFilter option[value^='home:']").count() == 0
    assert set(listed_ids(page)) == {device.id for device in DEVICES}


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (1440, "dark"),
                                         (390, "light"), (390, "dark")])
def test_homes_browse_controls_fit_without_selection_column(page, running_app, monkeypatch, width, scheme):
    serves_homes(page, running_app, monkeypatch)
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme=scheme)
    select_home(page, "home-b")
    home = page.locator("#homeFilter")
    assert home.get_attribute("aria-label") == "Filter by home"
    expect(home).to_be_focused()
    assert set(listed_ids(page)) == {PLUG.id, LAMP.id}
    assert page.locator("#thead input[type='checkbox'], #rows input[type='checkbox']").count() == 0
    search = page.locator("#filter").bounding_box()
    toggle = page.locator("#filtersBtn").bounding_box()
    assert toggle["x"] + toggle["width"] <= search["x"]
    assert abs(search["y"] + search["height"] / 2 - toggle["y"] - toggle["height"] / 2) <= 1
    box = home.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    popover = page.locator("#filtersPopover").bounding_box()
    assert popover["x"] >= 16 and popover["x"] + popover["width"] <= width - 16
    arrow = page.locator("#homeChevron").bounding_box()
    assert box["x"] + box["width"] - arrow["x"] - arrow["width"] >= 12
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_filter_precedes_search_and_escape_keeps_the_device_panel_open(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    expect(page.locator("#filtersPopover")).to_be_hidden()
    assert page.locator("#headerActions #selectDevicesBtn, #headerActions #excludedViewBtn").count() == 0
    assert page.locator(".list-filters #filtersBtn").count() == 1
    assert page.locator(".list-filters").evaluate(
        "el => [...el.children].map(child => child.id)") == ["filterWrap", "filter"]
    expect(page.locator("#filtersBtn")).to_have_accessible_name("Filter")
    page.locator("#filtersBtn").focus()
    page.keyboard.press("Tab")
    expect(page.locator("#filter")).to_be_focused()
    open_panel(page, PLUG.name)
    open_filters(page)
    expect(page.locator("#homeFilter")).to_be_focused()
    assert page.locator("#filtersPopover #selectDevicesBtn").count() == 1
    page.keyboard.press("Escape")
    expect(page.locator("#filtersPopover")).to_be_hidden()
    expect(page.locator("#filtersBtn")).to_be_focused()
    assert page.locator("#panel").get_attribute("aria-hidden") == "false"


def test_filters_popover_closes_on_outside_click_and_selection_enters_from_it(page):
    open_filters(page)
    page.locator("#count").click()
    expect(page.locator("#filtersPopover")).to_be_hidden()
    expect(page.locator("#filtersBtn")).to_have_attribute("aria-expanded", "false")
    enter_selection(page)
    expect(page.locator("#filtersPopover")).to_be_hidden()
    expect(page.locator("#selectAllDevices")).to_be_focused()
    page.locator("#selectionDoneBtn").click()
    expect(page.locator("#filtersBtn")).to_be_focused()
    assert page.locator("#rows input[type='checkbox']").count() == 0


def test_filters_clear_all_without_exclusions_resets_home_and_search_without_cloud_or_write(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if "/api/devices" in request.url or request.method == "PATCH" else None)
    select_home(page, "home-a")
    expect(page.locator("#filterCount")).to_be_visible()
    expect(page.locator("#filterCount")).to_have_text("1")
    page.locator("#filtersClose").click()
    expect(page.locator("#filterCount")).to_be_visible()
    page.locator("#filter").fill(PLUG.name)
    open_filters(page)
    assert page.locator("#resetHomeFilter").count() == 0
    assert page.locator(".filter-popover-head #clearAllFilters").count() == 1
    page.locator("#clearAllFilters").click()
    expect(page.locator("#homeFilter")).to_have_value("")
    expect(page.locator("#filter")).to_have_value("")
    expect(page.locator("#filterCount")).to_be_hidden()
    expect(page.locator("#clearAllFilters")).to_be_disabled()
    expect(page.locator("#homeFilter")).to_be_focused()
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    assert requests == []


def test_excluded_control_is_inside_filter_after_exclusion_and_hides_after_restoration(page):
    open_filters(page)
    expect(page.locator("#excludedViewBtn")).to_be_hidden()
    page.locator("#filtersClose").click()
    select_devices(page, PLUG)
    save_selection(page)
    expect(page.locator("#filtersPopover")).to_be_hidden()
    expect(page.locator("#filterCount")).to_have_text("1")
    assert page.locator(".list-filters > #excludedViewBtn").count() == 0
    open_filters(page)
    expect(page.locator("#filtersPopover #excludedViewBtn")).to_be_visible()
    expect(page.locator("#excludedViewBtn")).to_contain_text("1")
    switch_device_view(page)
    expect(page.locator("#filtersPopover")).to_be_hidden()
    expect(page.locator("#excludedViewBtn")).to_have_attribute("aria-pressed", "true")
    select_devices(page, PLUG)
    save_selection(page, restore=True)
    expect(page.locator("#filterCount")).to_be_hidden()
    open_filters(page)
    expect(page.locator("#excludedViewBtn")).to_be_visible()
    page.locator("#backToDevicesBtn").click()
    open_filters(page)
    expect(page.locator("#excludedViewBtn")).to_be_hidden()
    assert set(listed_ids(page)) == {device.id for device in DEVICES}


def test_filter_badge_combines_saved_exclusions_and_home_and_survives_reload(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    select_devices(page, PLUG, LAMP)
    save_selection(page)
    expect(page.locator("#filterCount")).to_have_text("2")
    select_home(page, "home-a")
    expect(page.locator("#filterCount")).to_have_text("3")
    expect(page.locator("#filtersBtn")).to_have_accessible_name(
        "Filter (3): 2 excluded devices, 1 home filter")
    expect(page.locator("#excludedViewBtn")).to_have_text("Excluded devices (2)")
    page.locator("#filtersClose").click()
    page.locator("#filter").fill("nothing matches")
    expect(page.locator("#filterCount")).to_have_text("3")
    page.reload(wait_until="networkidle")
    expect(page.locator("#filterCount")).to_have_text("3")
    open_filters(page)
    page.locator("#homeFilter").select_option("")
    expect(page.locator("#filterCount")).to_have_text("2")
    switch_device_view(page)
    select_devices(page, PLUG)
    save_selection(page, restore=True)
    expect(page.locator("#filterCount")).to_have_text("1")
    page.locator("#restoreAll").click()
    expect(page.locator("#filterCount")).to_be_hidden()
    expect(page.locator("#filtersBtn")).to_have_accessible_name("Filter")


@pytest.mark.parametrize("view", ["active", "excluded"])
def test_filters_clear_all_restores_every_exclusion_and_resets_home_search_and_view(
        page, running_app, monkeypatch, view):
    serves_homes(page, running_app, monkeypatch)
    select_devices(page, PLUG, LAMP)
    save_selection(page)
    select_home(page, "home-a")
    page.locator("#filtersClose").click()
    if view == "excluded":
        switch_device_view(page)
    page.locator("#filter").fill("nothing matches")
    open_filters(page)
    with page.expect_response(lambda response: response.url.endswith("/api/device-exclusions")
                              and response.request.method == "PATCH") as saved:
        page.locator("#clearAllFilters").click()
    saved.value.finished()
    assert saved.value.status == 200
    assert saved.value.request.post_data_json["restore_all"] is True
    expect(page.locator("#homeFilter")).to_have_value("")
    expect(page.locator("#filter")).to_have_value("")
    expect(page.locator("#filterCount")).to_be_hidden()
    expect(page.locator("#backToDevicesBtn")).to_be_hidden()
    expect(page.locator("#excludedViewBtn")).to_be_hidden()
    expect(page.locator("#clearAllFilters")).to_be_disabled()
    expect(page.locator("#homeFilter")).to_be_focused()
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []
    page.reload(wait_until="networkidle")
    expect(page.locator("#homeFilter")).to_have_value("")
    expect(page.locator("#filterCount")).to_be_hidden()
    assert set(listed_ids(page)) == {device.id for device in DEVICES}


def test_filters_clear_all_failed_save_keeps_filters_and_selection_then_retry_clears_them(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    select_devices(page, PLUG, LAMP)
    save_selection(page)
    select_home(page, "home-a")
    page.locator("#filtersClose").click()
    page.locator("#filter").fill(BARE.name)
    select_devices(page, BARE)

    def fail_write(route):
        if route.request.method == "PATCH":
            route.fulfill(status=503, json={"error": "storage_unavailable"})
        else:
            route.continue_()

    page.route("**/api/device-exclusions", fail_write)
    open_filters(page)
    page.locator("#clearAllFilters").click()
    expect(page.locator("#exclusionError")).to_contain_text("Could not clear all filters")
    expect(page.locator("#filterCount")).to_have_text("3")
    expect(page.locator("#homeFilter")).to_have_value("home:home-a")
    expect(page.locator("#filter")).to_have_value(BARE.name)
    expect(device_checkbox(page, BARE)).to_be_checked()
    assert set(page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"]) == {PLUG.id, LAMP.id}
    page.unroute("**/api/device-exclusions", fail_write)
    page.locator("#retryExclusions").click()
    expect(page.locator("#filterCount")).to_be_hidden()
    expect(page.locator("#filter")).to_have_value("")
    expect(page.locator("#homeFilter")).to_have_value("")
    expect(page.locator("#bulkActions")).to_be_hidden()
    expect(page.locator("#filtersBtn")).to_be_focused()
    expect(page.locator("#exclusionError")).to_be_hidden()
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []


def test_homes_changing_filter_clears_selection_and_excludes_only_current_home_matches(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    select_home(page, "home-a")
    select_devices(page, PLUG)
    select_home(page, "home-b")
    assert_no_selection(page)
    select_devices(page, LAMP)
    assert save_selection(page)["excluded_ids"] == [LAMP.id]
    assert listed_ids(page) == [PLUG.id]
    select_home(page, "home-a")
    assert set(listed_ids(page)) == {PLUG.id, BARE.id}
    switch_device_view(page)
    assert listed_ids(page) == []
    expect(page.locator("#restoreAll")).to_contain_text("all homes")
    page.locator("#restoreAll").click()
    expect(page.locator("#restoreAll")).to_be_hidden()
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []


def test_homes_missing_excluded_devices_are_unknown_and_can_still_be_restored(
        page, running_app, monkeypatch):
    serves_homes(page, running_app, monkeypatch)
    select_devices(page, PLUG)
    save_selection(page)
    serves_homes(page, running_app, monkeypatch, devices=[LAMP, SENSOR, BARE])
    switch_device_view(page)
    select_home(page, "home-a")
    assert listed_ids(page) == []
    select_home(page, "unknown")
    assert listed_ids(page) == [PLUG.id]
    expect(page.locator(f"#rows tr[data-id='{PLUG.id}'][data-missing]")).to_be_visible()
    select_devices(page, PLUG)
    assert save_selection(page, restore=True)["excluded_ids"] == []


def test_homes_gateway_context_stays_in_home_while_setup_and_scan_keep_full_account_evidence(
        page, running_app, monkeypatch):
    child = _named_sub(VALVE, ZIGBEE_GATEWAY)
    serves_homes(page, running_app, monkeypatch,
                 devices=list(DEVICES) + [ZIGBEE_GATEWAY, child],
                 membership={**HOME_MEMBERSHIP, ZIGBEE_GATEWAY.id: ["home-a"], child.id: ["home-b"]})
    select_home(page, "home-b")
    assert set(listed_ids(page)) == {PLUG.id, LAMP.id, child.id}
    page.fill("#filter", child.name)
    assert listed_ids(page) == [child.id], "a gateway in another home must not return as a context row"
    calls = _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=child.id),
        child.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    assert {ZIGBEE_GATEWAY.id, child.id} <= set(calls[0]["devices"])
    assert listed_ids(page) == [child.id]
    open_setup(page, child.name)
    assert setup_value(page, "device_id") == ZIGBEE_GATEWAY.id
    assert setup_value(page, "host") == "192.168.2.8"
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == child.local_key
    page.locator(f".setup-card [data-open-device='{ZIGBEE_GATEWAY.id}']").click()
    assert setup_value(page, "device_id") == ZIGBEE_GATEWAY.id
    assert listed_ids(page) == [child.id]
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == "Set up in…"


def test_homes_change_notices_and_csv_follow_the_selected_home(page, running_app, monkeypatch, tmp_path):
    serves_homes(page, running_app, monkeypatch)
    select_home(page, "home-a")
    changed = _with_rotated_key()
    changed[demo_devices.LAMP].name = "A renamed lamp in the other home"
    serves_homes(page, running_app, monkeypatch, devices=changed)
    expect(page.locator("#changesNotice")).to_contain_text("1 local key changed")
    assert "renamed" not in page.locator("#changesNotice").inner_text()
    assert {device["id"] for device in export_rows(page, tmp_path, "tuya-local-key.csv")} == {PLUG.id, BARE.id}
    select_home(page, "home-b")
    expect(page.locator("#changesNotice")).to_contain_text("1 device renamed")
    assert "1 local key changed" in page.locator("#changesNotice").inner_text()
    select_home(page, "home-empty")
    assert page.locator("#changesNotice").count() == 0


@pytest.mark.parametrize("previous_homes_known", [True, False])
def test_homes_removed_device_notices_use_prior_membership_and_legacy_removals_stay_unknown(
        page, running_app, monkeypatch, previous_homes_known):
    if previous_homes_known:
        serves_homes(page, running_app, monkeypatch)
    serves_homes(page, running_app, monkeypatch, devices=[PLUG, SENSOR])
    select_home(page, "home-a")
    if previous_homes_known:
        expect(page.locator("#changesNotice")).to_contain_text("1 device removed")
        assert BARE.name in page.locator("#changesNotice").inner_text()
        assert LAMP.name not in page.locator("#changesNotice").inner_text()
    else:
        assert page.locator("#changesNotice").count() == 0
    select_home(page, "home-b")
    if previous_homes_known:
        expect(page.locator("#changesNotice")).to_contain_text("1 device removed")
        assert LAMP.name in page.locator("#changesNotice").inner_text()
        assert BARE.name not in page.locator("#changesNotice").inner_text()
    else:
        assert page.locator("#changesNotice").count() == 0
    select_home(page, "unknown")
    if previous_homes_known:
        assert page.locator("#changesNotice").count() == 0
    else:
        expect(page.locator("#changesNotice")).to_contain_text("2 devices removed")
        assert BARE.name in page.locator("#changesNotice").inner_text()
        assert LAMP.name in page.locator("#changesNotice").inner_text()


# --------------------------------------------------------------------------- #
# Room filtering uses SDK room metadata, including confirmed empty assignments.
# --------------------------------------------------------------------------- #
DEVICE_ROOMS = {
    PLUG.id: {"id": "kitchen", "name": "Kitchen", "display_order": 1},
    LAMP.id: {"id": "living", "name": "Living room", "display_order": 2},
    BARE.id: None,
    # SENSOR deliberately has an unavailable room, not a confirmed No room.
}


def serves_rooms(page, app, monkeypatch, *, rooms=DEVICE_ROOMS, devices=DEVICES,
                 homes=HOMES, membership=HOME_MEMBERSHIP):
    _serves(app, monkeypatch, app.core.Devices(devices, homes=homes,
        home_ids=membership, device_rooms=rooms))
    refreshed(page, "#filtersBtn")


def select_room(page, room_id=""):
    open_filters(page)
    value = room_id if room_id in ("none", "unknown") else f"room:{room_id}" if room_id else ""
    page.locator("#roomFilter").select_option(value)


@pytest.mark.parametrize("width,theme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_rooms_home_scoped_dropdown_keyboard_and_layout(page, running_app, monkeypatch, width, theme):
    serves_rooms(page, running_app, monkeypatch)
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme=theme)
    select_home(page, "home-b")
    expect(page.locator("#roomFilter")).to_have_accessible_name("Filter by room")
    assert page.locator("#roomFilter option").all_inner_texts() == ["All rooms", "Kitchen", "Living room"]
    page.locator("#homeFilter").press("Tab")
    expect(page.locator("#roomFilter")).to_be_focused()
    select_room(page, "living")
    assert listed_ids(page) == [LAMP.id]
    expect(page.locator("#count")).to_contain_text("1 device")
    expect(page.locator("#filterCount")).to_have_text("2")
    expect(page.locator("#filtersBtn")).to_have_accessible_name("Filter (2): 1 home filter, 1 room filter")
    box = page.locator("#roomFilter").bounding_box()
    arrow = page.locator("#roomChevron").bounding_box()
    assert box["x"] + box["width"] - arrow["x"] - arrow["width"] >= 12
    popover = page.locator("#filtersPopover").bounding_box()
    assert popover["x"] >= 16 and popover["x"] + popover["width"] <= width - 16
    assert popover["y"] >= 16 and popover["y"] + popover["height"] <= 800 - 16
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"rooms-filter-{width}-{theme}.png", full_page=True)
    page.keyboard.press("Escape")
    expect(page.locator("#filtersBtn")).to_be_focused()


def test_rooms_home_changes_reset_only_incompatible_room_choices(page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    select_room(page, "living")
    select_home(page, "home-a")
    expect(page.locator("#roomFilter")).to_have_value("")
    assert set(listed_ids(page)) == {PLUG.id, BARE.id}
    assert page.locator("#roomFilter option").all_inner_texts() == ["All rooms", "Kitchen", "No room"]
    select_room(page, "kitchen")
    select_home(page, "home-b")
    expect(page.locator("#roomFilter")).to_have_value("room:kitchen")
    assert listed_ids(page) == [PLUG.id], "a shared device remains visible in either reported home"
    select_home(page, "home-empty")
    expect(page.locator("#roomFilter")).to_have_value("")
    expect(page.locator("#roomFilter")).to_be_disabled()
    assert listed_ids(page) == []


def test_rooms_unassigned_and_unknown_are_distinct_and_missing_exclusions_can_be_restored(
        page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    select_room(page, "none")
    assert listed_ids(page) == [BARE.id]
    select_room(page, "unknown")
    assert listed_ids(page) == [SENSOR.id]
    expect(page.locator("#roomHint")).to_contain_text("Some rooms could not be loaded")
    select_room(page)
    select_devices(page, PLUG)
    save_selection(page)
    serves_rooms(page, running_app, monkeypatch, devices=[LAMP, SENSOR, BARE])
    select_room(page, "unknown")
    switch_device_view(page)
    assert listed_ids(page) == [PLUG.id]
    enter_selection(page)
    page.locator(f"#rows tr[data-id='{PLUG.id}'] td.name-col").click()
    expect(device_checkbox(page, PLUG)).to_be_checked()
    save_selection(page, restore=True)
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []


def test_rooms_duplicate_names_are_unambiguous_and_names_are_escaped(page, running_app, monkeypatch):
    rooms = {**DEVICE_ROOMS, PLUG.id: {"id": "first", "name": "Bedroom"},
             LAMP.id: {"id": "second", "name": "Bedroom"},
             BARE.id: {"id": "special", "name": '<img src=x onerror="window.badRoom=true">'}}
    serves_rooms(page, running_app, monkeypatch, rooms=rooms)
    open_filters(page)
    assert page.locator("#roomFilter option[value='room:first']").inner_text() == "Bedroom (first)"
    assert page.locator("#roomFilter option[value='room:second']").inner_text() == "Bedroom (second)"
    select_room(page, "special")
    assert listed_ids(page) == [BARE.id]
    assert page.locator("#roomFilter img").count() == 0
    assert page.evaluate("window.badRoom === undefined")


def test_rooms_choice_survives_reload_and_is_scoped_to_the_account(page, restores_session, monkeypatch):
    serves_rooms(page, restores_session, monkeypatch)
    select_home(page, "home-b")
    select_room(page, "living")
    page.reload(wait_until="networkidle")
    expect(page.locator("#roomFilter")).to_have_value("room:living")
    assert listed_ids(page) == [LAMP.id]
    original = restores_session.core.load_session(restores_session.SESSION_FILE)
    restores_session.core.save_session(restores_session.SESSION_FILE, dict(original, user_code="room-account-b"))
    refreshed(page, "#filtersBtn")
    expect(page.locator("#homeFilter")).to_have_value("")
    expect(page.locator("#roomFilter")).to_have_value("")
    select_room(page, "none")
    restores_session.core.save_session(restores_session.SESSION_FILE, original)
    refreshed(page, "#filtersBtn")
    expect(page.locator("#homeFilter")).to_have_value("home:home-b")
    expect(page.locator("#roomFilter")).to_have_value("room:living")


def test_rooms_old_snapshot_requires_explicit_refresh_and_preserves_remembered_room(
        page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    select_room(page, "living")
    serves_homes(page, running_app, monkeypatch)
    expect(page.locator("#roomFilter")).to_have_value("")
    open_filters(page)
    expect(page.locator("#roomFilter")).to_be_disabled()
    expect(page.locator("#roomHint")).to_contain_text("Room information is not in this saved list")
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "refresh=1" in request.url else None)
    _serves(running_app, monkeypatch, running_app.core.Devices(DEVICES, homes=HOMES,
        home_ids=HOME_MEMBERSHIP, device_rooms=DEVICE_ROOMS))
    page.reload(wait_until="networkidle")
    assert requests == []
    open_filters(page)
    with page.expect_response(lambda response: response.url.endswith("/api/devices?refresh=1")):
        page.locator("#roomHint [data-refresh-rooms]").click()
    expect(page.locator("#roomFilter")).to_have_value("room:living")
    assert listed_ids(page) == [LAMP.id]
    assert len(requests) == 1


def test_rooms_partial_refresh_keeps_preference_and_recovers_on_retry(page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    select_room(page, "living")
    serves_rooms(page, running_app, monkeypatch, rooms={PLUG.id: DEVICE_ROOMS[PLUG.id], BARE.id: None})
    expect(page.locator("#roomFilter")).to_have_value("")
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    page.reload(wait_until="networkidle")
    serves_rooms(page, running_app, monkeypatch)
    expect(page.locator("#roomFilter")).to_have_value("room:living")
    assert listed_ids(page) == [LAMP.id]


def test_rooms_bulk_selection_exclusions_badge_and_clear_all_are_scoped_correctly(page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    select_devices(page, PLUG, LAMP)
    save_selection(page)
    select_home(page, "home-b")
    select_room(page, "living")
    expect(page.locator("#filterCount")).to_have_text("4")
    expect(page.locator("#filtersBtn")).to_have_accessible_name(
        "Filter (4): 2 excluded devices, 1 home filter, 1 room filter")
    switch_device_view(page)
    assert listed_ids(page) == [LAMP.id]
    open_filters(page)
    with page.expect_response(lambda response: response.request.method == "PATCH") as response:
        page.locator("#clearAllFilters").click()
    assert response.value.request.post_data_json["restore_all"] is True
    expect(page.locator("#roomFilter")).to_have_value("")
    expect(page.locator("#homeFilter")).to_have_value("")
    expect(page.locator("#filterCount")).to_be_hidden()
    expect(page.locator("#clearAllFilters")).to_be_disabled()
    assert set(listed_ids(page)) == {device.id for device in DEVICES}
    page.reload(wait_until="networkidle")
    expect(page.locator("#roomFilter")).to_have_value("")
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []


def test_rooms_changing_room_clears_selection_and_closes_out_of_scope_details(page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    open_panel(page, LAMP.name)
    select_room(page, "kitchen")
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")
    enter_selection(page)
    page.locator(f"#rows tr[data-id='{PLUG.id}'] td.name-col").click()
    expect(device_checkbox(page, PLUG)).to_be_checked()
    select_room(page, "living")
    expect(page.locator("#selectionCount")).to_have_text("0 selected")
    assert listed_ids(page) == [LAMP.id]
    page.locator(f"#rows tr[data-id='{LAMP.id}'] td.name-col").click()
    expect(device_checkbox(page, LAMP)).to_be_checked()
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")
    save_selection(page)
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == [LAMP.id]


def test_rooms_notices_and_csv_follow_room_scope_including_removed_devices(page, running_app, monkeypatch, tmp_path):
    rooms = {**DEVICE_ROOMS, BARE.id: DEVICE_ROOMS[PLUG.id]}
    serves_rooms(page, running_app, monkeypatch, rooms=rooms)
    changed = copy.deepcopy(DEVICES)
    changed[demo_devices.LAMP].name = "Renamed in the living room"
    changed[demo_devices.LAMP].local_key = "ROTATED-ROOM-KEY"
    serves_rooms(page, running_app, monkeypatch, rooms=rooms,
                 devices=[d for d in changed if d.id != BARE.id])
    select_room(page, "kitchen")
    expect(page.locator("#changesNotice")).to_contain_text("1 device removed")
    assert BARE.name in page.locator("#changesNotice").inner_text()
    assert "Renamed in the living room" not in page.locator("#changesNotice").inner_text()
    assert [device["id"] for device in export_rows(page, tmp_path, "tuya-local-key.csv")] == [PLUG.id]
    select_room(page, "living")
    expect(page.locator("#changesNotice")).to_contain_text("1 local key changed")
    assert BARE.name not in page.locator("#changesNotice").inner_text()
    assert [device["id"] for device in export_rows(page, tmp_path, "tuya-local-key.csv")] == [LAMP.id]


def test_rooms_clear_all_failed_write_preserves_room_then_retry_resets_it(page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    select_devices(page, PLUG)
    save_selection(page)
    select_home(page, "home-b")
    select_room(page, "living")

    def fail_write(route):
        if route.request.method == "PATCH":
            route.fulfill(status=503, json={"error": "storage_unavailable"})
        else:
            route.continue_()

    page.route("**/api/device-exclusions", fail_write)
    page.locator("#clearAllFilters").click()
    expect(page.locator("#exclusionError")).to_contain_text("Could not clear all filters")
    expect(page.locator("#roomFilter")).to_have_value("room:living")
    expect(page.locator("#filterCount")).to_have_text("3")
    page.unroute("**/api/device-exclusions", fail_write)
    page.locator("#retryExclusions").click()
    expect(page.locator("#roomFilter")).to_have_value("")
    expect(page.locator("#filterCount")).to_be_hidden()


def test_rooms_keep_gateway_setup_and_scan_evidence_outside_the_selected_room(page, running_app, monkeypatch):
    child = _named_sub(VALVE, ZIGBEE_GATEWAY)
    serves_rooms(page, running_app, monkeypatch,
        devices=list(DEVICES) + [ZIGBEE_GATEWAY, child],
        membership={**HOME_MEMBERSHIP, ZIGBEE_GATEWAY.id: ["home-b"], child.id: ["home-b"]},
        rooms={**DEVICE_ROOMS, ZIGBEE_GATEWAY.id: DEVICE_ROOMS[PLUG.id], child.id: DEVICE_ROOMS[LAMP.id]})
    fetches = []
    page.on("request", lambda request: fetches.append(request.url) if "/api/devices" in request.url else None)
    select_home(page, "home-b")
    select_room(page, "living")
    page.fill("#filter", child.name)
    assert listed_ids(page) == [child.id], "a gateway in another room must not return as a context row"
    calls = _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=child.id),
        child.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    assert {device.id for device in DEVICES} | {ZIGBEE_GATEWAY.id, child.id} == set(calls[0]["devices"])
    open_setup(page, child.name)
    assert setup_value(page, "device_id") == ZIGBEE_GATEWAY.id
    assert setup_value(page, "host") == "192.168.2.8"
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == child.local_key
    assert listed_ids(page) == [child.id]
    assert fetches == [], "changing room filters and using setup must not refetch Tuya devices or rooms"


@pytest.mark.parametrize("width,hint", [(1440, "rooms"), (390, "rooms"), (1440, "homes")])
def test_filters_stay_anchored_after_first_metadata_refresh(page, running_app, monkeypatch, width, hint):
    page.set_viewport_size({"width": width, "height": 800})
    open_filters(page)
    _serves(running_app, monkeypatch, running_app.core.Devices(DEVICES, homes=HOMES,
        home_ids=HOME_MEMBERSHIP, device_rooms=DEVICE_ROOMS))
    with page.expect_response(lambda response: response.url.endswith("/api/devices?refresh=1")):
        page.locator(f"[data-refresh-{hint}]").click()
    expect(page.locator("#devices")).to_be_visible()
    expect(page.locator("#filtersPopover")).to_be_visible()
    anchor = page.locator("#filtersBtn").bounding_box()
    popover = page.locator("#filtersPopover").bounding_box()
    assert popover["y"] == pytest.approx(anchor["y"] + anchor["height"] + 8, abs=1)
    expected_left = max(16, min(anchor["x"] + anchor["width"] - popover["width"], width - popover["width"] - 16))
    assert popover["x"] == pytest.approx(expected_left, abs=1)
    select_room(page, "kitchen")
    assert listed_ids(page) == [PLUG.id]


# Status and category choices share the existing view, selection, and export scope.
def select_status(page, value=""):
    open_filters(page)
    page.locator("#statusFilter").select_option(value)


def select_category(page, code=""):
    open_filters(page)
    page.locator("#categoryFilter").select_option("unknown" if code == "unknown" else f"category:{code}" if code else "")


def test_device_filters_cloud_status_is_independent_of_the_last_lan_result(page, running_app, monkeypatch):
    devices = copy.deepcopy(DEVICES)
    devices[demo_devices.PLUG].online = False
    serves_rooms(page, running_app, monkeypatch, devices=devices)
    _scans(running_app, monkeypatch, FIRST_SCAN)
    scanned(page)
    select_status(page, "cloud:online")
    assert set(listed_ids(page)) == {d.id for d in devices if d.online is True}
    select_status(page, "cloud:offline")
    assert set(listed_ids(page)) == {d.id for d in devices if d.online is False}
    select_status(page, "cloud:unknown")
    assert listed_ids(page) == []
    select_status(page, "lan:ok")
    assert listed_ids(page) == [PLUG.id], "a cloud-offline device can still be locally reachable"
    expect(page.locator("#filtersBtn")).to_have_accessible_name("Filter (1): 1 status filter")


def test_device_filters_cover_lan_outcomes_and_gateway_reports(page, running_app, monkeypatch):
    cases = {
        "ok": _lan("ok", "192.168.1.2", "3.4"),
        "busy": _lan("busy", "192.168.1.3", "3.4"),
        "key_mismatch": _lan("key_mismatch", "192.168.1.4", "3.4"),
        "unreachable": _lan("unreachable", "192.168.1.5"),
        "not_found": _lan("not_found"),
        "via_gateway": _lan("via_gateway", "192.168.1.6", "3.4", sub_online=True),
        "offline": _lan("via_gateway", "192.168.1.6", "3.4", sub_online=False),
        "gateway_not_found": _lan("via_gateway"),
        "not_checked": None,
    }
    devices, results = [], {}
    for status, result in cases.items():
        device = copy.deepcopy(PLUG)
        device.id, device.name = f"status-{status}", f"Device {status}"
        devices.append(device)
        if result:
            results[device.id] = result
    serves_rooms(page, running_app, monkeypatch, devices=devices, rooms={}, membership={})
    _scans(running_app, monkeypatch, results)
    scanned(page)
    for status in cases:
        select_status(page, f"lan:{status}")
        assert listed_ids(page) == [f"status-{status}"], status
    select_status(page, "lan:check_needed")
    expect(page.locator("#emptyDevices")).to_have_text("No devices match your filters.")
    select_status(page)
    assert set(listed_ids(page)) == {d.id for d in devices}


def test_device_filters_check_needed_does_not_claim_an_unnamed_gateway_is_unscanned(
        page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    _scans(running_app, monkeypatch, {
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4"),
    }, unnamed_gateways=[{"ip": "192.168.2.8", "version": "3.4", "sub_devices": [VALVE.id]}])
    scanned(page)
    select_status(page, "lan:check_needed")
    assert set(listed_ids(page)) == {ZIGBEE_GATEWAY.id, BLE_GATEWAY.id}
    select_status(page, "lan:not_checked")
    assert not {ZIGBEE_GATEWAY.id, BLE_GATEWAY.id} & set(listed_ids(page))


def test_device_filters_update_after_scanning_and_keep_all_devices_as_scan_targets(
        page, running_app, monkeypatch):
    calls = _scans(running_app, monkeypatch, FIRST_SCAN, {
        PLUG.id: _lan("unreachable", "192.168.1.61"),
        LAMP.id: _lan("ok", "192.168.1.42", "3.3"),
    })
    select_status(page, "lan:ok")
    assert listed_ids(page) == []
    scanned(page)
    assert listed_ids(page) == [PLUG.id]
    select_devices(page, PLUG)
    scanned(page)
    assert listed_ids(page) == [LAMP.id]
    assert_no_selection(page)
    assert all(set(call["devices"]) == {d.id for d in DEVICES} for call in calls)


def test_device_filters_category_labels_codes_and_missing_values_are_safe_and_stable(
        page, running_app, monkeypatch):
    devices = copy.deepcopy(DEVICES)
    devices[demo_devices.BARE].category = ""
    devices[demo_devices.SENSOR].category = '<custom & "category">'
    serves_rooms(page, running_app, monkeypatch, devices=devices)
    open_filters(page)
    labels = page.locator("#categoryFilter option").all_inner_texts()
    assert "Socket (cz)" in labels and "Light (dj)" in labels
    assert '<custom & "category">' in labels and "Unknown category" in labels
    assert page.locator("#categoryFilter custom").count() == 0
    select_category(page, "cz")
    assert listed_ids(page) == [PLUG.id]
    choices = page.locator("#categoryFilter option").all_inner_texts()
    select_home(page, "home-b")
    page.locator("#filtersClose").click()
    page.fill("#filter", "no matching device")
    assert page.locator("#categoryFilter option").all_inner_texts() == choices
    page.fill("#filter", "")
    select_home(page)
    select_category(page, '<custom & "category">')
    assert listed_ids(page) == [SENSOR.id]
    select_category(page, "unknown")
    assert listed_ids(page) == [BARE.id]


def test_device_filters_empty_category_scope_stays_selected_until_cleared(page, running_app, monkeypatch):
    select_category(page, "cz")
    serves_rooms(page, running_app, monkeypatch, devices=[LAMP])
    expect(page.locator("#categoryFilter")).to_have_value("category:cz")
    expect(page.locator("#emptyDevices")).to_have_text("No devices match your filters.")
    select_category(page)
    assert listed_ids(page) == [LAMP.id]


def test_device_filters_persist_per_account_and_survive_cached_reload(page, restores_session, monkeypatch):
    select_status(page, "cloud:online")
    select_category(page, "dj")
    page.reload(wait_until="networkidle")
    expect(page.locator("#statusFilter")).to_have_value("cloud:online")
    expect(page.locator("#categoryFilter")).to_have_value("category:dj")
    assert listed_ids(page) == [LAMP.id]
    original = restores_session.core.load_session(restores_session.SESSION_FILE)
    restores_session.core.save_session(restores_session.SESSION_FILE, dict(original, user_code="filter-account-b"))
    refreshed(page, "#filtersBtn")
    expect(page.locator("#statusFilter")).to_have_value("")
    expect(page.locator("#categoryFilter")).to_have_value("")
    select_status(page, "cloud:offline")
    select_category(page, "mcs")
    restores_session.core.save_session(restores_session.SESSION_FILE, original)
    refreshed(page, "#filtersBtn")
    expect(page.locator("#statusFilter")).to_have_value("cloud:online")
    expect(page.locator("#categoryFilter")).to_have_value("category:dj")


@pytest.mark.parametrize("storage", ["blocked", "invalid", "malformed-values"])
def test_device_filters_work_with_unavailable_or_invalid_saved_preferences(page, storage):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if storage == "blocked":
        page.add_init_script("""for (const method of ['getItem', 'setItem']) {
            Storage.prototype[method] = () => { throw new Error('Storage unavailable'); };
        }""")
    else:
        account = frontend_state(page, "accountId")
        page.evaluate("([key, value]) => localStorage.setItem(key, value)", [
            f"tuya-local-key.filters.{account}", "{" if storage == "invalid" else '{"status":"bogus","category":false}',
        ])
    page.reload(wait_until="networkidle")
    expect(page.locator("#statusFilter")).to_have_value("")
    expect(page.locator("#categoryFilter")).to_have_value("")
    select_status(page, "cloud:online")
    select_category(page, "dj")
    assert listed_ids(page) == [LAMP.id] and not errors


def test_device_filters_combine_with_location_search_exclusions_csv_and_clear_all_retry(
        page, running_app, monkeypatch, tmp_path):
    serves_rooms(page, running_app, monkeypatch)
    select_devices(page, PLUG)
    save_selection(page)
    select_home(page, "home-b")
    select_room(page, "living")
    select_status(page, "cloud:online")
    select_category(page, "dj")
    expect(page.locator("#filtersBtn")).to_have_accessible_name(
        "Filter (5): 1 excluded device, 1 home filter, 1 room filter, 1 status filter, 1 category filter")
    assert listed_ids(page) == [LAMP.id]
    assert [d["id"] for d in export_rows(page, tmp_path, "tuya-local-key.csv")] == [LAMP.id]
    page.fill("#filter", LAMP.name)
    select_devices(page, LAMP)
    open_filters(page)

    def fail_write(route):
        if route.request.method == "PATCH":
            route.fulfill(status=503, json={"error": "storage_unavailable"})
        else:
            route.continue_()

    page.route("**/api/device-exclusions", fail_write)
    page.locator("#clearAllFilters").click()
    expect(page.locator("#exclusionError")).to_contain_text("Could not clear all filters")
    expect(page.locator("#statusFilter")).to_have_value("cloud:online")
    expect(page.locator("#categoryFilter")).to_have_value("category:dj")
    expect(device_checkbox(page, LAMP)).to_be_checked()
    page.unroute("**/api/device-exclusions", fail_write)
    page.locator("#retryExclusions").click()
    for control in ("homeFilter", "roomFilter", "statusFilter", "categoryFilter", "filter"):
        expect(page.locator(f"#{control}")).to_have_value("")
    expect(page.locator("#filterCount")).to_be_hidden()
    assert set(listed_ids(page)) == {d.id for d in DEVICES}
    page.reload(wait_until="networkidle")
    expect(page.locator("#statusFilter")).to_have_value("")
    expect(page.locator("#categoryFilter")).to_have_value("")


@pytest.mark.parametrize("kind,value", [("status", "cloud:offline"), ("category", "dj")])
def test_device_filters_changing_scope_clears_selection_and_closes_details(
        page, kind, value):
    open_panel(page, PLUG.name)
    (select_status if kind == "status" else select_category)(page, value)
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")
    (select_status if kind == "status" else select_category)(page)
    select_devices(page, PLUG)
    (select_status if kind == "status" else select_category)(page, value)
    assert_no_selection(page)


def test_device_filters_missing_exclusions_have_unknown_category_and_cloud_status(
        page, running_app, monkeypatch):
    select_devices(page, PLUG)
    save_selection(page)
    serves_rooms(page, running_app, monkeypatch, devices=[LAMP, SENSOR, BARE])
    switch_device_view(page)
    select_category(page, "unknown")
    select_status(page, "cloud:unknown")
    assert listed_ids(page) == [PLUG.id]
    select_devices(page, PLUG)
    assert save_selection(page, restore=True)["excluded_ids"] == []


def test_device_filters_excluded_export_and_restore_all_keep_their_account_scope(
        page, running_app, monkeypatch, tmp_path):
    select_devices(page, PLUG, LAMP)
    save_selection(page)
    select_category(page, "dj")
    select_status(page, "cloud:online")
    switch_device_view(page)
    assert listed_ids(page) == [LAMP.id]
    assert [d["id"] for d in export_rows(page, tmp_path, "tuya-local-key-excluded.csv")] == [LAMP.id]
    expect(page.locator("#restoreAll")).to_have_text("Restore all (all filters)")
    page.locator("#restoreAll").click()
    expect(page.locator("#restoreAll")).to_be_hidden()
    assert page.request.get(page.url + "api/device-exclusions").json()["excluded_ids"] == []
    switch_device_view(page)
    assert listed_ids(page) == [LAMP.id], "restoring exclusions preserves the selected status and category"


def test_device_filters_removed_notices_keep_prior_category_and_cloud_status(page, running_app, monkeypatch):
    serves_rooms(page, running_app, monkeypatch)
    select_category(page, "cz")
    select_status(page, "cloud:online")
    serves_rooms(page, running_app, monkeypatch, devices=[LAMP, SENSOR, BARE])
    expect(page.locator("#changesNotice")).to_contain_text("1 device removed")
    assert PLUG.name in page.locator("#changesNotice").inner_text()
    assert LAMP.name not in page.locator("#changesNotice").inner_text()
    select_status(page, "cloud:offline")
    assert page.locator("#changesNotice").count() == 0
    select_status(page, "cloud:online")
    select_category(page, "dj")
    assert page.locator("#changesNotice").count() == 0


def test_device_filters_keep_gateway_setup_evidence_outside_the_selected_category(
        page, running_app, monkeypatch):
    child = _named_sub(VALVE, ZIGBEE_GATEWAY)
    serves_rooms(page, running_app, monkeypatch, devices=list(DEVICES) + [ZIGBEE_GATEWAY, child])
    cloud_requests = []
    page.on("request", lambda request: cloud_requests.append(request.url) if "/api/devices" in request.url else None)
    select_category(page, child.category)
    calls = _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=child.id),
        child.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    select_status(page, "lan:via_gateway")
    assert listed_ids(page) == [child.id]
    assert {ZIGBEE_GATEWAY.id, child.id} <= set(calls[0]["devices"])
    open_setup(page, child.name)
    assert setup_value(page, "device_id") == ZIGBEE_GATEWAY.id
    assert setup_value(page, "host") == "192.168.2.8"
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == child.local_key
    assert not cloud_requests


@pytest.mark.parametrize("width,theme,height", [(1440, "light", 800), (1440, "dark", 800),
                                              (390, "light", 640), (390, "dark", 640)])
def test_device_filters_layout_keyboard_and_chevron_spacing(page, running_app, monkeypatch, width, theme, height):
    serves_rooms(page, running_app, monkeypatch)
    page.set_viewport_size({"width": width, "height": height})
    page.emulate_media(color_scheme=theme)
    select_home(page, "home-b")
    expect(page.locator("#statusFilter")).to_have_accessible_name("Filter by status")
    expect(page.locator("#categoryFilter")).to_have_accessible_name("Filter by device category")
    page.locator("#roomFilter").press("Tab")
    expect(page.locator("#statusFilter")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#categoryFilter")).to_be_focused()
    for control, arrow_id in (("statusFilter", "statusChevron"), ("categoryFilter", "categoryChevron")):
        box, arrow = page.locator(f"#{control}").bounding_box(), page.locator(f"#{arrow_id}").bounding_box()
        assert box["x"] + box["width"] - arrow["x"] - arrow["width"] >= 12
    popover = page.locator("#filtersPopover").bounding_box()
    assert popover["x"] >= 16 and popover["x"] + popover["width"] <= width - 16
    assert popover["y"] >= 16 and popover["y"] + popover["height"] <= height - 16
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"device-filters-{width}-{theme}.png", full_page=False)
    page.keyboard.press("Escape")
    expect(page.locator("#filtersBtn")).to_be_focused()


# --------------------------------------------------------------------------- #
# Fix connection wizard
# --------------------------------------------------------------------------- #
def open_fix(page, name=PLUG.name):
    open_network(page, name)
    page.locator("#networkBody [data-network-action='fix']").click()
    expect(page.locator("#fixBody")).to_be_visible()


def fix_action(page, action):
    return page.locator(f"#fixBody [data-fix-action='{action}']")


def fix_heading(page):
    return page.locator("#fixBody [data-fix-heading]")


def fresh_scans(app, monkeypatch, *rounds):
    """Wizard success requires results produced during the requested job."""
    calls = _scans(app, monkeypatch, *rounds)
    original = app.lan_scan.scan

    def scan(*args, **kwargs):
        outcome = original(*args, **kwargs)
        for result in outcome["results"].values():
            result["checked_at"] = time.time()
        return outcome

    monkeypatch.setattr(app.lan_scan, "scan", scan)
    return calls


def fix_test(page, ip="192.168.2.61"):
    if not page.locator("#fixCheckIP").count():
        fix_action(page, "test").click()
    page.locator("#fixCheckIP").fill(ip)
    page.locator("#fixBody [data-lan-check] button").click()


def test_fix_connection_starts_without_requests_and_preserves_navigation_and_drafts(page):
    requests = []
    page.on("request", lambda request: requests.append(request))
    open_fix(page)
    expect(fix_heading(page)).to_have_text("Check the saved details")
    assert page.locator("#fixBody .fix-actions button").count() == 1
    assert page.locator("#fixBody [aria-current='step']").inner_text() == "1\nDetails"
    assert "Find it in the next step" in page.locator("#fixBody").inner_text()
    assert PLUG.ip not in page.locator("#fixBody").inner_text(), "the cloud IP is not a LAN address"
    fix_action(page, "test").click()
    assert fix_action(page, "details").count() == 0
    ip = page.locator("#fixCheckIP")
    expect(ip).to_be_focused()
    ip.fill("192.168.2.200")
    repaint(page)
    expect(ip).to_have_value("192.168.2.200")
    expect(ip).to_be_focused()
    page.keyboard.press("Escape")
    expect(fix_heading(page)).to_have_text("Check the saved details")
    fix_action(page, "test").click()
    expect(ip).to_have_value("192.168.2.200")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    expect(page.locator("#networkBody [data-network-action='fix']")).to_be_focused()
    page.locator("#networkBody [data-network-action='fix']").click()
    fix_action(page, "test").click()
    expect(ip).to_have_value("192.168.2.200")
    assert not [request for request in requests if request.method == "POST"]
    assert PLUG.local_key not in page.locator("#fixBody").inner_text()


def test_fix_connection_checks_a_manual_address_and_opens_the_verified_setup_values(page, running_app, monkeypatch):
    calls = fresh_scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("Connection checked")
    assert page.locator("#fixBody .fix-actions button").count() == 1
    assert calls[0]["only"] == [PLUG.id]
    assert calls[0]["targets"] == ["192.168.2.61"]
    assert "192.168.2.61" in page.locator("#fixBody").inner_text()
    assert PLUG.local_key not in page.locator("#fixBody").inner_text()
    fix_action(page, "setup").click()
    expect(page.locator("#setupBody")).to_be_visible()
    assert setup_value(page, "host") == "192.168.2.61"
    assert setup_value(page, "protocol_version") == "3.4"
    page.keyboard.press("Escape")
    expect(fix_heading(page)).to_have_text("Connection checked")
    expect(fix_action(page, "setup")).to_be_focused()


@pytest.mark.parametrize("status, title, guidance", [
    ("busy", "The connection was refused", "Resume the integration after the test"),
    ("key_mismatch", "The key was not accepted", "A different device may have taken its old address"),
    ("unreachable", "No device reply", "TCP port 6668"),
    ("not_found", "No device reply", "correct IoT VLAN"),
])
def test_fix_connection_explains_failed_attempts_at_an_unsaved_ip(
        page, running_app, monkeypatch, status, title, guidance):
    fresh_scans(running_app, monkeypatch, {PLUG.id: _lan(status, "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text(title)
    assert guidance in page.locator("#fixBody").inner_text()
    assert "Connection checked" not in page.locator("#fixBody").inner_text()
    assert "Tested IP" in page.locator("#fixBody").inner_text()
    assert page.locator("#fixBody dt[title='protocol_version']").count() == 0


def test_fix_connection_refreshes_a_rejected_key_and_retests_with_current_details(page, running_app, monkeypatch):
    fresh_scans(running_app, monkeypatch,
                {PLUG.id: _lan("key_mismatch", "192.168.2.61", "3.4")},
                {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("The key was not accepted")
    _serves(running_app, monkeypatch, _with_rotated_key("FIX-NEW-KEY-1234"))
    fix_action(page, "refresh").click()
    expect(fix_heading(page)).to_have_text("Test the device connection")
    expect(page.locator("#fixBody")).to_contain_text("Device list refreshed")
    fix_test(page)
    expect(fix_heading(page)).to_have_text("Connection checked")
    assert "Tuya supplied a changed local key" in page.locator("#fixBody").inner_text()
    assert "FIX-NEW-KEY-1234" not in page.locator("#fixBody").inner_text()
    fix_action(page, "setup").click()
    assert setup_field(page, "local_key").locator("button.setup-copy").get_attribute("data-copy") == "FIX-NEW-KEY-1234"


def test_fix_connection_scans_inside_the_wizard_and_explains_changed_settings(page, running_app, monkeypatch):
    calls = fresh_scans(running_app, monkeypatch,
                        {PLUG.id: _lan("unreachable", "192.168.2.61", "3.4")},
                        {PLUG.id: _lan("ok", "192.168.2.62", "3.5")})
    scanned(page)
    open_fix(page)
    fix_action(page, "test").click()
    fix_action(page, "scan").click()
    page.locator("#fixTargets").fill("192.168.2.61,192.168.2.62")
    repaint(page)
    expect(page.locator("#fixTargets")).to_have_value("192.168.2.61,192.168.2.62")
    page.locator("#fixBody [data-fix-scan] button").click()
    expect(fix_heading(page)).to_have_text("Connection checked")
    assert page.locator("#panel").get_attribute("aria-hidden") == "false"
    assert calls[1]["targets"] == ["192.168.2.61", "192.168.2.62"]
    assert calls[1]["only"] is None
    body = page.locator("#fixBody").inner_text()
    assert "IP changed from 192.168.2.61 to 192.168.2.62" in body
    assert "protocol changed from 3.4 to 3.5" in body


def test_fix_connection_distinguishes_a_checked_gateway_from_an_offline_child(page, running_app, monkeypatch):
    gateway = copy.deepcopy(ZIGBEE_GATEWAY)
    gateway.local_key = VALVE.local_key
    child = _named_sub(VALVE, gateway)
    _with_gateways(page, running_app, monkeypatch, gateway, child)
    calls = fresh_scans(running_app, monkeypatch, {
        gateway.id: _lan("ok", "192.168.2.8", "3.4"),
        child.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=gateway.id, sub_online=False),
    })
    open_fix(page, child.name)
    fix_test(page, "192.168.2.8")
    expect(fix_heading(page)).to_have_text("Gateway works, sub-device reported offline")
    assert calls[0]["only"] == [gateway.id]
    body = page.locator("#fixBody").inner_text()
    assert "checks the gateway connection only" in body
    assert "Gateway IP" in body
    assert child.local_key not in body


@pytest.mark.parametrize("device, explanation", [(BARE, "Tuya has not supplied a usable local key"),
                                                 (SENSOR, "gateway is missing from the device list")])
def test_fix_connection_handles_missing_keys_and_missing_gateways(page, device, explanation):
    open_fix(page, device.name)
    assert explanation in page.locator("#fixBody").inner_text()
    assert fix_action(page, "test").count() == 0
    assert page.locator("#fixBody [data-lan-check]").count() == 0
    expect(fix_action(page, "refresh")).to_be_enabled()


def test_fix_connection_does_not_guess_an_unidentified_gateway(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    open_fix(page, VALVE.name)
    assert "gateway has not been identified" in page.locator("#fixBody").inner_text()
    assert fix_action(page, "test").count() == 0
    page.locator(f"#fixBody [data-open-device='{BLE_GATEWAY.id}']").click()
    expect(page.locator("#panelSub")).to_contain_text(BLE_GATEWAY.name)
    expect(fix_action(page, "test")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#panelSub")).to_contain_text(VALVE.name)


def test_fix_connection_keeps_protocol_31_key_verification_unconfirmed(page, running_app, monkeypatch):
    fresh_scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.1")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("Device found, key not verified")
    assert page.locator("#fixBody .fix-result").get_attribute("data-tone") == "pending"


def test_fix_connection_explains_an_unavailable_scanner(page, running_app, monkeypatch):
    def unavailable():
        raise running_app.lan_scan.ScannerUnavailable("unavailable")

    monkeypatch.setattr(running_app.lan_scan, "require_scanner", unavailable)
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("The network checker is unavailable")
    assert "Restart the app" in page.locator("#fixBody").inner_text()


def test_fix_connection_failed_refresh_keeps_the_wizard_and_explains_the_saved_snapshot(page, running_app, monkeypatch):
    fresh_scans(running_app, monkeypatch, {PLUG.id: _lan("key_mismatch", "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("The key was not accepted")
    _raises(running_app, monkeypatch, RuntimeError("offline"))
    fix_action(page, "refresh").click()
    expect(page.locator("#fixBody")).to_contain_text("The device list could not be refreshed")
    assert "Tuya could not be reached" in page.locator("#fixBody").inner_text()
    expect(fix_action(page, "refresh")).to_be_enabled()


def test_fix_connection_refresh_preserves_a_control_focused_while_waiting(page, running_app, monkeypatch):
    fresh_scans(running_app, monkeypatch, {PLUG.id: _lan("key_mismatch", "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("The key was not accepted")
    held = []
    page.route("**/api/devices?refresh=1", lambda route: held.append(route))
    fix_action(page, "refresh").click()
    close = page.locator("#panelClose")
    close.focus()
    assert len(held) == 1
    held[0].continue_()
    expect(page.locator("#fixBody")).to_contain_text("Device list refreshed")
    expect(close).to_be_focused()


def test_fix_connection_expired_login_offers_relogin_without_clearing_saved_keys(page, running_app, monkeypatch):
    class SessionExpired(Exception):
        error_code = "-9999999"
        error_message = "sign invalid"

    fresh_scans(running_app, monkeypatch, {PLUG.id: _lan("key_mismatch", "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("The key was not accepted")
    _raises(running_app, monkeypatch, SessionExpired())
    fix_action(page, "refresh").click()
    expect(page.locator("#fixBody")).to_contain_text("Your Tuya login expired")
    assert frontend_state(page, "deviceData")[0]["local_key"] == PLUG.local_key
    fix_action(page, "login").click()
    expect(page.locator("#login")).to_be_visible()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"


def test_fix_connection_can_stop_a_scan_and_never_reuses_an_old_success(page, running_app, monkeypatch):
    fresh_scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("Connection checked")
    release = _held_scan(running_app, monkeypatch)
    page.locator("#panelBack").click()
    fix_action(page, "scan").click()
    page.locator("#fixTargets").fill("192.168.2.0/30")
    try:
        page.locator("#fixBody [data-fix-scan] button").click()
        expect(fix_heading(page)).to_have_text("Scanning the network")
        fix_action(page, "cancel").click()
        expect(fix_action(page, "cancel")).to_be_disabled()
        release.set()
        expect(fix_heading(page)).to_have_text("Check stopped")
        assert "Connection checked" not in page.locator("#fixBody").inner_text()
    finally:
        release.set()


def test_fix_connection_does_not_claim_success_when_final_results_cannot_be_loaded(page, running_app, monkeypatch):
    fresh_scans(running_app, monkeypatch,
                {PLUG.id: _lan("ok", "192.168.2.61", "3.4")},
                {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("Connection checked")
    page.locator("#panelBack").click()
    page.route("**/api/lan", lambda route: route.abort())
    fix_test(page)
    expect(fix_heading(page)).to_have_text("A new test is needed")
    assert "Connection checked" not in page.locator("#fixBody").inner_text()


def test_fix_connection_can_retry_a_failed_stop_request(page, running_app, monkeypatch):
    release = _held_scan(running_app, monkeypatch)

    def fail_stop(route):
        if route.request.method == "DELETE":
            route.abort()
        else:
            route.continue_()

    page.route("**/api/lan/scan", fail_stop)
    open_fix(page)
    fix_action(page, "test").click()
    fix_action(page, "scan").click()
    page.locator("#fixTargets").fill("192.168.2.0/30")
    try:
        page.locator("#fixBody [data-fix-scan] button").click()
        expect(fix_heading(page)).to_have_text("Scanning the network")
        fix_action(page, "cancel").click()
        expect(page.locator("#fixBody")).to_contain_text("The stop request failed")
        expect(fix_action(page, "cancel")).to_be_enabled()
        page.unroute("**/api/lan/scan", fail_stop)
        # Accept the stop request before allowing the held scan to finish.
        with page.expect_response(lambda r: r.url.endswith("/api/lan/scan") and
                                  r.request.method == "DELETE") as stopped:
            fix_action(page, "cancel").click()
        assert stopped.value.status == 200
        release.set()
        expect(fix_heading(page)).to_have_text("Check stopped")
    finally:
        release.set()


@pytest.mark.parametrize("missing_session", [True, False])
def test_fix_connection_stops_waiting_when_the_poll_loses_its_login_or_job(page, running_app, monkeypatch,
                                                                       missing_session):
    fresh_scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    account = frontend_state(page, "accountId")

    def lose_result(route):
        if route.request.method != "GET":
            route.continue_()
        elif missing_session:
            route.fulfill(status=401, json={"error": "not_logged_in"})
        else:
            route.fulfill(json={"account_id": account, "job": None})

    page.route("**/api/lan/scan", lose_result)
    open_fix(page)
    fix_test(page)
    expect(fix_heading(page)).to_have_text("Log in again" if missing_session else "The check could not finish")
    assert page.locator("#fixBody [aria-busy='true']").count() == 0
    if missing_session:
        expect(fix_action(page, "login")).to_be_enabled()


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (390, "dark"), (320, "light"), (320, "dark")])
def test_fix_connection_is_accessible_on_small_screens(page, width, scheme):
    page.set_viewport_size({"width": width, "height": 640})
    page.emulate_media(color_scheme=scheme)
    open_fix(page)
    close_box = page.locator("#panelClose").bounding_box()
    fix_action(page, "test").press("Enter")
    expect(page.locator("#fixCheckIP")).to_be_focused()
    fix_action(page, "scan").click()
    expect(page.locator("#fixTargets")).to_be_focused()
    page.locator("#fixTargets").fill("")
    repaint(page)
    expect(page.locator("#fixTargets")).to_have_value("")
    page.locator("#fixBody [data-fix-scan] button").scroll_into_view_if_needed()
    assert page.locator("#fixBody").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("#panel").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)


@pytest.mark.parametrize("child", [False, True])
def test_network_complete_connection_rechecks_saved_details_and_restores_troubleshooting_on_failure(
        page, running_app, monkeypatch, child):
    gateway = copy.deepcopy(ZIGBEE_GATEWAY)
    gateway.local_key = VALVE.local_key
    device = _named_sub(VALVE, gateway) if child else PLUG
    target = gateway if child else PLUG
    if child:
        _with_gateways(page, running_app, monkeypatch, gateway, device)
    calls = fresh_scans(running_app, monkeypatch,
                        {target.id: _lan("ok", "192.168.2.61", "3.4")},
                        {target.id: _lan("key_mismatch", "192.168.2.61", "3.4")})
    scanned(page)
    open_network(page, device.name)
    assert page.locator("#networkBody .network-actions button").count() == 1
    assert page.locator("#networkBody [data-lan-check]").count() == 0
    recheck = page.locator("#networkBody [data-network-action='recheck']")
    expect(recheck).to_have_text("Recheck connection")
    recheck.click()
    expect(page.locator("#networkBody [data-network-action='fix']")).to_be_visible()
    assert calls[1]["only"] == [target.id]
    assert calls[1]["targets"] == ["192.168.2.61"]
    assert page.locator("#networkBody [data-lan-check]").get_attribute("data-lan-check") == target.id


def test_fix_connection_back_steps_keep_ip_and_scan_drafts_without_duplicate_controls(page):
    open_fix(page)
    fix_action(page, "test").click()
    ip = page.locator("#fixCheckIP")
    ip.fill("192.168.2.200")
    fix_action(page, "scan").click()
    expect(page.locator("#panelBack")).to_have_text("Back to IP address")
    assert page.locator("#fixCheckIP").count() == 0
    page.locator("#fixTargets").fill("192.168.2.0/24")
    assert page.locator("#fixBody form button").count() == 1
    page.locator("#panelBack").click()
    expect(ip).to_have_value("192.168.2.200")
    expect(ip).to_be_focused()
    fix_action(page, "scan").click()
    expect(page.locator("#fixTargets")).to_have_value("192.168.2.0/24")
    page.keyboard.press("Escape")
    expect(ip).to_have_value("192.168.2.200")
    page.keyboard.press("Escape")
    expect(fix_heading(page)).to_have_text("Check the saved details")
    page.keyboard.press("Escape")
    expect(page.locator("#networkBody")).to_be_visible()


# --------------------------------------------------------------------------- #
# Manual local value reads
# --------------------------------------------------------------------------- #
def open_values(page, name=PLUG.name):
    open_panel(page, name)
    page.locator("#panelValuesOpen").click()
    expect(page.locator("#valuesBody")).to_be_visible()


def values_read(page):
    return page.locator("#valuesBody [data-values-action='read']")


def values_ready(page, app, monkeypatch, reply=None):
    _scans(app, monkeypatch, {PLUG.id: _lan("ok", "192.168.2.61", "3.4")})
    scanned(page)
    calls = []

    def read(context):
        calls.append(context)
        return copy.deepcopy(reply or {"status": "ok", "dps": {"1": False, "19": 427, "101": "extra value"},
                                       "read_at": demo_devices.UPDATE_TIME})

    monkeypatch.setattr(app.device_values, "read", read)
    return calls


def test_current_values_opening_does_not_read_and_missing_details_offer_network_setup(page):
    requests = []
    page.on("request", lambda request: requests.append(request))
    open_values(page)
    body = page.locator("#valuesBody")
    assert "Cloud snapshot" in body.inner_text()
    assert "Local reading: Not read yet" in body.inner_text()
    assert values_read(page).count() == 0
    assert "81.2 W" in body.locator("[data-values-dp='19'] [data-cloud-value]").inner_text()
    page.locator("#valuesBody [data-values-action='network']").click()
    expect(page.locator("#networkBody")).to_be_visible()
    page.keyboard.press("Escape")
    expect(body).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#panelValuesOpen")).to_be_focused()
    assert not [request for request in requests if request.url.endswith("/api/lan/read")]


def test_current_values_read_displays_separate_cloud_and_local_values_with_a_timestamp(page, running_app, monkeypatch):
    calls = values_ready(page, running_app, monkeypatch)
    open_values(page)
    values_read(page).click()
    expect(values_read(page)).to_have_text("Read again")
    row = page.locator("#valuesBody [data-values-dp='19']")
    assert "81.2 W" in row.locator("[data-cloud-value]").inner_text()
    assert "42.7 W" in row.locator("[data-local-value]").inner_text()
    assert "raw 427" in row.locator("[data-local-value]").inner_text()
    assert "2025-07-09 00:10:00" in page.locator("[data-values-read-at]").inner_text()
    assert page.locator("#valuesBody [data-values-dp='101'] .values-code").inner_text() == "Data point 101"
    assert len(calls) == 1 and calls[0]["ip"] == "192.168.2.61"
    assert calls[0]["version"] == "3.4"
    assert PLUG.local_key not in page.locator("#valuesBody").inner_text()
    row.locator("[data-local-value] [data-copy]").click()
    assert clipboard(page) == "427"
    assert frontend_state(page, "deviceData")[0]["status"]["cur_power"] == 812
    assert "dps" not in page.request.get(page.url + "api/lan").json()


def test_current_values_failed_retry_keeps_previous_values_and_their_old_timestamp(page, running_app, monkeypatch):
    values_ready(page, running_app, monkeypatch)
    open_values(page)
    values_read(page).click()
    expect(values_read(page)).to_have_text("Read again")
    timestamp = page.locator("[data-values-read-at]").inner_text()
    monkeypatch.setattr(running_app.device_values, "read", lambda context: {"status": "connection_failed"})
    values_read(page).click()
    expect(page.locator("#valuesBody .values-notice")).to_contain_text("Could not open a local connection")
    assert "previous reading is still shown" in page.locator("#valuesBody .values-notice").inner_text()
    assert page.locator("[data-values-read-at]").inner_text() == timestamp
    assert "42.7 W" in page.locator("#valuesBody [data-values-dp='19'] [data-local-value]").inner_text()


def test_current_values_read_targets_the_child_node_through_its_gateway(page, running_app, monkeypatch):
    child = _named_sub(VALVE, ZIGBEE_GATEWAY)
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, child)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=child.id),
        child.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    calls = []

    def read(context):
        calls.append(context)
        return {"status": "ok", "dps": {"1": True}, "read_at": demo_devices.UPDATE_TIME}

    monkeypatch.setattr(running_app.device_values, "read", read)
    open_values(page, child.name)
    values_read(page).click()
    expect(values_read(page)).to_have_text("Read again")
    assert calls[0]["host_id"] == ZIGBEE_GATEWAY.id
    assert calls[0]["node_id"] == child.node_id
    assert calls[0]["ip"] == "192.168.2.8"
    assert calls[0]["device_id"] == child.id


def test_current_values_missing_child_node_id_offers_a_refresh_instead_of_reading_the_gateway(page, running_app, monkeypatch):
    child = _named_sub(VALVE, ZIGBEE_GATEWAY)
    child.node_id = ""
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, child)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=child.id),
        child.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)
    open_values(page, child.name)
    assert values_read(page).count() == 0
    assert "Node ID" in page.locator("#valuesBody .values-notice").inner_text()
    refresh = page.locator("#valuesBody [data-values-action='refresh']")
    expect(refresh).to_have_text("Refresh device list")
    child.node_id = "new-node"
    _serves(running_app, monkeypatch, list(DEVICES) + [ZIGBEE_GATEWAY, child])
    refresh.click()
    expect(values_read(page)).to_be_enabled()


def test_current_values_refresh_discards_the_previous_local_reading(page, running_app, monkeypatch):
    values_ready(page, running_app, monkeypatch)
    open_values(page)
    values_read(page).click()
    expect(values_read(page)).to_have_text("Read again")
    refreshed(page, "#valuesBody")
    expect(values_read(page)).to_have_text("Read from device")
    expect(page.locator("[data-values-read-at]")).to_have_text("Local reading: Not read yet")


def test_current_values_does_not_show_an_inflight_reply_for_another_device(page, running_app, monkeypatch):
    values_ready(page, running_app, monkeypatch)
    held = []
    page.route("**/api/lan/read", lambda route: held.append(route))
    open_values(page)
    values_read(page).click()
    expect(values_read(page)).to_have_text("Reading…")
    page.locator("#panelClose").click()
    open_values(page, LAMP.name)
    assert len(held) == 1
    held[0].continue_()
    page.wait_for_function("""async () => {
        const { appState } = await import(new URL('static/js/shared/state.js', document.baseURI).href);
        return !appState.localReadPending;
    }""")
    assert "extra value" not in page.locator("#valuesBody").inner_text()
    assert "Local reading: Not read yet" in page.locator("#valuesBody").inner_text()


def test_current_values_read_temporarily_disables_connection_rechecks_without_losing_proof(page, running_app, monkeypatch):
    values_ready(page, running_app, monkeypatch)
    held = []
    page.route("**/api/lan/read", lambda route: held.append(route))
    open_values(page)
    values_read(page).click()
    page.locator("#panelBack").click()
    page.locator("#panelNetworkOpen").click()
    recheck = page.locator("#networkBody [data-network-action='recheck']")
    expect(recheck).to_be_disabled()
    assert network_summary(page) == "Connection checked"
    assert len(held) == 1
    held[0].continue_()
    expect(recheck).to_be_enabled()
    assert network_summary(page) == "Connection checked"
    assert page.locator("#networkBody [data-network-action='fix']").count() == 0


@pytest.mark.parametrize("status", ["no_values", "key_or_protocol", "unreachable"])
def test_current_values_explains_unsuccessful_reads_without_using_cloud_as_local(page, running_app, monkeypatch, status):
    values_ready(page, running_app, monkeypatch, {"status": status})
    open_values(page)
    values_read(page).click()
    expect(page.locator("#valuesBody .values-notice")).to_be_visible()
    expect(values_read(page)).to_be_enabled()
    assert "Local reading: Not read yet" in page.locator("#valuesBody").inner_text()
    assert "81.2 W" not in page.locator("#valuesBody [data-local-value]").all_inner_texts()


@pytest.mark.parametrize("width, scheme", [(1440, "light"), (390, "dark"), (320, "dark")])
def test_current_values_layout_and_keyboard_navigation(page, running_app, monkeypatch, width, scheme):
    values_ready(page, running_app, monkeypatch)
    page.set_viewport_size({"width": width, "height": 640})
    page.emulate_media(color_scheme=scheme)
    open_values(page)
    expect(values_read(page)).to_be_focused()
    values_read(page).press("Enter")
    expect(values_read(page)).to_have_text("Read again")
    expect(values_read(page)).to_be_focused()
    page.locator("#valuesBody .values-specs summary").click()
    expect(page.locator("[data-values-definition='cur_power']")).to_be_visible()
    assert page.locator("#valuesBody").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("#panel").evaluate("el => el.scrollWidth <= el.clientWidth")
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"current-values-{width}-{scheme}.png", full_page=False)
    page.keyboard.press("Escape")
    expect(page.locator("#panelValuesOpen")).to_be_focused()


# --------------------------------------------------------------------------- #
# Device directory and dedicated information pages
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("width, scheme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark"),
                                          (320, "light"), (320, "dark")])
def test_device_menu_only_shows_options_and_navigates_every_page(page, width, scheme):
    page.set_viewport_size({"width": width, "height": 640})
    page.emulate_media(color_scheme=scheme)
    requests = []
    page.on("request", lambda request: requests.append(request))
    open_panel(page, PLUG.name)
    entries = [("panelSetupOpen", "setupBody", "Set up in…"),
               ("panelNetworkOpen", "networkBody", "Local network"),
               ("panelValuesOpen", "valuesBody", "Current values"),
               ("panelInfoOpen", "panelBody", "Device information"),
               ("panelRawOpen", "rawBody", "Raw JSON")]
    menu = page.locator("#menuBody")
    assert menu.locator(".device-menu-label").all_inner_texts() == [title for _, _, title in entries] + ["Download a support report"]
    assert page.locator("#panelDataPointsOpen, #dataPointsBody").count() == 0
    assert menu.locator("dl, pre, .dps, [data-copy]").count() == 0
    assert PLUG.local_key not in menu.inner_html()
    expect(page.locator("#panelSetupOpen")).to_be_focused()
    close_box = page.locator("#panelClose").bounding_box()
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"device-menu-{width}-{scheme}.png", full_page=False)
    for entry, body, title in entries:
        page.locator(f"#{entry}").press("Enter")
        expect(page.locator(f"#{body}")).to_be_visible()
        expect(page.locator("#panelTitle")).to_have_text(title)
        expect(page.locator("#panelSub")).to_have_text(PLUG.name)
        assert page.locator("#panel [data-panel-page]").count() == 0
        assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
        assert page.locator(f"#{body}").evaluate("el => el.scrollWidth <= el.clientWidth")
        assert page.locator("#panel").evaluate("el => el.scrollWidth <= el.clientWidth")
        page.keyboard.press("Escape")
        expect(menu).to_be_visible()
        expect(page.locator(f"#{entry}")).to_be_focused()
    assert not [request for request in requests if request.method != "GET"]
    assert page.locator("#panelBack").is_hidden()


def test_device_menu_current_values_explains_empty_data_points(page):
    open_values(page, BARE.name)
    expect(page.locator("#valuesBody")).to_contain_text("No saved data points. A local read may return values the cloud does not list.")
    assert page.locator("#valuesBody .values-specs").count() == 0
    page.locator("#panelBack").click()
    expect(page.locator("#panelValuesOpen")).to_be_focused()
    page.locator("#panelRawOpen").click()
    assert json.loads(page.locator("#rawBody pre").inner_text())["id"] == BARE.id


def test_device_information_scroll_and_raw_key_visibility_survive_returning_to_menu(page):
    page.set_viewport_size({"width": 390, "height": 640})
    open_information(page, PLUG.name)
    scroll = page.locator("#panelBody").evaluate("el => { el.scrollTop = 320; return el.scrollTop; }")
    assert scroll > 0
    page.locator("#panelBack").click()
    page.locator("#panelRawOpen").click()
    assert PLUG.local_key not in page.locator("#rawBody pre").inner_text()
    page.locator("#rawBody [data-key-toggle]").click()
    assert PLUG.local_key in page.locator("#rawBody pre").inner_text()
    page.locator("#panelBack").click()
    assert PLUG.local_key not in page.locator("#menuBody").inner_text()
    page.locator("#panelInfoOpen").click()
    assert page.locator("#panelBody").evaluate("el => el.scrollTop") == pytest.approx(scroll, abs=1)
    assert PLUG.local_key in field_value(page, "local_key")
    page.locator("#panelClose").click()
    open_raw(page, PLUG.name)
    assert PLUG.local_key not in page.locator("#rawBody pre").inner_text()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark"), (320, "dark")])
def test_support_report_downloads_private_diagnostics_directly_from_the_menu(page, tmp_path, width, scheme):
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme=scheme)
    requests = []
    page.on("request", lambda request: requests.append(request))
    open_panel(page, PLUG.name)
    assert not [request for request in requests if request.url.endswith("/api/support-report")]
    control = page.locator("#panelSupportDownload")
    control.focus()
    with page.expect_download() as downloaded:
        control.press("Enter")
    filename = downloaded.value.suggested_filename
    assert filename.startswith("tuya-local-key-support-") and filename.endswith(".json")
    assert PLUG.id not in filename and PLUG.name not in filename
    path = tmp_path / filename
    downloaded.value.save_as(path)
    text = path.read_text()
    report = json.loads(text)
    assert report["schema_version"] == 1
    assert report["device"]["product_id"] == PLUG.product_id
    assert report["data_points"]["definitions"]
    for secret in (PLUG.id, PLUG.name, PLUG.local_key, PLUG.uid, PLUG.ip):
        assert secret not in text
    assert "account_id" not in report
    expect(page.locator("#menuBody [role='status']")).to_have_text("Support report downloaded.")
    expect(control).to_be_focused()
    assert page.locator("#menuBody").evaluate("el => el.scrollWidth <= el.clientWidth")
    posts = [request for request in requests if request.method == "POST"]
    assert len(posts) == 1 and posts[0].url.endswith("/api/support-report")
    assert page.locator("#panelTitle").inner_text() == PLUG.name


@pytest.mark.parametrize("failure", ["network", "wrong_account", "malformed"])
def test_support_report_failures_offer_a_retry_without_downloading_a_file(page, failure):
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    open_panel(page, PLUG.name)

    def fail(route):
        if failure == "network":
            route.abort()
        elif failure == "malformed":
            route.fulfill(status=200, content_type="application/json", body="null")
        else:
            route.fulfill(status=200, json={"account_id": "another-account", "report": {"schema_version": 1}})

    page.route("**/api/support-report", fail)
    control = page.locator("#panelSupportDownload")
    control.click()
    expect(page.locator("#menuBody [role='alert']")).to_have_text("Could not download the report. Try again.")
    expect(control).to_have_attribute("aria-busy", "false")
    assert downloads == []
    page.unroute("**/api/support-report", fail)
    with page.expect_download():
        control.click()
    expect(page.locator("#menuBody [role='status']")).to_have_text("Support report downloaded.")


@pytest.mark.parametrize("transition", ["close", "refresh", "another_device"])
def test_support_report_deduplicates_clicks_and_discards_replies_after_context_changes(page, transition):
    held, downloads = [], []
    page.route("**/api/support-report", lambda route: held.append(route))
    page.on("download", lambda download: downloads.append(download))
    open_panel(page, PLUG.name)
    control = page.locator("#panelSupportDownload")
    control.click()
    expect(control).to_have_attribute("aria-busy", "true")
    control.press("Enter")
    assert len(held) == 1
    if transition == "refresh":
        refreshed(page, "#menuBody")
    else:
        page.locator("#panelClose").click()
        if transition == "another_device":
            open_panel(page, LAMP.name)
            expect(control).to_have_attribute("aria-busy", "false")
    with page.expect_response(lambda response: response.url.endswith("/api/support-report")):
        held[0].continue_()
    page.wait_for_load_state("networkidle")
    assert downloads == []
    if transition != "close":
        expect(control).to_have_attribute("aria-busy", "false")


@pytest.mark.parametrize("outcome", ["success", "failure"])
@pytest.mark.parametrize("transition", ["close", "switch", "history"])
def test_support_report_feedback_clears_when_the_device_visit_ends(page, running_app, monkeypatch, outcome, transition):
    device = PLUG
    if transition == "history":
        child = _named_sub(VALVE, ZIGBEE_GATEWAY)
        _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, child)
        device = ZIGBEE_GATEWAY
    open_panel(page, device.name)
    control = page.locator("#panelSupportDownload")

    def fail(route):
        route.abort()

    if outcome == "failure":
        page.route("**/api/support-report", fail)
        control.click()
        expect(page.locator("#menuBody [role='alert']")).to_be_visible()
    else:
        with page.expect_download():
            control.click()
        expect(page.locator("#menuBody [role='status']")).to_be_visible()
    message = page.locator("#menuBody .device-menu-message")
    page.locator("#panelInfoOpen").click()
    page.locator("#panelBack").click()
    expect(message).to_be_visible()
    if transition == "close":
        page.locator("#panelClose").click()
        open_panel(page, device.name)
    elif transition == "switch":
        open_panel(page, LAMP.name)
        expect(message).to_have_count(0)
        open_panel(page, device.name)
    else:
        page.locator("#panelSetupOpen").click()
        page.locator(f"#setupBody [data-open-device='{child.id}']").click()
        page.locator("#panelBack").click()
        page.locator("#panelBack").click()
    expect(message).to_have_count(0)
    expect(control).to_have_attribute("aria-busy", "false")
    if outcome == "failure":
        page.unroute("**/api/support-report", fail)
    with page.expect_download():
        control.click()
    expect(page.locator("#menuBody [role='status']")).to_have_text("Support report downloaded.")


@pytest.mark.parametrize("transition", ["close", "switch"])
def test_support_report_reply_cannot_restore_feedback_after_leaving_and_returning(page, transition):
    held, downloads = [], []
    page.route("**/api/support-report", lambda route: held.append(route))
    page.on("download", lambda download: downloads.append(download))
    open_panel(page, PLUG.name)
    control = page.locator("#panelSupportDownload")
    control.click()
    expect(control).to_have_attribute("aria-busy", "true")
    if transition == "close":
        page.locator("#panelClose").click()
    else:
        open_panel(page, LAMP.name)
    open_panel(page, PLUG.name)
    expect(control).to_have_attribute("aria-busy", "false")
    with page.expect_response(lambda response: response.url.endswith("/api/support-report")):
        held[0].continue_()
    page.wait_for_load_state("networkidle")
    assert downloads == []
    expect(page.locator("#menuBody .device-menu-message")).to_have_count(0)


def reset_update_check(page):
    page.evaluate("""() => {
        for (const key of Object.keys(localStorage)) {
            if (key.startsWith('tuya-local-key:update-check:')) localStorage.removeItem(key);
        }
    }""")


def seed_cached_update(page):
    page.evaluate("""([version, latest, url]) => {
        const key = `tuya-local-key:update-check:v1:${new URL('.', document.baseURI).pathname}`;
        const value = JSON.stringify({ nextAutomaticAt: Date.now() + 2 * 86400000, result: {
            available: true, current_version: version, latest_version: latest,
            update_url: url, installation: 'standalone'
        }});
        localStorage.setItem(key, value);
        window.dispatchEvent(new StorageEvent('storage', { key, newValue: value }));
    }""", [CURRENT_VERSION, NEXT_VERSION, NEXT_RELEASE_URL])


@pytest.mark.parametrize("width", [1440, 320])
def test_disabled_update_checks_hide_cached_badges_and_make_no_requests(page, running_app, monkeypatch, width):
    page.set_viewport_size({"width": width, "height": 1000})
    page.emulate_media(color_scheme="dark" if width == 320 else "light")
    seed_cached_update(page)
    monkeypatch.setattr(running_app, "CHECK_FOR_UPDATES", False)
    calls, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/updates*", lambda route: calls.append(route.request) or route.fulfill(
        json={"enabled": False, "available": False, "status": "disabled"}))
    page.reload(wait_until="networkidle")
    expect(page.locator("#footerCheckUpdates, #updateBadge, #footerUpdateBadge, #updateDialog")).to_have_count(0)
    expect(page.locator(".footer-version")).to_be_visible()
    page.locator("#filter").fill("plug")
    open_panel(page, PLUG.name)
    page.locator("#panelValuesOpen").click()
    expect(page.locator("#valuesBody")).to_be_visible()
    page.keyboard.press("Escape")
    seed_cached_update(page)
    page.evaluate("""() => {
        window.dispatchEvent(new Event('pageshow'));
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    page.wait_for_load_state("networkidle")
    assert calls == [] and errors == []
    expect(page.locator("#updateBadge, #footerUpdateBadge")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("method", ["manual", "automatic"])
def test_an_open_tab_stops_checks_when_the_server_disables_updates(page, running_app, monkeypatch, method):
    if method == "automatic":
        seed_cached_update(page)
        expect(page.locator("#updateBadge")).to_be_visible()
    monkeypatch.setattr(running_app, "CHECK_FOR_UPDATES", False)
    requests = []
    page.on("request", lambda request: requests.append(request) if "/api/updates" in request.url else None)
    if method == "automatic":
        page.evaluate("Date.now = () => 1900000000000")
        page.locator("#count").click()
    else:
        page.locator("#footerCheckUpdates").click()
    expect(page.locator("#footerCheckUpdates")).to_be_hidden()
    expect(page.locator("#updateBadge")).to_be_hidden()
    expect(page.locator("#footerUpdateBadge")).to_be_hidden()
    expect(page.locator("#updateDialog")).to_be_hidden()
    page.evaluate("Date.now = () => 1900000000000")
    page.locator("#filter").fill("lamp")
    seed_cached_update(page)
    page.evaluate("window.dispatchEvent(new Event('pageshow'))")
    page.wait_for_load_state("networkidle")
    assert [request.method for request in requests] == ["POST" if method == "manual" else "GET"]
    expect(page.locator("#updateBadge")).to_be_hidden()
    expect(page.locator("#footerUpdateBadge")).to_be_hidden()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (1440, "dark"), (1024, "light"),
                                          (390, "dark"), (375, "light"), (320, "dark")])
@pytest.mark.parametrize("home_assistant", [False, True])
def test_update_badge_fits_the_header_and_links_to_the_right_update_page(page, width, scheme, home_assistant):
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme=scheme)
    target = ("https://my.home-assistant.io/redirect/supervisor_addon/?addon=a1b2c3d4_tuya_local_key" if home_assistant
              else NEXT_RELEASE_URL)
    reset_update_check(page)
    page.route("**/api/updates", lambda route: route.fulfill(json={
        "available": True, "current_version": CURRENT_VERSION, "latest_version": NEXT_VERSION,
        "update_url": target, "installation": "home_assistant" if home_assistant else "standalone",
        "check_after_seconds": 86400,
    }))
    page.reload(wait_until="networkidle")
    badge = page.locator("#updateBadge")
    expect(badge).to_be_visible()
    expect(page.locator("#updateDialog")).to_be_hidden()
    assert page.locator("header .header-version").count() == 0
    assert page.locator(".footer-version").inner_text().startswith("v")
    expect(page.locator("#footerCheckUpdates")).to_be_hidden()
    expect(page.locator("#footerUpdateBadge")).to_have_attribute("href", target)
    expect(badge).to_have_attribute("href", target)
    expect(badge).to_have_attribute("target", "_blank")
    assert NEXT_VERSION in badge.get_attribute("aria-label")
    assert "Home Assistant" in badge.get_attribute("title") if home_assistant else "GitHub" in badge.get_attribute("title")
    badge.focus()
    expect(badge).to_be_focused()
    assert page.locator("header").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"update-{width}-{scheme}-{'ha' if home_assistant else 'standalone'}.png")
    if width >= 900:
        open_panel(page, PLUG.name)
        page.wait_for_function("document.querySelector('#panel').getBoundingClientRect().right <= innerWidth + 0.5")
        # Wait for the body-padding transition and header observer to settle.
        page.wait_for_timeout(300)
        assert page.locator("header").evaluate("el => el.scrollWidth <= el.clientWidth")
        expect(badge).to_be_visible()


@pytest.mark.parametrize("response", [
    {"available": False, "current_version": CURRENT_VERSION, "latest_version": CURRENT_VERSION, "check_after_seconds": 86400},
    {"available": False, "installation": "home_assistant", "check_after_seconds": 1},
    {"available": True, "update_url": "javascript:alert(1)"},
    {"available": True, "update_url": "https://attacker.test/release"},
    {"available": True, "update_url": "https://github.com/other/repository/releases/tag/v4.0"},
    None,
])
def test_update_badge_stays_hidden_for_current_delayed_or_invalid_results(page, response):
    reset_update_check(page)
    page.route("**/api/updates", lambda route: route.fulfill(json=response))
    page.reload(wait_until="networkidle")
    expect(page.locator("#updateBadge")).to_be_hidden()
    expect(page.locator("#banner")).to_be_hidden()
    expect(row(page, PLUG)).to_be_visible()


def test_a_slow_or_failed_update_check_does_not_block_device_loading(page):
    held = []
    reset_update_check(page)
    page.route("**/api/updates", lambda route: held.append(route))
    page.reload(wait_until="domcontentloaded")
    expect(row(page, PLUG)).to_be_visible()
    assert len(held) == 1
    held[0].abort()
    expect(page.locator("#updateBadge")).to_be_hidden()
    expect(page.locator("#banner")).to_be_hidden()


def test_automatic_update_checks_wait_for_activity_and_a_full_day(page):
    calls = []
    reset_update_check(page)
    page.add_init_script("window.updateNow = 1800000000000; Date.now = () => window.updateNow;")

    def update(route):
        calls.append(route.request.url)
        route.fulfill(json={"available": False, "current_version": CURRENT_VERSION, "check_after_seconds": 1})

    page.route("**/api/updates", update)
    page.reload(wait_until="networkidle")
    assert len(calls) == 1
    page.wait_for_timeout(1200)
    assert len(calls) == 1, "no background timer, even if a short server retry was suggested"
    page.evaluate("window.updateNow += 86400000 - 1")
    page.locator("#filter").fill("plug")
    assert len(calls) == 1
    page.evaluate("window.updateNow += 1")
    page.wait_for_timeout(100)
    assert len(calls) == 1, "elapsed time alone does not start a check"
    with page.expect_response(lambda response: response.url.endswith("/api/updates")):
        page.locator("#filter").fill("lamp")
    assert len(calls) == 2
    page.reload(wait_until="networkidle")
    assert len(calls) == 2, "reopening the app keeps the daily cooldown"


def test_update_badge_remains_separate_from_the_home_action(page):
    reset_update_check(page)
    page.route("**/api/updates", lambda route: route.fulfill(json={
        "available": True, "current_version": CURRENT_VERSION, "latest_version": NEXT_VERSION, "installation": "standalone",
        "update_url": NEXT_RELEASE_URL, "check_after_seconds": 86400,
    }))
    page.reload(wait_until="networkidle")
    expect(page.locator("#updateBadge")).to_be_visible()
    open_panel(page, PLUG.name)
    assert page.locator("#homeLink .header-version, #homeLink #updateBadge").count() == 0
    page.locator("#homeLink").click()
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")
    expect(page.locator("#updateBadge")).to_be_visible()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark"), (320, "light")])
@pytest.mark.parametrize("home_assistant", [False, True])
def test_manual_update_check_finds_an_update_in_both_header_and_footer(page, width, scheme, home_assistant):
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme=scheme)
    calls = []
    target = ("https://my.home-assistant.io/redirect/supervisor_addon/?addon=a1b2c3d4_tuya_local_key" if home_assistant
              else NEXT_RELEASE_URL)

    def manual(route):
        calls.append(route.request.method)
        route.fulfill(json={"available": True, "current_version": CURRENT_VERSION, "latest_version": NEXT_VERSION,
                            "update_url": target, "installation": "home_assistant" if home_assistant else "standalone",
                            "last_check_succeeded": True})

    page.route("**/api/updates/check", manual)
    check = page.locator("#footerCheckUpdates")
    expect(check).to_be_visible()
    check.click()
    expect(page.locator("#updateBadge")).to_be_visible()
    expect(page.locator("#footerUpdateBadge")).to_be_visible()
    expect(check).to_be_hidden()
    expect(page.locator("#footerUpdateBadge")).to_have_attribute("href", target)
    dialog = page.locator("#updateDialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#updateDialogTitle")).to_have_text("Update available")
    expect(page.locator("#updateDialogText")).to_have_text(f"Version {NEXT_VERSION} is available.")
    expect(page.locator("#updateDialogLink")).to_have_attribute("href", target)
    expect(page.locator("#updateDialogLink")).to_have_attribute("target", "_blank")
    expect(page.locator("#updateDialogClose")).to_be_focused()
    assert page.locator("#footerUpdateStatus").count() == 0
    assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth")
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"manual-update-dialog-{width}-{scheme}-{'ha' if home_assistant else 'standalone'}.png")
    page.locator("#updateDialogClose").click()
    expect(dialog).to_be_hidden()
    expect(page.locator("#footerUpdateBadge")).to_be_focused()
    assert calls == ["POST"]
    assert page.locator("#appFooter").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("header .header-version").count() == 0
    automatic = []
    page.route("**/api/updates", lambda route: automatic.append(route) or route.continue_())
    page.reload(wait_until="networkidle")
    assert automatic == []
    expect(page.locator("#updateBadge")).to_be_visible()
    expect(page.locator("#footerUpdateBadge")).to_be_visible()
    expect(check).to_be_hidden()
    expect(dialog).to_be_hidden()
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"manual-update-{width}-{scheme}-{'ha' if home_assistant else 'standalone'}.png", full_page=True)


@pytest.mark.parametrize("result", ["current", "failed", "waiting"])
@pytest.mark.parametrize("width", [1440, 320])
def test_manual_update_check_reports_its_result_and_can_be_retried(page, result, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme="dark" if width == 320 else "light")
    requests = []

    def manual(route):
        requests.append(route.request)
        if result == "failed":
            route.abort()
        else:
            route.fulfill(json={"available": False, "current_version": CURRENT_VERSION, "status": "ok",
                                "last_check_succeeded": True, "waiting_for_release": result == "waiting"})

    page.route("**/api/updates/check", manual)
    check = page.locator("#footerCheckUpdates")
    check.click()
    expected = {"current": "No updates available", "failed": "Could not check for updates",
                "waiting": "Update coming soon"}[result]
    dialog = page.locator("#updateDialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#updateDialogTitle")).to_have_text(expected)
    expect(page.locator("#updateDialogLink")).to_be_hidden()
    assert page.locator("#footerUpdateStatus").count() == 0
    assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth")
    if SHOTS_ENABLED:
        page.screenshot(path=SHOTS_DIR / f"manual-update-dialog-{width}-{result}.png")
    page.locator("#updateDialogClose").click()
    expect(dialog).to_be_hidden()
    expect(check).to_be_visible()
    expect(check).to_be_enabled()
    expect(check).to_be_focused()
    expect(page.locator("#updateBadge")).to_be_hidden()
    check.click()
    expect(dialog).to_be_visible()
    expect(page.locator("#updateDialogTitle")).to_have_text(expected)
    assert len(requests) == 2


@pytest.mark.parametrize("dismiss", ["escape", "backdrop"])
def test_dismissing_an_update_popup_keeps_the_device_panel_open(page, dismiss):
    open_panel(page, PLUG.name)
    page.locator("#footerCheckUpdates").click()
    dialog = page.locator("#updateDialog")
    expect(dialog).to_be_visible()
    if dismiss == "escape":
        page.keyboard.press("Escape")
    else:
        page.mouse.click(5, 5)
    expect(dialog).to_be_hidden()
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "false")
    expect(page.locator("#footerCheckUpdates")).to_be_focused()


def test_failed_automatic_checks_also_keep_the_daily_cooldown(page):
    reset_update_check(page)
    calls = []

    def fail(route):
        calls.append(route.request)
        route.abort()

    page.route("**/api/updates", fail)
    page.reload(wait_until="networkidle")
    page.locator("#filter").fill("plug")
    page.reload(wait_until="networkidle")
    assert len(calls) == 1
    expect(page.locator("#footerCheckUpdates")).to_be_visible()


def test_cached_home_assistant_release_appears_after_an_hour_without_another_check(page):
    reset_update_check(page)
    page.add_init_script("window.updateNow = 1800000000000; Date.now = () => window.updateNow;")
    published = page.evaluate("new Date(1800000000000 - 3500000).toISOString()")
    calls = []

    def update(route):
        calls.append(route.request)
        route.fulfill(json={"available": False, "current_version": CURRENT_VERSION, "latest_version": NEXT_VERSION,
                            "installation": "home_assistant", "waiting_for_release": True,
                            "published_at": published, "check_after_seconds": 86400})

    page.route("**/api/updates", update)
    page.reload(wait_until="networkidle")
    expect(page.locator("#updateBadge")).to_be_hidden()
    page.evaluate("window.updateNow += 99999")
    page.locator("#count").click()
    expect(page.locator("#updateBadge")).to_be_hidden()
    page.evaluate("window.updateNow += 1")
    page.locator("#count").click()
    expect(page.locator("#updateBadge")).to_be_visible()
    expect(page.locator("#footerUpdateBadge")).to_have_attribute("href", "https://my.home-assistant.io/redirect/updates/")
    expect(page.locator("#footerCheckUpdates")).to_be_hidden()
    expect(page.locator("#updateDialog")).to_be_hidden()
    assert len(calls) == 1, "showing the cached release must not trigger another update request"
    page.reload(wait_until="networkidle")
    expect(page.locator("#updateBadge")).to_be_visible()
    assert len(calls) == 1


@pytest.mark.parametrize("available", [False, True])
def test_background_update_reply_preserves_the_open_header_menu_and_focus(page, available):
    reset_update_check(page)
    held = []
    page.route("**/api/updates", lambda route: held.append(route))
    page.reload(wait_until="domcontentloaded")
    expect(row(page, PLUG)).to_be_visible()
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!!document.querySelector('#moreMenu #logoutBtn')")
    page.locator("#moreBtn").click()
    menu = page.locator("#moreMenu")
    expect(menu).to_be_visible()
    focused = page.evaluate("document.activeElement.id")
    held[0].fulfill(json={"available": available, "current_version": CURRENT_VERSION, "latest_version": NEXT_VERSION,
                          "installation": "standalone", "last_check_succeeded": True,
                          "update_url": NEXT_RELEASE_URL})
    expect(page.locator("#footerCheckUpdates")).to_have_attribute("aria-busy", "false")
    expect(menu).to_be_visible()
    expect(page.locator(f"#{focused}")).to_be_focused()


def test_manual_update_check_is_disabled_while_a_request_is_running(page):
    held = []
    page.route("**/api/updates/check", lambda route: held.append(route))
    check = page.locator("#footerCheckUpdates")
    check.click()
    expect(check).to_be_disabled()
    expect(check).to_have_text("Checking…")
    page.locator("#filter").fill("plug")
    assert len(held) == 1
    held[0].fulfill(json={"available": False, "current_version": CURRENT_VERSION, "last_check_succeeded": True})
    expect(check).to_be_enabled()
