"""Durable, account-scoped device visibility preferences for the web app.

The deployment runs one process with threaded requests. A dedicated lock covers
each complete read/modify/write, and disk remains the source of truth: a failed
write never publishes an in-memory preference that was not saved.
"""

import hashlib
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import tuya_devices as core

VERSION = 1
MAX_DEVICE_ID_LENGTH = 256


class PreferenceError(Exception):
    """The preference file could not be safely read or saved."""


class AccountIdentityError(ValueError):
    """The saved session does not establish a usable account identity."""


def account_id(session):
    """Hash stable account fields; QR terminals, tokens and paths are excluded."""
    if not isinstance(session, dict):
        raise AccountIdentityError("A complete saved account is required.")
    fields = [session.get(name) for name in ("client_id", "user_code", "endpoint")]
    if any(not isinstance(value, str) or not value.strip() for value in fields):
        raise AccountIdentityError("Log in again to establish the account identity.")
    client_id, user_code, endpoint = (value.strip() for value in fields)
    try:
        parsed = urlsplit(endpoint)
        if (parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Invalid endpoint")
        port = parsed.port
        hostname = parsed.hostname.lower()
        if ":" in hostname:
            hostname = f"[{hostname}]"
        if port and port != (443 if parsed.scheme.lower() == "https" else 80):
            hostname += f":{port}"
        endpoint = urlunsplit((parsed.scheme.lower(), hostname,
                               parsed.path.rstrip("/"), "", ""))
    except ValueError as error:
        raise AccountIdentityError("The saved account endpoint is invalid.") from error
    identity = json.dumps([client_id, user_code, endpoint], separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def valid_device_id(value):
    return (isinstance(value, str) and 0 < len(value) <= MAX_DEVICE_ID_LENGTH
            and value == value.strip()
            and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value))


class PreferenceStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self):
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": VERSION, "accounts": {}}
        except (OSError, ValueError, UnicodeError, RecursionError) as error:
            raise PreferenceError("Could not read device preferences.") from error
        if (not isinstance(document, dict) or type(document.get("version")) is not int
                or document["version"] != VERSION
                or not isinstance(document.get("accounts"), dict)
                or set(document) != {"version", "accounts"}):
            raise PreferenceError("Device preferences have an unsupported or invalid format.")
        for digest, entry in document["accounts"].items():
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or not isinstance(entry, dict) or set(entry) != {"excluded_ids", "revision"}
                    or type(entry.get("revision")) is not int or entry["revision"] < 0
                    or not isinstance(entry.get("excluded_ids"), list)
                    or any(not valid_device_id(value) for value in entry["excluded_ids"])
                    or len(set(entry["excluded_ids"])) != len(entry["excluded_ids"])):
                raise PreferenceError("Device preferences contain invalid account data.")
        return document

    @staticmethod
    def _view(digest, entry):
        return {"excluded_ids": sorted(entry["excluded_ids"]),
                "revision": entry["revision"], "account_id": digest}

    def get(self, digest):
        with self._lock:
            document = self._read()
            return self._view(digest, document["accounts"].get(
                digest, {"excluded_ids": [], "revision": 0}))

    def update(self, digest, *, exclude=(), restore=(), restore_all=False):
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or any(not valid_device_id(value) for value in (*exclude, *restore))
                or set(exclude) & set(restore) or type(restore_all) is not bool
                or (restore_all and (exclude or restore))):
            raise ValueError("Invalid preference update")
        with self._lock:
            document = self._read()
            previous = document["accounts"].get(digest, {"excluded_ids": [], "revision": 0})
            ids = set() if restore_all else (set(previous["excluded_ids"]) | set(exclude)) - set(restore)
            if ids == set(previous["excluded_ids"]):
                return self._view(digest, previous)
            updated = {"excluded_ids": sorted(ids), "revision": previous["revision"] + 1}
            document["accounts"][digest] = updated
            try:
                payload = json.dumps(document, indent=2).encode("utf-8")
                core.atomic_write(self.path, payload, prefix=".preferences-")
            except (OSError, TypeError, ValueError) as error:
                raise PreferenceError("Could not save device preferences.") from error
            return self._view(digest, updated)
