import json
import logging
import socket
import sys
import threading
import time
from collections import defaultdict
from types import SimpleNamespace

import pytest

import lan_scan


# --------------------------------------------------------------------------- #
# A fake LAN: tinytuya's Device and the TCP sweep, answering like real devices
# --------------------------------------------------------------------------- #
class FakeTuya:
    """answers_wrong_keys: a v3.4/3.5 device that replies to a handshake with
    the wrong key (tinytuya then fills in remote_nonce and fails the HMAC
    check). timeouts: probes with its own key and version that time out first.
    """

    def __init__(self, dev_id, key, version, device22=False, gateway=False,
                 answers_wrong_keys=False, timeouts=0):
        self.id, self.key, self.version = dev_id, key, version
        self.device22, self.gateway = device22, gateway
        self.answers_wrong_keys, self.timeouts = answers_wrong_keys, timeouts


class FakeNetwork:
    """ip -> FakeTuya, or REFUSED / SILENT. Unknown addresses are silent."""

    def __init__(self, hosts):
        self.hosts = dict(hosts)
        self.probes = []                     # (ip, dev_id, version), in order
        self.active = defaultdict(int)
        self.max_active = defaultdict(int)
        self.lock = threading.Lock()
        self.sweeps = defaultdict(int)

    def tcp_state(self, ip, timeout=None):
        with self.lock:
            self.sweeps[ip] += 1
        host = self.hosts.get(ip, lan_scan.SILENT)
        return lan_scan.OPEN if isinstance(host, FakeTuya) else host

    def device(self, dev_id, ip, key, version):
        return FakeDevice(self, dev_id, ip, key, version)


class FakeDevice:
    def __init__(self, net, dev_id, ip, key, version):
        self.net, self.id, self.ip, self.key = net, dev_id, ip, key
        self.version = version
        self.dev_type = "default"
        self.disabledetect = False
        self.remote_nonce = b""
        # As tinytuya: local_key becomes the session key only once a 3.4/3.5
        # negotiation passes its HMAC check.
        self.real_local_key = self.local_key = key.encode("latin1")

    def status(self):
        net = self.net
        with net.lock:
            net.probes.append((self.ip, self.id, self.version))
            net.active[self.ip] += 1
            net.max_active[self.ip] = max(net.max_active[self.ip], net.active[self.ip])
        try:
            time.sleep(0.002)   # long enough for overlapping probes to show
            return self._answer(net.hosts.get(self.ip, lan_scan.SILENT))
        finally:
            with net.lock:
                net.active[self.ip] -= 1

    def _answer(self, host):
        if host == lan_scan.REFUSED:
            return {"Err": "901"}
        if not isinstance(host, FakeTuya):
            return {"Err": "905"}
        if self.version == "3.1":
            if host.version != "3.1":
                return {"Err": "904"}
            return {"devId": host.id, "dps": {"1": True}}   # plaintext: any key
        if host.version != self.version:
            return {"Err": "901"} if self.version == "3.3" else {"Err": "914"}
        handshake = self.version in ("3.4", "3.5")
        if self.key != host.key:
            if handshake and host.answers_wrong_keys:
                self.remote_nonce = b"n" * 16   # set before the HMAC check fails
            return {"Err": "914"} if handshake else {"Err": "904"}
        if host.timeouts:
            host.timeouts -= 1
            # A handshake that times out reads as 914, just like a wrong key.
            return {"Err": "914"} if handshake else {"Err": "902"}
        if handshake:
            self.remote_nonce = b"n" * 16
            self.local_key = b"session-key-0001"   # the negotiation finished
        if host.gateway:
            if self.disabledetect:
                return {"Err": "900", "Payload": "json obj data unvalid"}
            # tinytuya switches to device22, asks again, and gets nothing usable.
            return None
        if host.device22:
            self.dev_type = "device22"
        return {"dps": {"1": True}}

    def close(self):
        pass


