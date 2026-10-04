#!/usr/bin/env python3
"""
Find devices on the local network, and the Tuya protocol version each speaks.

The device-sharing API has no protocol version, and the `ip` it reports is the
account's WAN address, so both have to come from the device itself. This asks
each one directly over TCP 6668 with its local key, through tinytuya's Device:

  1. sweep()  opens (and immediately closes) a plain TCP connection to every
              target, to learn which addresses listen on 6668.
  2. scan()   works through the open addresses, trying the keys of the devices
              still unmatched, one protocol version at a time.

Nothing here relies on UDP broadcasts, so it works across routed VLANs, and
nothing here trusts the cloud `online` flag: an address is judged only by what
it did when asked. tinytuya's own scanner is not used either; it misses v3.5
devices, prints raw frames and can write keys to snapshot.json.

Local keys never appear in anything this returns, and tinytuya's logger (whose
debug output includes them) is capped at WARNING.
"""

import errno
import ipaddress
import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PORT = 6668
MAX_ADDRESSES = 1024
ALLOWED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10",  # shared address space: carrier networks, Tailscale
))

SWEEP_WORKERS = 64
SWEEP_TIMEOUT_SECONDS = 1.0
PROBE_WORKERS = 32
PROBE_TIMEOUT_SECONDS = 2.0
SCAN_BUDGET_SECONDS = 150.0
ADDRESS_BUDGET_SECONDS = 60.0
# A v3.5 device can sit behind every other key at 3.3 (about 0.1 s each), at
# 3.4 (about 1 s each) and at 3.5 (about 1 s each). An account with many devices
# gets that much time per address, so its v3.5 devices aren't cut off.
SECONDS_PER_KEY = 2.0

# 3.3 first: it is the most common, and trying it on a v3.4/v3.5 device fails
# in about 0.1 s, while 3.5 on a v3.4 device is the slow (6 s) case. 3.1 is tried
# last, and only once per address, because its replies are plaintext and name
# the device (see _v31_pass).
PASS_VERSIONS = ("3.3", "3.4", "3.5")
V31 = "3.1"

# Address labels from sweep().
OPEN, REFUSED, SILENT = "open", "refused", "silent"

# Per-device statuses.
OK = "ok"
BUSY = "busy"                  # its known address refused the connection
UNREACHABLE = "unreachable"    # its known address didn't answer
KEY_MISMATCH = "key_mismatch"  # its known address answered, but not to its key
NOT_FOUND = "not_found"        # no scanned address answered to its key
VIA_GATEWAY = "via_gateway"    # a sub-device; IP and version are its gateway's
STATUSES = (OK, BUSY, UNREACHABLE, KEY_MISMATCH, NOT_FOUND, VIA_GATEWAY)

# Categories Tuya uses for gateways, which have sub-devices of their own:
# tuya-local's HUB_CATEGORIES, plus "wg". A gateway polled without a cid answers
# "data unvalid", so the scan has to know one when it sees it.
GATEWAY_CATEGORIES = {
    "wg", "wg2", "wgsxj", "lyqwg", "bywg", "zigbee", "dgnzk", "videohub",
    "xnwg", "qtyycp", "alexa_yywg", "gywg", "cnwg", "wfcon",
}

# Pool ids for a sub-device key that no gateway in the list could be given.
# Never a device id: Tuya's have no colon.
UNCLAIMED = "unclaimed:"

# tuya-local's names for a version with the device22 quirk.
TUYA_LOCAL_DEVICE22 = {"3.3": "3.22", "3.4": "3.42", "3.5": "3.52"}

# tinytuya error codes (tinytuya.core.error_helper).
_ERR_CONNECT, _ERR_TIMEOUT, _ERR_OFFLINE = "901", "902", "905"


class TargetError(ValueError):
    """Raised by parse_targets() with a message fit to show the user."""


class ScannerUnavailable(RuntimeError):
    """tinytuya can't be loaded, or fails on every probe, so no device can be
    asked for its version. A scan raises this instead of reporting every
    device as not found."""


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
def parse_targets(text):
    """Addresses to scan, from "192.168.2.0/24, 192.168.3.7" and the like.

    Subnets and single IPs, separated by commas or whitespace. Only private
    IPv4 and 100.64.0.0/10 are allowed, MAX_ADDRESSES in total; a subnet's
    network and broadcast addresses are left out.
    """
    parts = [p for p in re.split(r"[,\s]+", str(text or "").strip()) if p]
    if not parts:
        raise TargetError(
            "Enter your IoT VLAN or subnet, such as 192.168.2.0/24, or a device IP such as 192.168.2.1."
        )

    addresses, seen = [], set()
    for part in parts:
        try:
            network = ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise TargetError(f"{part} is not a subnet or an IP address.") from None
        if network.version != 4:
            raise TargetError(f"{part}: only IPv4 is supported.")
        if not any(network.subnet_of(allowed) for allowed in ALLOWED_NETWORKS):
            raise TargetError(
                f"{part} is not a private network. Use addresses in 10.0.0.0/8, "
                "172.16.0.0/12, 192.168.0.0/16 or 100.64.0.0/10."
            )
        if network.num_addresses > MAX_ADDRESSES + 2:   # a /22 is 1,022 hosts
            raise TargetError(
                f"{part} is too large. Scan at most {MAX_ADDRESSES} addresses, such as four /24 subnets."
            )
        hosts = network.hosts() if network.prefixlen < 31 else iter(network)
        for address in hosts:
            ip = str(address)
            if ip not in seen:
                seen.add(ip)
                addresses.append(ip)
            if len(addresses) > MAX_ADDRESSES:
                raise TargetError(
                    f"That is more than {MAX_ADDRESSES} addresses. Scan at most four /24 subnets at a time."
                )
    return addresses


