"""Fresh observations, account transitions, and history HTTP contracts."""

import copy
import importlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


SESSION = {"client_id": "history-test", "user_code": "account-a", "terminal_id": "terminal-a",
           "endpoint": "https://example.test", "token_info": {"access_token": "secret-token"}}
KEY = "secret-key-00001"


@pytest.fixture()
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.setenv("HASS_OPTIONS_FILE", str(tmp_path / "no-options.json"))
    monkeypatch.setenv("CHECK_FOR_UPDATES", "false")
    monkeypatch.setenv("DEVICE_CACHE", "on")
    monkeypatch.setenv("CHANGE_HISTORY", "on")
    import app
    module = importlib.reload(app)
    module.app.config.update(TESTING=True)
    module.core.save_session(module.SESSION_FILE, copy.deepcopy(SESSION))
    state = {"devices": [SimpleNamespace(id="lamp", name="Lamp", local_key=KEY, category="cz")]}
    monkeypatch.setattr(module.core, "devices_from_session", lambda *a: state["devices"])
    monkeypatch.setattr(module.lan_scan, "require_scanner", lambda: None)
    client = module.app.test_client()
    return module, client, state


def refresh(client):
    response = client.get("/api/devices?refresh=1")
    assert response.status_code == 200
    return response.json


def history(client):
    response = client.get("/api/history")
    assert response.status_code == 200, response.json
    return response.json


def test_only_successful_fresh_fetches_record_changes(environment, monkeypatch):
    module, client, state = environment
    refresh(client)
    state["devices"][0].name = "Changed"
    for _ in range(3):
        client.get("/api/devices")
    assert history(client)["events"] == []
    refresh(client)
    assert len(history(client)["events"]) == 1
    def fail(*args):
        raise RuntimeError("provider failed")
    monkeypatch.setattr(module.core, "devices_from_session", fail)
    assert refresh(client)["stale"] is True
    assert len(history(client)["events"]) == 1


def test_history_reads_and_filters_never_contact_cloud_and_export_is_removed(environment, monkeypatch):
    module, client, state = environment
    refresh(client)
    state["devices"][0].name = "Renamed"
    refresh(client)
    monkeypatch.setattr(module.core, "devices_from_session", lambda *a: pytest.fail("History must be local"))
    assert len(history(client)["events"]) == 1
    assert client.get("/api/history?kind=removed").json["events"] == []
    response = client.get("/api/history?device_id=lamp")
    assert response.headers["Cache-Control"] == "no-store"
    assert KEY not in response.text and "secret-token" not in response.text and "key_hash" not in response.text
    assert client.get("/api/history/export").status_code == 404


def test_exclusions_are_visibility_preferences_and_do_not_modify_history(environment):
    module, client, state = environment
    refresh(client)
    account = history(client)["account_id"]
    client.patch("/api/device-exclusions", json={"account_id": account, "exclude": ["lamp"]})
    state["devices"][0].name = "Hidden lamp"
    refresh(client)
    assert history(client)["events"][0]["changes"][0]["kind"] == "renamed"


def test_same_account_qr_reauthentication_retains_journal(environment, monkeypatch):
    module, client, state = environment
    refresh(client)
    state["devices"][0].name = "Before re-login"
    refresh(client)
    before = history(client)
    module._pending["qr"] = {"user_code": SESSION["user_code"], "created_at": time.time()}
    monkeypatch.setattr(module.core, "poll_login", lambda *a: dict(SESSION, terminal_id="new-terminal"))
    assert client.post("/api/login/poll", json={"token": "qr"}).json["status"] == "confirmed"
    assert history(client)["epoch"] == before["epoch"]
    state["devices"][0].name = "After re-login"
    refresh(client)
    assert len(history(client)["events"]) == 2


