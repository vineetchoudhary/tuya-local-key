#!/usr/bin/env python3
"""
tuya_devices.py — list your Smart Life devices via QR-code login.

No Tuya IoT developer account, no Access ID/Secret. You log in with your own
Smart Life app account:

  1. Enter your Smart Life "user code" (app -> Me -> gear -> Account and
     Security -> User Code).
  2. Scan the QR code this prints, in the Smart Life app (+ -> Scan), and tap
     "Confirm login".
  3. The devices in your account are listed (customName/name, id, localKey,
     productId, productName, updateTime, uuid, ip, isOnline, ...).

The session token is cached (~/.config/tuya-smartlife/session.json) and
auto-refreshed, so you only scan again when the login fully expires.

Auth uses Home Assistant's public "device sharing" app credentials
(client_id HA_3y9q4ak7g4ephrvke, schema haauthorize) via Tuya's official
tuya-device-sharing-sdk — the same mechanism the Home Assistant Tuya
integration and make-all/tuya-local use.
"""

import argparse
import json
import os
import stat
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

# Home Assistant's public device-sharing app registration (not a secret; it is
# published in the HA/tuya-local source). The actual authorization is your QR
# scan — these just identify which app schema the login belongs to.
CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"

DEFAULT_SESSION = os.path.expanduser("~/.config/tuya-smartlife/session.json")
DEFAULT_QR_PNG = os.path.join(os.getcwd(), "tuya-login-qr.png")

# Device attributes exposed by tuya_sharing.CustomerDevice.
TOKEN_FIELDS = ("t", "uid", "expire_time", "access_token", "refresh_token")

# Request timeout for the SDK (seconds)
REQUEST_TIMEOUT_SECONDS = 60


class LoginError(Exception):
    """Raised when starting the QR login fails (e.g. an invalid user code)."""


