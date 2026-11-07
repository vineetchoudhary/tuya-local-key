"""One local status read, using saved connection details without cloud requests."""

import json
import time

import lan_scan

MAX_READING_BYTES = 128 * 1024


class ReadUnavailable(ValueError):
    """A stable error code suitable for the UI, without device secrets."""


def connection(devices, results, device_id):
    """Resolve the device or its identified gateway. The returned key is private."""
    by_id = {d.get("id"): d for d in devices}
    device = by_id.get(device_id)
    if not device:
        raise ReadUnavailable("unknown_device")
    gateways = lan_scan.gateway_ids(devices)
    saved = results.get(device_id) or {}
    sub = device_id not in gateways and (
        lan_scan.is_sub_device(device) or saved.get("status") == lan_scan.VIA_GATEWAY)
    gateway_id = (device.get("gateway_id") or saved.get("gateway_id")) if sub else None
    gateway = by_id.get(gateway_id) if gateway_id else None
    if sub and (not gateway or gateway_id == device_id):
        raise ReadUnavailable("gateway_unavailable")
    host = gateway if sub else device
    host_result = results.get(host["id"]) or {}
    same_gateway = not saved.get("gateway_id") or saved["gateway_id"] == gateway_id
    local = host_result if not sub or host_result.get("ip") else saved if same_gateway else {}
    source = by_id.get(host_result.get("key_from")) or {}
    key = host.get("local_key") or source.get("local_key") or (device.get("local_key") if sub else None)
    if not key:
        raise ReadUnavailable("no_local_key")
    node_id = str(device.get("node_id") or "") if sub else ""
    if sub and not node_id:
        raise ReadUnavailable("missing_node_id")
    ip, version = local.get("ip"), str(local.get("version") or "")
    if not ip:
        raise ReadUnavailable("missing_ip")
    try:
        if lan_scan.parse_targets(str(ip)) != [ip]:
            raise lan_scan.TargetError("one address required")
    except lan_scan.TargetError:
        raise ReadUnavailable("missing_ip") from None
    if version not in {"3.1", "3.2", "3.3", "3.4", "3.5"}:
        raise ReadUnavailable("missing_protocol")
    return {
        "device_id": device_id, "host_id": host["id"], "gateway_id": gateway_id,
        "ip": ip, "version": version, "key": key, "node_id": node_id,
        "device22": bool(local.get("device22")), "gateway": host["id"] in gateways,
        "dp_ids": sorted(str(dp) for dp in (device.get("local_strategy") or {}) if str(dp).isdigit()),
    }


def read(context):
    """Ask only for status, closing the device and gateway on every path.

    Never returns the raw reply, errors, device objects or connection key.
    A gateway's own reply is never substituted for its child's data points.
    """
    parent = child = None
    try:
        version = "3.3" if context["version"] == "3.2" else context["version"]
        parent = lan_scan._new_device(context["host_id"], context["ip"], context["key"], version)
        parent.disabledetect = context["gateway"]
        if context["device22"] or context["version"] == "3.2":
            parent.dev_type = "device22"
            parent.set_dpsUsed({dp: None for dp in context["dp_ids"]})
        device = parent
        if context["node_id"]:
            # Explicit CID and parent prevent TinyTuya's file/broadcast lookups.
            child = lan_scan._load_tinytuya().Device(
                context["device_id"], cid=context["node_id"], parent=parent,
                connection_timeout=lan_scan.PROBE_TIMEOUT_SECONDS,
                connection_retry_limit=1, connection_retry_delay=0,
            )
            device = child
        try:
            reply = device.status()
        except OSError:
            return {"status": "unreachable"}
        if not isinstance(reply, dict):
            return {"status": "no_values"}
        error = str(reply.get("Err")) if "Err" in reply else None
        if error:
            return {"status": {"901": "connection_failed", "902": "unreachable",
                               "905": "unreachable", "904": "key_or_protocol",
                               "914": "key_or_protocol"}.get(error, "no_values")}
        data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
        cid = reply.get("cid") or data.get("cid")
        if context["node_id"] and str(cid or "") != context["node_id"]:
            return {"status": "no_values"}
        if context["version"] == "3.1" and not context["node_id"]:
            # Plaintext 3.1 replies must name the expected device.
            if (reply.get("devId") or reply.get("gwId")) != context["host_id"]:
                return {"status": "device_mismatch"}
        dps = reply.get("dps", data.get("dps"))
        if not isinstance(dps, dict) or not dps:
            return {"status": "no_values"}
        dps = {str(dp): value for dp, value in dps.items() if str(dp).isdigit()}
        if not dps:
            return {"status": "no_values"}
        try:
            encoded = json.dumps(dps, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            return {"status": "invalid_values"}
        if len(encoded.encode("utf-8")) > MAX_READING_BYTES:
            return {"status": "invalid_values"}
        return {"status": "ok", "dps": dps, "read_at": time.time()}
    except lan_scan.ScannerUnavailable:
        raise
    except Exception:
        return {"status": "read_failed"}
    finally:
        for device in (child, parent):
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass
