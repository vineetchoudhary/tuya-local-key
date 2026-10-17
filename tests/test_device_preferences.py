import json
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

import device_preferences as preferences


SESSION = {"client_id": "client", "user_code": "account-a",
           "endpoint": "https://openapi.tuyaeu.com", "terminal_id": "terminal-a",
           "token_info": {"access_token": "secret"}}


def test_identity_is_stable_across_qr_login_tokens_and_endpoint_spelling():
    renewed = dict(SESSION, terminal_id="new-terminal", token_info={"access_token": "renewed"},
                   endpoint="HTTPS://OPENAPI.TUYAEU.COM:443/")
    assert preferences.account_id(renewed) == preferences.account_id(SESSION)
    assert preferences.account_id(dict(SESSION, user_code="b")) != preferences.account_id(SESSION)
    assert preferences.account_id(dict(SESSION, client_id="other")) != preferences.account_id(SESSION)
    assert preferences.account_id(dict(SESSION, endpoint="https://openapi.tuyaus.com")) != preferences.account_id(SESSION)


@pytest.mark.parametrize("session", [None, {}, {"user_code": "a"},
    dict(SESSION, client_id=""), dict(SESSION, user_code=[]),
    dict(SESSION, endpoint="not-a-url"), dict(SESSION, endpoint="https://example.com?token=secret")])
def test_incomplete_accounts_never_share_a_default_identity(session):
    with pytest.raises(preferences.AccountIdentityError):
        preferences.account_id(session)


def test_persisted_preferences_survive_restart_and_isolate_accounts(tmp_path):
    path = tmp_path / "device-preferences.json"
    store = preferences.PreferenceStore(path)
    account_a = preferences.account_id(SESSION)
    account_b = preferences.account_id(dict(SESSION, user_code="b"))
    assert store.get(account_a) == {"excluded_ids": [], "revision": 0, "account_id": account_a}
    assert store.update(account_a, exclude=["lamp", "plug"]) == {
        "excluded_ids": ["lamp", "plug"], "revision": 1, "account_id": account_a}
    restarted = preferences.PreferenceStore(path)
    assert restarted.get(account_a) == store.get(account_a)
    assert restarted.get(account_b)["excluded_ids"] == []
    restarted.update(account_b, exclude=["other"])
    assert restarted.get(account_a)["excluded_ids"] == ["lamp", "plug"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    for secret in ("account-a", "terminal-a", "secret", "openapi", "client"):
        assert secret not in text


def test_updates_are_delta_based_idempotent_and_allow_missing_device_restoration(tmp_path):
    store = preferences.PreferenceStore(tmp_path / "preferences.json")
    digest = preferences.account_id(SESSION)
    store.update(digest, exclude=["lamp", "plug", "plug"])
    assert store.update(digest, exclude=["plug"])["revision"] == 1
    result = store.update(digest, exclude=["sensor"], restore=["lamp", "absent"])
    assert result["excluded_ids"] == ["plug", "sensor"] and result["revision"] == 2
    assert store.update(digest, restore=["absent"])["revision"] == 2


def test_concurrent_deltas_preserve_every_update(tmp_path):
    store = preferences.PreferenceStore(tmp_path / "preferences.json")
    digest = preferences.account_id(SESSION)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda number: store.update(digest, exclude=[f"device-{number}"]), range(24)))
    result = store.get(digest)
    assert set(result["excluded_ids"]) == {f"device-{number}" for number in range(24)}
    assert result["revision"] == 24


@pytest.mark.parametrize("content", ["not json", "[]", '{"version":2,"accounts":{}}',
    '{"version":true,"accounts":{}}', '{"version":1,"accounts":[]}',
    json.dumps({"version": 1, "accounts": {"wrong": {"excluded_ids": [], "revision": 0}}}),
    json.dumps({"version": 1, "accounts": {preferences.account_id(SESSION): {"excluded_ids": [""], "revision": 1}}}),
    json.dumps({"version": 1, "accounts": {preferences.account_id(SESSION): {"excluded_ids": [], "revision": True}}})])
def test_corrupt_or_unsupported_files_are_not_overwritten(tmp_path, content):
    path = tmp_path / "preferences.json"
    path.write_text(content)
    store = preferences.PreferenceStore(path)
    digest = preferences.account_id(SESSION)
    with pytest.raises(preferences.PreferenceError):
        store.get(digest)
    with pytest.raises(preferences.PreferenceError):
        store.update(digest, exclude=["plug"])
    assert path.read_text() == content


def test_failed_write_leaves_disk_and_subsequent_reads_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    store = preferences.PreferenceStore(path)
    digest = preferences.account_id(SESSION)
    previous = store.update(digest, exclude=["plug"])
    saved = path.read_bytes()

    def fail(*args, **kwargs):
        raise PermissionError("read only storage")

    monkeypatch.setattr(preferences.core, "atomic_write", fail)
    with pytest.raises(preferences.PreferenceError):
        store.update(digest, exclude=["lamp"])
    assert path.read_bytes() == saved
    assert store.get(digest) == previous


def test_unreadable_store_is_an_error_instead_of_an_empty_set(tmp_path):
    # Reading a directory reliably fails even under privileged test users.
    store = preferences.PreferenceStore(tmp_path)
    with pytest.raises(preferences.PreferenceError):
        store.get(preferences.account_id(SESSION))


def test_manual_file_failure_is_observed_after_a_successful_read(tmp_path):
    path = tmp_path / "preferences.json"
    store = preferences.PreferenceStore(path)
    digest = preferences.account_id(SESSION)
    store.update(digest, exclude=["plug"])
    path.write_text("broken")
    with pytest.raises(preferences.PreferenceError):
        store.get(digest)


def test_restore_all_clears_every_persisted_id_and_keeps_other_accounts(tmp_path):
    store = preferences.PreferenceStore(tmp_path / "preferences.json")
    digest = preferences.account_id(SESSION)
    other = preferences.account_id(dict(SESSION, user_code="b"))
    store.update(digest, exclude=[f"device-{number}" for number in range(2100)])
    store.update(other, exclude=["other-device"])
    result = store.update(digest, restore_all=True)
    assert result["excluded_ids"] == [] and result["revision"] == 2
    assert store.update(digest, restore_all=True) == result
    assert store.get(other)["excluded_ids"] == ["other-device"]