def test_account_switch_and_logout_remove_old_journal(environment, monkeypatch):
    module, client, state = environment
    refresh(client)
    old = history(client)
    module._pending["qr"] = {"user_code": "account-b", "created_at": time.time()}
    monkeypatch.setattr(module.core, "poll_login", lambda *a: dict(SESSION, user_code="account-b"))
    assert client.post("/api/login/poll", json={"token": "qr"}).status_code == 200
    assert not Path(module.HISTORY_FILE).exists()
    assert history(client)["account_id"] != old["account_id"]
    assert client.get("/api/history", query_string={"account_id": old["account_id"]}).status_code == 409
    refresh(client)
    state["devices"][0].name = "Account B"
    refresh(client)
    assert history(client)["events"][0]["changes"][0]["before"] == "Lamp"
    assert client.post("/api/logout").status_code == 200
    assert not Path(module.HISTORY_FILE).exists() and not Path(module.HISTORY_KEY_FILE).exists()
    assert client.get("/api/history").status_code == 401


def test_clear_rejects_stale_account_or_epoch_and_keeps_comparison_baseline(environment):
    _, client, state = environment
    refresh(client)
    state["devices"][0].name = "B"
    refresh(client)
    before = history(client)
    assert client.delete("/api/history", json={"account_id": "old", "epoch": before["epoch"]}).status_code == 409
    assert client.delete("/api/history", json={"account_id": before["account_id"], "epoch": "old", "device_id": "lamp"}).status_code == 409
    response = client.delete("/api/history", json={"account_id": before["account_id"], "epoch": before["epoch"], "device_id": "lamp"})
    assert response.status_code == 200
    refresh(client)
    assert history(client)["events"] == []
    state["devices"][0].name = "C"
    refresh(client)
    assert history(client)["events"][0]["changes"][0]["before"] == "B"


def test_clear_during_cloud_refresh_prevents_late_history_write(environment, monkeypatch):
    module, client, state = environment
    refresh(client)
    before = history(client)
    entered, release = threading.Event(), threading.Event()
    def fetch(*args):
        entered.set()
        assert release.wait(5)
        return [SimpleNamespace(id="lamp", name="Late result", local_key=KEY)]
    monkeypatch.setattr(module.core, "devices_from_session", fetch)
    replies = []
    thread = threading.Thread(target=lambda: replies.append(module.app.test_client().get("/api/devices?refresh=1")))
    thread.start()
    try:
        assert entered.wait(2)
        assert client.delete("/api/history", json={"account_id": before["account_id"], "epoch": before["epoch"], "device_id": "lamp"}).status_code == 200
    finally:
        release.set()
        thread.join(5)
    assert replies[0].status_code == 200
    assert history(client)["events"] == []


def test_clear_during_cloud_refresh_keeps_other_devices_changes(environment, monkeypatch):
    module, client, state = environment
    other_key = "secret-key-00002"
    state["devices"].append(SimpleNamespace(id="other", name="Other", local_key=other_key, category="cz"))
    refresh(client)
    before = history(client)
    entered, release = threading.Event(), threading.Event()
    def fetch(*args):
        entered.set()
        assert release.wait(5)
        return [SimpleNamespace(id="lamp", name="Late lamp", local_key=KEY, category="cz"),
                SimpleNamespace(id="other", name="Other renamed", local_key=other_key, category="cz")]
    monkeypatch.setattr(module.core, "devices_from_session", fetch)
    replies = []
    thread = threading.Thread(target=lambda: replies.append(module.app.test_client().get("/api/devices?refresh=1")))
    thread.start()
    try:
        assert entered.wait(2)
        assert client.delete("/api/history", json={"account_id": before["account_id"], "epoch": before["epoch"], "device_id": "lamp"}).status_code == 200
    finally:
        release.set()
        thread.join(5)
    assert replies[0].status_code == 200
    assert client.get("/api/history?device_id=lamp").json["events"] == []
    renamed = client.get("/api/history?device_id=other").json["events"]
    assert [event["changes"][0]["after"] for event in renamed] == ["Other renamed"]


@pytest.mark.parametrize("setting,value", [
    ("HISTORY_RETENTION_DAYS", "0"), ("HISTORY_RETENTION_DAYS", "90d"), ("CHANGE_HISTORY", "enabled")])
