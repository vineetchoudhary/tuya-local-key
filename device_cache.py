#!/usr/bin/env python3
"""
Encrypted, on-disk cache of the device list.
"""

import json
import os
from pathlib import Path

import tuya_devices as core

CACHE_VERSION = 1


def _crypto():
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:      # a declared dependency; if some build ever ships
        return None, None    # without it, run memory-only instead of failing.
    return Fernet, InvalidToken


def _cipher(key_path, create=False):
    Fernet, _ = _crypto()
    if Fernet is None:
        return None
    try:
        key = Path(key_path).read_bytes().strip()
    except OSError:
        key = b""
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        pass
    if not create:
        return None
    key = Fernet.generate_key()
    core.atomic_write(key_path, key, prefix=".cache-key-")
    return Fernet(key)


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def load(path, key_path):
    if not os.path.isfile(path):
        return None

    Fernet, InvalidToken = _crypto()
    if Fernet is None:
        return None  # no crypto to read it with; leave it for a build that has

    cipher = _cipher(key_path)
    if cipher is None:
        _unlink(path)  # ciphertext with no key is dead weight
        return None

    try:
        stored = json.loads(cipher.decrypt(Path(path).read_bytes()))
    except (OSError, InvalidToken, ValueError):
        _unlink(path)
        return None

    if not isinstance(stored, dict) or stored.get("version") != CACHE_VERSION:
        _unlink(path)
        return None

    body = stored.get("body")
    cached_at = stored.get("cached_at")
    session_key = stored.get("session_key")
    if not isinstance(body, dict) or not isinstance(cached_at, (int, float)) or not session_key:
        _unlink(path)
        return None

    return {"body": body, "cached_at": float(cached_at), "session_key": session_key}


def save(path, key_path, entry):
    try:
        payload = json.dumps({
            "version": CACHE_VERSION,
            "session_key": entry["session_key"],
            "cached_at": entry["cached_at"],
            "body": entry["body"],
        }).encode("utf-8")
        cipher = _cipher(key_path, create=True)
        if cipher is None:
            return False
        core.atomic_write(path, cipher.encrypt(payload), prefix=".devices-")
    except (OSError, TypeError, ValueError):
        return False
    return True


def clear(path, key_path):
    _unlink(path)
    _unlink(key_path)