def likely_routers(text):
    """The .1 that starts each subnet in `text`, where a router usually sits.

    A router has nothing on port 6668, so it refuses the connection the way a
    busy device does. The scan still asks it, so a device there is still found,
    but leaves it out of the summary's address lists. A single IP typed on
    purpose never counts, and neither does a .1 in the middle of a bigger subnet.
    """
    routers = set()
    for part in re.split(r"[,\s]+", str(text or "").strip()):
        if "/" not in part:
            continue
        try:
            network = ipaddress.ip_network(part, strict=False)
        except ValueError:
            continue
        if network.version != 4 or network.prefixlen >= 31:
            continue
        first = network.network_address + 1
        if first.packed[-1] == 1:
            routers.add(str(first))
    return routers


def _sorted_ips(ips):
    return sorted(ips, key=ipaddress.ip_address)


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #
def tcp_state(ip, timeout=SWEEP_TIMEOUT_SECONDS):
    """OPEN, REFUSED or SILENT for ip:6668. Connects and sends nothing."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        code = sock.connect_ex((ip, PORT))
    except OSError:
        return SILENT
    finally:
        sock.close()
    if code == 0:
        return OPEN
    if code == errno.ECONNREFUSED:
        return REFUSED
    return SILENT


def sweep(ips, cancel=None, progress=None):
    """{ip: OPEN | REFUSED | SILENT}. Addresses that stay silent get one retry,
    since a device can miss the first attempt and answer a moment later."""
    labels = {}
    done = [0]
    lock = threading.Lock()

    def check(ip):
        if cancel is not None and cancel.is_set():
            return
        state = tcp_state(ip)
        with lock:
            labels[ip] = state
            done[0] += 1
            if progress:
                progress(done[0])

    with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
        list(pool.map(check, ips))
        silent = [ip for ip in ips if labels.get(ip) == SILENT]
        for ip, state in zip(silent, pool.map(_retry_state(cancel), silent)):
            if state:
                labels[ip] = state
    return labels


def _retry_state(cancel):
    def retry(ip):
        if cancel is not None and cancel.is_set():
            return None
        return tcp_state(ip)
    return retry


# --------------------------------------------------------------------------- #
# Probing one address with one key and version
# --------------------------------------------------------------------------- #
def _quiet_tinytuya():
    # Its debug output includes local keys; never let it through.
    logging.getLogger("tinytuya").setLevel(logging.WARNING)


def _load_tinytuya():
    # Imported here, not at the top, so nothing but a scan ever depends on it:
    # the app starts and lists devices even if tinytuya is missing or broken.
    try:
        import tinytuya
    except Exception as e:
        raise ScannerUnavailable(f"tinytuya could not be loaded ({type(e).__name__})") from e
    return tinytuya


def require_scanner():
    """Raise ScannerUnavailable now, before a scan does any work."""
    _load_tinytuya()


def _new_device(dev_id, ip, key, version):
    tinytuya = _load_tinytuya()
    _quiet_tinytuya()
    return tinytuya.Device(
        dev_id, address=ip, local_key=key, version=float(version),
        connection_timeout=PROBE_TIMEOUT_SECONDS,
        connection_retry_limit=1, connection_retry_delay=0,
    )


def probe(dev_id, ip, key, version, gateway=False):
    """Ask `ip` for `dev_id`'s status with `key` over protocol `version`.

    Returns {"match": bool, "device22": bool, "error": str | None,
    "dev_id": str | None, "plaintext": bool}. "dev_id" and "plaintext" are only
    filled for 3.1, whose reply is readable without the key. An error of
    "fault" means tinytuya itself failed, not the device.

    Raises ScannerUnavailable if tinytuya can't be loaded or built.
    """
    outcome = {"match": False, "device22": False, "error": None,
               "dev_id": None, "plaintext": False}
    # tinytuya broadcasts to find a device with no address, and reads
    # devices.json from the working directory for one with no key. Neither.
    if not ip or ip in ("Auto", "0.0.0.0") or not key:
        outcome["error"] = "invalid"
        return outcome
    try:
        device = _new_device(dev_id, ip, key, version)
    except ScannerUnavailable:
        raise
    except UnicodeError:   # a key tinytuya can't encode: this device, not tinytuya
        outcome["error"] = "invalid"
        return outcome
    except Exception as e:  # tinytuya itself is broken, for every device alike
        raise ScannerUnavailable(f"tinytuya failed to start a probe ({type(e).__name__})") from e
    try:
        if gateway:
            # Gateways polled without a cid answer "data unvalid", which would
            # otherwise switch the probe to device22.
            device.disabledetect = True
        try:
            result = device.status()
        except OSError:
            result = {"Err": _ERR_CONNECT}
        except Exception:
            outcome["error"] = "fault"
            return outcome
        return _classify(device, result, version, gateway, outcome)
    finally:
        try:
            device.close()
        except Exception:
            pass


def _classify(device, result, version, gateway, outcome):
    err = str(result.get("Err")) if isinstance(result, dict) and "Err" in result else None

    if version == V31:
        if isinstance(result, dict) and err is None:
            outcome["plaintext"] = True
            outcome["dev_id"] = result.get("devId") or result.get("gwId")
            outcome["match"] = bool(outcome["dev_id"])
        else:
            outcome["error"] = _error_kind(err)
        return outcome

    if isinstance(result, dict) and err is None:
        outcome["match"] = True
    elif version in ("3.4", "3.5") and _session_key_installed(device):
        # The session key negotiation finished, which proves the key even if
        # the status reply itself was an error.
        outcome["match"] = True
    elif err == "900" and b"data unvalid" in _as_bytes(result.get("Payload")):
        # Decrypted fine; just a gateway asked without a cid (detection off).
        outcome["match"] = True
    else:
        outcome["error"] = _error_kind(err)
        return outcome

    outcome["device22"] = (not gateway) and getattr(device, "dev_type", "") == "device22"
    return outcome


def _session_key_installed(device):
    # tinytuya swaps local_key for the session key only once the device's reply
    # passes the HMAC check and the negotiation finishes. remote_nonce is no
    # proof: tinytuya fills it in before that check, so a reply that fails it
    # (a wrong key) leaves it set too.
    real = getattr(device, "real_local_key", None)
    return bool(real) and getattr(device, "local_key", real) != real


def _as_bytes(value):
    if isinstance(value, bytes):
        return value
    return str(value or "").encode("utf-8", "replace")


def _error_kind(err):
    if err == _ERR_CONNECT:
        return "connect"
    if err == _ERR_OFFLINE:
        return "silent"
    if err == _ERR_TIMEOUT:
        return "timeout"
    return "key"   # 914 (handshake failed), 904/900 (didn't decrypt), no reply


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def is_gateway(device):
    """A hub for Zigbee or Bluetooth sub-devices. Tuya can mark a gateway `sub`
    too, so its category is what tells."""
    return device.get("category") in GATEWAY_CATEGORIES


def is_sub_device(device):
    """Reached through a gateway: marked `sub`, or naming another device as its
    gateway. A gateway never is, even when Tuya marks it `sub`."""
    if is_gateway(device):
        return False
    gateway_id = device.get("gateway_id")
    return bool(device.get("sub")) or bool(gateway_id and gateway_id != device.get("id"))


def _gateway_ids(devices):
    ids = {d.get("gateway_id") for d in devices if is_sub_device(d) and d.get("gateway_id")}
    ids |= {d.get("id") for d in devices if is_gateway(d)}
    return ids


def _roles(devices):
    """(gateway ids, sub-device ids) across the whole list. A device another
    one names as its gateway is a gateway, whatever its category."""
    gateways = _gateway_ids(devices)
    subs = {d["id"] for d in devices
            if d.get("id") and d["id"] not in gateways and is_sub_device(d)}
    return gateways, subs


def lend_keys(devices, known=None, explicit_only=False):
    """Local keys for the gateways Tuya lists without one.

    Tuya can leave a gateway's own entry without a local key and put that key
    on each of its sub-devices instead, often with no gateway_id to say whose
    they are. A sub-device's key is its gateway's, so a keyless gateway gets
    one back, in this order:

      1. from sub-devices that name it in gateway_id,
      2. from the sub-device whose key it answered to before (key_from in
         `known`, from an earlier scan or check),
      3. by elimination: one keyless gateway left, and one key left.

    With explicit_only, only the first applies.

    Returns (lent, unclaimed). lent is {gateway id: sub-device id whose key it
    gets}. unclaimed is {sub-device id: [sub-device ids]}, one entry per key no
    gateway could be given, under the first sub-device listed with it. Those
    keys are still worth trying: whatever answers to one is its sub-devices'
    gateway, even if the list can't say which gateway that is.
    """
    gateways, subs = _roles(devices)
    by_id = {d["id"]: d for d in devices if d.get("id")}
    keyed = {d["local_key"] for d in devices
             if d.get("id") and d.get("local_key") and d["id"] not in subs}
    keyless = [d["id"] for d in devices
               if d.get("id") in gateways and not d.get("local_key")]

    groups, named = {}, {}   # key -> sub-device ids, and the gateway ids they name
    for dev_id in (d["id"] for d in devices if d.get("id") in subs):
        sub = by_id[dev_id]
        key, gateway_id = sub.get("local_key"), sub.get("gateway_id")
        if not key or key in keyed:
            continue
        if gateway_id in by_id and gateway_id not in keyless:
            continue   # it goes with the gateway it names, whatever its key
        groups.setdefault(key, []).append(dev_id)
        if gateway_id:
            named.setdefault(key, set()).add(gateway_id)

    lent, taken = {}, set()
    for key, names in named.items():
        gateway_id = next(iter(names)) if len(names) == 1 else None
        if gateway_id in keyless and gateway_id not in lent:
            lent[gateway_id] = groups[key][0]
            taken.add(key)
    # From here on, a key whose sub-devices name a gateway is left alone: that
    # gateway is outside the list, or they don't agree on one.
    for gateway_id in [] if explicit_only else keyless:
        source = ((known or {}).get(gateway_id) or {}).get("key_from")
        key = (by_id.get(source) or {}).get("local_key")
        if (gateway_id not in lent and key in groups and source in groups[key]
                and key not in taken and key not in named):
            lent[gateway_id] = source
            taken.add(key)
    gateways_left = [g for g in keyless if g not in lent]
    keys_left = [k for k in groups if k not in taken and k not in named]
    if not explicit_only and len(gateways_left) == 1 and len(keys_left) == 1:
        lent[gateways_left[0]] = groups[keys_left[0]][0]
        taken.add(keys_left[0])

    unclaimed = {ids[0]: ids for key, ids in groups.items() if key not in taken}
    return lent, unclaimed


def _foreign(sub_ids, by_id):
    """Sub-devices that name a gateway the key wasn't lent to: one outside the
    list, or more than one. Their key never names a gateway in the list."""
    return any(by_id[i].get("gateway_id") for i in sub_ids)


def check_refusal(devices, device_id, known=None):
    """Why device_id can't be checked on its own: "sub_device", "no_local_key",
    or None when it can be."""
    device = next((d for d in devices if d.get("id") == device_id), None) or {}
    gateways, subs = _roles(devices)
    if device_id in subs:
        return "sub_device"
    if device.get("local_key"):
        return None
    lent, _ = lend_keys(devices, known)
    # A check of a keyless gateway also tries any key no sub-device ties to a gateway.
    by_id = {d["id"]: d for d in devices if d.get("id")}
    _, untied = lend_keys(devices, explicit_only=True)
    if device_id in lent or (
            device_id in gateways and any(not _foreign(ids, by_id) for ids in untied.values())):
        return None
    return "no_local_key"


def _plain_result(status, checked_at, ip=None, version=None, device22=False, **extra):
    result = {"status": status, "ip": ip, "version": version,
              "device22": bool(device22), "checked_at": checked_at}
    result.update(extra)
    return result


def budgets_for(pool_size):
    """(whole scan, per address) budgets in seconds, for that many keyed devices.

    An address that never matches costs keys x versions probes, so the per-address
    budget grows with the account (SECONDS_PER_KEY), and the scan's with it.
    """
    address = max(ADDRESS_BUDGET_SECONDS, SECONDS_PER_KEY * pool_size)
    return SCAN_BUDGET_SECONDS - ADDRESS_BUDGET_SECONDS + address, address


class _Scan:
    def __init__(self, targets, devices, known, progress, cancel, budget, address_budget,
                 clock, only, routers):
        self.clock = clock
        self.started = clock()
        self.cancel = cancel or threading.Event()
        self.report = progress
        self.known = {k: v for k, v in (known or {}).items() if v and v.get("ip")}
        self.only = None if only is None else set(only)

        # Gateways and sub-devices are told apart across the whole list, even
        # when only some of it is looked for: a sub-device's gateway_id can be
        # what marks a gateway, and its key what a keyless gateway answers to.
        gateways, sub_ids = _roles(devices)
        lent, unclaimed = lend_keys(devices, known)
        by_id = {d["id"]: d for d in devices if d.get("id")}
        # The keyless gateways a check asks about, except any its sub-devices
        # name in gateway_id: that is Tuya's own word, which no check overrides.
        named_by_subs = {by_id[i].get("gateway_id") for i in sub_ids}
        checking = [g for g in sorted(self.only or ()) if g in gateways and g in by_id
                    and not by_id[g].get("local_key") and g not in named_by_subs]
        if checking:
            # The IP typed for the check says which gateway is there, whichever
            # key answers: its own first, if it has one, then any other no
            # sub-device ties to a gateway. That undoes a check that took one
            # gateway for another, which a later scan would only repeat.
            own = {by_id[lent[g]]["local_key"] for g in checking if g in lent}
            _, untied = lend_keys(devices, explicit_only=True)
            unclaimed = {first: ids for first, ids in untied.items()
                         if by_id[first]["local_key"] not in own}
        # The account's own keys first, then the keys borrowed from sub-devices,
        # so a 3.1 device is asked with its own id before any borrowed one.
        candidates, borrowed = [], []
        for d in devices:
            dev_id = d.get("id")
            if not dev_id or dev_id in sub_ids:
                continue
            if d.get("local_key"):
                candidates.append(dict(d, _gateway=dev_id in gateways))
            elif dev_id in lent:
                source = lent[dev_id]
                borrowed.append(dict(d, local_key=by_id[source]["local_key"], _gateway=True,
                                     _key_from=source))
        candidates += borrowed
        self.unclaimed = {}   # pool id -> the sub-devices listed with that key
        for first, ids in unclaimed.items():
            pool_id = UNCLAIMED + first
            self.unclaimed[pool_id] = ids
            # Asked as the sub-device, the only id there is. From 3.4 on, the
            # id never goes over the wire.
            candidates.append({"id": pool_id, "local_key": by_id[first]["local_key"],
                               "_gateway": True, "_key_from": first, "_probe_id": first})

        # Unclaimed keys that can name a gateway, and the keyless gateways they
        # might name: in a scan, those not lent a key, and in a check, the one
        # checked.
        self.foreign = {p for p, ids in self.unclaimed.items() if _foreign(ids, by_id)}
        nameless_keys = set(self.unclaimed) - self.foreign
        if only is None:
            self.nameable = [d["id"] for d in devices if d.get("id") in gateways
                             and not d.get("local_key") and d["id"] not in lent]
        else:
            self.nameable = checking
        if not nameless_keys:
            self.nameable = []
        self.pool = [c for c in candidates
                     if only is None or c["id"] in self.only
                     or (c["id"] in nameless_keys and self.nameable)]
        self.by_id = {d["id"]: d for d in self.pool}
        self.devices = (sum(d["id"] not in self.unclaimed for d in self.pool)
                        + sum(g not in self.by_id for g in self.nameable))

        # Each sub-device takes its result from the gateway it names, or else
        # from whatever has its key, looked for first.
        key_owner = {c["local_key"]: c["id"] for c in candidates}
        key_owner.update({c["local_key"]: c["id"] for c in self.pool})
        self.subs = []   # (sub-device, pool id or gateway id)
        for d in devices:
            if d.get("id") not in sub_ids:
                continue
            gateway_id = d.get("gateway_id")
            owner = gateway_id if gateway_id in by_id else (
                key_owner.get(d.get("local_key")) or gateway_id)
            if only is None or owner in self.only or owner in self.by_id:
                self.subs.append((d, owner))

        default_budget, default_address_budget = budgets_for(len(self.pool))
        self.deadline = self.started + (default_budget if budget is None else budget)
        self.address_budget = default_address_budget if address_budget is None else address_budget

        ips = list(dict.fromkeys(targets))
        looked_for = [d["id"] for d in self.pool] + self.nameable
        for dev_id in looked_for:
            known_ip = self.known.get(dev_id, {}).get("ip")
            if known_ip and known_ip not in ips:
                ips.append(known_ip)
        self.ips = ips
        # A remembered device's own address is never taken for the router.
        self.routers = set(routers or ()) - {self.known.get(i, {}).get("ip") for i in looked_for}

        self.lock = threading.Lock()
        self.labels = {}
        self.matched = {}        # device id -> {"ip", "version", "device22"}
        self.claimed = {}        # ip -> device id
        self.key_failures = {}   # (ip, device id) -> key failures at its remembered version
        self.asked = set()       # (ip, device id)
        self.finished = set()    # addresses that were asked with every key still unmatched
        self.out_of_budget = set()
        self.probes = 0
        self.faults = 0
        self.progress = {"phase": "sweep", "addresses": len(ips), "swept": 0,
                         "open": 0, "probed": 0, "matched": 0, "devices": self.devices}

    # -- progress ----------------------------------------------------------- #
    def _emit(self, **changes):
        with self.lock:
            self.progress.update(changes)
            snapshot = dict(self.progress)
        if self.report:
            self.report(snapshot)

    def _stopped(self, address_deadline=None):
        if self.cancel.is_set():
            return True
        now = self.clock()
        return now >= self.deadline or (address_deadline is not None and now >= address_deadline)

    # -- the address workers ------------------------------------------------ #
    def _order(self, ip):
        """(device, version, remembered) steps to try at ip, the likeliest first.

        A device remembered at ip is asked at its remembered version twice
        before any other: one timeout must not read as a key that stopped
        working, and a key that really changed fails both times.
        """
        here = [d for d in self.pool if self.known.get(d["id"], {}).get("ip") == ip]
        elsewhere = [d for d in self.pool if d["id"] in self.known and d not in here]
        unknown = [d for d in self.pool if d["id"] not in self.known]
        # Devices remembered at another address are likely to be found there.
        rest = unknown + elsewhere
        # Keys borrowed from sub-devices go last at each version, so a device
        # with a key of its own is asked just as if there were none.
        rest = [d for d in rest if "_key_from" not in d] + [d for d in rest if "_key_from" in d]
        steps = []
        for dev in here:
            remembered = self.known[dev["id"]].get("version")
            if remembered in PASS_VERSIONS + (V31,):
                steps += [(dev, remembered, True)] * 2
            steps += [(dev, v, False) for v in PASS_VERSIONS if v != remembered]
        for version in PASS_VERSIONS:
            steps += [(dev, version, False) for dev in rest]
        return steps

    def _claim(self, ip, dev_id, version, device22):
        with self.lock:
            if dev_id in self.matched or ip in self.claimed:
                return False
            self.matched[dev_id] = {"ip": ip, "version": version, "device22": device22}
            self.claimed[ip] = dev_id
            matched = sum(k not in self.unclaimed for k in self.matched)
        self._emit(matched=matched)
        return True

    def _unmatched(self, dev_id):
        with self.lock:
            return dev_id not in self.matched

    def _ask(self, ip, dev, version):
        """Probe once. Returns (settled, outcome): settled when the address now
        has an owner, or turned out to be a 3.1 device outside this account."""
        outcome = probe(dev.get("_probe_id", dev["id"]), ip, dev["local_key"], version,
                        dev["_gateway"])
        with self.lock:
            self.asked.add((ip, dev["id"]))
            self.probes += 1
            self.faults += outcome["error"] == "fault"
        if version == V31:
            if outcome["dev_id"]:
                owner = self.by_id.get(outcome["dev_id"])
                if owner is not None:
                    self._claim(ip, owner["id"], V31, False)
                return True, outcome
            return False, outcome
        if outcome["match"]:
            self._claim(ip, dev["id"], version, outcome["device22"])
            return True, outcome
        return False, outcome

    def _work(self, ip):
        address_deadline = self.clock() + self.address_budget
        attempts = {}   # (device id, version) -> probes so far
        failures = 0
        try:
            for dev, version, remembered in self._order(ip):
                step = (dev["id"], version)
                if attempts.get(step, 0) >= (2 if remembered else 1):
                    continue
                if not self._unmatched(dev["id"]):
                    continue
                if self._stopped(address_deadline):
                    self._note_budget(ip)
                    return
                attempts[step] = attempts.get(step, 0) + 1
                settled, outcome = self._ask(ip, dev, version)
                if settled:
                    return
                # Only the remembered version speaks for the key: at any other,
                # a key error can just be the wrong version (3.5 on a v3.4
                # device fails exactly like a wrong key).
                if remembered and version in PASS_VERSIONS and outcome["error"] == "key":
                    with self.lock:
                        key = (ip, dev["id"])
                        self.key_failures[key] = self.key_failures.get(key, 0) + 1
                if outcome["error"] in ("connect", "silent"):
                    failures += 1
                    if failures >= 2:
                        # A wrong version can also drop the connection, so only
                        # give up if the address itself stopped answering.
                        state = tcp_state(ip)
                        if state != OPEN:
                            with self.lock:
                                self.labels[ip] = state
                            return
                        failures = 0
                else:
                    failures = 0
            if self._v31_pass(ip, address_deadline):
                with self.lock:
                    self.finished.add(ip)
        finally:
            with self.lock:
                self.progress["probed"] += 1
            self._emit()

    def _v31_pass(self, ip, address_deadline):
        """3.1 replies in plaintext, so any key "works" and the reply's devId is
        what identifies the device. One probe settles whether the address speaks
        3.1 at all; the rest are only for a device that ignores a query that
        doesn't carry its own id.

        Returns False if the budget or a cancel stopped it first."""
        for dev in list(self.pool):
            if not self._unmatched(dev["id"]):
                continue
            if self._stopped(address_deadline):
                self._note_budget(ip)
                return False
            settled, outcome = self._ask(ip, dev, V31)
            if settled:
                return True   # a 3.1 device, identified (or not one of this account's)
            if not outcome["plaintext"]:
                return True   # not a 3.1 device
        return True

    def _note_budget(self, ip):
        if not self.cancel.is_set():
            with self.lock:
                self.out_of_budget.add(ip)

    # -- the whole scan ----------------------------------------------------- #
    def run(self):
        require_scanner()
        self._emit()
        self.labels = sweep(self.ips, self.cancel, progress=lambda n: self._emit(swept=n))
        open_ips = [ip for ip in self.ips if self.labels.get(ip) == OPEN]
        self._emit(phase="probe", swept=len(self.ips), open=len(open_ips))

        if open_ips and self.pool and not self._stopped():
            with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as workers:
                try:
                    list(workers.map(self._work, open_ips))
                except BaseException:
                    # Ctrl+C, or one worker's error: stop the others before the
                    # pool waits for them, or each finishes its whole address.
                    self.cancel.set()
                    raise
        if self.probes and self.faults == self.probes:
            # One odd device can trip tinytuya; all of them means tinytuya is broken,
            # and "not found" for every device would be a lie.
            raise ScannerUnavailable("tinytuya failed on every probe")
        self._emit(phase="done")
        return self._outcome(open_ips)

    def _outcome(self, open_ips):
        cancelled = self.cancel.is_set()
        checked_at = time.time()
        named = self._name_unclaimed()
        # A gateway checked where another key answered than the one it had:
        # that key is its own now, and the old one's sub-devices aren't its.
        renamed = set(named.values()) & set(self.by_id)
        results = {}
        for dev in self.pool:
            dev_id = dev["id"]
            if dev_id in self.unclaimed or dev_id in renamed:
                continue
            # key_from names the sub-device whose key a keyless gateway got.
            extra = {"key_from": dev["_key_from"]} if "_key_from" in dev else {}
            if dev_id in self.matched:
                found = self.matched[dev_id]
                results[dev_id] = _plain_result(OK, checked_at, found["ip"],
                                                found["version"], found["device22"], **extra)
            elif not cancelled:
                results[dev_id] = dict(self._missing(dev_id, checked_at), **extra)

        for pool_id, gateway_id in named.items():
            found = self.matched[pool_id]
            results[gateway_id] = _plain_result(OK, checked_at, found["ip"], found["version"],
                                                key_from=self.by_id[pool_id]["_key_from"])
        for gateway_id in self.nameable:
            if gateway_id not in results and gateway_id in self.known and not cancelled:
                # It keeps the address it was judged at, even if nothing there
                # answered, so a failed check at a mistyped IP isn't saved over
                # what was known (see app._merge_lan).
                results[gateway_id] = dict(self._missing(gateway_id, checked_at),
                                           ip=self.known[gateway_id]["ip"])

        subs_reached = 0
        for sub, owner in self.subs:
            if owner in renamed:
                continue   # its key didn't answer where its gateway turned out to be
            if owner in self.unclaimed:
                found = self.matched.get(owner)
                gateway_id = named.get(owner)
                if self.only is not None and gateway_id is None:
                    continue   # a check speaks only for the gateway it was asked about
                gateway = _plain_result(OK, checked_at, found["ip"], found["version"]) if found else {}
            else:
                gateway_id, gateway = owner, results.get(owner) or {}
            if cancelled and gateway.get("status") != OK:
                continue
            # Still via_gateway when the gateway wasn't reached, with no IP or
            # version: that replaces an older result that had them.
            reached = gateway.get("status") == OK
            subs_reached += reached
            results[sub["id"]] = _plain_result(
                VIA_GATEWAY, checked_at,
                gateway.get("ip") if reached else None,
                gateway.get("version") if reached else None,
                gateway_id=sub.get("gateway_id") or gateway_id,
            )

        # Gateways that answered to a sub-device's key, but which keyless
        # gateway each one is, nothing tells: a check at its IP will.
        unnamed = sorted(
            ({"ip": found["ip"], "version": found["version"], "sub_devices": self.unclaimed[pool_id]}
             for pool_id, found in self.matched.items()
             if pool_id in self.unclaimed and pool_id not in self.foreign and pool_id not in named),
            key=lambda g: ipaddress.ip_address(g["ip"]),
        ) if set(self.nameable) - set(named.values()) else []

        refused = {ip for ip, s in self.labels.items() if s == REFUSED}
        # Only addresses asked with every key: one that ran out of time, or that
        # a cancel stopped, may still belong to a device here.
        unmatched = {ip for ip in self.finished
                     if ip not in self.claimed and self.labels.get(ip) == OPEN}
        late = {ip for ip in self.out_of_budget if ip not in self.claimed}
        # The likely router acts like nothing of ours, so it isn't listed as if
        # it might be one. "routers" records what was left out.
        routers = self.routers & (refused | unmatched | late)
        summary = {
            "addresses": len(self.ips),
            "open": len(open_ips),
            "refused": _sorted_ips(refused - routers),
            "unmatched": _sorted_ips(unmatched - routers),
            "out_of_budget": _sorted_ips(late - routers),
            "routers": _sorted_ips(routers),
            "devices": self.devices,
            "matched": sum(k not in self.unclaimed for k in self.matched) + len(named),
            "sub_devices": len(self.subs),
            "sub_devices_reached": subs_reached,
            "unnamed_gateways": unnamed,
            "duration": round(self.clock() - self.started, 1),
            "cancelled": cancelled,
            "finished_at": checked_at,
        }
        return {"results": results, "summary": summary}

    def _name_unclaimed(self):
        """{unclaimed pool id: gateway id}, for each unclaimed key that answered
        where exactly one keyless gateway is known to be: the IP typed in for a
        check of it, or one remembered from such a check."""
        at = {}
        for gateway_id in self.nameable:
            ip = self.known.get(gateway_id, {}).get("ip")
            if ip:
                at.setdefault(ip, []).append(gateway_id)
        named = {}
        for pool_id in set(self.unclaimed) - self.foreign:
            found = self.matched.get(pool_id)
            here = at.get(found["ip"], []) if found else []
            if len(here) == 1:
                named[pool_id] = here[0]
        return named

    def _missing(self, dev_id, checked_at):
        """Why a device wasn't matched, judged at the address it was last seen."""
        previous = self.known.get(dev_id) or {}
        ip = previous.get("ip")
        version, device22 = previous.get("version"), previous.get("device22", False)
        if not ip:
            return _plain_result(NOT_FOUND, checked_at, None, version, device22)
        state = self.labels.get(ip)
        if self.claimed.get(ip, dev_id) != dev_id:
            status, ip = NOT_FOUND, None   # another device answers there now
        elif state == REFUSED:
            status = BUSY
        elif state == OPEN and self.key_failures.get((ip, dev_id), 0) >= 2:
            status = KEY_MISMATCH   # its remembered version failed on the key twice
        elif state == OPEN and (ip, dev_id) not in self.asked:
            status, ip = NOT_FOUND, None   # the budget ran out before it was asked
        else:
            status = UNREACHABLE
        return _plain_result(status, checked_at, ip, version, device22)