@pytest.fixture()
def lan(monkeypatch):
    def install(hosts):
        net = FakeNetwork(hosts)
        monkeypatch.setattr(lan_scan, "tcp_state", net.tcp_state)
        monkeypatch.setattr(lan_scan, "_new_device", net.device)
        monkeypatch.setattr(lan_scan, "require_scanner", lambda: None)
        return net
    return install


def dev(dev_id, key, name=None, **extra):
    return dict(id=dev_id, local_key=key, name=name or dev_id, **extra)


KEYS = {n: f"key-{n}-0123456789"[:16] for n in "abcdefgh"}


# --------------------------------------------------------------------------- #
# parse_targets
# --------------------------------------------------------------------------- #
def test_targets_accept_subnets_and_single_ips():
    ips = lan_scan.parse_targets("192.168.1.0/30, 10.0.0.7  172.16.5.9,10.0.0.7")

    assert ips == ["192.168.1.1", "192.168.1.2", "10.0.0.7", "172.16.5.9"]


def test_targets_leave_out_network_and_broadcast_addresses():
    ips = lan_scan.parse_targets("172.16.5.0/24")

    assert len(ips) == 254
    assert ips[0] == "172.16.5.1" and ips[-1] == "172.16.5.254"


def test_targets_allow_the_shared_address_space():
    assert lan_scan.parse_targets("100.64.0.10") == ["100.64.0.10"]


@pytest.mark.parametrize("text", [
    "", "   ", "8.8.8.8", "1.2.3.0/24", "fe80::1", "not-an-ip", "192.168.1.0/33",
    "127.0.0.1", "169.254.1.1",
])
def test_targets_reject_anything_but_private_ipv4(text):
    with pytest.raises(lan_scan.TargetError):
        lan_scan.parse_targets(text)


def test_targets_reject_more_than_1024_addresses():
    four = ", ".join(f"10.0.{n}.0/24" for n in range(4))
    assert len(lan_scan.parse_targets(four)) == 4 * 254

    with pytest.raises(lan_scan.TargetError, match="1024"):
        lan_scan.parse_targets(four + ", 10.0.9.0/24")
    with pytest.raises(lan_scan.TargetError, match="too large"):
        lan_scan.parse_targets("10.0.0.0/16")


# --------------------------------------------------------------------------- #
# sweep and tcp_state
# --------------------------------------------------------------------------- #
def test_tcp_state_tells_open_from_refused(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        monkeypatch.setattr(lan_scan, "PORT", port)
        assert lan_scan.tcp_state("127.0.0.1") == lan_scan.OPEN
    finally:
        listener.close()
    assert lan_scan.tcp_state("127.0.0.1") == lan_scan.REFUSED


def test_sweep_labels_addresses_and_retries_the_silent_ones_once(monkeypatch):
    calls = defaultdict(int)

    def state(ip, timeout=None):
        calls[ip] += 1
        if ip == "10.0.0.3":   # the fridge plug: misses the first sweep
            return lan_scan.OPEN if calls[ip] > 1 else lan_scan.SILENT
        return {"10.0.0.1": lan_scan.OPEN, "10.0.0.2": lan_scan.REFUSED}.get(ip, lan_scan.SILENT)

    monkeypatch.setattr(lan_scan, "tcp_state", state)
    labels = lan_scan.sweep(["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"])

    assert labels == {"10.0.0.1": "open", "10.0.0.2": "refused",
                      "10.0.0.3": "open", "10.0.0.4": "silent"}
    assert calls["10.0.0.1"] == 1 and calls["10.0.0.2"] == 1
    assert calls["10.0.0.3"] == 2 and calls["10.0.0.4"] == 2


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ip,key", [("", "k" * 16), (None, "k" * 16), ("Auto", "k" * 16),
                                    ("0.0.0.0", "k" * 16), ("10.0.0.1", "")])
def test_probe_never_builds_a_device_without_an_ip_and_a_key(monkeypatch, ip, key):
    def boom(*args):
        raise AssertionError("tinytuya would broadcast or read devices.json")

    monkeypatch.setattr(lan_scan, "_new_device", boom)

    assert lan_scan.probe("id", ip, key, "3.3")["error"] == "invalid"