# --------------------------------------------------------------------------- #
# Session persistence
# --------------------------------------------------------------------------- #
def load_session(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


_session_write_lock = threading.Lock()


def save_session(path, session):
    path = os.fspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with _session_write_lock:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 600: tokens are sensitive
            except OSError:
                pass
            os.replace(tmp, path)  # atomic on POSIX
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


class _SessionSaver:
    """Passed to Manager; the SDK calls update_token() on every auto-refresh.

    Duck-typed (the SDK only needs an .update_token(dict) method), so we don't
    have to import tuya_sharing just to define this.
    """

    def __init__(self, path, session):
        self.path = path
        self.session = session

    def update_token(self, token_info):
        self.session["token_info"] = {k: token_info.get(k) for k in TOKEN_FIELDS}
        save_session(self.path, self.session)


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
USER_CODE_HELP = """\
You need your Smart Life "user code":
  Open the Smart Life app -> Me -> tap the gear icon (top-right)
  -> Account and Security -> User Code (at the bottom).
"""


def render_qr(content, png_path):
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(content)
    qr.make(fit=True)
    # Terminal QR (works in most terminals; invert scans better on dark themes).
    qr.print_ascii(invert=True)
    # Reliable PNG fallback in case the terminal QR won't scan.
    try:
        qr.make_image().save(png_path)
        print(f"(Saved {png_path} — open it and scan if the one above won't.)\n")
    except Exception:
        pass


def qr_png_bytes(content):
    """Return PNG bytes for a QR code encoding `content` (for the web UI)."""
    import io
    import qrcode

    buf = io.BytesIO()
    qrcode.make(content).save(buf, format="PNG")
    return buf.getvalue()


def mint_qr_token(user_code):
    """Ask Tuya for a login QR token. Raises LoginError on failure."""
    from tuya_sharing import LoginControl

    resp = LoginControl().qr_code(CLIENT_ID, SCHEMA, user_code)
    if not resp.get("success"):
        raise LoginError(f"Could not start login [{resp.get('code')}]: {resp.get('msg')}")
    return resp["result"]["qrcode"]


def _session_from_info(info, user_code):
    return {
        "client_id": CLIENT_ID,
        "user_code": user_code,
        "terminal_id": info.get("terminal_id"),
        "endpoint": info.get("endpoint") or info.get("end_point"),
        "token_info": {k: info.get(k) for k in TOKEN_FIELDS},
    }


def poll_login(token, user_code):
    """One non-blocking login check. Returns a session dict on success, else None."""
    from tuya_sharing import LoginControl

    try:
        ok, result = LoginControl().login_result(token, CLIENT_ID, user_code)
    except Exception:
        return None
    return _session_from_info(result, user_code) if ok else None


def qr_login(user_code, scheme, png_path, poll_seconds=150):
    """Interactive (CLI) QR login: prints the QR and blocks until confirmed."""
    if not user_code:
        print(USER_CODE_HELP)
        user_code = input("Enter your Smart Life user code: ").strip()
    if not user_code:
        raise SystemExit("A user code is required.")

    try:
        token = mint_qr_token(user_code)
    except LoginError as e:
        raise SystemExit(f"{e}\nDouble-check the user code (from the Smart Life app).")

    print("\nScan this QR code in the Smart Life app  (+  ->  Scan),")
    print("then tap \"Confirm login\":\n")
    render_qr(f"{scheme}--qrLogin?token={token}", png_path)

    print("Waiting for you to confirm in the app", end="", flush=True)
    deadline = time.time() + poll_seconds
    session = None
    while time.time() < deadline:
        session = poll_login(token, user_code)
        if session:
            break
        print(".", end="", flush=True)
        time.sleep(2)
    print()

    if not session:
        raise SystemExit(
            "Login timed out — the QR code expires quickly. Re-run and scan promptly."
        )
    print("Logged in.\n")
    return session

def _apply_request_timeout():
    # ponytail: rebinding the SDK's module globals is the only injection point;
    # ceiling: silently no-ops if the SDK stops reading DEFAULT_TIMEOUT here.
    try:
        import tuya_sharing.customerapi as customerapi
        import tuya_sharing.user as user

        customerapi.DEFAULT_TIMEOUT = user.DEFAULT_TIMEOUT = REQUEST_TIMEOUT_SECONDS
    except (ImportError, AttributeError):
        pass


def build_manager(session, session_path):
    from tuya_sharing import Manager

    _apply_request_timeout()
    saver = _SessionSaver(session_path, session)
    return Manager(
        session.get("client_id", CLIENT_ID),
        session["user_code"],
        session["terminal_id"],
        session["endpoint"],
        session["token_info"],
        saver,
    )


def devices_from_session(session, session_path):
    """Build a Manager from a session and return its device list."""
    manager = build_manager(session, session_path)
    manager.update_device_cache()  # refreshes the token if needed
    return list(manager.device_map.values())


def get_devices(args):
    """Obtain devices, reusing a cached session when possible."""
    session = None if args.relogin else load_session(args.session)

    if session:
        try:
            return devices_from_session(session, args.session)
        except Exception as e:
            print(f"Saved session unusable ({e}); re-authenticating…\n")

    session = qr_login(args.user_code, args.scheme, args.qr_png)
    save_session(args.session, session)
    return devices_from_session(session, args.session)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def fmt_time(value):
    if value in (None, "", 0):
        return "-"
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return str(value)
    if ts > 1_000_000_000_000:  # milliseconds
        ts //= 1000
    try:
        dt = datetime.fromtimestamp(ts, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return str(value)  # out-of-range timestamp: show it raw, don't 500 the list
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def g(device, attr, default=None):
    return getattr(device, attr, default)


def print_devices(devices):
    online = sum(1 for d in devices if g(d, "online"))
    print(f"Found {len(devices)} device(s) — "
          f"{online} online, {len(devices) - online} offline\n")
    for i, d in enumerate(devices, 1):
        title = g(d, "name") or "(unnamed)"
        status = "online" if g(d, "online") else "offline"
        print(f"[{i:>3}] {title}  —  {status}")
        rows = [
            ("name", g(d, "name") or "-"),
            ("id", g(d, "id")),
            ("uuid", g(d, "uuid") or "-"),
            ("localKey", g(d, "local_key") or "-"),
            ("productId", g(d, "product_id") or "-"),
            ("productName", g(d, "product_name") or "-"),
            ("category", g(d, "category") or "-"),
            ("ip", g(d, "ip") or "-"),
            ("isOnline", bool(g(d, "online"))),
            ("updateTime", fmt_time(g(d, "update_time"))),
            ("activeTime", fmt_time(g(d, "active_time"))),
        ]
        for k, v in rows:
            print(f"      {k:<12}: {v}")
        print()


JSON_ATTRS = [
    "name", "id", "uuid", "local_key", "product_id", "product_name",
    "category", "ip", "online", "sub", "active_time", "update_time",
    "create_time", "time_zone",
]


def to_dict(device):
    return {a: g(device, a) for a in JSON_ATTRS}


def web_dict(device):
    """to_dict() with timestamps humanized and `online` coerced to bool (web UI)."""
    d = to_dict(device)
    for key in ("active_time", "update_time", "create_time"):
        d[key] = fmt_time(d.get(key))
    d["online"] = bool(d.get("online"))
    return d


def export_csv(devices, path):
    import csv

    time_cols = {"active_time", "update_time", "create_time"}
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(JSON_ATTRS)
        for d in devices:
            writer.writerow([
                fmt_time(g(d, a)) if a in time_cols else g(d, a)
                for a in JSON_ATTRS
            ])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="List Smart Life devices via QR-code login (no developer account)."
    )
    p.add_argument("--user-code", help="Smart Life user code (else you'll be prompted)")
    p.add_argument("--scheme", default="smartlife", choices=["tuyaSmart", "smartlife"],
                   help="QR login scheme prefix (default: smartlife; Smart Life scans both)")
    p.add_argument("--session", default=DEFAULT_SESSION,
                   help=f"Session cache file (default: {DEFAULT_SESSION})")
    p.add_argument("--qr-png", default=DEFAULT_QR_PNG,
                   help=f"Where to save the QR PNG fallback (default: {DEFAULT_QR_PNG})")
    p.add_argument("--relogin", action="store_true",
                   help="Ignore the cached session and scan a new QR code")
    p.add_argument("--logout", action="store_true",
                   help="Delete the cached session and exit")
    p.add_argument("--json", action="store_true", help="Output raw JSON")
    p.add_argument("--csv", metavar="PATH", help="Also write results to a CSV file")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.logout:
        if os.path.isfile(args.session):
            os.remove(args.session)
            print(f"Removed {args.session}")
        else:
            print("No cached session to remove.")
        return

    devices = get_devices(args)

    if args.json:
        print(json.dumps([to_dict(d) for d in devices], indent=2, ensure_ascii=False))
    else:
        print_devices(devices)

    if args.csv:
        export_csv(devices, args.csv)
        print(f"Wrote {len(devices)} row(s) to {args.csv}")

    if not devices:
        print("No devices found in this account. Make sure your devices are "
              "paired in the Smart Life app under this account.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
