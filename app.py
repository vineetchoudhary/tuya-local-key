#!/usr/bin/env python3
"""
Web interface for tuya_devices — QR-login to Smart Life and list your devices.

Endpoints (JSON unless noted):
  GET  /                    -> the single-page UI (HTML)
  GET  /api/state           -> {"logged_in": bool}
  POST /api/login/start     -> {"token", "qr"} (qr is a data: PNG) | {"error"}
  POST /api/login/poll      -> {"status": "pending"|"confirmed"|"expired"}
  GET  /api/devices         -> {"devices": [...], "cached_at": ts} | 401/502 {"error"}
  POST /api/logout          -> {"ok": true}

Auth state (the device-sharing session) is cached at SESSION_FILE so it survives
restarts; mount it on a volume in Docker.
"""

import base64
import hmac
import json
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

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
AUTH_USERNAME = _setting("AUTH_USERNAME")
AUTH_PASSWORD = _setting("AUTH_PASSWORD")

DEVICE_CACHE_TTL_SECONDS = 24 * 60 * 60
SESSION_INVALID_ERROR_CODES = {
    "1002",  # access_token is null
    "1010",  # token is expired
    "1011",  # token invalid
    "1012",  # token status is invalid
    "1400",  # token invalid
    "2029",  # session status is invalid,
}

app = Flask(__name__)

# token -> login info, for in-flight logins (single-process; guarded by a lock).
_pending = {}
_lock = threading.Lock()
PENDING_LOGIN_TTL_SECONDS = 180
_devices_cache = None
_devices_cache_lock = threading.Lock()
_devices_fetch_lock = threading.Lock()


def _cleanup_pending(now=None):
    now = now or time.time()
    expired = [
        token for token, info in _pending.items()
        if now - info["created_at"] > PENDING_LOGIN_TTL_SECONDS
    ]
    for token in expired:
        _pending.pop(token, None)


def _session_cache_key(session):
    return (
        SESSION_FILE,
        session.get("client_id"),
        session.get("user_code"),
        session.get("terminal_id"),
        session.get("endpoint"),
    )


def _clear_devices_cache():
    global _devices_cache
    with _devices_cache_lock:
        _devices_cache = None


def _devices_cache_for_session(session):
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


@app.get("/api/state")
def state():
    return jsonify({"logged_in": os.path.isfile(SESSION_FILE)})


@app.post("/api/login/start")
def login_start():
    data = request.get_json(silent=True) or {}
    user_code = (data.get("user_code") or "").strip()
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
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
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
    global _devices_cache
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
            stale_cache = cache

        try:
            devs = core.devices_from_session(session, SESSION_FILE)
        except Exception as e:
            if _is_session_invalid_error(e):
                _clear_devices_cache()
                return jsonify({"error": "session_invalid"}), 401
            if refresh and stale_cache:
                body = dict(stale_cache["body"])
                body["refresh_failed"] = True
                return jsonify(body)
            return jsonify({"error": "fetch_failed"}), 502

        now = time.time()
        body = {
            "devices": [core.web_dict(d) for d in devs],
            "cached_at": now,
            "cache_expires_at": now + DEVICE_CACHE_TTL_SECONDS,
        }
        with _devices_cache_lock:
            _devices_cache = {
                "body": body,
                "cached_at": now,
                "session_key": _session_cache_key(session),
            }
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


if __name__ == "__main__":
    # Dev server. In Docker we run via waitress (see Dockerfile).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
