"""Encrypted history of observed configuration changes, independent of caches.

The baseline and events are one atomic document. Only fresh, accepted
observations enter this store; cached responses and UI filters never do.
"""

import base64
import copy
import hashlib
import hmac
import ipaddress
import json
import math
import os
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

import lan_scan
import tuya_devices as core

VERSION = 1
MAX_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 10000
KINDS = frozenset({
    "added", "removed", "reappeared", "renamed", "key_changed", "key_available",
    "key_unavailable", "home_changed", "room_changed", "room_renamed",
    "gateway_changed", "node_changed", "role_changed", "identity_changed",
    "ip_changed", "protocol_changed", "connection_changed", "sub_device_changed",
})
_maintenance_stores = weakref.WeakSet()
_maintenance_lock = threading.Lock()
_maintenance_started = False


def _schedule_maintenance(store):
    global _maintenance_started
    with _maintenance_lock:
        _maintenance_stores.add(store)
        if _maintenance_started:
            return
        _maintenance_started = True

    def maintain():
        sleeper = threading.Event()
        while not sleeper.wait(3600):
            with _maintenance_lock:
                stores = list(_maintenance_stores)
            for item in stores:
                try:
                    item.maintain()
                except HistoryError as error:
                    item.error = str(error)

    threading.Thread(target=maintain, daemon=True, name="history-retention").start()


class HistoryError(Exception):
    """A safe error code; never include provider payloads or secrets."""