def test_invalid_history_settings_never_erase_saved_history(environment, monkeypatch, setting, value):
    module, client, state = environment
    refresh(client)
    state["devices"][0].name = "Renamed"
    refresh(client)
    saved = history(client)
    with monkeypatch.context() as patch:
        patch.setenv(setting, value)
        broken = importlib.reload(module).app.test_client()
        assert broken.get("/api/state").status_code == 200   # every page load
        response = broken.delete("/api/history", json={
            "account_id": saved["account_id"], "epoch": saved["epoch"], "device_id": "lamp"})
        assert response.status_code == 503 and response.json["error"] == "history_configuration"
        assert broken.get("/api/history").json["error"] == "history_configuration"
    restored = importlib.reload(module).app.test_client()
    assert len(history(restored)["events"]) == 1


def test_history_write_failure_is_reported_without_losing_live_devices(environment, monkeypatch):
    module, client, state = environment
    refresh(client)
    state["devices"][0].name = "Live result"
    with monkeypatch.context() as patch:
        patch.setattr(module._history, "_write", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        result = refresh(client)
    assert result["devices"][0]["name"] == "Live result"
    assert result["history_error"] == "history_save_failed"
    assert history(client)["recording_error"] == "history_save_failed"


def test_unreadable_history_requires_explicit_reset_and_invalidates_stale_reset_token(environment):
    module, client, _ = environment
    refresh(client)
    Path(module.HISTORY_FILE).write_bytes(b"broken")
    first = client.get("/api/history")
    assert first.status_code == 503 and first.json["error"] == "history_invalid"
    assert client.post("/api/history/reset", json={"account_id": first.json["account_id"],
        "reset_token": first.json["reset_token"]}).status_code == 409
    Path(module.HISTORY_FILE).write_bytes(b"changed-broken")
    assert client.post("/api/history/reset", json={"account_id": first.json["account_id"],
        "reset_token": first.json["reset_token"], "confirm_reset": True}).status_code == 409
    latest = client.get("/api/history").json
    assert client.post("/api/history/reset", json={"account_id": latest["account_id"],
        "reset_token": latest["reset_token"], "confirm_reset": True}).status_code == 200
    assert history(client)["events"] == []


@pytest.mark.parametrize("query", ["limit=0", "limit=abc", "kind=bad", "source=bad", "since=nan", "until=inf", "since=10&until=2", "q=" + "x" * 257])
def test_invalid_queries_are_bounded_errors(environment, query):
    _, client, _ = environment
    assert client.get("/api/history?" + query).status_code == 400


def _run_scan(client, **data):
    response = client.post("/api/lan/scan", json=data or {"targets": "10.0.0.0/30"})
    assert response.status_code == 202, response.json
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get("/api/lan/scan").json["job"]
        if job and job["state"] != "running":
            return job
        time.sleep(0.005)
    pytest.fail("scan did not finish")


def outcome(result, *, covered=True, cancelled=False):
    return {"results": {"lamp": result}, "coverage": {"lamp": covered},
            "summary": {"cancelled": cancelled, "finished_at": time.time()}}


def _remember_pre_history_lan(module, *, evidence=True):
    """A real saved connection from before history started recording LAN checks."""
    devices = module._session_devices(SESSION)
    current = {device["id"]: device for device in devices}
    checked_at = time.time() - 86400
    result = {"status": "ok", "ip": "10.0.0.1", "version": "3.3", "device22": False,
              "checked_at": checked_at}
    job = {"key_digests": {key: module._key_digest(device.get("local_key")) for key, device in current.items()},
           "device_links": {key: module._device_link(device) for key, device in current.items()}}
    results = {"lamp": result}
    proof = module._lan_key_evidence(job, "lamp", result, results, current)
    with module._lan_lock:
        module._save_lan(module._session_cache_key(SESSION), {
            "results": results, "key_evidence": {"lamp": proof} if evidence else {},
            "summary": None, "targets": "10.0.0.0/30", "changes": None, "changes_at": None})
    return checked_at


@pytest.mark.parametrize("single", [True, False], ids=["recheck", "network-scan"])
@pytest.mark.parametrize("result,kinds", [
    ({"status": "unreachable", "ip": "10.0.0.1"}, ["connection_changed"]),
    ({"status": "busy", "ip": "10.0.0.1"}, ["connection_changed"]),
    ({"status": "key_mismatch", "ip": "10.0.0.1"}, ["connection_changed"]),
    ({"status": "ok", "ip": "10.0.0.2", "version": "3.4"}, ["ip_changed", "protocol_changed"]),
])
def test_first_check_compares_with_trusted_saved_connection(environment, monkeypatch, single, result, kinds):
    module, client, _ = environment
    refresh(client)
    checked_at = _remember_pre_history_lan(module)
    assert history(client)["events"] == []
    monkeypatch.setattr(module.lan_scan, "scan", lambda *a, **kw: outcome(result))
    _run_scan(client, **({"device_id": "lamp", "ip": "10.0.0.1"} if single else {}))
    entries = history(client)["events"]
    assert len(entries) == 1
    assert [change["kind"] for change in entries[0]["changes"]] == kinds
    assert entries[0]["previous_observed_at"] == checked_at
    if kinds == ["connection_changed"]:
        assert entries[0]["changes"][0]["before"] == "ok"
        assert entries[0]["changes"][0]["after"] == result["status"]


@pytest.mark.parametrize("problem", ["missing-proof", "changed-key", "changed-link", "missing-time", "future-time"])
def test_untrusted_cached_connections_are_not_history_baselines(environment, monkeypatch, problem):
    module, client, _ = environment
    refresh(client)
    _remember_pre_history_lan(module, evidence=problem != "missing-proof")
    with module._lan_lock:
        body = module._lan_body_for(module._session_cache_key(SESSION))
        if problem == "changed-key":
            body["key_evidence"]["lamp"]["key_digests"]["lamp"] = module._key_digest("old-key")
        elif problem == "changed-link":
            body["key_evidence"]["lamp"]["device_links"]["lamp"]["gateway_id"] = "old-gateway"
        elif problem == "missing-time":
            del body["results"]["lamp"]["checked_at"]
        elif problem == "future-time":
            body["results"]["lamp"]["checked_at"] = time.time() + 86400
    monkeypatch.setattr(module.lan_scan, "scan", lambda *a, **kw: outcome({"status": "unreachable", "ip": "10.0.0.1"}))
    assert _run_scan(client, device_id="lamp", ip="10.0.0.1")["state"] == "done"
    assert history(client)["events"] == []


def test_cache_import_never_replaces_an_existing_history_baseline(environment, monkeypatch):
    module, client, _ = environment
    refresh(client)
    account = history(client)["account_id"]
    module._history.observe_lan(account, {"lamp": {"status": "busy"}}, module._session_devices(SESSION), "known-busy")
    _remember_pre_history_lan(module)
    monkeypatch.setattr(module.lan_scan, "scan", lambda *a, **kw: outcome({"status": "unreachable", "ip": "10.0.0.1"}))
    _run_scan(client, device_id="lamp", ip="10.0.0.1")
    assert history(client)["events"][0]["changes"][0]["before"] == "busy"


def test_cached_connection_from_before_identity_change_is_not_revived(environment, monkeypatch):
    module, client, state = environment
    state["devices"][0].uuid = "old-identity"
    refresh(client)
    _remember_pre_history_lan(module)
    state["devices"][0].uuid = "replacement-identity"
    refresh(client)
    monkeypatch.setattr(module.lan_scan, "scan", lambda *a, **kw: outcome({"status": "unreachable", "ip": "10.0.0.1"}))
    _run_scan(client, device_id="lamp", ip="10.0.0.1")
    assert [entry for entry in history(client)["events"] if entry["source"] == "lan"] == []


def test_lan_scope_failure_recovery_and_guessed_ip_rules(environment, monkeypatch):
    module, client, _ = environment
    refresh(client)
    rounds = [outcome({"status": "ok", "ip": "10.0.0.1", "version": "3.3"}),
              outcome({"status": "unreachable", "ip": "10.0.0.1"}, covered=False),
              outcome({"status": "unreachable", "ip": "10.0.0.9"}),
              outcome({"status": "busy", "ip": "10.0.0.1"}),
              outcome({"status": "ok", "ip": "10.0.0.2", "version": "3.4"})]
    monkeypatch.setattr(module.lan_scan, "scan", lambda *a, **kw: rounds.pop(0))
    _run_scan(client)
    _run_scan(client)
    _run_scan(client, device_id="lamp", ip="10.0.0.9")
    assert history(client)["events"] == []
    _run_scan(client)
    _run_scan(client)
    changes = history(client)["events"][0]["changes"]
    assert [c["kind"] for c in changes] == ["connection_changed", "ip_changed", "protocol_changed"]


def test_cancelled_scan_keeps_positive_changes_but_suppresses_negative_claims(environment, monkeypatch):
    module, client, _ = environment
    refresh(client)
    rounds = [outcome({"status": "ok", "ip": "10.0.0.1", "version": "3.3"}),
              outcome({"status": "unreachable", "ip": "10.0.0.1"}, cancelled=True),
              outcome({"status": "ok", "ip": "10.0.0.2", "version": "3.3"}, cancelled=True)]
    monkeypatch.setattr(module.lan_scan, "scan", lambda *a, **kw: rounds.pop(0))
    for _ in range(3):
        _run_scan(client)
    events = history(client)["events"]
    assert len(events) == 1 and events[0]["changes"][0]["kind"] == "ip_changed"


def test_completed_scan_using_rotated_key_has_no_lan_history(environment, monkeypatch):
    module, client, state = environment
    refresh(client)
    entered, release = threading.Event(), threading.Event()
    def scan(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return outcome({"status": "ok", "ip": "10.0.0.1", "version": "3.3"})
    monkeypatch.setattr(module.lan_scan, "scan", scan)
    assert client.post("/api/lan/scan", json={"targets": "10.0.0.1"}).status_code == 202
    try:
        assert entered.wait(2)
        state["devices"][0].local_key = "changed-key-00001"
        refresh(client)
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while client.get("/api/lan/scan").json["job"]["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert all(e["source"] == "cloud" for e in history(client)["events"])


def test_scan_started_before_a_sub_device_was_repaired_cannot_restore_its_key_link(environment, monkeypatch):
    """A re-paired sub-device keeps its key but gets a new uuid. A scan that
    began before that refresh must not hand its key back to the keyless hub."""
    module, client, state = environment
    hub = SimpleNamespace(id="hub", name="Hub", local_key="", category="wg2", sub=True)
    child = SimpleNamespace(id="child", name="Sensor", local_key=KEY, category="mcs", sub=True,
                            node_id="n1", uuid="first-pairing")
    state["devices"] = [hub, child]
    refresh(client)
    found = {"results": {"hub": {"status": "ok", "ip": "10.0.0.5", "version": "3.3", "key_from": "child"},
                         "child": {"status": "via_gateway", "ip": "10.0.0.5", "version": "3.3", "gateway_id": "hub"}},
             "coverage": {"hub": True, "child": True}, "summary": {"cancelled": False, "finished_at": time.time()}}
    monkeypatch.setattr(module.lan_scan, "scan", lambda *a, **kw: copy.deepcopy(found))
    _run_scan(client, targets="10.0.0.5")

    entered, release = threading.Event(), threading.Event()
    def slow_scan(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return copy.deepcopy(found)
    monkeypatch.setattr(module.lan_scan, "scan", slow_scan)
    assert client.post("/api/lan/scan", json={"targets": "10.0.0.5"}).status_code == 202
    try:
        assert entered.wait(2)
        child.uuid = "second-pairing"
        refresh(client)
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while client.get("/api/lan/scan").json["job"]["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.005)

    # With the link restored, the sub-device's next key would be charged to the hub.
    child.local_key = "rotated-key-00002"
    refresh(client)
    hub_changes = [c["kind"] for e in history(client)["events"] if e["device_id"] == "hub" for c in e["changes"]]
    assert "key_changed" not in hub_changes


def test_logout_failure_does_not_claim_success_or_delete_history(environment, monkeypatch):
    module, client, _ = environment
    refresh(client)
    original = module.os.remove
    def fail_session(path):
        if str(path) == module.SESSION_FILE:
            raise PermissionError("cannot remove")
        return original(path)
    monkeypatch.setattr(module.os, "remove", fail_session)
    assert client.post("/api/logout").status_code == 503
    assert Path(module.SESSION_FILE).exists() and Path(module.HISTORY_FILE).exists()
    assert client.get("/api/history").status_code == 200


@pytest.mark.parametrize("path,method", [("/api/history", "GET"),
                                        ("/api/history", "DELETE"), ("/api/history/reset", "POST")])
def test_history_uses_existing_authentication(environment, monkeypatch, path, method):
    module, client, _ = environment
    monkeypatch.setattr(module, "AUTH_USERNAME", "admin")
    monkeypatch.setattr(module, "AUTH_PASSWORD", "password")
    assert client.open(path, method=method).status_code == 401
    if method == "GET":
        assert client.open(path, method=method, auth=("admin", "password")).status_code == 200
        assert client.open(path, method=method, headers={"X-Ingress-Path": "/ingress/test"},
                           environ_overrides={"REMOTE_ADDR": "172.30.32.2"}).status_code == 200


@pytest.mark.parametrize("path,method", [("/api/history", "DELETE"), ("/api/history/reset", "POST")])
def test_history_mutations_bound_chunked_json_and_refuse_non_objects(environment, path, method):
    import io
    _, client, _ = environment
    assert client.open(path, method=method, json=[]).status_code == 400
    payload = b'{"padding":"' + b"x" * 10000 + b'"}'
    assert client.open(path, method=method, content_type="application/json",
                       environ_overrides={"wsgi.input": io.BytesIO(payload),
                                          "CONTENT_LENGTH": "", "wsgi.input_terminated": True}).status_code == 400


@pytest.mark.parametrize("problem", ["room", "field_at", "missing_cloud_at", "events"])
def test_malformed_history_never_breaks_live_refresh_and_offers_confirmed_reset(environment, problem):
    from cryptography.fernet import Fernet
    module, client, state = environment
    refresh(client)
    path = Path(module.HISTORY_FILE)
    cipher = Fernet(Path(module.HISTORY_KEY_FILE).read_bytes())
    document = json.loads(cipher.decrypt(path.read_bytes()))
    if problem == "missing_cloud_at":
        del document["cloud_at"]
    elif problem == "events":
        document["events"] = {}
    else:
        document["cloud"]["lamp"][problem] = []
    damaged = cipher.encrypt(json.dumps(document).encode())
    path.write_bytes(damaged)
    state["devices"][0].name = "Still usable"
    live = refresh(client)
    assert live["devices"][0]["name"] == "Still usable"
    assert live["history_error"] == "history_invalid"
    failed = client.get("/api/history")
    assert failed.status_code == 503 and failed.json["error"] == "history_invalid"
    assert path.read_bytes() == damaged
    assert client.post("/api/history/reset", json={"account_id": failed.json["account_id"],
        "reset_token": failed.json["reset_token"], "confirm_reset": True}).status_code == 200
    refresh(client)
    assert history(client)["events"] == []


def test_clear_only_affects_the_requested_devices_history(environment):
    _, client, state = environment
    state["devices"].append(SimpleNamespace(id="other", name="Other", local_key=KEY))
    refresh(client)
    for d in state["devices"]:
        d.name += " renamed"
    refresh(client)
    before = history(client)
    other_before = client.get("/api/history?device_id=other").json["events"]
    assert client.delete("/api/history", json={"account_id": before["account_id"],
        "epoch": before["epoch"], "device_id": "lamp"}).status_code == 200
    assert client.get("/api/history?device_id=lamp").json["events"] == []
    assert client.get("/api/history?device_id=other").json["events"] == other_before
    refresh(client)
    assert client.get("/api/history?device_id=lamp").json["events"] == []
    state["devices"][0].name = "Next name"
    refresh(client)
    event = client.get("/api/history?device_id=lamp").json["events"][0]
    assert event["changes"][0]["before"] == "Lamp renamed"
    assert client.get("/api/history?device_id=other").json["events"] == other_before


@pytest.mark.parametrize("device_id,status", [(None, 400), ("", 400), ([], 400), ("unknown", 404)])
def test_clear_never_falls_back_to_clearing_the_account(environment, device_id, status):
    _, client, state = environment
    refresh(client)
    state["devices"][0].name = "Changed"
    refresh(client)
    before = history(client)
    assert client.delete("/api/history", json={"account_id": before["account_id"],
        "epoch": before["epoch"], "device_id": device_id}).status_code == status
    assert history(client)["events"] == before["events"]