def scan(targets, devices, known=None, progress=None, cancel=None,
         budget=None, address_budget=None, clock=time.monotonic, only=None, routers=None):
    """Match `devices` to addresses among `targets` and learn their versions.

    targets:  IPs from parse_targets(). Addresses in `known` are added.
    devices:  dicts with id, name, local_key, and sub/gateway_id/category.
              A gateway listed without a key is tried with its sub-devices'
              keys (lend_keys()).
    known:    {device id: {"ip", "version", "device22", "key_from"}} from an
              earlier scan; each device is tried first at its remembered
              address and version. For a keyless gateway, key_from is the
              sub-device whose key it answered to.
    progress: called with a dict of counters as the scan moves along.
    cancel:   a threading.Event; a cancelled scan reports only what it matched.
    budget, address_budget: seconds for the whole scan and for one address. By
              default they grow with the number of devices (budgets_for()).
    only:     device ids to look for, for a check of one device. The rest of
              `devices` still tell which are gateways, and the sub-devices of
              the ones looked for get results through them. A keyless gateway
              checked at an IP takes whichever unclaimed key answers there.
    routers:  addresses that are probably the router (likely_routers()). They
              are scanned like the rest, but unless a device answers there they
              are left out of the summary's lists (and recorded under "routers").

    Returns {"results": {device id: result}, "summary": {...}}. No key appears
    in either. The summary's unnamed_gateways lists the addresses where a
    gateway answered to a sub-device's key while more than one keyless gateway
    could be the one there. Raises ScannerUnavailable when tinytuya can't do
    the job.
    """
    return _Scan(targets, devices, known, progress, cancel, budget, address_budget,
                 clock, only, routers).run()