def test_probe_devices_use_short_timeouts_and_one_retry(monkeypatch):
    built = {}

    class Device:
        def __init__(self, *args, **kwargs):
            built.update(kwargs, args=args)

    monkeypatch.setitem(sys.modules, "tinytuya", SimpleNamespace(Device=Device))
    logging.getLogger("tinytuya").setLevel(logging.DEBUG)

    lan_scan._new_device("id", "10.0.0.1", "k" * 16, "3.4")

    assert built["address"] == "10.0.0.1"
    assert built["version"] == 3.4
    assert built["connection_retry_limit"] == 1
    assert built["connection_retry_delay"] == 0
    assert built["connection_timeout"] <= 2
    assert logging.getLogger("tinytuya").level == logging.WARNING


def test_probe_matches_a_v34_handshake_even_if_the_status_reply_fails(monkeypatch):
    class Device:
        dev_type = "default"
        remote_nonce = b"n" * 16
        real_local_key = b"k" * 16
        local_key = b"s" * 16   # the session key: the negotiation finished

        def status(self):
            return {"Err": "902"}

        def close(self):
            pass

    monkeypatch.setattr(lan_scan, "_new_device", lambda *a: Device())

    assert lan_scan.probe("id", "10.0.0.1", "k" * 16, "3.4")["match"] is True
    assert lan_scan.probe("id", "10.0.0.1", "k" * 16, "3.3")["match"] is False


def test_a_v34_reply_that_fails_the_hmac_check_is_not_a_match(monkeypatch):
    class Device:
        dev_type = "default"
        remote_nonce = b"n" * 16              # tinytuya fills it in before its HMAC check
        real_local_key = local_key = b"k" * 16  # no session key: the check failed

        def status(self):
            return {"Err": "914"}

        def close(self):
            pass

    monkeypatch.setattr(lan_scan, "_new_device", lambda *a: Device())

    outcome = lan_scan.probe("id", "10.0.0.1", "k" * 16, "3.4")
    assert outcome["match"] is False and outcome["error"] == "key"


def test_the_session_key_check_follows_real_tinytuya():
    """Pins the tinytuya behaviour the 3.4/3.5 match relies on. No network: an
    address and a version are given, so the constructor connects to nothing."""
    tinytuya = pytest.importorskip("tinytuya")
    device = tinytuya.Device("id", address="192.168.2.1", local_key="k" * 16, version=3.4)
    try:
        assert not lan_scan._session_key_installed(device)
        device.remote_nonce = b"r" * 16   # what tinytuya sets before its HMAC check
        assert not lan_scan._session_key_installed(device), "a filled-in nonce proves nothing"
        device._negotiate_session_key_generate_finalize()   # runs once the HMAC check passes
        assert lan_scan._session_key_installed(device)
    finally:
        device.close()


def test_probe_treats_a_v31_reply_as_naming_the_device_not_proving_the_key(lan):
    lan({"10.0.0.1": FakeTuya("old", KEYS["a"], "3.1")})

    outcome = lan_scan.probe("someone-else", "10.0.0.1", KEYS["b"], "3.1")

    assert outcome["dev_id"] == "old"
    assert outcome["plaintext"] is True


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def test_scan_matches_a_mix_of_versions(lan):
    net = lan({
        "10.0.0.1": FakeTuya("a", KEYS["a"], "3.3"),
        "10.0.0.2": FakeTuya("b", KEYS["b"], "3.4"),
        "10.0.0.3": FakeTuya("c", KEYS["c"], "3.5"),
        "10.0.0.4": FakeTuya("d", KEYS["d"], "3.1"),
        "10.0.0.5": lan_scan.REFUSED,
    })
    devices = [dev(n, KEYS[n]) for n in "abcd"]

    out = lan_scan.scan(lan_scan.parse_targets("10.0.0.0/29"), devices)

    found = {k: (r["status"], r["ip"], r["version"]) for k, r in out["results"].items()}
    assert found == {
        "a": ("ok", "10.0.0.1", "3.3"), "b": ("ok", "10.0.0.2", "3.4"),
        "c": ("ok", "10.0.0.3", "3.5"), "d": ("ok", "10.0.0.4", "3.1"),
    }
    summary = out["summary"]
    assert summary["matched"] == 4 and summary["devices"] == 4
    assert summary["refused"] == ["10.0.0.5"]
    assert summary["unmatched"] == [] and summary["cancelled"] is False
    assert all(n == 1 for n in net.max_active.values()), "one connection per address"


def test_scan_tries_versions_in_order_at_each_address(lan):
    net = lan({"10.0.0.3": FakeTuya("c", KEYS["c"], "3.5")})

    lan_scan.scan(["10.0.0.3"], [dev("c", KEYS["c"])])

    assert [v for ip, _, v in net.probes] == ["3.3", "3.4", "3.5"]


def test_scan_never_opens_two_connections_to_one_address(lan):
    hosts = {f"10.0.0.{n}": FakeTuya(f"d{n}", f"key-{n:02d}-abcdefghij"[:16], "3.5")
             for n in range(1, 9)}
    net = lan(hosts)
    devices = [dev(h.id, h.key) for h in hosts.values()]

    out = lan_scan.scan(list(hosts), devices)

    assert out["summary"]["matched"] == 8
    assert max(net.max_active.values()) == 1


def test_a_remembered_device_is_checked_first_at_its_last_address(lan):
    net = lan({
        "10.0.0.1": FakeTuya("a", KEYS["a"], "3.5"),
        "10.0.0.2": FakeTuya("b", KEYS["b"], "3.3"),
    })
    known = {"a": {"ip": "10.0.0.1", "version": "3.5"}}

    out = lan_scan.scan(["10.0.0.1", "10.0.0.2"], [dev("a", KEYS["a"]), dev("b", KEYS["b"])], known)

    assert out["results"]["a"]["status"] == "ok"
    assert [p for p in net.probes if p[0] == "10.0.0.1"] == [("10.0.0.1", "a", "3.5")]


def test_a_remembered_address_outside_the_targets_is_still_checked(lan):
    lan({"10.0.9.9": FakeTuya("a", KEYS["a"], "3.3")})

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])], {"a": {"ip": "10.0.9.9", "version": "3.3"}})

    assert out["results"]["a"]["ip"] == "10.0.9.9"


def test_a_version_change_is_found_at_the_remembered_address(lan):
    lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.4")})

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])], {"a": {"ip": "10.0.0.1", "version": "3.3"}})

    assert out["results"]["a"]["version"] == "3.4"


def test_a_v34_device_that_answers_wrong_keys_is_still_matched_to_its_own(lan):
    # Device "a" is asked first. A reply to its wrong key must not let it claim b's address.
    lan({"10.0.0.1": FakeTuya("b", KEYS["b"], "3.4", answers_wrong_keys=True)})

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"]), dev("b", KEYS["b"])])

    assert out["results"]["b"]["status"] == "ok"
    assert out["results"]["a"]["status"] == "not_found"


def test_one_timeout_at_the_remembered_version_is_retried(lan):
    net = lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.4", timeouts=1)})
    known = {"a": {"ip": "10.0.0.1", "version": "3.4"}}

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])], known)

    assert out["results"]["a"]["status"] == "ok"
    assert [v for _, _, v in net.probes] == ["3.4", "3.4"]


def test_wrong_version_errors_never_read_as_a_changed_key(lan):
    # It keeps timing out at its own version, and the other versions fail the
    # way a wrong key does. That is no reason to say the key changed.
    lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.3", timeouts=99)})
    known = {"a": {"ip": "10.0.0.1", "version": "3.3"}}

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])], known)

    assert out["results"]["a"]["status"] == "unreachable"