def _identifier(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _account(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _timestamp(value, nullable=False):
    if value is None:
        return nullable
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_text(value):
    return (isinstance(value, str) and len(value) <= 2048
            and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))


def _room(value):
    return (isinstance(value, dict) and value.get("state") in {"assigned", "unassigned"}
            and (value["state"] != "assigned" or _identifier(value.get("id")))
            and ("name" not in value or _valid_text(value["name"])))


def _protocol(value):
    return (isinstance(value, dict) and value.get("version") in {"3.1", "3.3", "3.4", "3.5"}
            and type(value.get("device22")) is bool)


def _ip(value):
    if not isinstance(value, str):
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _home_change(value):
    return (isinstance(value, list) and len(value) <= 256 and all(
        _identifier(item) or isinstance(item, dict) and _identifier(item.get("id"))
        and _valid_text(item.get("name")) for item in value))


def _valid_change(change):
    if not isinstance(change, dict) or change.get("kind") not in KINDS:
        return False
    if "previous_observed_at" in change and not _timestamp(change["previous_observed_at"], nullable=True):
        return False
    if "key_authenticated" in change and type(change["key_authenticated"]) is not bool:
        return False
    kind = change["kind"]
    # Private/legacy key values are ignored by the public projection. Never
    # inspect or export them as display values, even in an older document.
    if kind in {"key_changed", "key_available", "key_unavailable"}:
        return True
    validators = {
        "renamed": _valid_text, "room_renamed": _valid_text,
        "gateway_changed": lambda v: v is None or _valid_text(v),
        "node_changed": lambda v: v is None or _valid_text(v),
        "identity_changed": _valid_text, "home_changed": _home_change,
        "room_changed": _room, "ip_changed": _ip, "protocol_changed": _protocol,
        "role_changed": lambda v: type(v) is bool,
        "sub_device_changed": lambda v: type(v) is bool,
        "connection_changed": lambda v: isinstance(v, str) and v in {"ok", "busy", "unreachable", "key_mismatch"},
    }
    validate = validators.get(kind)
    values = [change[key] for key in ("before", "after") if key in change]
    return not values if validate is None else bool(values) and all(validate(v) for v in values)


def _valid_document(doc):
    """Validate every structure used by comparisons before pruning or mutation.

    Unknown private metadata may remain in storage, but required fields and
    known nested values must be usable. A decryptable malformed document is
    recoverable through the same explicit reset as invalid ciphertext.
    """
    required = {"version", "account_id", "epoch", "next_sequence", "started_at", "cloud_at",
                "cloud", "lan", "events", "operations", "pruned_through", "clock_at"}
    if not required <= doc.keys():
        return False
    if (not _account(doc["account_id"]) or not isinstance(doc["epoch"], str)
            or len(doc["epoch"]) != 32 or any(c not in "0123456789abcdef" for c in doc["epoch"])
            or type(doc["next_sequence"]) is not int or doc["next_sequence"] < 1
            or type(doc["pruned_through"]) is not int or not 0 <= doc["pruned_through"] < doc["next_sequence"]
            or not _timestamp(doc["started_at"]) or not _timestamp(doc["clock_at"])
            or not _timestamp(doc["cloud_at"], nullable=True)
            or not isinstance(doc["cloud"], dict) or not isinstance(doc["lan"], dict)
            or not isinstance(doc["events"], list) or not isinstance(doc["operations"], list)
            or "lan_cache_seeded" in doc and type(doc["lan_cache_seeded"]) is not bool
            or "clear_revision" in doc and (type(doc["clear_revision"]) is not int or doc["clear_revision"] < 0)
            or len(doc["operations"]) > 512 or any(not isinstance(op, str) or not 0 < len(op) <= 256
                                                 for op in doc["operations"])):
        return False
    clears = doc.get("device_clears", {})
    if not isinstance(clears, dict) or any(
            not _identifier(device_id) or not isinstance(marker, dict)
            or type(marker.get("through")) is not int
            or not 0 <= marker["through"] < doc["next_sequence"]
            or "revision" in marker and (type(marker["revision"]) is not int
                                         or not 0 < marker["revision"] <= doc.get("clear_revision", 0))
            or not _timestamp(marker.get("at")) for device_id, marker in clears.items()):
        return False
    for dev_id, device in doc["cloud"].items():
        if (not _identifier(dev_id) or not isinstance(device, dict) or device.get("id") != dev_id
                or type(device.get("present")) is not bool or not _timestamp(device.get("observed_at"))):
            return False
        if any(field in device and not _valid_text(device[field]) for field in ("name", "uuid", "category", "node_id")):
            return False
        if ("gateway_id" in device and device["gateway_id"] is not None and not _valid_text(device["gateway_id"])
                or "sub" in device and type(device["sub"]) is not bool
                or "key_owner" in device and not _identifier(device["key_owner"])
                or "key_hash" in device and not _account(device["key_hash"])
                or "owned_key_hash" in device and not _account(device["owned_key_hash"])
                or "key_state" in device and device["key_state"] not in {"available", "unavailable"}
                or device.get("key_state") == "available" and "key_hash" not in device
                or "room" in device and not _room(device["room"])
                or "removed_at" in device and not _timestamp(device["removed_at"])
                or not device["present"] and "removed_at" not in device):
            return False
        if "home_ids" in device and (not isinstance(device["home_ids"], list)
                or len(device["home_ids"]) > 256 or any(not _identifier(i) for i in device["home_ids"])):
            return False
        if "home_names" in device and (not isinstance(device["home_names"], dict)
                or any(not _identifier(i) or not _valid_text(name) for i, name in device["home_names"].items())):
            return False
        if "field_at" in device and (not isinstance(device["field_at"], dict)
                or any(not _timestamp(value) for value in device["field_at"].values())):
            return False
    for dev_id, result in doc["lan"].items():
        if (not _identifier(dev_id) or not isinstance(result, dict) or not _timestamp(result.get("observed_at"))
                or "status" in result and result["status"] not in {"ok", "busy", "unreachable", "key_mismatch"}
                or "status" not in result and "sub_online" not in result
                or "ip" in result and not _ip(result["ip"])
                or "protocol" in result and not _protocol(result["protocol"])
                or "confirmed_at" in result and not _timestamp(result["confirmed_at"])
                or "gateway_id" in result and not _identifier(result["gateway_id"])
                or "key_from" in result and not _identifier(result["key_from"])
                or "sub_online" in result and (type(result["sub_online"]) is not bool or not _identifier(result.get("gateway_id")))):
            return False
    last = 0
    for event in doc["events"]:
        if (not isinstance(event, dict) or type(event.get("sequence")) is not int
                or not last < event["sequence"] < doc["next_sequence"]
                or not _identifier(event.get("device_id")) or not _valid_text(event.get("name"))
                or event.get("source") not in {"cloud", "lan"}
                or not isinstance(event.get("operation_id"), str) or not 0 < len(event["operation_id"]) <= 256
                or not _timestamp(event.get("observed_at")) or "previous_observed_at" not in event
                or not _timestamp(event["previous_observed_at"], nullable=True)
                or not isinstance(event.get("related_device_ids"), list)
                or any(not _identifier(i) for i in event["related_device_ids"])
                or not isinstance(event.get("changes"), list) or not event["changes"]
                or any(not _valid_change(change) for change in event["changes"])):
            return False
        last = event["sequence"]
    return True


def _remembered_source_current(gateway, source, doc, by_id, projected, gateways):
    previous = doc["cloud"].get(source) or {}
    current = projected.get(source) or {}
    if (source not in by_id or source in gateways or not previous.get("present")
            or not lan_scan.is_sub_device(by_id[source])
            or current.get("gateway_id") and current["gateway_id"] != gateway):
        return False
    return all(previous[field] == current[field] for field in (
        "gateway_id", "node_id", "sub", "category", "uuid") if field in previous and field in current)


def _text(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise HistoryError("invalid_observation")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise HistoryError("invalid_observation")
    return value


def _fingerprint(secret, value):
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _new(account, now):
    return {"version": VERSION, "account_id": account, "epoch": uuid.uuid4().hex,
            "next_sequence": 1, "started_at": now, "cloud_at": None,
            "cloud": {}, "lan": {}, "events": [], "operations": [],
            "pruned_through": 0, "clock_at": now, "device_clears": {}, "clear_revision": 0,
            "lan_cache_seeded": False}


def _parse_epoch(value):
    """(journal epoch, clear revision) of an epoch() token. A bare epoch is revision 0."""
    journal, _, revision = str(value).partition(".")
    try:
        number = int(revision) if revision else 0
    except ValueError:
        number = 0
    return journal, max(number, 0)


def _key_view(record):
    """Key fields to compare. A sub-device that uses its hub's key is compared
    by the private fingerprint of the key Tuya lists for it."""
    if not record.get("key_owner"):
        return record
    view = dict(record, key_state="available")
    view.pop("key_hash", None)
    if record.get("owned_key_hash"):
        view["key_hash"] = record["owned_key_hash"]
    return view


def _event_visible(event, device_id, clears):
    audience = {event["device_id"], *event["related_device_ids"]}
    if device_id:
        audience &= {device_id}
    return any(event["sequence"] > clears.get(dev_id, {}).get("through", 0) for dev_id in audience)


def _cloud_device(device, secret):
    dev_id = device.get("id")
    if not _identifier(dev_id):
        raise HistoryError("invalid_observation")
    result = {"id": dev_id, "present": True}
    for field in ("name", "uuid", "category", "gateway_id", "node_id"):
        if field in device and device[field] is not None:
            result[field] = _text(device[field])
        elif field == "gateway_id" and field in device:
            result[field] = None
    if type(device.get("sub")) is bool:
        result["sub"] = device["sub"]
    if "local_key" in device:
        key = device["local_key"]
        if key is None or key == "":
            result["key_state"] = "unavailable"
        elif isinstance(key, str) and len(key) <= 256:
            result.update(key_state="available", key_hash=_fingerprint(secret, key))
        else:
            raise HistoryError("invalid_observation")
    if isinstance(device.get("home_ids"), list):
        ids = device["home_ids"]
        if len(ids) > 256 or any(not _identifier(i) for i in ids):
            raise HistoryError("invalid_observation")
        result["home_ids"] = sorted(set(ids))
        names = device.get("home_names")
        if isinstance(names, list) and len(names) == len(ids) and all(isinstance(n, str) and len(n) <= 2048 for n in names):
            result["home_names"] = dict(zip(ids, names))
    if device.get("room_status") == "unassigned":
        result["room"] = {"state": "unassigned"}
    elif device.get("room_status") == "assigned" and _identifier(device.get("room_id")):
        result["room"] = {"state": "assigned", "id": device["room_id"]}
        if isinstance(device.get("room_name"), str):
            result["room"]["name"] = _text(device["room_name"])
    return result


def _change(kind, before=None, after=None, **extra):
    result = {"kind": kind, **extra}
    if before is not None:
        result["before"] = before
    if after is not None:
        result["after"] = after
    return result


def _public_event(event):
    """Keep export/API fields independent of future private storage metadata."""
    public = {key: event[key] for key in (
        "sequence", "device_id", "name", "source", "observed_at", "previous_observed_at",
        "operation_id", "related_device_ids")}
    public["changes"] = []
    for change in event["changes"]:
        fields = ("kind", "previous_observed_at", "key_authenticated")
        if change["kind"] not in {"key_changed", "key_available", "key_unavailable"}:
            fields += ("before", "after")
        public["changes"].append({key: change[key] for key in fields if key in change})
    return public


def _cloud_changes(previous, current):
    changes = []
    for field, kind in (("name", "renamed"), ("home_ids", "home_changed"),
                        ("gateway_id", "gateway_changed"), ("node_id", "node_changed"),
                        ("sub", "role_changed"), ("uuid", "identity_changed")):
        if field in previous and field in current and previous[field] != current[field]:
            before, after = previous[field], current[field]
            if field == "home_ids":
                before = [{"id": i, "name": previous.get("home_names", {}).get(i) or i} for i in before]
                after = [{"id": i, "name": current.get("home_names", {}).get(i) or i} for i in after]
            changes.append(_change(kind, before, after))
    old_hash, new_hash = previous.get("key_hash"), current.get("key_hash")
    if new_hash and old_hash and new_hash != old_hash:
        changes.append(_change("key_changed"))
    elif previous.get("key_state") == "unavailable" and current.get("key_state") == "available":
        changes.append(_change("key_available"))
    elif previous.get("key_state") == "available" and current.get("key_state") == "unavailable":
        changes.append(_change("key_unavailable"))
    before, after = previous.get("room"), current.get("room")
    if before is not None and after is not None:
        if (before.get("state"), before.get("id")) != (after.get("state"), after.get("id")):
            changes.append(_change("room_changed", before, after))
        elif "name" in before and "name" in after and before["name"] != after["name"]:
            changes.append(_change("room_renamed", before["name"], after["name"]))
    return changes


def _append(doc, device, changes, source, now, previous_at, operation, related=()):
    if not changes:
        return
    doc["events"].append({
        "sequence": doc["next_sequence"], "device_id": device["id"],
        "name": device.get("name") or device["id"], "source": source,
        "observed_at": now, "previous_observed_at": previous_at,
        "operation_id": operation, "changes": changes,
        "related_device_ids": sorted(set(related) - {device["id"]}),
    })
    doc["next_sequence"] += 1


class HistoryStore:
    """Bounded single-account journal with serialized read/modify/write.

    Advisory file locking also prevents simultaneous independent processes
    from overwriting a journal read before another writer committed it.
    The web app still owns account transitions and acquisition generation checks.
    """

    def __init__(self, path, key_path, *, enabled=True, persist=True,
                 retention_days=90, max_events=MAX_EVENTS, clock=time.time, forbidden_paths=()):
        self.path, self.key_path = Path(path), Path(key_path)
        self.enabled, self.persist = enabled, persist
        self.retention_days, self.max_events = retention_days, max_events
        self.clock = clock
        self._lock = threading.RLock()
        self._memory = self._empty = None
        self._memory_secret = os.urandom(32)
        self._maintenance_account = None
        self._pending_delete = False
        self.error = None
        # Lasts for this process: nothing prunes, clears or rewrites the
        # journal with limits nobody configured.
        self._config_error = None
        try:
            protected = {Path(p).resolve() for p in forbidden_paths}
            self._paths_safe = (self.path.resolve() != self.key_path.resolve()
                                and self.path.resolve() not in protected and self.key_path.resolve() not in protected)
        except OSError:
            self._paths_safe = False
        limits_valid = (type(retention_days) is int and 1 <= retention_days <= 3650
                        and type(max_events) is int and 1 <= max_events <= MAX_EVENTS)
        if not self._paths_safe:
            self._config_error = "history_configuration"
        elif not enabled or not persist:
            # An explicit "off" removes saved history even with an invalid
            # retention value: no retention applies to deleted files.
            try:
                self.purge()
            except HistoryError as error:
                self.error = str(error)
        if self._paths_safe and enabled and not limits_valid:
            self._config_error = "history_configuration"
        if self._config_error:
            self.error = self._config_error

    @contextmanager
    def _guard(self):
        with self._lock:
            if not self._paths_safe:
                raise HistoryError("history_configuration")
            if not self.persist:
                yield
                return
            try:
                import fcntl
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(descriptor)
                    raise HistoryError("history_busy") from None
            except ImportError:
                raise HistoryError("history_lock_unavailable") from None
            except OSError:
                raise HistoryError("history_unavailable") from None
            try:
                yield
            finally:
                os.close(descriptor)

    def _cipher(self, create=False):
        try:
            if self.key_path.stat().st_size > 4096:
                raise HistoryError("history_key_unavailable")
            key = self.key_path.read_bytes().strip()
        except FileNotFoundError:
            if not create or self.path.exists():
                raise HistoryError("history_key_unavailable") from None
            key = Fernet.generate_key()
            core.atomic_write(self.key_path, key, prefix=".history-key-")
        try:
            return Fernet(key), hmac.new(key, b"history-comparison-v1", hashlib.sha256).digest()
        except (ValueError, TypeError):
            raise HistoryError("history_key_unavailable") from None

    def _read(self, account):
        if not _account(account):
            raise HistoryError("account_identity_unavailable")
        if not self.persist:
            doc = self._memory
        else:
            try:
                if self.path.stat().st_size > MAX_BYTES * 2:
                    raise HistoryError("history_invalid")
                cipher, _ = self._cipher()
                doc = json.loads(cipher.decrypt(self.path.read_bytes()))
            except FileNotFoundError:
                doc = None
            except (InvalidToken, ValueError, UnicodeError, RecursionError):
                raise HistoryError("history_invalid") from None
            except OSError:
                raise HistoryError("history_unavailable") from None
        if doc is None:
            if self._empty is None or self._empty["account_id"] != account:
                self._empty = _new(account, self.clock())
            return copy.deepcopy(self._empty)
        if not isinstance(doc, dict) or type(doc.get("version")) is not int:
            raise HistoryError("history_invalid")
        if doc["version"] != VERSION:
            raise HistoryError("history_version")
        try:
            valid = _valid_document(doc)
        except (TypeError, ValueError, AttributeError, RecursionError):
            valid = False
        if not valid:
            raise HistoryError("history_invalid")
        if doc.get("account_id") != account:
            raise HistoryError("account_changed")
        return copy.deepcopy(doc)

    def _prune(self, doc):
        if self._config_error:
            raise HistoryError(self._config_error)
        now = self.clock()
        # A backward clock correction must not expire events out of order.
        clock_at = doc.get("clock_at", now)
        cutoff = now - self.retention_days * 86400
        events = doc["events"]
        retained = [e for e in events if e["observed_at"] >= cutoff] if now >= clock_at else events
        retained = retained[-self.max_events:]
        kept = {e["sequence"] for e in retained}
        removed = [e["sequence"] for e in events if e["sequence"] not in kept]
        if removed:
            doc["pruned_through"] = max(doc.get("pruned_through", 0), *removed)
        doc["events"] = retained
        for dev_id, device in list(doc["cloud"].items()):
            if not device.get("present") and device.get("removed_at", now) < cutoff:
                del doc["cloud"][dev_id]
                doc["lan"].pop(dev_id, None)
        audience = {dev_id for event in retained for dev_id in (event["device_id"], *event["related_device_ids"])}
        for dev_id in list(doc.get("device_clears", {})):
            if dev_id not in doc["cloud"] and dev_id not in doc["lan"] and dev_id not in audience:
                del doc["device_clears"][dev_id]
        # Reads need not rewrite the encrypted document just to change a clock.

    def _write(self, doc):
        try:
            valid = _valid_document(doc)
        except (TypeError, ValueError, AttributeError, RecursionError):
            valid = False
        if not valid:
            raise HistoryError("invalid_observation")
        self._prune(doc)
        payload = json.dumps(doc, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        while len(payload) > MAX_BYTES and doc["events"]:
            removed = doc["events"][:max(1, len(doc["events"]) // 4)]
            doc["pruned_through"] = max(doc["pruned_through"], removed[-1]["sequence"])
            del doc["events"][:len(removed)]
            payload = json.dumps(doc, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        if len(payload) > MAX_BYTES:
            raise HistoryError("history_limit")
        if self.persist:
            cipher, _ = self._cipher(create=True)
            core.atomic_write(self.path, cipher.encrypt(payload), prefix=".history-")
            # Persist the rename as well as the already-fsynced file contents.
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        else:
            self._memory = copy.deepcopy(doc)
        self._empty = None

    def _apply(self, account, operation, mutate, *, epoch=None):
        if not self.enabled:
            return None
        if self._config_error:
            raise HistoryError(self._config_error)
        if self.persist and self._pending_delete:
            raise HistoryError("history_delete_failed")
        try:
            with self._guard():
                doc = self._read(account)
                skip = set()
                if epoch is not None:
                    journal, revision = _parse_epoch(epoch)
                    if journal != doc["epoch"]:
                        return None  # a reset invalidates already-running observations
                    # A device cleared after this observation began keeps its
                    # earlier baseline. Only its part of the result is dropped.
                    skip = {dev_id for dev_id, marker in doc.get("device_clears", {}).items()
                            if marker.get("revision", 0) > revision}
                if operation in doc["operations"]:
                    return doc["epoch"]
                if not isinstance(operation, str) or not 0 < len(operation) <= 256:
                    raise HistoryError("invalid_observation")
                self._prune(doc)
                mutate(doc, skip)
                doc["clock_at"] = max(self.clock(), doc.get("clock_at", 0))
                doc["operations"] = (doc["operations"] + [operation])[-512:]
                self._write(doc)
                if not self._pending_delete:
                    self.error = None
                self._maintenance_account = account
                if self.persist:
                    _schedule_maintenance(self)
                return doc["epoch"]
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            self.error = "history_save_failed"
            raise HistoryError(self.error) from None
        except HistoryError as error:
            self.error = str(error)
            raise

    def observe_cloud(self, account, devices, operation, *, epoch=None, observed_at=None, seed=False):
        now = self.clock() if observed_at is None else observed_at

        def mutate(doc, skip):
            if not _timestamp(now):
                raise HistoryError("invalid_observation")
            secret = self._cipher(create=True)[1] if self.persist else self._memory_secret
            projected = [_cloud_device(device, secret) for device in devices]
            if len({d["id"] for d in projected}) != len(projected):
                raise HistoryError("invalid_observation")
            by_id = {d["id"]: d for d in devices}
            projected_by_id = {d["id"]: d for d in projected}
            gateways = lan_scan.gateway_ids(devices)
            for device in projected:
                if "sub" in device:
                    device["sub"] = device["id"] not in gateways and lan_scan.is_sub_device(by_id[device["id"]])
            lent, _ = lan_scan.lend_keys(devices, explicit_only=True)
            inherited_baselines = {}
            for gateway in gateways:
                source = (doc["lan"].get(gateway) or {}).get("key_from")
                if _remembered_source_current(gateway, source, doc, by_id, projected_by_id, gateways):
                    if gateway not in lent:
                        lent[gateway] = source
                    previous_source = doc["cloud"][source]
                    previous_gateway = doc["cloud"].get(gateway) or {}
                    if (lent.get(gateway) == source and not previous_gateway.get("key_hash")
                            and previous_source.get("key_hash")):
                        inherited_baselines[gateway] = {
                            "key_hash": previous_source["key_hash"], "key_state": "available",
                            "field_at": {**previous_gateway.get("field_at", {}),
                                         "key_hash": previous_source.get("field_at", {}).get(
                                             "key_hash", previous_source["observed_at"])}}
            # Each gateway's key before this observation, to tell its own
            # rotation from a sub-device moving onto it.
            prior_keys = {gateway: {**(doc["cloud"].get(gateway) or {}),
                                    **inherited_baselines.get(gateway, {})}.get("key_hash")
                          for gateway in gateways}
            effective_keys = {}
            for gateway in gateways:
                raw_key = (by_id.get(gateway) or {}).get("local_key")
                source_key = (by_id.get(lent.get(gateway)) or {}).get("local_key")
                key = raw_key or source_key
                if key and gateway in projected_by_id:
                    projected_by_id[gateway].update(key_state="available", key_hash=_fingerprint(secret, key))
                    effective_keys.setdefault(key, set()).add(gateway)
            owners = {}
            for device in devices:
                if device["id"] in gateways or not lan_scan.is_sub_device(device):
                    continue
                candidates = effective_keys.get(device.get("local_key"), set())
                named = device.get("gateway_id")
                owner = named if named in candidates else next(iter(candidates)) if len(candidates) == 1 and not named else None
                if owner:
                    child = projected_by_id[device["id"]]
                    listed = child.pop("key_hash", None)
                    child.pop("key_state", None)
                    child["key_owner"] = owner
                    if listed:
                        # Private like key_hash: the key Tuya lists for it, so a
                        # move to another hub is not taken for that hub's rotation.
                        child["owned_key_hash"] = listed
                    owners.setdefault(owner, []).append(device["id"])
            initial = doc["cloud_at"] is None or seed
            if initial:
                skip = frozenset()
            previous = doc["cloud"]
            current_ids = {d["id"] for d in projected}
            for device in projected:
                dev_id = device["id"]
                if dev_id in skip:
                    continue  # cleared after this observation began
                before = {**(previous.get(dev_id) or {}), **inherited_baselines.get(dev_id, {})}
                changes = []
                if not initial:
                    if not before:
                        changes = [_change("added")]
                    else:
                        if not before.get("present"):
                            changes.append(_change("reappeared"))
                        changes.extend(_cloud_changes(_key_view(before), _key_view(device)))
                        owner = device.get("key_owner")
                        if owner and prior_keys.get(owner) and _key_view(before).get("key_hash") == prior_keys[owner]:
                            # Its hub's own rotation: one entry on the hub, linked to this device.
                            changes = [change for change in changes if change["kind"] != "key_changed"]
                fields = {"renamed": "name", "key_changed": "key_hash", "key_available": "key_state",
                          "key_unavailable": "key_state", "home_changed": "home_ids", "room_changed": "room",
                          "room_renamed": "room_name", "gateway_changed": "gateway_id", "node_changed": "node_id",
                          "role_changed": "sub", "identity_changed": "uuid"}
                known_at = before.get("field_at", {})
                for change in changes:
                    timestamp = known_at.get(fields.get(change["kind"]))
                    if timestamp is not None and timestamp != before.get("observed_at"):
                        change["previous_observed_at"] = timestamp
                combined = {**before, **device, "observed_at": now}
                combined["field_at"] = {**known_at, **{field: now for field in fields.values() if field in device}}
                if "name" in device.get("room", {}):
                    combined["field_at"]["room_name"] = now
                if device.get("key_owner"):
                    combined.pop("key_hash", None)
                    combined.pop("key_state", None)
                    combined["field_at"].update(key_hash=now, key_state=now)
                else:
                    # A sub-device that no longer uses a hub's key keeps no stale owner.
                    combined.pop("key_owner", None)
                    combined.pop("owned_key_hash", None)
                # An unknown optional room name cannot erase a known label.
                if (before.get("room", {}).get("id") == device.get("room", {}).get("id")
                        and "room" in device):
                    combined["room"] = {**before.get("room", {}), **device["room"]}
                combined.pop("removed_at", None)
                _append(doc, combined, changes, "cloud", now, before.get("observed_at"), operation,
                        [i for i in owners.get(dev_id, ()) if i not in skip])
                if any(c["kind"] in {"identity_changed", "gateway_changed", "node_changed", "role_changed"}
                       for c in changes):
                    doc["lan"].pop(dev_id, None)
                    # Nor may a keyless gateway keep borrowing this device's key:
                    # its next LAN check has to show which key it answers to.
                    for result in doc["lan"].values():
                        if result.get("key_from") == dev_id:
                            del result["key_from"]
                previous[dev_id] = combined
            for dev_id, device in previous.items():
                if dev_id in skip:
                    continue
                if dev_id not in current_ids and device.get("present"):
                    if not initial:
                        _append(doc, device, [_change("removed")], "cloud", now,
                                device.get("observed_at"), operation)
                    device.update(present=False, removed_at=now)
            doc["cloud_at"] = now

        return self._apply(account, operation, mutate, epoch=epoch)

    def seed_lan_cache(self, account, results, devices, *, epoch=None):
        """Import trusted pre-history observations once, without creating events.

        The caller checks cached key/relationship evidence against current
        devices. Never replace an existing history baseline or reintroduce one
        after a later identity change deliberately invalidated it.
        """
        current = {device["id"] for device in devices}
        now = self.clock()

        def mutate(doc, skip):
            if doc.get("lan_cache_seeded"):
                return
            invalidated = {}
            for event in doc["events"]:
                if any(change["kind"] in {"identity_changed", "gateway_changed", "node_changed", "role_changed"}
                       for change in event["changes"]):
                    invalidated[event["device_id"]] = max(invalidated.get(event["device_id"], 0), event["observed_at"])
            for dev_id, result in results.items():
                observed_at = result.get("checked_at")
                if (dev_id in skip or dev_id not in current or dev_id in doc["lan"]
                        or not _timestamp(observed_at) or observed_at > now):
                    continue
                dependencies = {dev_id, result.get("gateway_id"), result.get("key_from")} - {None}
                if any(invalidated.get(dep, 0) >= observed_at for dep in dependencies):
                    continue
                if result.get("status") == lan_scan.OK:
                    version = str(result.get("version") or "")
                    protocol = {"version": "3.3" if version == "3.2" else version,
                                "device22": bool(result.get("device22")) or version == "3.2"}
                    if not _ip(result.get("ip")) or not _protocol(protocol):
                        continue
                    baseline = {"status": lan_scan.OK, "ip": str(ipaddress.ip_address(result["ip"])),
                                "protocol": protocol, "observed_at": observed_at, "confirmed_at": observed_at}
                    if _identifier(result.get("key_from")):
                        baseline["key_from"] = result["key_from"]
                elif (result.get("status") == lan_scan.VIA_GATEWAY
                      and type(result.get("sub_online")) is bool and _identifier(result.get("gateway_id"))):
                    baseline = {"sub_online": result["sub_online"], "gateway_id": result["gateway_id"],
                                "observed_at": observed_at}
                else:
                    continue
                doc["lan"][dev_id] = baseline
            doc["lan_cache_seeded"] = True

        return self._apply(account, "seed:lan-cache", mutate, epoch=epoch)

    def observe_lan(self, account, results, devices, operation, *, epoch=None):
        now = self.clock()
        by_id = {d["id"]: d for d in devices}
        gateways = lan_scan.gateway_ids(devices)

        def mutate(doc, skip):
            for dev_id, result in results.items():
                device = by_id.get(dev_id)
                if device is None or dev_id in skip:
                    continue
                previous = doc["lan"].get(dev_id) or {}
                status = result.get("status")
                if status == lan_scan.VIA_GATEWAY:
                    # Gateway reachability isn't a child's online state.
                    online = result.get("sub_online")
                    gateway = result.get("gateway_id")
                    if type(online) is not bool or not gateway:
                        continue
                    after = {"sub_online": online, "gateway_id": gateway, "observed_at": now}
                    changes = []
                    if (previous.get("gateway_id") == gateway and "sub_online" in previous
                            and previous["sub_online"] != online):
                        changes.append(_change("sub_device_changed", previous["sub_online"], online))
                    _append(doc, device, changes, "lan", now, previous.get("observed_at"), operation)
                    doc["lan"][dev_id] = after
                    continue
                if status not in {lan_scan.OK, lan_scan.BUSY, lan_scan.UNREACHABLE, lan_scan.KEY_MISMATCH}:
                    continue
                changes = []
                after = {**previous, "status": status, "observed_at": now}
                if previous.get("status") and previous["status"] != status:
                    changes.append(_change("connection_changed", previous["status"], status,
                                           key_authenticated=status == lan_scan.OK and result.get("version") != "3.1"))
                if status == lan_scan.OK:
                    try:
                        ip = str(ipaddress.ip_address(result["ip"]))
                    except (KeyError, ValueError):
                        continue
                    version = str(result.get("version") or "")
                    protocol = {"version": "3.3" if version == "3.2" else version,
                                "device22": bool(result.get("device22")) or version == "3.2"}
                    if protocol["version"] not in {"3.1", "3.3", "3.4", "3.5"}:
                        continue
                    if previous.get("ip") and previous["ip"] != ip:
                        changes.append(_change("ip_changed", previous["ip"], ip))
                    if previous.get("protocol") and previous["protocol"] != protocol:
                        changes.append(_change("protocol_changed", previous["protocol"], protocol))
                    for change in changes:
                        if (change["kind"] in {"ip_changed", "protocol_changed"}
                                and previous.get("confirmed_at") != previous.get("observed_at")):
                            change["previous_observed_at"] = previous.get("confirmed_at")
                    after.update(ip=ip, protocol=protocol, confirmed_at=now)
                    if _identifier(result.get("key_from")):
                        after["key_from"] = result["key_from"]
                    else:
                        after.pop("key_from", None)  # it answered to a key of its own
                # Inherited changes are linked to the gateway's one event.
                related = [i for i, r in results.items()
                           if r.get("gateway_id") == dev_id] if dev_id in gateways else []
                related += [d["id"] for d in devices if d.get("gateway_id") == dev_id]
                related = [i for i in related if i not in skip]
                _append(doc, device, changes, "lan", now, previous.get("observed_at"), operation, related)
                doc["lan"][dev_id] = after

        return self._apply(account, operation, mutate, epoch=epoch)

    def view(self, account, *, limit=50, cursor=None, device_id="", kind="", source="",
             query="", since=None, until=None, export=False):
        if self._config_error:
            raise HistoryError(self._config_error)
        if self.persist and self._pending_delete:
            raise HistoryError("history_delete_failed")
        if (type(limit) is not int or not 1 <= limit <= 100 or kind and kind not in KINDS
                or source not in {"", "cloud", "lan"} or len(query) > 256
                or device_id and not _identifier(device_id)
                or any(v is not None and (not math.isfinite(v) or v < 0) for v in (since, until))
                or since is not None and until is not None and since > until):
            raise HistoryError("invalid_history_query")
        try:
            with self._guard():
                doc = self._read(account)
                before = copy.deepcopy(doc)
                self._prune(doc)
                if doc != before and self.enabled and (not self.persist or self.path.exists()):
                    self._write(doc)
                head = doc["next_sequence"] - 1
                upper, after = head, head + 1
                filters = [device_id, kind, source, query, since, until]
                filter_id = hashlib.sha256(json.dumps(filters).encode()).hexdigest()
                if cursor:
                    if len(cursor) > 1024:
                        raise HistoryError("history_cursor_expired")
                    try:
                        value = json.loads(base64.urlsafe_b64decode(cursor.encode() + b"=" * (-len(cursor) % 4)))
                        if (value[0] != doc["epoch"] or value[1] != filter_id
                                or type(value[2]) is not int or type(value[3]) is not int
                                or not 0 <= value[2] <= head or not 1 <= value[3] <= value[2] + 1
                                or value[3] <= doc["pruned_through"]):
                            raise ValueError()
                        upper, after = value[2:4]
                    except (ValueError, TypeError, IndexError, KeyError, UnicodeError):
                        raise HistoryError("history_cursor_expired") from None
                query = query.casefold()
                clears = doc.get("device_clears", {})
                scoped = [e for e in reversed(doc["events"]) if _event_visible(e, device_id, clears)]
                events = [e for e in (_public_event(event) for event in scoped)
                          if e["sequence"] <= upper and e["sequence"] < after
                          and (not source or e["source"] == source)
                          and (not kind or any(c["kind"] == kind for c in e["changes"]))
                          and (not query or query in json.dumps([e["name"], e["device_id"], e["changes"]], ensure_ascii=False).casefold())
                          and (since is None or e["observed_at"] >= since)
                          and (until is None or e["observed_at"] <= until)]
                page = events if export else events[:limit]
                next_cursor = None
                if not export and len(events) > limit:
                    next_cursor = base64.urlsafe_b64encode(json.dumps(
                        [doc["epoch"], filter_id, upper, page[-1]["sequence"]]).encode()).decode().rstrip("=")
                labels = {d["id"]: {"id": d["id"], "name": d.get("name") or d["id"],
                                      "present": d.get("present", False)} for d in doc["cloud"].values()}
                for event in doc["events"]:
                    labels.setdefault(event["device_id"], {"id": event["device_id"], "name": event["name"], "present": False})
                return {"account_id": account, "enabled": self.enabled,
                        "mode": "persistent" if self.persist else "temporary",
                        "epoch": doc["epoch"], "latest_sequence": head,
                        "started_at": max(doc["started_at"], clears.get(device_id, {}).get("at", doc["started_at"])),
                        "last_cloud_at": doc["cloud_at"], "retention_days": self.retention_days,
                        "max_events": self.max_events, "pruned": doc["pruned_through"] > 0,
                        "events": page, "next_cursor": next_cursor, "total": len(scoped),
                        "matching": len(events), "devices": sorted(labels.values(), key=lambda d: (d["name"].casefold(), d["id"])),
                        "recording_error": self.error}
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise HistoryError("history_unavailable") from None

    def epoch(self, account):
        """The token an observation carries from its start. A reset drops the
        whole observation, a device clear only that device's part."""
        if not self.enabled:
            return None
        with self._guard():
            doc = self._read(account)
            return f"{doc['epoch']}.{doc.get('clear_revision', 0)}"

    def clear(self, account, epoch, device_id=None):
        if self._config_error:
            raise HistoryError(self._config_error)
        if self.persist and self._pending_delete:
            raise HistoryError("history_delete_failed")
        with self._guard():
            doc = self._read(account)
            if _parse_epoch(epoch)[0] != doc["epoch"]:
                raise HistoryError("history_cursor_expired")
            if device_id is None:
                # Internal whole-journal reset, used for installation/test
                # lifecycle only. The device UI/API always supplies an ID.
                doc.update(events=[], operations=[], epoch=uuid.uuid4().hex, next_sequence=1,
                           started_at=self.clock(), pruned_through=0, device_clears={}, clear_revision=0)
                doc["cloud"] = {i: d for i, d in doc["cloud"].items() if d.get("present")}
                doc["lan"] = {i: d for i, d in doc["lan"].items() if i in doc["cloud"]}
            else:
                if not _identifier(device_id):
                    raise HistoryError("invalid_device")
                if (device_id not in doc["cloud"] and device_id not in doc["lan"]
                        and not any(device_id in (e["device_id"], *e["related_device_ids"]) for e in doc["events"])):
                    raise HistoryError("unknown_device")
                revision = doc.get("clear_revision", 0) + 1
                doc["clear_revision"] = revision
                doc.setdefault("device_clears", {})[device_id] = {
                    "through": doc["next_sequence"] - 1, "at": self.clock(), "revision": revision}
                # Shared gateway entries remain available to other devices.
                # Remove an entry only once no device can still view it.
                doc["events"] = [e for e in doc["events"] if _event_visible(e, "", doc["device_clears"])]
                # The epoch stays, so other devices' running observations still
                # count. The revision makes them skip this device.
            try:
                self._write(doc)
                self.error = None
            except OSError:
                raise HistoryError("history_save_failed") from None

    def maintain(self):
        """Local-only retention while the app is running, including idle periods."""
        if not self.enabled or not self.persist or self._pending_delete or self._config_error:
            return
        try:
            with self._guard():
                if not self._maintenance_account or not self.path.exists():
                    return
                doc = self._read(self._maintenance_account)
                before = copy.deepcopy(doc)
                self._prune(doc)
                if doc != before:
                    self._write(doc)
        except OSError:
            raise HistoryError("history_save_failed") from None

    def activate(self, account):
        """Resume retention after a restart without fetching from Tuya."""
        if not self.enabled or not self.persist or self._config_error or not self.path.exists():
            return
        with self._lock:
            self._maintenance_account = account
            try:
                self.maintain()
                _schedule_maintenance(self)
            except HistoryError as error:
                self.error = str(error)

    def purge(self):
        # A logout/switch also discards transient history and pending epochs.
        with self._lock:
            if not self._paths_safe:
                raise HistoryError("history_configuration")
            self._memory = self._empty = None
            self._maintenance_account = None
            failed = False
            for path in (self.path, self.key_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    failed = True
            self._memory_secret = os.urandom(32)
            # A logout deletes history on request. A configuration error still stands.
            self.error = "history_delete_failed" if failed else self._config_error
            self._pending_delete = failed
            if failed:
                raise HistoryError(self.error)
