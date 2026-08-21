import csv
import json
import sys
import stat
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import tuya_devices as core


def test_session_round_trip_uses_owner_only_permissions(tmp_path):
    path = tmp_path / "session.json"
    session = {
        "user_code": "demo",
        "terminal_id": "terminal",
        "endpoint": "https://example.test",
        "token_info": {"access_token": "demo-token"},
    }

    core.save_session(path, session)

    assert core.load_session(path) == session
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_session_returns_none_for_missing_or_invalid_json(tmp_path):
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")

    assert core.load_session(missing) is None
    assert core.load_session(invalid) is None


def test_save_session_ignores_chmod_errors(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    monkeypatch.setattr(core.os, "chmod", lambda *args: (_ for _ in ()).throw(OSError))

    core.save_session(path, {"token_info": {}})

    assert core.load_session(path) == {"token_info": {}}


def test_save_session_is_atomic_and_leaves_original_on_error(tmp_path):
    path = tmp_path / "session.json"
    core.save_session(path, {"token_info": {"access_token": "good"}})

    class Unserializable:
        pass

    try:
        core.save_session(path, {"token_info": Unserializable()})
    except TypeError:
        pass
    else:
        raise AssertionError("expected a serialization error")

    # The good session survived and no temp file was left behind.
    assert core.load_session(path) == {"token_info": {"access_token": "good"}}
    assert [p.name for p in tmp_path.iterdir()] == ["session.json"]


def test_concurrent_session_saves_never_corrupt(tmp_path):
    path = tmp_path / "session.json"
    core.save_session(path, {"token_info": {"access_token": "seed"}})
    errors = []

    def writer(n):
        try:
            for i in range(25):
                core.save_session(path, {"token_info": {"access_token": f"{n}-{i}"}})
                if core.load_session(path) is None:
                    raise AssertionError("torn read: session file was incomplete")
        except Exception as exc:  # surface any thread failure to the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert errors == []
    assert core.load_session(path) is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_session_saver_updates_token_fields(tmp_path):
    path = tmp_path / "session.json"
    session = {"token_info": {"access_token": "old"}}
    saver = core._SessionSaver(path, session)

    saver.update_token({
        "t": 123,
        "uid": "user",
        "expire_time": 3600,
        "access_token": "access",
        "refresh_token": "refresh",
        "ignored": "value",
    })

    assert session["token_info"] == {
        "t": 123,
        "uid": "user",
        "expire_time": 3600,
        "access_token": "access",
        "refresh_token": "refresh",
    }
    assert core.load_session(path)["token_info"] == session["token_info"]


def test_qr_helpers_create_png_files(tmp_path, capsys):
    png_path = tmp_path / "login.png"

    core.render_qr("smartlife--qrLogin?token=demo", png_path)
    png_bytes = core.qr_png_bytes("smartlife--qrLogin?token=demo")

    assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert "Saved" in capsys.readouterr().out


def test_render_qr_ignores_png_save_errors(monkeypatch, tmp_path):
    class FakeQRCode:
        def __init__(self, border):
            self.border = border

        def add_data(self, content):
            self.content = content

        def make(self, fit):
            self.fit = fit

        def print_ascii(self, invert):
            self.invert = invert

        def make_image(self):
            return SimpleNamespace(save=lambda path: (_ for _ in ()).throw(OSError))

    monkeypatch.setitem(sys.modules, "qrcode", SimpleNamespace(QRCode=FakeQRCode))

    core.render_qr("smartlife--qrLogin?token=demo", tmp_path / "login.png")


def test_mint_qr_token_success_and_failure(monkeypatch):
    class FakeLoginControl:
        def __init__(self):
            self.calls = []

        def qr_code(self, client_id, schema, user_code):
            self.calls.append((client_id, schema, user_code))
            if user_code == "bad-user":
                return {"success": False, "code": "1001", "msg": "bad user"}
            return {"success": True, "result": {"qrcode": "demo-token"}}

    monkeypatch.setitem(sys.modules, "tuya_sharing", SimpleNamespace(LoginControl=FakeLoginControl))

    assert core.mint_qr_token("good-user") == "demo-token"
    try:
        core.mint_qr_token("bad-user")
    except core.LoginError as exc:
        assert "1001" in str(exc)
        assert "bad user" in str(exc)
    else:
        raise AssertionError("LoginError was not raised")


def test_poll_login_returns_session_or_none(monkeypatch):
    class FakeLoginControl:
        def login_result(self, token, client_id, user_code):
            if token == "confirmed":
                return True, {
                    "terminal_id": "terminal",
                    "end_point": "https://example.test",
                    "t": 123,
                    "uid": "user",
                    "expire_time": 3600,
                    "access_token": "access",
                    "refresh_token": "refresh",
                }
            if token == "error":
                raise RuntimeError("network")
            return False, {}

    monkeypatch.setitem(sys.modules, "tuya_sharing", SimpleNamespace(LoginControl=FakeLoginControl))

    session = core.poll_login("confirmed", "user-code")

    assert session["user_code"] == "user-code"
    assert session["terminal_id"] == "terminal"
    assert session["endpoint"] == "https://example.test"
    assert session["token_info"]["access_token"] == "access"
    assert core.poll_login("pending", "user-code") is None
    assert core.poll_login("error", "user-code") is None


def test_web_dict_formats_times_and_preserves_device_fields():
    device = SimpleNamespace(
        name="Living Room Lamp",
        id="d7ddc303490bb07ca5rqmj",
        uuid="4e1ea52584f6a774",
        local_key="5vps+n4FwxR2?df;",
        product_id="ofxioj0ypuygidrs",
        product_name="Wi-Fi Smart Bulb",
        category="dj",
        ip="192.168.1.42",
        online=1,
        sub=False,
        active_time=1_720_000_000,
        update_time=1_720_000_000_000,
        create_time=None,
        time_zone="+05:30",
    )

    data = core.web_dict(device)

    assert data["id"] == "d7ddc303490bb07ca5rqmj"
    assert data["uuid"] == "4e1ea52584f6a774"
    assert data["local_key"] == "5vps+n4FwxR2?df;"
    assert data["product_id"] == "ofxioj0ypuygidrs"
    assert data["online"] is True
    assert data["active_time"] != "-"
    assert data["update_time"] != "-"
    assert data["create_time"] == "-"


def demo_device(**overrides):
    """A device shaped like tuya_sharing.CustomerDevice after a device-list fetch."""
    device = SimpleNamespace(
        name="Kitchen Plug",
        id="d7ddc303490bb07ca5rqmj",
        uuid="4e1ea52584f6a774",
        local_key="5vps+n4FwxR2?df;",
        product_id="ofxioj0ypuygidrs",
        product_name="Energy Monitoring Plug",
        category="cz",
        ip="192.168.1.61",
        online=True,
        sub=False,
        support_local=True,
        time_zone="+05:30",
        asset_id="",
        active_time=1_720_000_000,
        create_time=1_690_000_000,
        update_time=1_752_000_000,
        # Tuya returns fields the SDK does not declare; they must survive too.
        protocol_version="3.3",
        status={"switch_1": True, "cur_power": 812,
                "cycle_time": [{"start": "0800"}, {"start": "2000"}]},
        function={"switch_1": SimpleNamespace(code="switch_1", type="Boolean", values="{}")},
        status_range={"cur_power": SimpleNamespace(code="cur_power", type="Integer",
                                                  values='{"unit":"W"}', report_type="minux")},
        local_strategy={1: {"status_code": "switch_1", "config_item": {"pid": "ofxioj0ypuygidrs"}}},
    )
    for key, value in overrides.items():
        setattr(device, key, value)
    return device


def test_device_dict_exposes_specs_and_undeclared_fields():
    data = core.device_dict(demo_device())

    # Known scalars keep their display order, and the spec maps come last.
    assert list(data)[:6] == ["name", "id", "uuid", "local_key", "product_id", "product_name"]
    assert list(data)[-4:] == ["status", "function", "status_range", "local_strategy"]
    assert data["protocol_version"] == "3.3"
    assert data["status"]["cur_power"] == 812
    assert data["status"]["cycle_time"] == [{"start": "0800"}, {"start": "2000"}]
    # Namespaces flatten to dicts and int dp ids become JSON-safe strings.
    assert data["function"]["switch_1"] == {"code": "switch_1", "type": "Boolean", "values": "{}"}
    assert data["status_range"]["cur_power"]["report_type"] == "minux"
    assert data["local_strategy"]["1"]["status_code"] == "switch_1"
    assert json.dumps(data)  # nothing left that json cannot serialize


def test_device_dict_skips_missing_fields_and_empty_maps():
    data = core.device_dict(SimpleNamespace(id="sparse-1", name="Unpaired Relay",
                                            online=False, status={}, function={}))

    assert data == {"name": "Unpaired Relay", "id": "sparse-1", "online": False}


def test_web_dict_humanizes_times_and_keeps_raw_epochs():
    data = core.web_dict(demo_device(online=1))

    assert data["active_time"] == core.fmt_time(1_720_000_000)
    assert data["epochs"] == {
        "active_time": 1_720_000_000,
        "create_time": 1_690_000_000,
        "update_time": 1_752_000_000,
    }
    assert data["online"] is True
    assert data["status"]["switch_1"] is True


def test_export_csv_covers_every_flat_field(tmp_path):
    path = tmp_path / "devices.csv"
    other = SimpleNamespace(id="lamp-1", name="Lamp", online=False, room_name="Study")

    core.export_csv([demo_device(), other], path)

    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["local_key"] == "5vps+n4FwxR2?df;"
    assert rows[0]["protocol_version"] == "3.3"
    assert rows[0]["update_time"] == core.fmt_time(1_752_000_000)
    # A column only the second device has still gets a header, and the maps do not.
    assert rows[1]["room_name"] == "Study"
    assert rows[0]["room_name"] == ""
    assert not {"status", "function", "local_strategy"} & set(rows[0])


def test_print_devices_lists_fields_and_counts_spec_maps(capsys):
    core.print_devices([demo_device()])

    out = capsys.readouterr().out
    assert "protocol_version: 3.3" in out
    assert "asset_id      : -" in out            # empty values read as "-"
    assert "status        : 3 entries" in out    # maps are counted; --json has the detail
    assert "function      : 1 entry" in out
    assert f"update_time   : {core.fmt_time(1_752_000_000)}" in out


def test_fmt_time_handles_invalid_values():
    assert core.fmt_time(None) == "-"
    assert core.fmt_time("not-a-time") == "not-a-time"


def test_fmt_time_renders_utc_and_survives_out_of_range():
    expected = datetime.fromtimestamp(1_720_000_000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    assert core.fmt_time(1_720_000_000) == expected
    assert expected.endswith(" UTC")
    # Milliseconds normalize to the same instant.
    assert core.fmt_time(1_720_000_000_000) == expected
    # Int-parseable but out-of-range degrades to the raw value, never raises
    # (M2: one bad device timestamp must not 500 the whole list).
    assert core.fmt_time(10**20) == "100000000000000000000"


def test_apply_request_timeout_bumps_sdk_default():
    core._apply_request_timeout()
    import tuya_sharing.customerapi as customerapi

    assert customerapi.DEFAULT_TIMEOUT == core.REQUEST_TIMEOUT_SECONDS == 60


def test_print_devices_outputs_summary_and_fields(capsys):
    devices = [
        SimpleNamespace(
            name="Demo Plug",
            id="device-id",
            uuid="uuid",
            local_key="local-key",
            product_id="product-id",
            product_name="Plug",
            category="cz",
            ip="192.168.1.10",
            online=True,
            update_time=1_720_000_000,
            active_time=0,
        ),
        SimpleNamespace(name="Offline Lamp", id="lamp-id", online=False),
    ]

    core.print_devices(devices)

    out = capsys.readouterr().out
    assert "Found 2 device(s)" in out
    assert "1 online, 1 offline" in out
    assert "Demo Plug" in out
    assert "local-key" in out
    assert "Offline Lamp" in out


def test_export_csv_quotes_special_local_key(tmp_path):
    path = tmp_path / "devices.csv"
    device = SimpleNamespace(
        name="Demo Plug",
        id="d7ddc303490bb07ca5rqmj",
        uuid="4e1ea52584f6a774",
        local_key='5vps+n4FwxR2?df;"',
        product_id="ofxioj0ypuygidrs",
        product_name="Energy Monitoring Plug",
        category="cz",
        ip="192.168.1.61",
        online=True,
        sub=False,
        active_time=0,
        update_time=0,
        create_time=0,
        time_zone="+05:30",
    )

    core.export_csv([device], path)

    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["local_key"] == '5vps+n4FwxR2?df;"'


def test_parse_args_defaults_to_smartlife_scheme():
    assert core.parse_args([]).scheme == "smartlife"


def test_build_manager_passes_session_to_sdk(monkeypatch, tmp_path):
    calls = []

    class FakeManager:
        def __init__(self, *args):
            calls.append(args)

    monkeypatch.setitem(sys.modules, "tuya_sharing", SimpleNamespace(Manager=FakeManager))
    session = {
        "client_id": "client",
        "user_code": "user-code",
        "terminal_id": "terminal",
        "endpoint": "https://example.test",
        "token_info": {"access_token": "token"},
    }

    manager = core.build_manager(session, tmp_path / "session.json")

    assert isinstance(manager, FakeManager)
    assert calls[0][:5] == (
        "client",
        "user-code",
        "terminal",
        "https://example.test",
        {"access_token": "token"},
    )
    assert isinstance(calls[0][5], core._SessionSaver)


def test_devices_from_session_refreshes_manager_cache(monkeypatch):
    devices = [SimpleNamespace(id="device-1")]
    manager = SimpleNamespace(
        device_map={"device-1": devices[0]},
        update_device_cache=lambda: None,
    )
    monkeypatch.setattr(core, "build_manager", lambda session, path: manager)

    assert core.devices_from_session({"token_info": {}}, "session.json") == devices


def test_get_devices_reuses_session_or_reauthenticates(tmp_path, monkeypatch, capsys):
    session_path = tmp_path / "session.json"
    cached_session = {"token_info": {"access_token": "cached"}}
    core.save_session(session_path, cached_session)
    devices = [SimpleNamespace(id="device-1")]
    calls = []

    def fake_devices_from_session(session, path):
        calls.append((session, path))
        if session == cached_session and len(calls) == 2:
            raise RuntimeError("expired")
        return devices

    monkeypatch.setattr(core, "devices_from_session", fake_devices_from_session)
    monkeypatch.setattr(core, "qr_login", lambda user_code, scheme, qr_png: {"token_info": {"access_token": "new"}})

    args = SimpleNamespace(relogin=False, session=str(session_path), user_code="user", scheme="smartlife", qr_png="qr.png")
    assert core.get_devices(args) == devices
    assert calls[-1] == (cached_session, str(session_path))

    assert core.get_devices(args) == devices
    assert "Saved session unusable" in capsys.readouterr().out
    assert core.load_session(session_path)["token_info"]["access_token"] == "new"

    args.relogin = True
    assert core.get_devices(args) == devices


def test_qr_login_success_and_timeout(monkeypatch, tmp_path, capsys):
    current_time = [0]
    sessions = [None, {"token_info": {"access_token": "token"}}]
    monkeypatch.setattr(core, "mint_qr_token", lambda user_code: "qr-token")
    monkeypatch.setattr(core, "render_qr", lambda content, path: None)
    monkeypatch.setattr(core, "poll_login", lambda token, user_code: sessions.pop(0))
    monkeypatch.setattr(core.time, "time", lambda: current_time[0])
    monkeypatch.setattr(core.time, "sleep", lambda seconds: current_time.__setitem__(0, current_time[0] + seconds))

    session = core.qr_login("user-code", "smartlife", tmp_path / "qr.png", poll_seconds=5)

    assert session["token_info"]["access_token"] == "token"
    assert "Logged in." in capsys.readouterr().out

    monkeypatch.setattr(core, "poll_login", lambda token, user_code: None)
    current_time[0] = 0
    try:
        core.qr_login("user-code", "smartlife", tmp_path / "qr.png", poll_seconds=1)
    except SystemExit as exc:
        assert "Login timed out" in str(exc)
    else:
        raise AssertionError("SystemExit was not raised")


def test_qr_login_validates_user_code_and_login_errors(monkeypatch):
    monkeypatch.setattr(core, "mint_qr_token", lambda user_code: (_ for _ in ()).throw(core.LoginError("bad code")))

    monkeypatch.setattr("builtins.input", lambda prompt: "")
    try:
        core.qr_login("", "smartlife", "qr.png")
    except SystemExit as exc:
        assert "A user code is required" in str(exc)
    else:
        raise AssertionError("SystemExit was not raised")

    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(OSError))

    try:
        core.qr_login("", "smartlife", "qr.png")
    except OSError:
        pass
    else:
        raise AssertionError("input should have failed under captured stdin")

    try:
        core.qr_login("bad-user", "smartlife", "qr.png")
    except SystemExit as exc:
        assert "Double-check the user code" in str(exc)
    else:
        raise AssertionError("SystemExit was not raised")


def test_main_outputs_json_csv_and_empty_message(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "devices.csv"
    device = SimpleNamespace(
        name="Demo Plug",
        id="device-id",
        uuid="uuid",
        local_key="local-key",
        product_id="product-id",
        product_name="Plug",
        category="cz",
        ip="192.168.1.10",
        online=True,
        sub=False,
        active_time=0,
        update_time=0,
        create_time=0,
        time_zone="UTC",
    )
    monkeypatch.setattr(core, "get_devices", lambda args: [device])

    core.main(["--json", "--csv", str(csv_path)])

    out = capsys.readouterr().out
    assert '"name": "Demo Plug"' in out
    assert "Wrote 1 row(s)" in out
    assert csv_path.exists()

    monkeypatch.setattr(core, "get_devices", lambda args: [])
    core.main([])

    assert "No devices found" in capsys.readouterr().out


def test_logout_removes_cached_session(tmp_path, monkeypatch, capsys):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"token_info": {}}), encoding="utf-8")
    monkeypatch.setattr(core, "get_devices", lambda args: [])

    core.main(["--logout", "--session", str(session)])

    assert not session.exists()
    assert "Removed" in capsys.readouterr().out


def test_logout_reports_missing_cached_session(tmp_path, capsys):
    core.main(["--logout", "--session", str(tmp_path / "missing.json")])

    assert "No cached session to remove." in capsys.readouterr().out
