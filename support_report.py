"""Allowlisted, anonymous diagnostics built from an existing device snapshot."""

from datetime import datetime, timezone
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import re
import time

import lan_scan

MAX_DATA_POINTS = 1024
MAX_DEFINITION_BYTES = 256 * 1024
PRIVATE_FIELDS = {
    "id", "uuid", "name", "local_key", "uid", "owner_id", "asset_id", "node_id",
    "gateway_id", "ip", "local_ip", "mac", "mac_address", "ssid", "room_name",
    "room_id", "home_name", "home_id", "lat", "lon", "latitude", "longitude",
}
STATUSES = {"ok", "via_gateway", "busy", "key_mismatch", "unreachable", "not_found"}
VERSIONS = {"3.1", "3.2", "3.3", "3.4", "3.5"}
JOB_ERRORS = {"scan_failed", "scanner_unavailable", "not_logged_in", "account_changed"}
ADDRESS = re.compile(r"(?:\b(?:\d{1,3}\.){3}\d{1,3}\b|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b)")


def _number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) else None
    except OverflowError:
        return None


def _timestamp(value):
    value = _number(value)
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str) and value:
        yield value


def _redactor(devices, session):
    private = set(_strings(session or {}))
    for device in devices:
        for field in PRIVATE_FIELDS:
            value = device.get(field)
            if value is not None and not isinstance(value, (dict, list, bool)):
                private.add(str(value))
    private.discard("")
    embedded = sorted((value for value in private if len(value) >= 4), key=len, reverse=True)

    def clean(value):
        if not isinstance(value, str):
            return None
        if value in private:
            return "[redacted]"
        for secret in embedded:
            value = value.replace(secret, "[redacted]")
        return ADDRESS.sub("[redacted]", value)[:1024]

    return clean


def _app_version():
    # Prefer the source actually running, rather than another installed checkout.
    try:
        text = Path(__file__).with_name("pyproject.toml").read_text()
        project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
        match = project and re.search(r'(?m)^version\s*=\s*"([\w.+-]+)"', project[1])
        if match:
            return match[1]
    except OSError:
        pass
    return _package_version("tuya-local-key")


def _package_version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"
    except (OSError, ValueError):
        return "unavailable"


def _connection(result):
    version = str(result.get("version") or "")
    status = result.get("status")
    return {
        "status": status if status in STATUSES else "not_checked",
        "local_ip_available": bool(result.get("ip")),
        "protocol_version": version if version in VERSIONS else None,
        "device22": bool(result.get("device22")),
        "checked_at": _timestamp(result.get("checked_at")),
        "key_matches_current_details": result.get("key_current") is True,
        "key_verified": bool(result.get("ip") and status in {"ok", "via_gateway"}
                             and version in VERSIONS - {"3.1"} and result.get("key_current") is True),
        "verification_scope": "gateway" if status == "via_gateway" else "device",
        "sub_device_online": result.get("sub_online") if status == "via_gateway"
        and result.get("ip") and type(result.get("sub_online")) is bool else None,
    }


def _definitions(raw, clean):
    try:
        spec = json.loads(raw) if isinstance(raw, str) and len(raw) <= 32768 else raw
    except (ValueError, TypeError):
        return {}
    if not isinstance(spec, dict):
        return {}
    result = {}
    for field in ("min", "max", "step", "scale", "maxlen"):
        value = _number(spec.get(field))
        if value is not None:
            result[field] = value
    if isinstance(spec.get("unit"), str):
        result["unit"] = clean(spec["unit"])
    for field in ("range", "label"):
        if isinstance(spec.get(field), list):
            result[field] = [clean(value) if isinstance(value, str) else value
                             for value in spec[field][:256]
                             if isinstance(value, str) or _number(value) is not None or type(value) is bool]
    return result