def test_checking_one_device_still_knows_its_gateway_from_the_whole_list(lan):
    net = lan({"10.0.0.1": FakeTuya("gw", KEYS["a"], "3.3", gateway=True)})
    devices = [
        dev("gw", KEYS["a"], category="hub-with-a-new-category"),   # only its sub-device says it's a gateway
        dev("sensor", KEYS["a"], sub=True, gateway_id="gw"),
        dev("lamp", KEYS["b"]),
    ]

    out = lan_scan.scan(["10.0.0.1"], devices, {"gw": {"ip": "10.0.0.1", "version": "3.3"}}, only=["gw"])

    assert out["results"]["gw"]["status"] == "ok"
    assert out["results"]["sensor"]["ip"] == "10.0.0.1", "its sub-devices come along"
    assert "lamp" not in out["results"]
    assert {dev_id for _, dev_id, _ in net.probes} == {"gw"}


def test_device22_is_flagged(lan):
    lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.3", device22=True)})

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])])

    assert out["results"]["a"]["device22"] is True
    assert lan_scan.TUYA_LOCAL_DEVICE22["3.3"] == "3.22"


def test_a_gateway_is_matched_without_being_flagged_device22(lan):
    lan({"10.0.0.1": FakeTuya("gw", KEYS["a"], "3.3", gateway=True)})
    devices = [
        dev("gw", KEYS["a"], category="wg2"),
        dev("sensor", KEYS["a"], sub=True, gateway_id="gw", node_id="a4c1"),
    ]

    out = lan_scan.scan(["10.0.0.1"], devices)

    assert out["results"]["gw"]["status"] == "ok"
    assert out["results"]["gw"]["device22"] is False


def test_sub_devices_share_the_gateways_key_but_never_claim_its_address(lan):
    net = lan({"10.0.0.1": FakeTuya("gw", KEYS["a"], "3.4")})
    devices = [
        # Listed first, with the gateway's key: the pool must not let it win.
        dev("sensor", KEYS["a"], sub=True, gateway_id="gw"),
        dev("gw", KEYS["a"]),
        dev("orphan", KEYS["b"], sub=True, gateway_id="missing"),
    ]

    out = lan_scan.scan(["10.0.0.1"], devices)

    assert out["results"]["gw"]["status"] == "ok"
    assert out["results"]["sensor"] == dict(
        out["results"]["sensor"], status="via_gateway", ip="10.0.0.1", version="3.4", gateway_id="gw")
    assert out["results"]["orphan"]["status"] == "via_gateway"
    assert out["results"]["orphan"]["ip"] is None
    assert all(p[1] != "sensor" for p in net.probes)
    assert out["summary"]["devices"] == 1 and out["summary"]["sub_devices"] == 2
    assert out["summary"]["sub_devices_reached"] == 1, "the orphan's gateway was never found"


def test_devices_without_a_local_key_are_left_out(lan):
    net = lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.3")})

    out = lan_scan.scan(["10.0.0.1"], [dev("nokey", ""), dev("a", KEYS["a"])])

    assert "nokey" not in out["results"]
    assert all(p[1] != "nokey" for p in net.probes)


@pytest.mark.parametrize("host,status,version", [
    (lan_scan.REFUSED, "busy", "3.3"),
    (lan_scan.SILENT, "unreachable", "3.3"),
    (FakeTuya("a", "rotated-key-9999", "3.3"), "key_mismatch", "3.3"),
    (FakeTuya("a", "rotated-key-9999", "3.4"), "key_mismatch", "3.4"),
])
def test_an_unmatched_remembered_device_is_judged_at_its_last_address(lan, host, status, version):
    lan({"10.0.0.1": host})
    known = {"a": {"ip": "10.0.0.1", "version": version, "device22": False}}

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])], known)

    result = out["results"]["a"]
    assert (result["status"], result["ip"], result["version"]) == (status, "10.0.0.1", version)


def test_a_remembered_address_taken_by_another_device_is_not_found(lan):
    lan({"10.0.0.1": FakeTuya("b", KEYS["b"], "3.3")})
    known = {"a": {"ip": "10.0.0.1", "version": "3.3"}}

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"]), dev("b", KEYS["b"])], known)

    assert out["results"]["a"]["status"] == "not_found"
    assert out["results"]["a"]["ip"] is None
    assert out["results"]["b"]["status"] == "ok"


