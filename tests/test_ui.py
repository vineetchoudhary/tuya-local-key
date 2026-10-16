"""Browser smoke tests for templates/index.html — the behaviour Python can't reach.

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
import shutil
import sys
import threading
import types
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import demo_devices  # noqa: E402

pytestmark = pytest.mark.ui

DEVICES = demo_devices.devices()
PLUG = DEVICES[demo_devices.PLUG]
BROWSER_CHANNELS = (None, "msedge", "chrome")   # None = playwright's own Chromium
MASK = "•"
APP = None      # the reloaded app module the server runs, for tests that vary it

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
        mp.delenv("AUTH_USERNAME", raising=False)
        mp.delenv("AUTH_PASSWORD", raising=False)
        import app

        app = importlib.reload(app)
        APP = app
        mp.setattr(app.core, "devices_from_session", lambda session, path: DEVICES)
        httpd = make_server("127.0.0.1", 0, app.app, threaded=True)
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
        f'<a class="shot" href="{s["file"]}" target="_blank">'
        f'<img src="{s["file"]}" alt="{html.escape(s["name"])}" loading="lazy"></a>'
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
    name = f"{len(gallery) + 1:02d}-{status}-{node.name}.png"
    try:
        # A fixed drawer belongs to the viewport. A full-page capture places it
        # partway down a scrolled mobile page instead of showing the visible UI.
        panel_open = page.locator("#panel").get_attribute("aria-hidden") == "false"
        page.screenshot(path=SHOTS_DIR / name, full_page=not panel_open)
    except PlaywrightError:
        return          # a dead page must not mask the test's own failure
    gallery.append({"name": node.name, "status": status, "file": name})


def cell_texts(page, column):
    return page.locator(f"#rows tr td:nth-child({column})").all_inner_texts()


def open_panel(page, name):
    page.click(f"#rows tr:has-text('{name}') td:first-child")
    # "visible", not just "attached": the panel is visibility:hidden until the
    # slide-in starts, and innerText reads empty while it is.
    page.wait_for_selector("#panel[aria-hidden='false']", state="visible")
    page.wait_for_function("document.getElementById('panel').getBoundingClientRect().right <= innerWidth + 0.5")


def open_network(page, name):
    open_panel(page, name)
    page.locator("#panelNetworkOpen").click()
    page.wait_for_selector("#networkBody", state="visible")


def field_value(page, key):
    """The panel's rendered value for a device field, addressed by its raw name."""
    return page.locator(f"#panelBody dt[title='{key}'] + dd:visible, #networkBody dt[title='{key}'] + dd:visible").inner_text()


def clipboard(page):
    return page.evaluate("navigator.clipboard.readText()")


def test_table_shows_the_scan_columns_only(page):
    headers = page.locator("#thead th").all_inner_texts()

    assert [h.strip() for h in headers] == [
        "Name", "Status", "ID", "Local Key / Node ID", "Protocol", "Product ID", "Product Name",
        "Update Time", "Details",
    ]
    assert page.locator("#rows tr").count() == len(DEVICES)
    # Fields that moved into the panel are not duplicated in the table.
    body = page.locator("#rows").inner_text()
    for moved in (PLUG.uuid, PLUG.category, PLUG.ip):
        assert moved not in body


def test_table_and_panel_local_key_visibility_are_independent(page):
    keys = page.locator("#rows .key")
    assert MASK in keys.first.inner_text()
    assert PLUG.local_key not in page.locator("#rows").inner_text()
    open_panel(page, PLUG.name)
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
    open_panel(page, PLUG.name)
    page.locator("#panelBody [data-key-toggle]").click()
    assert PLUG.local_key in field_value(page, "local_key")
    page.locator("#panelClose").click()
    open_panel(page, PLUG.name)
    assert MASK in field_value(page, "local_key")
    page.locator("#panelBody [data-key-toggle]").click()
    open_panel(page, LAMP.name)
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
    open_panel(page, PLUG.name)

    assert page.locator("#panelTitle").inner_text() == PLUG.name
    assert PLUG.product_name in page.locator("#panelSub").inner_text()
    assert field_value(page, "uuid") == PLUG.uuid
    assert field_value(page, "category") == PLUG.category
    assert field_value(page, "ip") == PLUG.ip
    assert field_value(page, "model") == PLUG.model
    assert field_value(page, "support_local") == "Yes"
    assert field_value(page, "asset_id") == "-"          # empty string, not "None"
    assert page.locator("#rows tr.selected").count() == 1


