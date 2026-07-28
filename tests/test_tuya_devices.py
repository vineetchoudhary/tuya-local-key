import csv
import json
import stat
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


def test_logout_removes_cached_session(tmp_path, monkeypatch, capsys):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"token_info": {}}), encoding="utf-8")
    monkeypatch.setattr(core, "get_devices", lambda args: [])

    core.main(["--logout", "--session", str(session)])

    assert not session.exists()
    assert "Removed" in capsys.readouterr().out