def test_an_open_address_with_no_matching_key_is_reported(lan):
    lan({"10.0.0.1": FakeTuya("stranger", KEYS["h"], "3.3")})

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])])

    assert out["results"]["a"]["status"] == "not_found"
    assert out["summary"]["unmatched"] == ["10.0.0.1"]


def test_an_address_that_stops_answering_is_given_up(lan, monkeypatch):
    net = lan({"10.0.0.1": FakeTuya("x", KEYS["h"], "3.3")})
    original = net.device

    def flaky(dev_id, ip, key, version):
        net.hosts[ip] = lan_scan.REFUSED   # e.g. another client took the connection
        return original(dev_id, ip, key, version)

    monkeypatch.setattr(lan_scan, "_new_device", flaky)
    devices = [dev(n, KEYS[n]) for n in "abcdefg"]

    out = lan_scan.scan(["10.0.0.1"], devices)

    assert len(net.probes) == 2
    assert out["summary"]["refused"] == ["10.0.0.1"]


def test_a_cancelled_scan_reports_only_what_it_matched(lan):
    lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.3")})
    cancel = threading.Event()
    cancel.set()

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])], cancel=cancel)

    assert out["results"] == {}
    assert out["summary"]["cancelled"] is True


def test_the_address_budget_bounds_an_address_that_never_matches(lan):
    net = lan({"10.0.0.1": FakeTuya("stranger", KEYS["h"], "3.5")})
    now = [0.0]

    def clock():
        now[0] += 1.0   # every look at the clock costs a second
        return now[0]

    devices = [dev(n, KEYS[n]) for n in "abcdefg"]
    out = lan_scan.scan(["10.0.0.1"], devices, clock=clock, address_budget=5)

    assert len(net.probes) < 7 * 4
    assert out["summary"]["out_of_budget"] == ["10.0.0.1"]
    assert out["summary"]["unmatched"] == [], "not every key was tried there"
    assert {r["status"] for r in out["results"].values()} == {"not_found"}


def test_addresses_a_cancel_stopped_are_not_reported_as_unmatched(lan):
    lan({"10.0.0.1": FakeTuya("stranger", KEYS["h"], "3.3")})
    cancel = threading.Event()

    def progress(snapshot):
        if snapshot["phase"] == "probe":
            cancel.set()   # cancelled right after the sweep

    out = lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])], progress=progress, cancel=cancel)

    assert out["summary"]["cancelled"] is True
    assert out["summary"]["unmatched"] == [] and out["summary"]["out_of_budget"] == []


def test_an_interrupted_scan_stops_every_address_promptly(lan, monkeypatch):
    class Interrupt(BaseException):   # what Ctrl+C raises is a BaseException too
        pass

    net = lan({
        "10.0.0.1": FakeTuya("x", KEYS["h"], "3.3"),
        "10.0.0.2": FakeTuya("y", KEYS["g"], "3.3"),
    })
    answer = FakeDevice.status
    busy = threading.Event()

    def status(self):
        if self.ip == "10.0.0.1":
            busy.wait(5)   # interrupted while the other address is mid-way through its keys
            raise Interrupt()
        busy.set()
        time.sleep(0.02)
        return answer(self)

    monkeypatch.setattr(FakeDevice, "status", status)

    with pytest.raises(Interrupt):
        lan_scan.scan(["10.0.0.1", "10.0.0.2"], [dev(n, KEYS[n]) for n in "abcdef"])

    # Without a cancel, the other address would work through all 19 of its probes.
    assert len([p for p in net.probes if p[0] == "10.0.0.2"]) <= 3


def test_the_budgets_grow_with_the_account():
    assert lan_scan.budgets_for(10) == (150.0, 60.0)
    assert lan_scan.budgets_for(100) == (290.0, 200.0)


