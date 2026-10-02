#!/usr/bin/env python3
"""
Web interface for tuya_devices — QR-login to Smart Life and list your devices.

Endpoints (JSON unless noted):
  GET  /                    -> the single-page UI (HTML)
  GET  /api/state           -> {"logged_in": bool}
  POST /api/login/start     -> {"token", "qr"} (qr is a data: PNG) | {"error"}
  POST /api/login/poll      -> {"status": "pending"|"confirmed"|"expired"}
  GET  /api/devices         -> {"devices": [...], "cached_at": ts,
                                "changes"?: {...}, "stale"?: true} | 401/502 {"error"}
  POST /api/logout          -> {"ok": true}
  GET  /api/lan             -> {"results", "summary", "changes", "changes_at",
                                "targets", "job"} | 401
  POST /api/lan/scan        -> 202 {"job"} for {"targets": "..."} or
                               {"device_id", "ip"?} | 400/401/404/409/503 {"error"}
  GET  /api/lan/scan        -> {"job": {...} | null}
  DELETE /api/lan/scan      -> {"ok": true}
"""

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

import device_cache
import lan_scan
import tuya_devices as core

SESSION_FILE = os.environ.get(
    "SESSION_FILE", os.path.expanduser("~/.config/tuya-smartlife/session.json")
)
OPTIONS_FILE = Path(os.environ.get("HASS_OPTIONS_FILE", "/data/options.json"))
APP_ICON = Path(__file__).resolve().parent / "tuya_local_key" / "icon.png"


