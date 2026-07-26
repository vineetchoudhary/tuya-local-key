#!/usr/bin/env python3
"""
Web interface for tuya_devices — QR-login to Smart Life and list your devices.

Endpoints (JSON unless noted):
  GET  /                    -> the single-page UI (HTML)
  GET  /api/state           -> {"logged_in": bool}
  POST /api/login/start     -> {"token", "qr"} (qr is a data: PNG) | {"error"}
  GET  /api/login/poll      -> {"status": "pending"|"confirmed"|"expired"}
  GET  /api/devices         -> {"devices": [...]} | 401 {"error"}
  POST /api/logout          -> {"ok": true}

Auth state (the device-sharing session) is cached at SESSION_FILE so it survives
restarts; mount it on a volume in Docker.
"""

import base64
import os
import threading

from flask import Flask, jsonify, render_template, request

import tuya_devices as core

SESSION_FILE = os.environ.get(
    "SESSION_FILE", os.path.expanduser("~/.config/tuya-smartlife/session.json")
)
QR_SCHEME = os.environ.get("QR_SCHEME", "tuyaSmart")

app = Flask(__name__)

# token -> user_code, for in-flight logins (single-process; guarded by a lock).
_pending = {}
_lock = threading.Lock()


@app.get("/")
def index():
    return render_template("index.html")


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
        _pending[token] = user_code

    png = base64.b64encode(
        core.qr_png_bytes(f"{QR_SCHEME}--qrLogin?token={token}")
    ).decode()
    return jsonify({"token": token, "qr": f"data:image/png;base64,{png}"})


@app.get("/api/login/poll")
def login_poll():
    token = request.args.get("token", "")
    with _lock:
        user_code = _pending.get(token)
    if not user_code:
        return jsonify({"status": "expired"}), 404

    session = core.poll_login(token, user_code)
    if session:
        core.save_session(SESSION_FILE, session)
        with _lock:
            _pending.pop(token, None)
        return jsonify({"status": "confirmed"})
    return jsonify({"status": "pending"})


@app.get("/api/devices")
def devices():
    session = core.load_session(SESSION_FILE)
    if not session:
        return jsonify({"error": "not_logged_in"}), 401
    try:
        devs = core.devices_from_session(session, SESSION_FILE)
    except Exception as e:
        return jsonify({"error": f"session_invalid: {e}"}), 401
    return jsonify({"devices": [core.web_dict(d) for d in devs]})


@app.post("/api/logout")
def logout():
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass
    with _lock:
        _pending.clear()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Dev server. In Docker we run via waitress (see Dockerfile).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