def test_panel_surfaces_fields_the_sdk_does_not_document(page):
    open_panel(page, "Balcony Door Sensor")

    assert "OTHER FIELDS" in page.locator("#panelBody").inner_text().upper()
    assert field_value(page, "firmware_channel") == "beta"
    assert field_value(page, "sub") == "Yes"
    assert field_value(page, "gateway_id") == "ebd8f1c0a1b2c3d4e5"


def test_panel_shows_local_time_utc_and_epoch(page):
    open_panel(page, PLUG.name)

    updated = field_value(page, "update_time")

    # Asia/Kolkata puts this epoch on the next calendar day: a UTC-only render fails here.
    assert "2025-07-09 00:10:00" in updated
    assert "2025-07-08 18:40:00 UTC" in updated
    assert str(demo_devices.UPDATE_TIME) in updated
    # The table reads local, not UTC.
    assert "2025-07-09 00:10:00" in cell_texts(page, 8)[0]


def data_points(page):
    """{code: (dp id, value, specification line)} from the panel's data point list."""
    page.locator("#panelBody details.data-points summary").click()
    points = {}
    for item in page.locator("#panelBody ul.dps li").all():
        meta = item.locator(".dp-meta")
        points[item.locator(".dp-code").inner_text()] = (
            item.locator(".dp-id").inner_text(),
            item.locator(".dp-val").inner_text(),
            meta.inner_text() if meta.count() else "",
        )
    return points


def test_data_points_map_dp_ids_to_codes_values_and_specs(page):
    open_panel(page, PLUG.name)
    disclosure = page.locator("#panelBody details.data-points")
    assert not disclosure.evaluate("el => el.open")
    assert disclosure.locator("ul.dps").is_hidden()

    points = data_points(page)

    assert disclosure.locator("summary").inner_text() == "Data points (2)"
    assert disclosure.locator("ul.dps").is_visible()
    # dp id comes from local_strategy, the value from status, the rest from the spec.
    assert points["cur_power"] == ("19", "812", "Integer · read-only · 0–50000 W · scale 1 · minux")
    assert points["switch_1"] == ("1", "true", "Boolean · read/write")
    disclosure.locator("summary").focus()
    page.keyboard.press("Enter")
    assert not disclosure.evaluate("el => el.open")
    assert disclosure.locator("ul.dps").is_hidden()


def test_data_points_fall_back_when_the_device_has_no_local_mapping(page):
    open_panel(page, "Balcony Door Sensor")   # support_local False: no dp ids to show

    points = data_points(page)

    assert points["battery_percentage"] == ("-", "84", "Integer · read-only · 0–100 %")


def test_panel_degrades_for_a_device_with_no_specs_or_timestamps(page):
    open_panel(page, "Unpaired Relay")

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
    assert cell_texts(page, 1) == [PLUG.name]

    page.fill("#filter", "cur_power")       # a data point code, only in the specs
    assert cell_texts(page, 1) == [PLUG.name]

    page.fill("#filter", "00:10:00")        # local time, rendered in the browser
    assert len(cell_texts(page, 1)) == 3    # every device that has timestamps

    page.fill("#filter", "no-such-device")
    assert page.locator("#rows tr").count() == 0

    page.fill("#filter", "")
    assert page.locator("#rows tr").count() == len(DEVICES)


def test_sorting_by_name_toggles_direction(page):
    page.click("#thead th[data-key='name']")
    ascending = cell_texts(page, 1)
    assert ascending == sorted(ascending, key=str.lower)

    page.click("#thead th[data-key='name']")
    assert cell_texts(page, 1) == list(reversed(ascending))


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
    open_panel(page, PLUG.name)
    page.click("#panelBody details.raw summary")

    dumped = page.locator("#panelBody details.raw pre").inner_text()
    assert MASK in dumped
    assert PLUG.local_key not in dumped

    page.click("#panelBody details.raw button")
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
    before = page.evaluate("devicesCachedAt")
    page.click("#refreshBtn")
    page.wait_for_function("(prev) => devicesCachedAt !== prev", arg=before)

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
    page.wait_for_function("""expected =>
        !lanPollTimer && lanData.job && lanData.job.id === expected.job.id &&
        lanData.job.state === 'done' &&
        JSON.stringify(lanData.summary) === JSON.stringify(expected.summary)
    """, arg=outcome)
    page.wait_for_selector("#lanSummaryNotice")


def row(page, device):
    return page.locator(f"#rows tr:has-text('{device.name}')")