def _options():
    try:
        return json.loads(OPTIONS_FILE.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


_OPTS = _options()


def _setting(name, default=""):
    """HA's options.json (if present) wins over the env var."""
    value = _OPTS.get(name) or _OPTS.get(name.lower())
    return str(value).strip() if value else os.environ.get(name, default)


QR_SCHEME = _setting("QR_SCHEME", "smartlife")
LAN_SUBNET = _setting("LAN_SUBNET")
AUTH_USERNAME = _setting("AUTH_USERNAME")
AUTH_PASSWORD = _setting("AUTH_PASSWORD")

DEVICE_CACHE_TTL_SECONDS = 3 * 24 * 60 * 60

_SESSION_DIR = os.path.dirname(SESSION_FILE) or "."
DEVICE_CACHE_FILE = os.environ.get(
    "DEVICE_CACHE_FILE", os.path.join(_SESSION_DIR, "devices.cache")
)
DEVICE_CACHE_KEY_FILE = os.environ.get(
    "DEVICE_CACHE_KEY_FILE", os.path.join(_SESSION_DIR, "cache.key")
)
LAN_CACHE_FILE = os.environ.get(
    "LAN_CACHE_FILE", os.path.join(_SESSION_DIR, "lan.cache")
)
DEVICE_CACHE_PERSIST = _setting("DEVICE_CACHE", "on").lower() not in {
    "off", "0", "false", "no",
}
SESSION_INVALID_ERROR_CODES = {
    "1002",  # access_token is null
    "1010",  # token is expired
    "1011",  # token invalid
    "1012",  # token status is invalid
    "1400",  # token invalid
    "2029",  # session status is invalid,
}

app = Flask(__name__)
# Keep core.web_dict()'s field order (name/id/local key first, specs last); the
# UI and the CSV export follow it.
app.json.sort_keys = False

# token -> login info, for in-flight logins (single-process; guarded by a lock).
_pending = {}
_lock = threading.Lock()
PENDING_LOGIN_TTL_SECONDS = 180
_devices_cache = None
_devices_cache_loaded = False
_devices_cache_lock = threading.Lock()
_devices_fetch_lock = threading.Lock()
# LAN scan results, stored like the device list and cleared with it. One scan
# job at a time, on its own thread; _lan_lock guards both.
_lan_cache = None
_lan_cache_loaded = False
_lan_job = None
_lan_lock = threading.Lock()


def _cleanup_pending(now=None):
    now = now or time.time()
    expired = [
        token for token, info in _pending.items()
        if now - info["created_at"] > PENDING_LOGIN_TTL_SECONDS
    ]
    for token in expired:
        _pending.pop(token, None)


def _session_cache_key(session):
    """Identity of the logged-in session, digested.

    Hashed rather than kept as-is because it is stored with the cache, and the
    identity includes the account's user code.
    """
    identity = json.dumps([
        SESSION_FILE,
        session.get("client_id"),
        session.get("user_code"),
        session.get("terminal_id"),
        session.get("endpoint"),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _load_persisted_cache():
    global _devices_cache, _devices_cache_loaded
    if _devices_cache_loaded:
        return
    _devices_cache_loaded = True
    if not DEVICE_CACHE_PERSIST:
        # Turning the setting off should take any list already stored with it,
        # not leave one sitting there until the next logout.
        device_cache.clear(DEVICE_CACHE_FILE, DEVICE_CACHE_KEY_FILE, LAN_CACHE_FILE)
        return
    if _devices_cache is not None:
        return
    _devices_cache = device_cache.load(DEVICE_CACHE_FILE, DEVICE_CACHE_KEY_FILE)


def _clear_devices_cache():
    global _devices_cache, _devices_cache_loaded
    # LAN results go with the device list. Drop the running scan first, so it
    # can't write its results back after the files below are gone.
    _clear_lan()
    with _devices_cache_lock:
        _devices_cache = None
        _devices_cache_loaded = True  # nothing left on disk worth reading
        device_cache.clear(DEVICE_CACHE_FILE, DEVICE_CACHE_KEY_FILE, LAN_CACHE_FILE)


def _devices_cache_for_session(session):
    _load_persisted_cache()
    if not _devices_cache:
        return None
    if _devices_cache["session_key"] != _session_cache_key(session):
        return None
    return _devices_cache


def _cached_devices_response(session, now):
    cache = _devices_cache_for_session(session)
    if not cache:
        return None
    if now - cache["cached_at"] >= DEVICE_CACHE_TTL_SECONDS:
        return None
    return cache["body"]


def _snapshot_body(cache, refresh, reason):
    body = dict(cache["body"])
    body["stale"] = True
    body["stale_reason"] = reason
    if refresh:
        body["refresh_failed"] = True
    return body


def _is_session_invalid_error(error):
    if isinstance(error, KeyError):
        return True

    code = str(getattr(error, "error_code", ""))
    if code in SESSION_INVALID_ERROR_CODES:
        return True

    message = str(getattr(error, "error_message", error)).lower()
    if "sign invalid" in message or "signature invalid" in message:
        return code == "-9999999" or "-9999999" in message

    return any(
        marker in message
        for marker in (
            "access_token is null",
            "invalid token",
            "token is expired",
            "token invalid",
            "token expired",
            "token status is invalid",
            "login expired",
            "session expired",
            "session status is invalid",
        )
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/icon.png")
def app_icon():
    return send_file(APP_ICON, mimetype="image/png", max_age=86400)


@app.before_request
def _require_auth():
    if not (AUTH_USERNAME and AUTH_PASSWORD):
        return

    if request.headers.get("X-Ingress-Path"):
        return

    auth = request.authorization
    ok = bool(auth) and auth.type == "basic" and (
        hmac.compare_digest((auth.username or "").encode(), AUTH_USERNAME.encode())
        & hmac.compare_digest((auth.password or "").encode(), AUTH_PASSWORD.encode())
    )
    if not ok:
        return ("Authentication required.", 401,
                {"WWW-Authenticate": 'Basic realm="Tuya Local Key"'})


@app.after_request
def no_store_api_responses(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _json_object():
    """The request's JSON body when it is an object, else {} (never a 500)."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@app.get("/api/state")
def state():
    return jsonify({"logged_in": os.path.isfile(SESSION_FILE)})


@app.post("/api/login/start")
def login_start():
    data = _json_object()
    user_code = str(data.get("user_code") or "").strip()
    if not user_code:
        return jsonify({"error": "A user code is required."}), 400
    try:
        token = core.mint_qr_token(user_code)
    except core.LoginError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # network/SDK error
        return jsonify({"error": f"Could not reach Tuya: {e}"}), 502

    with _lock:
        _cleanup_pending()
        _pending[token] = {"user_code": user_code, "created_at": time.time()}

    png = base64.b64encode(
        core.qr_png_bytes(f"{QR_SCHEME}--qrLogin?token={token}")
    ).decode()
    return jsonify({"token": token, "qr": f"data:image/png;base64,{png}"})


@app.post("/api/login/poll")
def login_poll():
    data = _json_object()
    token = str(data.get("token") or "").strip()
    with _lock:
        _cleanup_pending()
        login = _pending.get(token)
        user_code = login["user_code"] if login else None
    if not user_code:
        return jsonify({"status": "expired"}), 404

    session = core.poll_login(token, user_code)
    if session:
        try:
            core.save_session(SESSION_FILE, session)
        except Exception as e:
            return jsonify({"error": f"session_save_failed: {e}"}), 500
        with _lock:
            _pending.pop(token, None)
        _clear_devices_cache()
        return jsonify({"status": "confirmed"})
    return jsonify({"status": "pending"})


@app.get("/api/devices")
def devices():
    global _devices_cache, _devices_cache_loaded
    session = core.load_session(SESSION_FILE)
    if not session:
        return jsonify({"error": "not_logged_in"}), 401
    refresh = request.args.get("refresh") in {"1", "true", "yes"}
    request_start = time.time()

    if not refresh:
        with _devices_cache_lock:
            cached = _cached_devices_response(session, request_start)
        if cached:
            return jsonify(cached)

    with _devices_fetch_lock:
        with _devices_cache_lock:
            cache = _devices_cache_for_session(session)

            if cache and cache["cached_at"] >= request_start:
                return jsonify(cache["body"])
            if not refresh:
                cached = _cached_devices_response(session, request_start)
                if cached:
                    return jsonify(cached)
            previous = cache

        try:
            devs = core.devices_from_session(session, SESSION_FILE)
        except Exception as e:
            invalid = _is_session_invalid_error(e)
            if previous:
                return jsonify(_snapshot_body(
                    previous, refresh, "session_invalid" if invalid else "fetch_failed"
                ))
            if invalid:
                _clear_devices_cache()
                return jsonify({"error": "session_invalid"}), 401
            return jsonify({"error": "fetch_failed"}), 502

        now = time.time()
        listed = [core.web_dict(d) for d in devs]
        body = {
            "devices": listed,
            "cached_at": now,
            "cache_expires_at": now + DEVICE_CACHE_TTL_SECONDS,
        }
        if previous:
            changes = core.diff_devices(previous["body"].get("devices", []), listed)
            if any(changes.values()):
                body["changes"] = changes
        entry = {
            "body": body,
            "cached_at": now,
            "session_key": _session_cache_key(session),
        }
        with _devices_cache_lock:
            _devices_cache = entry
            _devices_cache_loaded = True
        if DEVICE_CACHE_PERSIST:
            device_cache.save(DEVICE_CACHE_FILE, DEVICE_CACHE_KEY_FILE, entry)
        return jsonify(body)


@app.post("/api/logout")
def logout():
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass
    with _lock:
        _pending.clear()
    _clear_devices_cache()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# LAN scan (see lan_scan): local IPs and protocol versions
# --------------------------------------------------------------------------- #
def _empty_lan_body():
    return {"results": {}, "summary": None, "targets": "", "changes": None, "changes_at": None}


def _lan_body_for(session_key):
    """The stored LAN results for this session, or None. Hold _lan_lock."""
    global _lan_cache, _lan_cache_loaded
    if not _lan_cache_loaded:
        _lan_cache_loaded = True
        if DEVICE_CACHE_PERSIST and _lan_cache is None:
            _lan_cache = device_cache.load(LAN_CACHE_FILE, DEVICE_CACHE_KEY_FILE)
    if not _lan_cache or _lan_cache["session_key"] != session_key:
        return None
    return _lan_cache["body"]


def _save_lan(session_key, body):
    """Hold _lan_lock."""
    global _lan_cache, _lan_cache_loaded
    entry = {"body": body, "cached_at": time.time(), "session_key": session_key}
    _lan_cache = entry
    _lan_cache_loaded = True
    if DEVICE_CACHE_PERSIST:
        device_cache.save(LAN_CACHE_FILE, DEVICE_CACHE_KEY_FILE, entry, prefix=".lan-")


def _clear_lan():
    global _lan_cache, _lan_cache_loaded, _lan_job
    with _lan_lock:
        if _lan_job is not None:
            _lan_job["cancel"].set()
        _lan_job = None
        _lan_cache = None
        _lan_cache_loaded = True  # the caller removes the file


def _key_digest(local_key):
    return hashlib.sha256(str(local_key or "").encode("utf-8")).hexdigest()


def _job_view(job):
    if not job:
        return None
    view = {k: job.get(k) for k in (
        "id", "kind", "state", "device_id", "targets", "started_at",
        "finished_at", "summary", "result", "error",
    )}
    view["progress"] = dict(job.get("progress") or {})
    # Cancelled, but the checks already under way still finishing.
    view["cancelling"] = job.get("state") == "running" and job["cancel"].is_set()
    return view


def _lan_session():
    session = core.load_session(SESSION_FILE)
    return session, (_session_cache_key(session) if session else None)


def _session_devices(session):
    with _devices_cache_lock:
        cache = _devices_cache_for_session(session)
    if not cache:
        return None
    return [d for d in cache["body"].get("devices", []) if isinstance(d, dict)]


def _merge_lan(job, outcome, current):
    """Fold a finished scan into the stored results. Hold _lan_lock."""
    key = job["session_key"]
    stored = _lan_body_for(key) or _empty_lan_body()
    body = dict(stored)
    previous = stored.get("results") or {}

    if job["kind"] == "device":
        # A device found is news wherever it was found. A failure only says
        # something about the address the device was known at: one at an
        # address typed in by hand (a typo, a guess) must not replace what is
        # stored. The job's own result still reports it.
        checked = outcome["results"].get(job["device_id"]) or {}
        known_ip = (previous.get(job["device_id"]) or {}).get("ip")
        if checked.get("status") != lan_scan.OK and checked.get("ip") != known_ip:
            return

    results = {}
    for dev_id, result in outcome["results"].items():
        device = current.get(dev_id)
        if device is None:
            continue   # gone from the device list while the scan ran
        if job["key_digests"].get(dev_id) != _key_digest(device.get("local_key")):
            continue   # a Refresh changed its key mid-scan; this result proves nothing
        results[dev_id] = result

    names = {dev_id: d.get("name") or "" for dev_id, d in current.items()}
    changes = lan_scan.diff_results(previous, results, names)
    merged = {dev_id: r for dev_id, r in previous.items() if dev_id in current}
    merged.update(results)
    body["results"] = merged

    now = time.time()
    whole = job["kind"] == "scan" and not outcome["summary"]["cancelled"]
    if job["kind"] == "scan":
        body["summary"] = outcome["summary"]
        body["targets"] = job["targets"]
    if whole:
        # A full scan speaks for every device, like a Refresh does for the list.
        body["changes"] = changes if any(changes.values()) else None
        body["changes_at"] = now if body["changes"] else None
    else:
        # A single check or a cancelled scan only speaks for what it reached:
        # what it found replaces that device's entry of the same kind, and a
        # device that answers to its key again no longer counts as failing.
        existing = body.get("changes") or {}
        combined = {}
        for kind, entries in changes.items():
            fresh = {e["id"] for e in entries}
            kept = [
                e for e in existing.get(kind, [])
                if e["id"] not in fresh and not (
                    kind == "local_key_failed"
                    and (results.get(e["id"]) or {}).get("status") == lan_scan.OK)
            ]
            combined[kind] = sorted(kept + entries, key=lambda e: (e["name"].lower(), e["id"]))
        if not any(combined.values()):
            body["changes"] = body["changes_at"] = None
        else:
            body["changes"] = combined
            if any(changes.values()) or not body.get("changes_at"):
                body["changes_at"] = now   # something new: show the notice again
    _save_lan(key, body)


def _run_lan_job(job, targets, devices, known, only):
    def progress(snapshot):
        with _lan_lock:
            job["progress"] = snapshot

    # Anything tinytuya does wrong ends here, in this job. The device list and
    # the stored results are never touched by a scan that didn't finish.
    try:
        outcome = lan_scan.scan(targets, devices, known, progress=progress,
                                cancel=job["cancel"], only=only)
    except lan_scan.ScannerUnavailable as e:
        app.logger.warning("LAN scan unavailable: %s", e)
        with _lan_lock:
            job.update(state="failed", error="scanner_unavailable", finished_at=time.time())
        return
    except Exception as e:
        # The type only: a message could carry a device's repr, which has its key.
        app.logger.warning("LAN scan failed: %s", type(e).__name__)
        with _lan_lock:
            job.update(state="failed", error="scan_failed", finished_at=time.time())
        return

    try:
        session, session_key = _lan_session()
        current = {d.get("id"): d for d in (_session_devices(session) or [])} if session else {}
        with _lan_lock:
            if _lan_job is not job or session_key != job["session_key"]:
                # Logged out, or logged in again, while it ran: the results belong
                # to a device list that is gone.
                job.update(state="discarded", finished_at=time.time())
                return
            _merge_lan(job, outcome, current)
            job.update(
                state="cancelled" if outcome["summary"]["cancelled"] else "done",
                finished_at=time.time(),
                summary=outcome["summary"],
                result=outcome["results"].get(job["device_id"]) if job["device_id"] else None,
            )
    except Exception as e:
        # A job left "running" would refuse every later scan until a logout.
        app.logger.warning("LAN scan results not saved: %s", type(e).__name__)
        with _lan_lock:
            job.update(state="failed", error="scan_failed", finished_at=time.time())


@app.get("/api/lan")
def lan_state():
    session, session_key = _lan_session()
    if not session:
        return jsonify({"error": "not_logged_in"}), 401
    with _devices_cache_lock:
        _load_persisted_cache()  # also drops a stored LAN file when DEVICE_CACHE=off
    with _lan_lock:
        body = _lan_body_for(session_key) or _empty_lan_body()
        job = _lan_job if _lan_job and _lan_job["session_key"] == session_key else None
        return jsonify({
            "results": body.get("results") or {},
            "summary": body.get("summary"),
            "changes": body.get("changes"),
            "changes_at": body.get("changes_at"),
            "targets": body.get("targets") or LAN_SUBNET,
            "job": _job_view(job),
        })


def _bad_targets(message):
    return jsonify({"error": "bad_targets", "message": message}), 400


@app.post("/api/lan/scan")
def lan_scan_start():
    global _lan_job
    session, session_key = _lan_session()
    if not session:
        return jsonify({"error": "not_logged_in"}), 401
    data = _json_object()

    # Keys come from the device list already loaded: a scan never calls Tuya.
    devices = _session_devices(session)
    if not devices:
        return jsonify({"error": "no_devices"}), 409
    with _lan_lock:
        stored = (_lan_body_for(session_key) or _empty_lan_body()).get("results") or {}
    remembered = {
        dev_id: {"ip": r["ip"], "version": r.get("version"), "device22": r.get("device22", False)}
        for dev_id, r in stored.items()
        if r.get("ip") and r.get("status") != lan_scan.VIA_GATEWAY
    }

    device_id = str(data.get("device_id") or "").strip()
    if device_id:
        device = next((d for d in devices if d.get("id") == device_id), None)
        if device is None:
            return jsonify({"error": "unknown_device"}), 404
        if lan_scan.is_sub_device(device):
            return jsonify({"error": "sub_device"}), 400
        if not device.get("local_key"):
            # Bluetooth-only devices: Tuya returns no local key to ask with.
            return jsonify({"error": "no_local_key"}), 400
        text = str(data.get("ip") or "").strip() or remembered.get(device_id, {}).get("ip", "")
        try:
            targets = lan_scan.parse_targets(text)
        except lan_scan.TargetError as e:
            return _bad_targets(str(e))
        if len(targets) != 1:
            return _bad_targets("Enter one IP address.")
        known = {device_id: dict(remembered.get(device_id, {}), ip=targets[0])}
        # The whole list goes along, so a gateway is known as one, but only this
        # device (and its sub-devices) is looked for.
        kind, text, only = "device", targets[0], [device_id]
    else:
        text = str(data.get("targets") or "").strip()
        try:
            targets = lan_scan.parse_targets(text)
        except lan_scan.TargetError as e:
            return _bad_targets(str(e))
        known, kind, only = remembered, "scan", None

    try:
        lan_scan.require_scanner()
    except lan_scan.ScannerUnavailable as e:
        app.logger.warning("LAN scan unavailable: %s", e)
        return jsonify({"error": "scanner_unavailable"}), 503

    job = {
        "id": base64.urlsafe_b64encode(os.urandom(9)).decode(),
        "kind": kind,
        "state": "running",
        "device_id": device_id or None,
        "targets": text,
        "started_at": time.time(),
        "finished_at": None,
        "progress": {},
        "summary": None,
        "result": None,
        "error": None,
        "session_key": session_key,
        "key_digests": {d.get("id"): _key_digest(d.get("local_key")) for d in devices},
        "cancel": threading.Event(),
    }
    with _lan_lock:
        if _lan_job is not None and _lan_job["state"] == "running":
            return jsonify({"error": "scan_running"}), 409
        _lan_job = job
        view = _job_view(job)
    # Its own thread, so a scan doesn't hold one of waitress's request threads.
    thread = threading.Thread(
        target=_run_lan_job, args=(job, targets, devices, known, only), name="lan-scan", daemon=True
    )
    try:
        thread.start()
    except RuntimeError:   # no thread to be had: don't leave the job "running"
        app.logger.warning("LAN scan could not start a thread")
        with _lan_lock:
            job.update(state="failed", error="scan_failed", finished_at=time.time())
        return jsonify({"error": "scan_failed"}), 503
    return jsonify({"job": view}), 202


@app.get("/api/lan/scan")
def lan_scan_progress():
    session, session_key = _lan_session()
    if not session:
        return jsonify({"error": "not_logged_in"}), 401
    with _lan_lock:
        job = _lan_job if _lan_job and _lan_job["session_key"] == session_key else None
        return jsonify({"job": _job_view(job)})


@app.delete("/api/lan/scan")
def lan_scan_cancel():
    session, session_key = _lan_session()
    if not session:
        return jsonify({"error": "not_logged_in"}), 401
    with _lan_lock:
        if _lan_job and _lan_job["session_key"] == session_key and _lan_job["state"] == "running":
            _lan_job["cancel"].set()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Dev server. In Docker we run via waitress (see Dockerfile).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
