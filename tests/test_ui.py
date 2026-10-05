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
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
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
    loaded.goto(server, wait_until="networkidle")
    loaded.wait_for_selector("#rows tr")
    try:
        yield loaded
    finally:
        if SHOTS_ENABLED:
            record_shot(loaded, gallery, request.node)
        context.close()


def record_shot(page, gallery, node):
    """Screenshot the page as the test leaves it, pass or fail."""
    report = getattr(node, "rep_call", None)
    status = "fail" if report is None or report.failed else "pass"
    name = f"{len(gallery) + 1:02d}-{status}-{node.name}.png"
    try:
        page.screenshot(path=SHOTS_DIR / name, full_page=True)
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


def field_value(page, key):
    """The panel's rendered value for a device field, addressed by its raw name."""
    return page.locator(f"#panelBody dt[title='{key}'] + dd").inner_text()


def clipboard(page):
    return page.evaluate("navigator.clipboard.readText()")


def test_table_shows_the_scan_columns_only(page):
    headers = page.locator("#thead th").all_inner_texts()

    assert [h.strip() for h in headers] == [
        "Name", "Status", "ID", "Local Key", "Protocol", "Product ID", "Product Name",
        "Update Time", "Details",
    ]
    assert page.locator("#rows tr").count() == len(DEVICES)
    # Fields that moved into the panel are not duplicated in the table.
    body = page.locator("#rows").inner_text()
    for moved in (PLUG.uuid, PLUG.category, PLUG.ip):
        assert moved not in body


def test_local_keys_are_masked_until_the_toggle_reveals_them(page):
    keys = page.locator("#rows .key")
    assert MASK in keys.first.inner_text()
    assert PLUG.local_key not in page.locator("#rows").inner_text()

    page.click("#thead [data-key-toggle]")
    assert PLUG.local_key in page.locator("#rows").inner_text()

    # The panel shares the one toggle, in both directions.
    open_panel(page, PLUG.name)
    assert PLUG.local_key in field_value(page, "local_key")
    page.click("#panelBody [data-key-toggle]")
    assert MASK in field_value(page, "local_key")
    assert PLUG.local_key not in page.locator("#rows").inner_text()


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

    points = data_points(page)

    assert "DATA POINTS (2)" in page.locator("#panelBody").inner_text()
    # dp id comes from local_strategy, the value from status, the rest from the spec.
    assert points["cur_power"] == ("19", "812", "Integer · read-only · 0–50000 W · scale 1 · minux")
    assert points["switch_1"] == ("1", "true", "Boolean · read/write")


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
    with app._devices_cache_lock:
        app._devices_cache = None
        app._devices_cache_loaded = True
    app._clear_lan()
    app.device_cache.clear(app.DEVICE_CACHE_FILE, app.DEVICE_CACHE_KEY_FILE, app.LAN_CACHE_FILE)


@pytest.fixture()
def running_app(server):
    """The app behind `server`, with its cached list reset when the test ends."""
    yield APP
    _forget_devices(APP)


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
    page.click("#refreshBtn")
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


def _lan(status, ip=None, version=None, device22=False, **extra):
    return dict(status=status, ip=ip, version=version, device22=device22,
                checked_at=1_752_000_000, **extra)


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
    page.click("#scanStart")
    page.wait_for_selector("#lanSummaryNotice")
    page.wait_for_function("!lanPollTimer")


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

    open_panel(page, LAMP.name)

    assert field_value(page, "local_ip") == "192.168.1.42"
    assert "3.3" in field_value(page, "protocol_version")
    assert "3.22" in field_value(page, "protocol_version"), "tuya-local's name for the quirk"
    assert "busy" in field_value(page, "lan_status")
    assert "another local client" in field_value(page, "lan_status")
    assert page.locator("#panelBody [data-lan-check] input").input_value() == "192.168.1.42"


def test_a_sub_device_points_to_its_gateway_instead_of_a_check(page):
    open_panel(page, SENSOR.name)

    body = page.locator("#panelBody").inner_text()
    assert "reached through its gateway" in body
    assert "check it at an IP address below" not in body, "there is no check below to point to"
    assert page.locator("#panelBody [data-lan-check]").count() == 0


def test_a_device_without_a_local_key_offers_no_check(page):
    open_panel(page, BARE.name)   # Tuya returned no local_key for it

    body = page.locator("#panelBody").inner_text()
    assert "doesn't return a local key" in body
    assert "check it at an IP address below" not in body
    assert page.locator("#panelBody [data-lan-check]").count() == 0


def test_a_sub_device_whose_gateway_wasnt_found_is_greyed_out(page, running_app, monkeypatch):
    _scans(running_app, monkeypatch, FIRST_SCAN)   # the sensor's gateway: not reached
    scanned(page)

    badge = row(page, SENSOR).locator("td:nth-child(2) .badge")
    assert "muted" in badge.get_attribute("class")
    assert "reached through" not in page.locator("#lanSummaryNotice").inner_text()
    open_panel(page, SENSOR.name)
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
    open_panel(page, SENSOR.name)
    assert f"which reports it {state}" in field_value(page, "lan_status")