def _data_points(device, clean):
    def mapping(field):
        value = device.get(field)
        return value if isinstance(value, dict) else {}

    ranges, functions, status = mapping("status_range"), mapping("function"), mapping("status")
    ids = {}
    for dp_id, entry in mapping("local_strategy").items():
        if (isinstance(entry, dict) and isinstance(entry.get("status_code"), str)
                and str(dp_id).isdigit() and len(str(dp_id)) <= 5 and int(dp_id) <= 65535):
            ids[entry["status_code"]] = str(dp_id)
    codes = sorted(code for code in set(ranges) | set(functions) | set(status) | set(ids) if isinstance(code, str))
    rows, size = [], 0
    for code in codes[:MAX_DATA_POINTS]:
        spec = ranges.get(code) if isinstance(ranges.get(code), dict) else {}
        fn = functions.get(code) if isinstance(functions.get(code), dict) else {}
        row = {
            "code": clean(code), "dp_id": ids.get(code),
            "type": clean(spec.get("type") or fn.get("type")),
            "readable": code in ranges, "writable": code in functions,
            "definitions": _definitions(spec.get("values") or fn.get("values"), clean),
        }
        row_size = len(json.dumps(row, ensure_ascii=True).encode("utf-8"))
        if size + row_size > MAX_DEFINITION_BYTES:
            break
        rows.append(row)
        size += row_size
    return {"count": len(codes), "definitions": rows, "truncated": len(codes) > len(rows)}


def _device(device, result, clean, gateways):
    return {
        "role": "gateway" if device.get("id") in gateways else "sub_device"
        if lan_scan.is_sub_device(device) or result.get("status") == "via_gateway" else "device",
        **{field: clean(device.get(field)) for field in ("category", "product_id", "product_name", "model")},
        "cloud_online": device.get("online") if type(device.get("online")) is bool else None,
        "local_control_supported": device.get("support_local") if type(device.get("support_local")) is bool else None,
        "details_available": {field: bool(device.get(field)) for field in ("id", "uuid", "local_key", "node_id", "gateway_id")},
        "connection": _connection(result),
    }


def build(devices, results, device_id, snapshot_at, job=None, session=None, now=None):
    """Never serializes raw records, scan targets, exceptions or current values."""
    current = {device.get("id"): device for device in devices}
    device = current[device_id]
    clean, gateways = _redactor(devices, session), lan_scan.gateway_ids(devices)
    result = results.get(device_id) or {}
    selected = _device(device, result, clean, gateways)
    gateway_id = (device.get("gateway_id") or result.get("gateway_id")) if selected["role"] == "sub_device" else None
    now = time.time() if now is None else now
    report = {
        "schema_version": 1, "generated_at": _timestamp(now),
        "app": {"name": "Tuya Local Key", "version": _app_version()},
        "runtime": {"python": platform.python_version(), "os": platform.system(),
                    "architecture": platform.machine(),
                    "dependencies": {name: _package_version(name) for name in
                                     ("tinytuya", "tuya-device-sharing-sdk", "Flask", "cryptography")}},
        "snapshot_at": _timestamp(snapshot_at),
        "device": selected,
        "data_points": _data_points(device, clean),
        "privacy": {"excluded": ["local keys and credentials", "device and account identifiers",
                                 "personal device/home/room names", "network addresses and scan targets",
                                 "coordinates", "current values", "raw records and logs"],
                    "collection": "Existing snapshots only; no cloud request, network check or device read."},
    }
    if gateway_id:
        gateway = current.get(gateway_id)
        report["gateway"] = _device(gateway, results.get(gateway_id) or {}, clean, gateways) if gateway else {"listed": False}
    if job and (job.get("kind") == "scan" or job.get("device_id") in {device_id, gateway_id}):
        recent = {"kind": job.get("kind") if job.get("kind") in {"scan", "device"} else "unknown",
                  "state": job.get("state") if job.get("state") in {"running", "done", "cancelled", "failed", "discarded"} else "unknown",
                  "started_at": _timestamp(job.get("started_at")), "finished_at": _timestamp(job.get("finished_at")),
                  "cancelling": job.get("cancelling") is True}
        if job.get("error"):
            recent["error"] = job["error"] if job["error"] in JOB_ERRORS else "unknown_error"
        if job.get("kind") == "device":
            recent["target"] = "device" if job.get("device_id") == device_id else "gateway"
            recent["result"] = _connection(job.get("result") or {})
            # Raw job replies have no current-key evidence. The saved device
            # connection above supplies that separately, without guessing.
            recent["result"].pop("key_verified")
            recent["result"].pop("key_matches_current_details")
        summary = job.get("summary") or job.get("progress") or {}
        recent["summary"] = {field: _number(summary[field]) for field in
                             ("addresses", "open", "devices", "matched", "duration") if field in summary}
        report["last_network_check"] = recent
    return report
