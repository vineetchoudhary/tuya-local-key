import json
import os
import stat
from pathlib import Path

import pytest

import device_cache


ENTRY = {
    "body": {
        "devices": [{"name": "Kitchen Plug", "local_key": "s3cret-local-key"}],
        "cached_at": 1_000.0,
    },
    "cached_at": 1_000.0,
    "session_key": "0f1e2d3c-session-digest",
}


@pytest.fixture()
def paths(tmp_path):
    return tmp_path / "devices.cache", tmp_path / "cache.key"


def _write_payload(path, key_path, payload):
    cipher = device_cache._cipher(key_path, create=True)
    Path(path).write_bytes(cipher.encrypt(json.dumps(payload).encode("utf-8")))


def test_save_then_load_round_trips(paths):
    path, key_path = paths

    assert device_cache.save(path, key_path, ENTRY) is True
    assert device_cache.load(path, key_path) == ENTRY


def test_stored_cache_never_exposes_local_keys(paths):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)

    blob = path.read_bytes()

    assert b"s3cret-local-key" not in blob
    assert b"Kitchen Plug" not in blob
    assert b"session-digest" not in blob


def test_cache_and_key_are_owner_readable_only(paths):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)

    for written in (path, key_path):
        assert stat.S_IMODE(os.stat(written).st_mode) == 0o600, written


def test_load_is_a_miss_when_nothing_is_stored(paths):
    path, key_path = paths

    assert device_cache.load(path, key_path) is None


def test_load_without_the_key_drops_the_unreadable_cache(paths):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)
    os.remove(key_path)

    assert device_cache.load(path, key_path) is None
    assert not path.exists()


def test_load_rejects_a_tampered_cache(paths):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)

    blob = bytearray(path.read_bytes())
    middle = len(blob) // 2
    blob[middle] = ord("B") if blob[middle] == ord("A") else ord("A")
    path.write_bytes(bytes(blob))

    assert device_cache.load(path, key_path) is None
    assert not path.exists()


def test_load_ignores_a_cache_written_by_another_version(paths):
    path, key_path = paths
    _write_payload(path, key_path, dict(ENTRY, version=device_cache.CACHE_VERSION + 1))

    assert device_cache.load(path, key_path) is None
    assert not path.exists()


V = device_cache.CACHE_VERSION


@pytest.mark.parametrize("payload", [
    {},                                                          # no framing at all
    {"body": {}, "cached_at": 1.0, "session_key": "d"},          # no version
    {"version": V},                                              # version only
    {"version": V, "body": [], "cached_at": 1.0, "session_key": "d"},     # body isn't a record
    {"version": V, "body": {}, "cached_at": "soon", "session_key": "d"},  # unusable timestamp
    {"version": V, "body": {}, "cached_at": 1.0, "session_key": ""},      # no session identity
    ["not", "a", "record"],                                      # not a mapping
])
def test_load_rejects_a_malformed_cache(paths, payload):
    path, key_path = paths
    _write_payload(path, key_path, payload)

    assert device_cache.load(path, key_path) is None
    assert not path.exists()


def test_load_without_cryptography_leaves_the_cache_alone(paths, monkeypatch):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)
    monkeypatch.setattr(device_cache, "_crypto", lambda: (None, None))

    assert device_cache.load(path, key_path) is None
    assert path.exists()


def test_clear_removes_the_cache_and_its_key(paths):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)

    device_cache.clear(path, key_path)

    assert not path.exists()
    assert not key_path.exists()
    device_cache.clear(path, key_path)  # idempotent: logout twice is fine


def test_clear_seals_a_copy_that_outlived_it(paths, tmp_path):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)
    escaped = tmp_path / "backup.cache"
    escaped.write_bytes(path.read_bytes())

    device_cache.clear(path, key_path)
    device_cache.save(path, key_path, ENTRY)  # mints a fresh key

    assert device_cache.load(escaped, key_path) is None
    assert device_cache.load(path, key_path) == ENTRY


def test_save_reuses_the_existing_key(paths):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)
    key = key_path.read_bytes()

    device_cache.save(path, key_path, ENTRY)

    assert key_path.read_bytes() == key


def test_save_replaces_an_unusable_key(paths):
    path, key_path = paths
    key_path.write_bytes(b"not-a-fernet-key")

    assert device_cache.save(path, key_path, ENTRY) is True
    assert device_cache.load(path, key_path) == ENTRY


def test_save_rejects_an_entry_it_cannot_serialize(paths):
    path, key_path = paths

    entry = {"body": {"devices": object()}, "cached_at": 1.0, "session_key": "d"}

    assert device_cache.save(path, key_path, entry) is False
    assert not path.exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores the directory mode this relies on")
def test_save_survives_an_unwritable_data_directory(tmp_path):
    directory = tmp_path / "readonly"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        saved = device_cache.save(
            directory / "devices.cache", directory / "cache.key", ENTRY
        )
    finally:
        directory.chmod(0o700)

    assert saved is False


def test_save_leaves_no_temp_files_behind(paths, tmp_path):
    path, key_path = paths
    device_cache.save(path, key_path, ENTRY)
    device_cache.save(path, key_path, ENTRY)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["cache.key", "devices.cache"]