def test_an_ip_being_typed_survives_a_repaint(page):
    open_panel(page, LAMP.name)
    page.fill("#panelBody [data-lan-check] input", "192.168.1.5")

    page.evaluate("render()")   # what a finished scan or the key toggle does

    assert page.locator("#panelBody [data-lan-check] input").input_value() == "192.168.1.5"
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
    open_panel(page, LAMP.name)

    page.fill("#panelBody [data-lan-check] input", "192.168.1.50")
    page.click("#panelBody [data-lan-check] button")
    page.wait_for_selector("#panelBody :text('Found at 192.168.1.50')")

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
    page.fill("#filter", PLUG.name)
    assert footnote.is_hidden(), "only while a key it explains is listed"
    page.fill("#filter", "")
    open_panel(page, ZIGBEE_GATEWAY.name)
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
    assert names[-1].startswith(ZIGBEE_GATEWAY.name), "the valve sorts with the devices that have none"

    open_panel(page, VALVE.name)
    labels = page.locator("#panelBody dl.fields dt").all_inner_texts()
    assert "Gateway IP" in labels and "Gateway protocol" in labels
    assert "Local IP" not in labels and "Protocol version" not in labels
    assert field_value(page, "protocol_version") == "3.4"

    with page.expect_download() as download:
        page.click("#csvBtn")
    path = tmp_path / "devices.csv"
    download.value.save_as(path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    assert rows[VALVE.id]["protocol_version"] == "3.4", "scripts still get the gateway's version"
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

    open_panel(page, ZIGBEE_GATEWAY.name)
    page.fill("#panelBody [data-lan-check] input", "192.168.2.8")
    page.click("#panelBody [data-lan-check] button")
    page.wait_for_selector("#panelBody :text('Found at 192.168.2.8')")

    note = page.locator("#panelBody .lan-note").last.inner_text()
    assert "It answered to the local key Tuya lists on Garden Valve." in note
    assert "Before, it had the one on Water Timer." in note
    assert "Mesh Gateway had that key, so check it again at its own IP." in note


def test_a_keyless_gateway_says_how_to_find_its_key(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, BLE_GATEWAY, VALVE, WATER_TIMER)
    _scans(running_app, monkeypatch, {
        BLE_GATEWAY.id: _lan("not_found", "192.168.2.7"),
    })

    open_panel(page, BLE_GATEWAY.name)
    body = page.locator("#panelBody").inner_text()
    assert "Tuya lists this gateway's local key on its sub-devices" in body
    assert "Use Scan network, or check it at its IP" in body
    page.fill("#panelBody [data-lan-check] input", "192.168.2.7")
    page.click("#panelBody [data-lan-check] button")

    page.wait_for_selector("#panelBody :text('none of the keys Tuya lists on sub-devices answered there')")


def test_a_sub_device_links_to_the_gateway_the_scan_found_it_through(page, running_app, monkeypatch):
    _with_gateways(page, running_app, monkeypatch, ZIGBEE_GATEWAY, VALVE)
    _scans(running_app, monkeypatch, {
        ZIGBEE_GATEWAY.id: _lan("ok", "192.168.2.8", "3.4", key_from=VALVE.id),
        VALVE.id: _lan("via_gateway", "192.168.2.8", "3.4", gateway_id=ZIGBEE_GATEWAY.id),
    })
    scanned(page)

    open_panel(page, VALVE.name)
    assert "reached through its gateway: Zigbee Gateway" in page.locator("#panelBody").inner_text()
    page.click(f"#panelBody [data-open-device='{ZIGBEE_GATEWAY.id}']")

    page.wait_for_function(f"document.querySelector('#panelTitle').innerText === {ZIGBEE_GATEWAY.name!r}")


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

    found = "192.168.2.8 (Garden Valve), 192.168.2.9 (Water Timer)"
    notice = page.locator("#lanSummaryNotice").inner_text()
    assert f"2 gateways answered to local keys Tuya lists on sub-devices: {found}." in notice
    for gateway in (ZIGBEE_GATEWAY, BLE_GATEWAY):
        assert row(page, gateway).locator("td:nth-child(2)").inner_text().split() == ["LAN", "check", "needed"]
    open_panel(page, VALVE.name)
    assert "which answered at 192.168.2.8 to this device's key" in page.locator("#panelBody").inner_text()
    open_panel(page, BLE_GATEWAY.name)
    body = page.locator("#panelBody").inner_text()
    assert "Tuya lists this gateway's local key on its sub-devices" in body
    assert "When that leaves one gateway, it's checked too." in body

    page.click("#panelBody [data-check-ip='192.168.2.9']")
    page.wait_for_selector("#panelBody :text('Found at 192.168.2.9')")

    assert (calls[1]["targets"], calls[1]["only"]) == (["192.168.2.9"], [BLE_GATEWAY.id])
    assert (calls[2]["targets"], calls[2]["only"]) == (["192.168.2.8"], [ZIGBEE_GATEWAY.id])
    message = page.locator("#panelBody .lan-note").last.inner_text()
    assert "It answered to the local key Tuya lists on Water Timer." in message
    assert "That left one gateway, so Zigbee Gateway was checked too: found at 192.168.2.8." in message
    page.click("#thead [data-key-toggle]")
    assert WATER_TIMER.local_key in row(page, BLE_GATEWAY).inner_text()
    assert VALVE.local_key in row(page, ZIGBEE_GATEWAY).inner_text()
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