# --------------------------------------------------------------------------- #
# Comparing two scans
# --------------------------------------------------------------------------- #
def version_label(version, device22=False):
    if not version:
        return ""
    return f"{version} (device22)" if device22 else str(version)


def diff_results(previous, current, names):
    """What changed for devices that were reachable before, keyed like
    tuya_devices.diff_devices(): a moved version or IP breaks a local
    integration as surely as a rotated key does.

    Only a device that was OK before counts; the rest had nothing to compare.
    """
    changes = {"version_changed": [], "ip_changed": [], "local_key_failed": []}
    for dev_id, now in current.items():
        was = (previous or {}).get(dev_id) or {}
        if was.get("status") != OK:
            continue
        name = names.get(dev_id) or ""
        if now.get("status") == OK:
            before = version_label(was.get("version"), was.get("device22"))
            after = version_label(now.get("version"), now.get("device22"))
            if before != after:
                changes["version_changed"].append(
                    {"id": dev_id, "name": name, "was": before, "now": after})
            if was.get("ip") != now.get("ip"):
                changes["ip_changed"].append(
                    {"id": dev_id, "name": name, "was": was.get("ip") or "", "now": now.get("ip") or ""})
        elif now.get("status") == KEY_MISMATCH:
            changes["local_key_failed"].append({"id": dev_id, "name": name})
    for entries in changes.values():
        entries.sort(key=lambda e: (e["name"].lower(), e["id"]))
    return changes
