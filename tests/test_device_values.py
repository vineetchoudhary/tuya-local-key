import copy
import types

import pytest

import device_values


PLUG = {"id": "plug", "local_key": "0123456789abcdef", "local_strategy": {1: {}}}
GATEWAY = {"id": "gateway", "category": "wg2", "sub": True}
CHILD = {"id": "child", "sub": True, "gateway_id": "gateway", "node_id": "node-a",
         "local_key": "abcdef0123456789"}
SAVED = {"plug": {"ip": "10.0.0.3", "version": "3.4"},
         "gateway": {"ip": "10.0.0.8", "version": "3.3", "key_from": "child"}}


def test_connection_uses_saved_lan_address_instead_of_cloud_ip():
    device = dict(PLUG, ip="8.8.8.8")
    context = device_values.connection([device], SAVED, "plug")
    assert context["ip"] == "10.0.0.3"
    assert context["key"] == PLUG["local_key"]


def test_child_uses_its_named_gateways_key_and_node_id():
    context = device_values.connection([GATEWAY, CHILD], SAVED, "child")
    assert context["host_id"] == "gateway"
    assert context["key"] == CHILD["local_key"]
    assert context["node_id"] == "node-a"
    assert context["ip"] == "10.0.0.8"


def test_child_rejects_an_old_scan_from_a_different_gateway():
    saved = {"child": {"ip": "10.0.0.9", "version": "3.4", "gateway_id": "other"}}
    with pytest.raises(device_values.ReadUnavailable, match="missing_ip"):
        device_values.connection([GATEWAY, CHILD], saved, "child")


@pytest.mark.parametrize("device, saved, code", [
    (dict(PLUG, local_key=""), SAVED, "no_local_key"),
    (PLUG, {}, "missing_ip"),
    (PLUG, {"plug": {"ip": "8.8.8.8", "version": "3.4"}}, "missing_ip"),
    (PLUG, {"plug": {"ip": "10.0.0.3"}}, "missing_protocol"),
    (dict(CHILD, node_id=""), SAVED, "missing_node_id"),
    (dict(CHILD, gateway_id="missing"), SAVED, "gateway_unavailable"),
])
def test_read_requires_real_connection_details(device, saved, code):
    with pytest.raises(device_values.ReadUnavailable, match=code):
        device_values.connection([device, GATEWAY], saved, device["id"])


class FakeDevice:
    def __init__(self, reply):
        self.reply = reply
        self.closed = False
        self.calls = 0

    def status(self):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def close(self):
        self.closed = True

    def set_dpsUsed(self, dps):
        self.dps = dps


def install(monkeypatch, reply):
    parent = FakeDevice(reply)
    args = []

    def new(*values):
        args.append(values)
        return parent

    monkeypatch.setattr(device_values.lan_scan, "_new_device", new)
    return parent, args


def test_read_returns_only_data_points_and_closes_the_connection(monkeypatch):
    parent, _ = install(monkeypatch, {"dps": {"1": True, "19": 427}, "local_key": PLUG["local_key"],
                                    "device": object(), "Payload": "private"})
    context = device_values.connection([PLUG], SAVED, "plug")
    result = device_values.read(context)
    assert result["dps"] == {"1": True, "19": 427}
    assert result["status"] == "ok"
    assert set(result) == {"status", "dps", "read_at"}
    assert parent.calls == 1 and parent.closed


@pytest.mark.parametrize("reply, status", [
    ({"Err": "901", "Payload": "secret"}, "connection_failed"),
    ({"Err": "905"}, "unreachable"), ({"Err": "902"}, "unreachable"),
    ({"Err": "904"}, "key_or_protocol"), ({"Err": "914"}, "key_or_protocol"),
    ({"Err": "900"}, "no_values"), (None, "no_values"), ({"dps": {}}, "no_values"),
    ({"dps": {"bad": 1}}, "no_values"), (OSError("secret"), "unreachable"),
    (RuntimeError("secret"), "read_failed"), ({"dps": {"1": float("nan")}}, "invalid_values"),
    ({"dps": {"1": "a" * (device_values.MAX_READING_BYTES + 1)}}, "invalid_values"),
])
def test_failures_return_no_raw_payload_and_always_close(monkeypatch, reply, status):
    parent, _ = install(monkeypatch, reply)
    result = device_values.read(device_values.connection([PLUG], SAVED, "plug"))
    assert result == {"status": status}
    assert parent.closed


@pytest.mark.parametrize("cid, expected", [("node-a", "ok"), ("other-node", "no_values"), (None, "no_values")])
def test_child_reads_its_own_reply_and_closes_child_and_gateway(monkeypatch, cid, expected):
    parent, _ = install(monkeypatch, {"dps": {"1": "gateway value"}})
    child = FakeDevice({"cid": cid, "data": {"dps": {"1": "child value"}}})
    arguments = {}

    def build(device_id, **kwargs):
        arguments.update(kwargs, device_id=device_id)
        return child

    monkeypatch.setattr(device_values.lan_scan, "_load_tinytuya", lambda: types.SimpleNamespace(Device=build))
    result = device_values.read(device_values.connection([GATEWAY, CHILD], SAVED, "child"))
    assert result["status"] == expected
    if expected == "ok":
        assert result["dps"] == {"1": "child value"}
    assert parent.calls == 0 and child.calls == 1
    assert parent.closed and child.closed
    assert arguments["parent"] is parent and arguments["cid"] == "node-a"


def test_device22_uses_known_ids_without_automatic_discovery(monkeypatch):
    parent, args = install(monkeypatch, {"dps": {"1": 1}})
    saved = copy.deepcopy(SAVED)
    saved["plug"]["version"] = "3.2"
    result = device_values.read(device_values.connection([PLUG], saved, "plug"))
    assert result["status"] == "ok"
    assert args[0][-1] == "3.3"
    assert parent.dev_type == "device22" and parent.dps == {"1": None}


@pytest.mark.parametrize("device_id, expected", [("plug", "ok"), ("other", "device_mismatch"), (None, "device_mismatch")])
def test_plaintext_31_must_name_the_expected_device(monkeypatch, device_id, expected):
    install(monkeypatch, {"devId": device_id, "dps": {"1": True}})
    saved = {"plug": {"ip": "10.0.0.3", "version": "3.1"}}
    assert device_values.read(device_values.connection([PLUG], saved, "plug"))["status"] == expected