def test_a_scan_fills_the_protocol_column_and_the_status_badges(page, running_app, monkeypatch):
    calls = _scans(running_app, monkeypatch, FIRST_SCAN)

    scanned(page, "192.168.1.0/30")

    assert calls[0]["targets"] == ["192.168.1.1", "192.168.1.2"]
    assert calls[0]["routers"] == {"192.168.1.1"}, "scanned, but taken for the router"
    assert row(page, PLUG).locator("td:nth-child(5)").inner_text() == "3.4"
    assert "3.3" in row(page, LAMP).locator("td:nth-child(5)").inner_text()
    assert "device22" in row(page, LAMP).locator("td:nth-child(5)").inner_text()
    assert row(page, PLUG).locator("td:nth-child(2)").inner_text().split() == ["LAN", "reachable"]
    assert row(page, LAMP).locator("td:nth-child(2)").inner_text().split() == ["LAN", "busy"]
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

    assert row(page, PLUG).locator("td:nth-child(5)").inner_text() == "3.5"
    open_network(page, PLUG.name)
    assert field_value(page, "local_ip") == "192.168.2.62"


def test_a_devices_status_falls_back_to_the_cloud_flag_without_a_scan(page):
    assert row(page, PLUG).locator("td:nth-child(2)").inner_text() == "online"
    assert row(page, PLUG).locator("td:nth-child(5)").inner_text() == "-"


def test_the_status_column_sorts_lan_results_ahead_of_the_cloud_flag(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, {PLUG.id: _lan("ok", "192.168.1.61", "3.4"),
                                      BARE.id: _lan("busy", "192.168.1.9", "3.3")})
    scanned(page)

    page.click("#thead th[data-key='online']")

    names = [n.strip() for n in cell_texts(page, 1)]
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

    badge = row(page, SENSOR).locator("td:nth-child(2) .badge")
    assert "muted" in badge.get_attribute("class")
    assert "reached through" not in page.locator("#lanSummaryNotice").inner_text()
    open_network(page, SENSOR.name)
    assert "which the last scan didn't find" in field_value(page, "lan_status")


def test_a_sub_device_reached_through_its_gateway_reads_as_reachable(page, running_app, monkeypatch):
    reached = dict(FIRST_SCAN, **{SENSOR.id: _lan("via_gateway", "192.168.1.61", "3.4",
                                                  gateway_id=SENSOR.gateway_id)})
    _scans(running_app, monkeypatch, reached)
    scanned(page)

    badge = row(page, SENSOR).locator("td:nth-child(2) .badge")
    assert "on" in badge.get_attribute("class").split()
    assert "plus 1 sub-device reached through its gateway" in page.locator("#lanSummaryNotice").inner_text()


@pytest.mark.parametrize("online, text, cls", [(True, "via gateway", "on"), (False, "offline", "off")])
def test_a_sub_device_reads_as_its_gateway_reports_it(page, running_app, monkeypatch, online, text, cls):
    reported = dict(FIRST_SCAN, **{SENSOR.id: _lan("via_gateway", "192.168.1.61", "3.4",
                                                   gateway_id=SENSOR.gateway_id, sub_online=online)})
    _scans(running_app, monkeypatch, reported, sub_devices_offline=int(not online))
    scanned(page)

    badge = row(page, SENSOR).locator("td:nth-child(2) .badge")
    assert badge.inner_text().split() == ["LAN", *text.split()]
    assert cls in badge.get_attribute("class").split()
    state = "online" if online else "offline"
    assert f"Its gateway reports it {state}." in badge.get_attribute("title")
    assert page.evaluate(f"isOnline(deviceData.find(d => d.id === {SENSOR.id!r}))") is online
    notice = page.locator("#lanSummaryNotice").inner_text()
    assert ("Its gateway reports it offline." in notice) is not online
    open_network(page, SENSOR.name)
    assert f"which reports it {state}" in field_value(page, "lan_status")


def test_an_ip_being_typed_survives_a_repaint(page):
    open_network(page, LAMP.name)
    page.fill("#networkBody [data-lan-check] input", "192.168.1.5")

    page.evaluate("render()")   # what a finished scan or the key toggle does

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
    page.wait_for_function("!lanPollTimer")