def test_progress_counts_the_sweep_and_the_matches(lan):
    lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.3")})
    seen = []

    lan_scan.scan(["10.0.0.1", "10.0.0.2"], [dev("a", KEYS["a"])], progress=seen.append)

    assert seen[0]["phase"] == "sweep" and seen[0]["addresses"] == 2
    assert seen[-1]["phase"] == "done"
    assert seen[-1]["matched"] == 1 and seen[-1]["open"] == 1 and seen[-1]["probed"] == 1


def test_no_key_appears_in_any_output(lan):
    lan({
        "10.0.0.1": FakeTuya("a", KEYS["a"], "3.3"),
        "10.0.0.2": FakeTuya("b", "rotated-key-9999", "3.4"),
    })
    seen = []
    known = {"b": {"ip": "10.0.0.2", "version": "3.4"}}

    out = lan_scan.scan(["10.0.0.1", "10.0.0.2"], [dev("a", KEYS["a"]), dev("b", KEYS["b"])],
                        known, progress=seen.append)
    changes = lan_scan.diff_results({"b": {"status": "ok", "ip": "10.0.0.2", "version": "3.4"}},
                                    out["results"], {"a": "A", "b": "B"})

    text = json.dumps([out, seen, changes])
    for key in (KEYS["a"], KEYS["b"]):
        assert key not in text


# --------------------------------------------------------------------------- #
# diff_results
# --------------------------------------------------------------------------- #
def test_diff_reports_version_ip_and_key_failures_for_devices_that_were_ok():
    ok = lambda ip, v, d22=False: {"status": "ok", "ip": ip, "version": v, "device22": d22}
    previous = {
        "moved": ok("10.0.0.1", "3.3"), "upgraded": ok("10.0.0.2", "3.3"),
        "quirk": ok("10.0.0.3", "3.3"), "rotated": ok("10.0.0.4", "3.4"),
        "same": ok("10.0.0.5", "3.5"), "was-busy": {"status": "busy", "ip": "10.0.0.6", "version": "3.3"},
    }
    current = {
        "moved": ok("10.0.0.9", "3.3"), "upgraded": ok("10.0.0.2", "3.5"),
        "quirk": ok("10.0.0.3", "3.3", True), "rotated": {"status": "key_mismatch"},
        "same": ok("10.0.0.5", "3.5"), "was-busy": ok("10.0.0.6", "3.4"), "new": ok("10.0.0.7", "3.3"),
    }
    names = {k: k.title() for k in current}

    changes = lan_scan.diff_results(previous, current, names)

    assert changes["version_changed"] == [
        {"id": "quirk", "name": "Quirk", "was": "3.3", "now": "3.3 (device22)"},
        {"id": "upgraded", "name": "Upgraded", "was": "3.3", "now": "3.5"},
    ]
    assert changes["ip_changed"] == [
        {"id": "moved", "name": "Moved", "was": "10.0.0.1", "now": "10.0.0.9"}]
    assert changes["local_key_failed"] == [{"id": "rotated", "name": "Rotated"}]


# --------------------------------------------------------------------------- #
# Connections are never held
# --------------------------------------------------------------------------- #
def test_every_probe_closes_its_connection_matched_or_not(monkeypatch):
    """A Tuya device takes one local connection at a time: holding one would lock
    Home Assistant or tuya-local out of it."""
    built = []

    class Device:
        def __init__(self, answer):
            self.answer, self.closed = answer, False
            self.dev_type, self.remote_nonce = "default", b""

        def status(self):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        def close(self):
            self.closed = True

    answers = iter([{"dps": {"1": True}}, {"Err": "904"}, {"Err": "905"}, RuntimeError("reset")])

    def new_device(*args):
        built.append(Device(next(answers)))
        return built[-1]

    monkeypatch.setattr(lan_scan, "_new_device", new_device)
    outcomes = [lan_scan.probe("id", "10.0.0.1", "k" * 16, "3.3") for _ in range(4)]

    assert [o["match"] for o in outcomes] == [True, False, False, False]
    assert all(d.closed for d in built)


