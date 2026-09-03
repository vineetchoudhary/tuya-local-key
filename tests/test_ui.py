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
        "Name", "Status", "ID", "Local Key", "Product ID", "Product Name",
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
    assert field_value(page, "asset_id") == "—"          # empty string, not "None"
    assert page.locator("#rows tr.selected").count() == 1


def test_panel_surfaces_fields_the_sdk_does_not_document(page):
    open_panel(page, "Balcony Door Sensor")

    assert "OTHER FIELDS" in page.locator("#panelBody").inner_text().upper()
    assert field_value(page, "protocol_version") == "3.3"
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
    assert "2025-07-09 00:10:00" in cell_texts(page, 7)[0]


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

    assert points["battery_percentage"] == ("—", "84", "Integer · read-only · 0–100 %")


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
    app.device_cache.clear(app.DEVICE_CACHE_FILE, app.DEVICE_CACHE_KEY_FILE)


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
    assert f"1 device added — Hallway Sensor" in notice
    assert f"1 device removed — {dropped.name}" in notice
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