def test_one_device_can_be_checked_from_the_panel(page, running_app, monkeypatch):
    calls = _scans(running_app, monkeypatch, {LAMP.id: _lan("ok", "192.168.1.50", "3.5")})
    open_network(page, LAMP.name)

    page.fill("#networkBody [data-lan-check] input", "192.168.1.50")
    page.click("#networkBody [data-lan-check] button")
    page.wait_for_selector("#networkBody :text('Found at 192.168.1.50')")

    assert calls[0]["targets"] == ["192.168.1.50"]
    assert calls[0]["only"] == [LAMP.id], "the whole list goes along, but only the lamp is looked for"
    assert row(page, LAMP).locator("td:nth-child(5)").inner_text() == "3.5"
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
    heading = page.locator("#thead th").nth(3)
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

    cell = row(page, sub).locator("td:nth-child(4)")
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
    open_panel(page, sub.name)

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
        assert child.locator("td:nth-child(5)").inner_text() == "-"
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
    key_cell = row(page, ZIGBEE_GATEWAY).locator("td:nth-child(4)")
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
    open_panel(page, ZIGBEE_GATEWAY.name)
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

    protocol = row(page, VALVE).locator("td:nth-child(5)")
    assert protocol.inner_text() == "-", "a sub-device has no protocol version of its own"
    assert protocol.locator(".dash").get_attribute("title") == \
        "Reached through its gateway, which uses protocol 3.4"
    assert row(page, ZIGBEE_GATEWAY).locator("td:nth-child(5)").inner_text() == "3.4"
    page.click("#thead th[data-key='protocol_version']")
    names = page.locator("#rows tr td:first-child").all_inner_texts()
    assert names[-2].startswith(ZIGBEE_GATEWAY.name)
    assert names[-1].startswith(VALVE.name), "a sub-device stays under its gateway when sorting by protocol"

    open_panel(page, VALVE.name)
    assert field_value(page, "node_id") == VALVE.node_id
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
    }, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
    })
    scanned(page)

    open_network(page, ZIGBEE_GATEWAY.name)
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

    expected_title = f"Local network for “{ZIGBEE_GATEWAY.name}”"
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
        assert row(page, gateway).locator("td:nth-child(2)").inner_text().split() == ["LAN", "check", "needed"]
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
    assert row(page, PLUG).locator("td:nth-child(5)").inner_text() == "3.4", "results come back on load"


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


def test_every_action_fits_in_a_wide_header(page):
    assert in_menu(page) == []
    assert page.locator("#moreWrap").is_hidden()