def test_new_devices_never_ask_tinytuya_for_a_persistent_socket(monkeypatch):
    built = {}

    class Device:
        def __init__(self, *args, **kwargs):
            built.update(kwargs)

    monkeypatch.setitem(sys.modules, "tinytuya", SimpleNamespace(Device=Device))
    lan_scan._new_device("id", "10.0.0.1", "k" * 16, "3.3")

    assert not built.get("persist"), "tinytuya closes after each exchange unless persist=True"


def test_the_sweep_closes_every_socket_it_opens(monkeypatch):
    opened = []

    class Sock:
        def __init__(self, *args):
            self.closed = False
            opened.append(self)

        def settimeout(self, t):
            pass

        def connect_ex(self, addr):
            return 0

        def close(self):
            self.closed = True

    monkeypatch.setattr(lan_scan.socket, "socket", Sock)
    lan_scan.sweep([f"10.0.0.{n}" for n in range(1, 21)])

    assert len(opened) == 20 and all(s.closed for s in opened)


# --------------------------------------------------------------------------- #
# A broken tinytuya fails the scan, never the app, and never looks like "not found"
# --------------------------------------------------------------------------- #
def test_importing_lan_scan_never_imports_tinytuya():
    import subprocess

    code = "import sys, lan_scan; print('tinytuya' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(__import__("pathlib").Path(lan_scan.__file__).parent))
    assert out.stdout.strip() == "False", out.stderr


def test_a_missing_tinytuya_is_reported_before_the_scan_starts(monkeypatch):
    monkeypatch.setitem(sys.modules, "tinytuya", None)   # import now raises ImportError
    swept = []
    monkeypatch.setattr(lan_scan, "tcp_state", lambda ip, timeout=None: swept.append(ip))

    with pytest.raises(lan_scan.ScannerUnavailable, match="could not be loaded"):
        lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])])
    with pytest.raises(lan_scan.ScannerUnavailable):
        lan_scan.probe("a", "10.0.0.1", KEYS["a"], "3.3")
    assert swept == [], "no network work for a scan that can't finish"


def test_a_tinytuya_that_fails_to_build_devices_stops_the_scan(lan, monkeypatch):
    lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.3")})

    def broken(*args):
        raise TypeError("Device() got an unexpected keyword argument")

    monkeypatch.setattr(lan_scan, "_new_device", broken)

    with pytest.raises(lan_scan.ScannerUnavailable, match="TypeError"):
        lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"])])


def test_a_key_tinytuya_cannot_encode_only_skips_that_device(monkeypatch):
    def bad_key(*args):
        raise UnicodeEncodeError("latin-1", "ключ", 0, 1, "ordinal not in range")

    monkeypatch.setattr(lan_scan, "_new_device", bad_key)

    assert lan_scan.probe("a", "10.0.0.1", "ключ" * 4, "3.3")["error"] == "invalid"


def test_a_tinytuya_crashing_on_every_probe_is_unavailable_not_not_found(lan, monkeypatch):
    lan({"10.0.0.1": FakeTuya("a", KEYS["a"], "3.3")})
    monkeypatch.setattr(FakeDevice, "status", lambda self: 1 / 0)

    with pytest.raises(lan_scan.ScannerUnavailable, match="every probe"):
        lan_scan.scan(["10.0.0.1"], [dev("a", KEYS["a"]), dev("b", KEYS["b"])])


def test_one_device_crashing_a_probe_doesnt_stop_the_scan(lan, monkeypatch):
    lan({
        "10.0.0.1": FakeTuya("a", KEYS["a"], "3.3"),
        "10.0.0.2": FakeTuya("b", KEYS["b"], "3.3"),
    })
    answer = FakeDevice.status

    def odd(self):
        if self.ip == "10.0.0.2":
            raise ValueError("tinytuya choked on this one's reply")
        return answer(self)

    monkeypatch.setattr(FakeDevice, "status", odd)
    out = lan_scan.scan(["10.0.0.1", "10.0.0.2"], [dev("a", KEYS["a"]), dev("b", KEYS["b"])])

    assert out["results"]["a"]["status"] == "ok"
    assert out["results"]["b"]["status"] == "not_found"