def test_a_narrow_header_moves_actions_into_the_menu_on_one_line(page):
    one_line = page.locator("header").bounding_box()["height"]

    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!document.getElementById('moreWrap').classList.contains('hidden')")

    assert page.locator("header").bounding_box()["height"] == one_line
    assert "refreshBtn" not in in_menu(page), "Refresh is the last to go"
    assert "logoutBtn" in in_menu(page) and "csvBtn" in in_menu(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    assert page.locator("#moreMenu").is_hidden()

    page.click("#moreBtn")
    assert page.locator("#moreMenu").is_visible()
    assert page.evaluate("document.activeElement.id") == in_menu(page)[0]
    page.click("#moreMenu #scanBtn")

    assert page.locator("#moreMenu").is_hidden()
    assert page.locator("#scanBox").is_visible()

    page.set_viewport_size({"width": 1440, "height": 1000})
    page.wait_for_function("document.getElementById('moreWrap').classList.contains('hidden')")
    assert in_menu(page) == []


def test_the_menu_closes_on_escape_and_on_an_outside_click(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!document.getElementById('moreWrap').classList.contains('hidden')")

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
    page.wait_for_function("!document.getElementById('moreWrap').classList.contains('hidden')")

    assert "logoutBtn" in in_menu(page)


def test_the_menu_button_is_as_tall_as_the_buttons_beside_it(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_function("!document.getElementById('moreWrap').classList.contains('hidden')")

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
    page.wait_for_function("!document.getElementById('moreWrap').classList.contains('hidden')")

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
    assert page.locator("#panelSetupOpen").is_visible()
    assert page.locator("#panelBody .setup-card").count() == 0
    assert page.locator("#setupBody").is_hidden()
    assert page.locator("#panelBack").is_hidden()
    page.locator("#panelBody").evaluate("el => { el.scrollTop = 350; }")
    entry = page.locator("#panelSetupEntry")
    assert entry.evaluate("el => parseFloat(getComputedStyle(el).borderBottomWidth)") == 1
    gap = page.locator("#panelBody").evaluate("""el =>
        el.getBoundingClientRect().top - document.getElementById('panelSetupEntry').getBoundingClientRect().bottom
    """)
    assert gap >= 12

    page.locator("#panelSetupOpen").press("Enter")
    assert page.locator("#panelTitle").inner_text() == f"Set up “{PLUG.name}” in…"
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert page.locator("#panelSub").is_hidden()
    assert page.locator("#panelBody").is_hidden()
    assert page.locator("#setupBody").is_visible()
    assert page.locator("#setupBody dl.fields, #setupBody details.raw").count() == 0
    assert page.locator("[data-setup-tool='tuya-local']").evaluate("el => el === document.activeElement")

    page.keyboard.press("Escape")
    assert page.locator("#panelTitle").inner_text() == PLUG.name
    assert page.locator("#panelClose").bounding_box() == pytest.approx(close_box, abs=0.5)
    assert page.locator("#panelSub").is_visible()
    assert page.locator("#panelBody").is_visible()
    assert page.locator("#setupBody").is_hidden()
    assert page.locator("#panelSetupOpen").evaluate("el => el === document.activeElement")
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
    assert page.locator("#panelTitle").inner_text() == f"Set up “{VALVE.name}” in…"
    assert page.locator("#panelSub").is_hidden()
    assert setup_value(page, "device_cid") == VALVE.node_id
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == f"Set up “{ZIGBEE_GATEWAY.name}” in…"
    expect(page.locator(f".setup-children [data-open-device='{VALVE.id}']")).to_be_focused()
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == ZIGBEE_GATEWAY.name
    assert field_value(page, "id") == ZIGBEE_GATEWAY.id
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
    open_panel(page, PLUG.name)
    page.locator("#panelBody details.raw summary").click()
    page.locator("#panelBody [data-key-toggle]").click()
    assert PLUG.local_key in page.locator("#panelBody details.raw pre").inner_text()
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
    assert MASK in field_value(page, "local_key")
    assert PLUG.local_key not in page.locator("#panelBody details.raw pre").inner_text()
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
    open_panel(page, PLUG.name)
    page.locator("#panelBody details.data-points summary").click()
    page.locator("#panelBody details.raw summary").click()
    scroll = page.locator("#panelBody").evaluate("el => { el.scrollTop = 350; return el.scrollTop; }")
    page.locator("#panelSetupOpen").click()
    page.locator("[data-setup-tool='tuya-local']").focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("[data-setup-tool='localtuya']").evaluate("el => el === document.activeElement")
    page.locator("[data-setup-advanced] summary").click()
    page.locator("#thead [data-key-toggle]").click()
    assert page.locator("[data-setup-tool='localtuya']").get_attribute("aria-selected") == "true"
    assert page.locator("[data-setup-advanced]").evaluate("el => el.open")
    page.locator("#panelBack").click()
    assert page.locator("#panelBody details.data-points").evaluate("el => el.open")
    assert page.locator("#panelBody details.raw").evaluate("el => el.open")
    assert page.locator("#panelBody").evaluate("el => el.scrollTop") == scroll
    assert page.locator("#panelSetupOpen").evaluate("el => el === document.activeElement")
    page.locator("#panelSetupOpen").click()
    assert page.locator("[data-setup-advanced]").evaluate("el => el.open")
    page.locator("[data-setup-tool='localtuya']").focus()
    page.keyboard.press("End")
    assert page.locator("[data-setup-tool='tinytuya']").evaluate("el => el === document.activeElement")
    page.keyboard.press("Home")
    assert page.locator("[data-setup-tool='tuya-local']").evaluate("el => el === document.activeElement")


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
    assert title.text_content() == f"Set up “{sub.name}” in…"
    assert title.get_attribute("title") == title.text_content()
    assert title.evaluate("el => el.clientHeight <= 2 * parseFloat(getComputedStyle(el).lineHeight) + 1")
    assert title.evaluate("el => el.clientHeight > parseFloat(getComputedStyle(el).lineHeight)")
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
    assert page.locator("#panelTitle").inner_text() == f"Local network for “{PLUG.name}”"
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
    page.wait_for_selector("#networkBody .lan-note:text('Found at 192.168.2.8')")

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
    page.wait_for_function("panelCheck && panelCheck.kind === 'error' && !lanPollTimer")
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
    assert page.locator("#panelTitle").inner_text() == f"Local network for “{device.name}”"
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
    page.evaluate("render()")
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
        PLUG.id: _lan("ok", "192.168.2.61", "3.4"),
    }, {
        PLUG.id: _lan("ok", "192.168.2.62", "3.5"),
    })
    scanned(page)
    open_network(page, PLUG.name)
    ip_input = page.locator("#networkBody [data-lan-check] input")
    assert ip_input.evaluate("el => el.value === el.defaultValue")
    ip_input.evaluate("el => { el.focus(); el.setSelectionRange(2, 9); }")

    page.evaluate("render()")

    expect(ip_input).to_be_focused()
    assert ip_input.evaluate("el => [el.selectionStart, el.selectionEnd]") == [2, 9]
    scanned(page)
    assert ip_input.input_value() == "192.168.2.62"
    assert ip_input.evaluate("el => el.defaultValue") == "192.168.2.62"
    assert field_value(page, "protocol_version") == "3.5"


def test_network_scan_action_opens_the_actual_network_scan_form(page):
    open_network(page, PLUG.name)
    page.locator("#networkBody [data-network-action='scan']").click()
    assert page.locator("#panel").get_attribute("aria-hidden") == "true"
    assert page.locator("#scanBox").is_visible()
    expect(page.locator("#scanTargets")).to_be_focused()


def test_setup_network_history_returns_to_setup_then_details_with_their_state(page):
    page.set_viewport_size({"width": 390, "height": 640})
    open_panel(page, PLUG.name)
    page.locator("#panelBody details.data-points summary").click()
    page.locator("#panelBody details.raw summary").click()
    details_scroll = page.locator("#panelBody").evaluate("el => { el.scrollTop = 350; return el.scrollTop; }")
    assert details_scroll > 0
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

    assert page.locator("#panelTitle").inner_text() == f"Set up “{PLUG.name}” in…"
    assert page.locator("#setupBody [data-setup-tool='localtuya']").get_attribute("aria-selected") == "true"
    assert page.locator("#setupBody [data-localtuya-edition]").input_value() == "original"
    assert page.locator("#setupBody [data-setup-advanced]").evaluate("el => el.open")
    expect(manual_ip).to_be_focused()
    assert page.locator("#panelBack").inner_text() == "Back to device details"
    page.keyboard.press("Escape")

    assert page.locator("#panelTitle").inner_text() == PLUG.name
    assert page.locator("#panelBody details.data-points").evaluate("el => el.open")
    assert page.locator("#panelBody details.raw").evaluate("el => el.open")
    assert page.locator("#panelBody").evaluate("el => el.scrollTop") == pytest.approx(details_scroll, abs=1)
    expect(page.locator("#panelSetupOpen")).to_be_focused()
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
    assert page.locator("#panelTitle").inner_text() == f"Set up “{sub.name}” in…"
    setup_tool(page, "tinytuya")
    page.locator("#panelBack").click()

    assert page.locator("#panelTitle").inner_text() == f"Set up “{ZIGBEE_GATEWAY.name}” in…"
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
    assert page.locator("#panelTitle").inner_text() == f"Local network for “{ZIGBEE_GATEWAY.name}”"
    assert page.locator("#panelBack").inner_text() == "Back to local network"
    ip_input.fill("192.168.2.246")
    page.keyboard.press("Escape")

    assert page.locator("#panelTitle").inner_text() == f"Local network for “{sub.name}”"
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
    assert page.locator("#panelBack").inner_text() == "Back to device details"
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

    assert page.locator("#panelTitle").inner_text() == f"Set up “{sub.name}” in…"
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

    assert page.locator("#panelTitle").inner_text() == f"Set up “{ZIGBEE_GATEWAY.name}” in…"
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
    page.evaluate("""() => {
        const body = document.createElement('div');
        body.id = 'panelExtraTestBody';
        body.className = 'panel-body hidden';
        body.tabIndex = -1;
        document.getElementById('panel').append(body);
        PANEL_PAGES.extra_test = {
            body: body.id,
            backLabel: 'Back to additional setup',
            title: d => `Additional setup for “${d.name}”`,
            render: () => '<details><summary>Additional options</summary>' +
                '<div style="height:900px">Extra preferences</div></details>' +
                '<button type="button" data-setup-action="network">Local network</button>',
            focus: 'summary',
        };
        const opener = document.querySelector('#setupBody [data-setup-action="network"]');
        opener.addEventListener('click', event => {
            event.stopPropagation();
            navigatePanel(selectedId, 'extra_test', opener);
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
    assert page.locator("#panelTitle").inner_text() == f"Set up “{PLUG.name}” in…"
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
    assert title.inner_text() == f"Local network for “{sub.name}”"
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
