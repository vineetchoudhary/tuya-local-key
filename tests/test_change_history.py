"""History's observable contract: durable changes, honest evidence and isolation."""

import copy
import hashlib
import json
import threading

import pytest
from cryptography.fernet import Fernet

import change_history as history

ACCOUNT = hashlib.sha256(b"account-a").hexdigest()
OTHER = hashlib.sha256(b"account-b").hexdigest()
KEY_A = "private-key-00001"
KEY_B = "private-key-00002"


@pytest.fixture()
def journal(tmp_path):
    now = [1800000000.0]
    store = history.HistoryStore(tmp_path / "history.enc", tmp_path / "history.key", clock=lambda: now[0])
    return store, now


def device(name="Lamp", key=KEY_A, **fields):
    return {"id": "lamp", "name": name, "local_key": key, **fields}


def cloud(store, devices, operation, **kwargs):
    store.observe_cloud(ACCOUNT, devices, operation, **kwargs)


def events(store, **kwargs):
    return store.view(ACCOUNT, **kwargs)["events"]


def kinds(store):
    return [[change["kind"] for change in event["changes"]] for event in events(store)]


def test_initial_snapshot_is_quiet_and_cache_reads_create_no_events(journal):
    store, now = journal
    cloud(store, [device()], "first")
    now[0] += 1
    cloud(store, [device()], "same")
    assert events(store) == []
    assert events(store) == []
    assert store.view(ACCOUNT)["last_cloud_at"] == now[0]


def test_restart_retains_baseline_and_groups_name_and_key_changes(journal):
    store, now = journal
    cloud(store, [device()], "first")
    now[0] += 100
    store = history.HistoryStore(store.path, store.key_path, clock=lambda: now[0])
    cloud(store, [device("Kitchen lamp", KEY_B)], "next")
    assert kinds(store) == [["renamed", "key_changed"]]
    entry = events(store)[0]
    assert entry["previous_observed_at"] == now[0] - 100
    assert entry["observed_at"] == now[0]
    assert entry["name"] == "Kitchen lamp"
    assert entry["changes"][0] == {"kind": "renamed", "before": "Lamp", "after": "Kitchen lamp"}


def test_keys_are_absent_even_from_decrypted_history_and_public_models(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    cloud(store, [device(key=KEY_B)], "b")
    ciphertext = store.path.read_bytes()
    assert b"Lamp" not in ciphertext and KEY_A.encode() not in ciphertext
    decoded = Fernet(store.key_path.read_bytes()).decrypt(ciphertext)
    assert KEY_A.encode() not in decoded and KEY_B.encode() not in decoded
    public = json.dumps(store.view(ACCOUNT, export=True))
    assert "key_hash" not in public and KEY_A not in public and KEY_B not in public
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.key_path.stat().st_mode & 0o777 == 0o600


def test_retries_are_idempotent_but_a_b_a_changes_are_preserved(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    cloud(store, [device("B")], "b")
    cloud(store, [device("B")], "b")
    cloud(store, [device()], "c")
    assert [e["changes"][0]["after"] for e in events(store)] == ["Lamp", "B"]


def test_added_removed_and_reappeared_are_distinct_and_names_are_historical(journal):
    store, _ = journal
    cloud(store, [], "empty-initial")
    cloud(store, [device()], "add")
    cloud(store, [], "remove")
    cloud(store, [device("Returned")], "return")
    assert kinds(store) == [["reappeared", "renamed"], ["removed"], ["added"]]
    assert events(store)[1]["name"] == "Lamp"
    assert store.view(ACCOUNT, device_id="lamp")["devices"][0]["present"] is True


def test_unknown_fields_do_not_erase_known_key_room_or_home(journal):
    store, _ = journal
    full = device(home_ids=["home"], room_status="assigned", room_id="r", room_name="Study")
    cloud(store, [full], "a")
    cloud(store, [{"id": "lamp", "room_status": "unknown"}], "unknown")
    cloud(store, [full], "known-again")
    assert events(store) == []
    cloud(store, [dict(full, room_status="unassigned", room_id=None)], "remove-room")
    assert kinds(store) == [["room_changed"]]


def test_key_availability_is_not_rotation_and_last_known_key_is_retained(journal):
    store, _ = journal
    cloud(store, [device(key="")], "a")
    cloud(store, [device()], "available")
    cloud(store, [device(key="")], "unavailable")
    cloud(store, [device(key=KEY_B)], "rotated")
    assert kinds(store) == [["key_changed"], ["key_unavailable"], ["key_available"]]


def test_room_rename_is_not_a_move_and_home_order_is_not_a_change(journal):
    store, _ = journal
    first = device(home_ids=["a", "b"], room_status="assigned", room_id="r", room_name="Old")
    cloud(store, [first], "a")
    cloud(store, [dict(first, home_ids=["b", "a"], room_name="New")], "b")
    assert kinds(store) == [["room_renamed"]]


def test_reordered_devices_and_duplicate_invalid_inventory_do_not_create_false_removals(journal):
    store, _ = journal
    other = {"id": "other", "name": "Other"}
    cloud(store, [device(), other], "first")
    cloud(store, [other, device()], "reorder")
    with pytest.raises(history.HistoryError, match="invalid_observation"):
        cloud(store, [device(), device()], "bad")
    assert events(store) == []
    cloud(store, [device(), other], "recovered")
    assert events(store) == []


def lan(store, result, operation, devices=None, **kwargs):
    store.observe_lan(ACCOUNT, result, devices or [device()], operation, **kwargs)


def ok(ip="10.0.0.1", version="3.3", **kwargs):
    return {"status": "ok", "ip": ip, "version": version, **kwargs}


def test_failed_checks_preserve_address_and_protocol_comparison(journal):
    store, _ = journal
    cloud(store, [device()], "cloud")
    lan(store, {"lamp": ok()}, "a")
    lan(store, {"lamp": {"status": "unreachable", "ip": "10.0.0.1"}}, "failure")
    lan(store, {"lamp": {"status": "unreachable", "ip": "10.0.0.1"}}, "same-failure")
    lan(store, {"lamp": ok("10.0.0.2", "3.4")}, "recover")
    assert kinds(store) == [["connection_changed", "ip_changed", "protocol_changed"], ["connection_changed"]]


def test_lan_cache_seed_is_quiet_preserves_times_and_survives_restart(journal):
    store, now = journal
    fleet = [device()]
    cloud(store, fleet, "cloud")
    before = now[0] - 100
    store.seed_lan_cache(ACCOUNT, {"lamp": ok(checked_at=before)}, fleet)
    assert events(store) == []
    store = history.HistoryStore(store.path, store.key_path, clock=lambda: now[0])
    now[0] += 10
    lan(store, {"lamp": {"status": "unreachable", "ip": "10.0.0.1"}}, "first-fresh-check")
    entry = events(store)[0]
    assert entry["previous_observed_at"] == before
    assert entry["changes"][0]["before"] == "ok"
    assert entry["changes"][0]["after"] == "unreachable"
    assert entry["observed_at"] == now[0]


def test_child_cache_seed_requires_explicit_online_report(journal):
    store, now = journal
    fleet = [device(), {"id": "child", "name": "Sensor", "sub": True, "gateway_id": "lamp"}]
    cloud(store, fleet, "cloud")
    before = now[0] - 100
    store.seed_lan_cache(ACCOUNT, {"child": {"status": "via_gateway", "gateway_id": "lamp", "sub_online": True,
                                           "checked_at": before}}, fleet)
    now[0] += 10
    lan(store, {"child": {"status": "via_gateway", "gateway_id": "lamp", "sub_online": False}}, "offline", fleet)
    entry = events(store, device_id="child")[0]
    assert entry["previous_observed_at"] == before
    assert entry["changes"] == [{"kind": "sub_device_changed", "before": True, "after": False}]


def test_lan_cache_seed_respects_clear_epoch(journal):
    store, now = journal
    fleet = [device()]
    cloud(store, fleet, "cloud")
    old_epoch = store.epoch(ACCOUNT)
    store.clear(ACCOUNT, old_epoch, device_id="lamp")
    store.seed_lan_cache(ACCOUNT, {"lamp": ok(checked_at=now[0] - 100)}, fleet, epoch=old_epoch)
    lan(store, {"lamp": {"status": "unreachable", "ip": "10.0.0.1"}}, "new-check")
    assert events(store) == []


def test_lan_cache_import_stays_complete_after_operation_ids_are_pruned(journal):
    store, now = journal
    fleet = [device(uuid="original")]
    cloud(store, fleet, "cloud")
    old = {"lamp": ok(checked_at=now[0] - 100)}
    store.seed_lan_cache(ACCOUNT, old, fleet)
    changed = [device(uuid="replacement")]
    cloud(store, changed, "identity-change")
    # Exercise the bounded retry list without hundreds of unrelated disk writes.
    with store._guard():
        doc = store._read(ACCOUNT)
        doc["operations"] = []
        store._write(doc)
    now[0] += 91 * 86400
    store = history.HistoryStore(store.path, store.key_path, clock=lambda: now[0])
    store.seed_lan_cache(ACCOUNT, old, changed)
    lan(store, {"lamp": {"status": "unreachable", "ip": "10.0.0.1"}}, "new-identity-check", changed)
    assert not any(entry["source"] == "lan" for entry in events(store))


def test_device22_protocol_alias_does_not_create_false_change(journal):
    store, _ = journal
    lan(store, {"lamp": ok(version="3.2")}, "first")
    lan(store, {"lamp": ok(device22=True)}, "alias")
    assert events(store) == []
    lan(store, {"lamp": ok()}, "quirk-removed")
    assert kinds(store) == [["protocol_changed"]]


def test_31_recovery_never_claims_key_authentication(journal):
    store, _ = journal
    lan(store, {"lamp": ok()}, "a")
    lan(store, {"lamp": {"status": "busy"}}, "b")
    lan(store, {"lamp": ok(version="3.1")}, "c")
    assert events(store)[0]["changes"][0]["key_authenticated"] is False


def test_gateway_ip_change_is_one_entry_visible_in_child_history(journal):
    store, _ = journal
    devices = [device(), {"id": "child", "name": "Sensor", "sub": True, "gateway_id": "lamp"}]
    child = {"status": "via_gateway", "gateway_id": "lamp", "sub_online": True}
    lan(store, {"lamp": ok(), "child": child}, "first", devices)
    lan(store, {"lamp": ok("10.0.0.2"), "child": child}, "moved", devices)
    assert kinds(store) == [["ip_changed"]]
    assert len(events(store, device_id="child")) == 1
    assert events(store)[0]["related_device_ids"] == ["child"]


def test_unknown_child_state_and_gateway_failure_do_not_imply_child_offline(journal):
    store, _ = journal
    devices = [device(), {"id": "child", "name": "Sensor", "sub": True, "gateway_id": "lamp"}]
    first = {"status": "via_gateway", "gateway_id": "lamp", "sub_online": True}
    lan(store, {"child": first}, "a", devices)
    lan(store, {"child": {"status": "via_gateway", "gateway_id": "lamp"}}, "unknown", devices)
    assert events(store) == []
    lan(store, {"child": dict(first, sub_online=False)}, "offline", devices)
    assert kinds(store) == [["sub_device_changed"]]


def test_account_mismatch_never_returns_or_overwrites_another_accounts_data(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    ciphertext = store.path.read_bytes()
    with pytest.raises(history.HistoryError, match="account_changed"):
        store.view(OTHER)
    with pytest.raises(history.HistoryError, match="account_changed"):
        store.observe_cloud(OTHER, [], "other")
    assert store.path.read_bytes() == ciphertext


def test_clear_preserves_baseline_and_invalidates_inflight_epoch(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    epoch = store.epoch(ACCOUNT)
    cloud(store, [device("B")], "b")
    store.clear(ACCOUNT, epoch)
    assert events(store) == []
    assert store.epoch(ACCOUNT) != epoch
    cloud(store, [], "old-running-refresh", epoch=epoch)
    assert events(store) == []
    cloud(store, [device("B")], "new-same")
    assert events(store) == []
    cloud(store, [device("C")], "new-change")
    assert kinds(store) == [["renamed"]]


@pytest.mark.parametrize("cleared", ["gateway", "one", "two"])
def test_clearing_one_device_preserves_other_devices_shared_gateway_events(journal, cleared):
    store, now = journal
    devices = [{"id": "gateway", "name": "Hub", "category": "wg2", "local_key": KEY_A},
               {"id": "one", "name": "One", "sub": True, "gateway_id": "gateway"},
               {"id": "two", "name": "Two", "sub": True, "gateway_id": "gateway"}]
    cloud(store, devices, "baseline")
    store.observe_lan(ACCOUNT, {"gateway": ok()}, devices, "first-ip")
    store.observe_lan(ACCOUNT, {"gateway": ok("10.0.0.2")}, devices, "moved")
    before = {i: events(store, device_id=i) for i in ("gateway", "one", "two")}
    epoch = store.epoch(ACCOUNT)
    now[0] += 10
    store.clear(ACCOUNT, epoch, device_id=cleared)
    restarted = history.HistoryStore(store.path, store.key_path, clock=lambda: now[0])
    for dev_id in before:
        assert events(restarted, device_id=dev_id) == ([] if dev_id == cleared else before[dev_id])
    assert restarted.view(ACCOUNT, device_id=cleared)["started_at"] == now[0]
    store = restarted
    store.observe_lan(ACCOUNT, {"gateway": ok("10.0.0.3")}, devices, "new-move")
    assert len(events(store, device_id=cleared)) == 1
    assert events(store, device_id=cleared)[0]["changes"][0]["before"] == "10.0.0.2"


def test_individual_clears_remove_unshared_events_without_resetting_other_sequences(journal):
    store, _ = journal
    other = {"id": "other", "name": "Other"}
    cloud(store, [device(), other], "a")
    cloud(store, [device("New"), dict(other, name="Other new")], "b")
    before = events(store, device_id="other")
    store.clear(ACCOUNT, store.epoch(ACCOUNT), device_id="lamp")
    assert events(store, device_id="lamp") == []
    assert events(store, device_id="other") == before
    assert [e["device_id"] for e in events(store)] == ["other"]
    cloud(store, [device("Newest"), dict(other, name="Other new")], "c")
    assert events(store, device_id="lamp")[0]["sequence"] > before[0]["sequence"]


def test_device_clear_drops_only_that_devices_part_of_running_observations(journal):
    store, _ = journal
    other = {"id": "other", "name": "Other"}
    fleet = [device(), other]
    cloud(store, fleet, "baseline")
    lan(store, {"lamp": ok(), "other": ok("10.0.0.2")}, "first-check", fleet)
    token = store.epoch(ACCOUNT)   # a refresh and a scan begin
    store.clear(ACCOUNT, token, device_id="lamp")
    renamed = [device("Lamp renamed"), dict(other, name="Other renamed")]
    cloud(store, renamed, "running-refresh", epoch=token)
    lan(store, {"lamp": ok("10.0.0.9"), "other": ok("10.0.0.8")}, "running-scan", fleet, epoch=token)
    assert events(store, device_id="lamp") == []
    assert [[c["kind"] for c in e["changes"]] for e in events(store, device_id="other")] == [
        ["ip_changed"], ["renamed"]]
    # The cleared device kept its earlier baseline, so a fresh refresh compares with it.
    cloud(store, renamed, "next-refresh")
    assert events(store, device_id="lamp")[0]["changes"] == [
        {"kind": "renamed", "before": "Lamp", "after": "Lamp renamed"}]


def test_device_clear_accepts_older_history_without_clear_markers(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    cloud(store, [device("New")], "b")
    cipher = Fernet(store.key_path.read_bytes())
    document = json.loads(cipher.decrypt(store.path.read_bytes()))
    document.pop("device_clears", None)
    store.path.write_bytes(cipher.encrypt(json.dumps(document).encode()))
    store.clear(ACCOUNT, store.epoch(ACCOUNT), device_id="lamp")
    assert events(store, device_id="lamp") == []


def test_disk_failure_keeps_baseline_and_next_observation_compares_last_durable_data(journal, monkeypatch):
    store, _ = journal
    cloud(store, [device()], "a")
    original = history.core.atomic_write
    with monkeypatch.context() as patch:
        patch.setattr(history.core, "atomic_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(history.HistoryError, match="history_save_failed"):
            cloud(store, [device("B")], "failed")
    assert history.core.atomic_write is original
    cloud(store, [device("C")], "recovered")
    assert events(store)[0]["changes"][0]["before"] == "Lamp"


@pytest.mark.parametrize("problem", ["ciphertext", "key", "schema"])
def test_corruption_missing_key_and_future_schema_preserve_files(journal, problem):
    store, _ = journal
    cloud(store, [device()], "a")
    if problem == "ciphertext":
        store.path.write_bytes(b"broken")
    elif problem == "key":
        store.key_path.unlink()
    else:
        cipher = Fernet(store.key_path.read_bytes())
        doc = json.loads(cipher.decrypt(store.path.read_bytes()))
        doc["version"] = 999
        store.path.write_bytes(cipher.encrypt(json.dumps(doc).encode()))
    original = store.path.read_bytes()
    with pytest.raises(history.HistoryError):
        store.view(ACCOUNT)
    with pytest.raises(history.HistoryError):
        cloud(store, [], "should-not-overwrite")
    assert store.path.read_bytes() == original


def test_pagination_is_stable_while_new_entries_arrive_and_clear_invalidates_cursor(journal):
    store, _ = journal
    for index in range(5):
        cloud(store, [device(str(index))], str(index))
    first = store.view(ACCOUNT, limit=2)
    cloud(store, [device("new")], "new")
    second = store.view(ACCOUNT, limit=2, cursor=first["next_cursor"])
    assert [e["sequence"] for e in first["events"] + second["events"]] == [4, 3, 2, 1]
    store.clear(ACCOUNT, first["epoch"])
    with pytest.raises(history.HistoryError, match="history_cursor_expired"):
        store.view(ACCOUNT, cursor=first["next_cursor"])


def test_filters_export_removed_devices_and_untrusted_names_are_data(journal):
    store, now = journal
    cloud(store, [device()], "a")
    now[0] += 10
    cloud(store, [device("<img onerror=bad()>")], "b")
    now[0] += 10
    cloud(store, [], "c")
    assert len(events(store, kind="removed", source="cloud")) == 1
    assert len(events(store, query="onerror")) == 2
    assert len(events(store, since=now[0] - 1)) == 1
    assert store.view(ACCOUNT, export=True)["devices"][0]["present"] is False


def test_retention_prunes_events_and_expired_tombstones(journal):
    store, now = journal
    cloud(store, [device()], "a")
    cloud(store, [], "remove")
    now[0] += 91 * 86400
    assert events(store) == []
    assert store.view(ACCOUNT)["pruned"] is True
    cloud(store, [device()], "new-after-expiry")
    assert kinds(store) == [["added"]]


def test_count_limit_clock_rollback_and_purge(journal):
    store, now = journal
    store.max_events = 2
    for i in range(5):
        now[0] -= 1
        cloud(store, [device(str(i))], str(i))
    assert [e["sequence"] for e in events(store)] == [4, 3]
    assert store.view(ACCOUNT)["pruned"] is True
    store.purge()
    assert not store.path.exists() and not store.key_path.exists()
    assert events(store) == []


def test_memory_only_and_disabled_modes_remove_previous_persistence(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    transient = history.HistoryStore(store.path, store.key_path, persist=False)
    cloud(transient, [device()], "a")
    cloud(transient, [device("New")], "b")
    assert len(events(transient)) == 1
    assert transient.view(ACCOUNT)["mode"] == "temporary"
    assert not store.path.exists() and not store.key_path.exists()
    disabled = history.HistoryStore(store.path, store.key_path, enabled=False)
    cloud(disabled, [device()], "ignored")
    assert not store.path.exists() and not store.key_path.exists()


def test_concurrent_observations_do_not_lose_changes(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    threads = [threading.Thread(target=cloud, args=(store, [device(str(i))], str(i))) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert len(events(store)) == 12


def test_gateway_key_rotation_is_one_entry_linked_to_children(journal):
    store, _ = journal
    def fleet(key):
        return [{"id": "gateway", "name": "Hub", "category": "wg2", "sub": True},
                {"id": "one", "name": "One", "sub": True, "gateway_id": "gateway", "local_key": key},
                {"id": "two", "name": "Two", "sub": True, "gateway_id": "gateway", "local_key": key}]
    cloud(store, fleet(KEY_A), "a")
    cloud(store, fleet(KEY_B), "b")
    assert kinds(store) == [["key_changed"]]
    assert events(store)[0]["device_id"] == "gateway"
    assert events(store)[0]["related_device_ids"] == ["one", "two"]
    assert len(events(store, device_id="two")) == 1


@pytest.mark.parametrize("change", ["gateway", "direct", "identity", "node"])
def test_remembered_gateway_key_source_is_invalidated_when_child_relationship_changes(journal, change):
    store, _ = journal
    first = [{"id": "gw-a", "name": "Hub A", "category": "wg2"},
             {"id": "gw-b", "name": "Hub B", "category": "wg2"},
             {"id": "child", "name": "Sensor", "sub": True, "local_key": KEY_A,
              "gateway_id": None, "node_id": "one", "uuid": "old-identity"}]
    cloud(store, first, "first")
    store.observe_lan(ACCOUNT, {"gw-a": ok(key_from="child")}, first, "identified")
    updated = copy.deepcopy(first)
    updated[2]["local_key"] = KEY_B
    if change == "gateway":
        updated[2]["gateway_id"] = "gw-b"
    elif change == "direct":
        updated[2]["sub"] = False
    elif change == "identity":
        updated[2]["uuid"] = "replacement-identity"
    else:
        updated[2]["node_id"] = "two"
    cloud(store, updated, "moved")
    assert not any(e["device_id"] == "gw-a" and any(c["kind"] == "key_changed" for c in e["changes"])
                   for e in events(store))


def test_remembered_gateway_key_source_still_detects_real_rotation(journal):
    store, _ = journal
    first = [{"id": "gw", "name": "Hub", "category": "wg2"},
             {"id": "child", "name": "Sensor", "sub": True, "local_key": KEY_A}]
    cloud(store, first, "first")
    store.observe_lan(ACCOUNT, {"gw": ok(key_from="child")}, first, "identified")
    updated = copy.deepcopy(first)
    updated[1]["local_key"] = KEY_B
    cloud(store, updated, "rotated")
    assert any(e["device_id"] == "gw" and any(c["kind"] == "key_changed" for c in e["changes"])
               for e in events(store))
    # One rotation, one entry: the sub-device whose key the hub uses is linked to it.
    assert [(e["device_id"], e["related_device_ids"]) for e in events(store)] == [("gw", ["child"])]


@pytest.mark.parametrize("change", ["node", "identity"])
def test_dropped_gateway_key_source_stays_dropped_on_later_refreshes(journal, change):
    store, _ = journal
    first = [{"id": "gw-a", "name": "Hub A", "category": "wg2"},
             {"id": "gw-b", "name": "Hub B", "category": "wg2"},
             {"id": "child", "name": "Sensor", "sub": True, "local_key": KEY_A,
              "gateway_id": None, "node_id": "one", "uuid": "old-identity"}]
    cloud(store, first, "first")
    store.observe_lan(ACCOUNT, {"gw-a": ok(key_from="child")}, first, "identified")
    moved = copy.deepcopy(first)
    moved[2]["local_key"] = KEY_B
    moved[2]["node_id" if change == "node" else "uuid"] = "changed"
    cloud(store, moved, "moved")
    cloud(store, moved, "unchanged")
    rotated = copy.deepcopy(moved)
    rotated[2]["local_key"] = "private-key-00003"
    cloud(store, rotated, "rotated")
    assert not any(e["device_id"] == "gw-a" for e in events(store))
    assert (events(store)[0]["device_id"], kinds(store)[0]) == ("child", ["key_changed"])


def test_gateway_answering_to_its_own_key_no_longer_borrows_one(journal):
    store, _ = journal
    fleet = [{"id": "gw", "name": "Hub", "category": "wg2"},
             {"id": "child", "name": "Sensor", "sub": True, "local_key": KEY_A}]
    cloud(store, fleet, "first")
    store.observe_lan(ACCOUNT, {"gw": ok(key_from="child")}, fleet, "borrowed")
    store.observe_lan(ACCOUNT, {"gw": ok()}, fleet, "own-key")
    rotated = copy.deepcopy(fleet)
    rotated[1]["local_key"] = KEY_B
    cloud(store, rotated, "rotated")
    assert [(e["device_id"], [c["kind"] for c in e["changes"]]) for e in events(store)] == [
        ("child", ["key_changed"])]


def test_sub_device_moving_to_another_hub_or_off_it_is_recorded(journal):
    store, _ = journal
    hubs = [{"id": "hub-1", "name": "Hub 1", "category": "wg2", "local_key": KEY_A},
            {"id": "hub-2", "name": "Hub 2", "category": "wg2", "local_key": KEY_B}]
    sensor = {"id": "sensor", "name": "Sensor", "sub": True, "node_id": "n1", "local_key": KEY_A}
    cloud(store, hubs + [sensor], "on-hub-1")
    cloud(store, hubs + [dict(sensor, local_key=KEY_B)], "moved-to-hub-2")
    cloud(store, hubs + [dict(sensor, local_key="private-key-00003")], "off-both-hubs")
    cloud(store, hubs + [sensor], "back-on-hub-1")
    cloud(store, hubs + [dict(sensor, local_key="")], "key-withdrawn")
    assert kinds(store) == [["key_unavailable"], ["key_changed"], ["key_changed"], ["key_changed"]]
    assert {e["device_id"] for e in events(store)} == {"sensor"}
    decoded = Fernet(store.key_path.read_bytes()).decrypt(store.path.read_bytes())
    assert KEY_A.encode() not in decoded and KEY_B.encode() not in decoded


def test_different_child_supplies_same_gateway_key_without_rotation(journal):
    store, _ = journal
    fleet = [{"id": "gateway", "name": "Hub", "category": "wg2"},
             {"id": "one", "name": "One", "sub": True, "gateway_id": "gateway", "local_key": KEY_A},
             {"id": "two", "name": "Two", "sub": True, "gateway_id": "gateway", "local_key": KEY_A}]
    cloud(store, fleet, "a")
    cloud(store, [fleet[0], fleet[2]], "b")
    assert kinds(store) == [["removed"]]


def test_unknown_field_preserves_its_own_previous_known_timestamp(journal):
    store, now = journal
    cloud(store, [device()], "a")
    first = now[0]
    now[0] += 10
    cloud(store, [{"id": "lamp", "name": "Lamp"}], "unknown-key")
    now[0] += 10
    cloud(store, [device(key=KEY_B)], "rotated")
    assert events(store)[0]["previous_observed_at"] == first + 10
    assert events(store)[0]["changes"][0]["previous_observed_at"] == first


def test_idle_maintenance_prunes_without_cloud_access(journal):
    store, now = journal
    cloud(store, [device()], "a")
    cloud(store, [device("B")], "b")
    now[0] += 91 * 86400
    store.maintain()
    assert events(store) == []


def test_conflicting_paths_never_delete_or_overwrite_other_app_data(tmp_path):
    session = tmp_path / "session.json"
    session.write_text("private session")
    store = history.HistoryStore(session, tmp_path / "history.key", enabled=False, forbidden_paths=[session])
    assert store.error == "history_configuration"
    with pytest.raises(history.HistoryError, match="history_configuration"):
        store.purge()
    assert session.read_text() == "private session"


def test_invalid_retention_preserves_existing_journal(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    before = store.path.read_bytes()
    invalid = history.HistoryStore(store.path, store.key_path, retention_days=0)
    with pytest.raises(history.HistoryError, match="history_configuration"):
        invalid.view(ACCOUNT)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("retention", [0, -1, 5000, None])
def test_invalid_retention_never_prunes_clears_or_records(journal, retention):
    store, now = journal
    other = {"id": "other", "name": "Other"}
    cloud(store, [device(), other], "a")
    cloud(store, [device("B"), dict(other, name="Other B")], "b")
    token = store.epoch(ACCOUNT)
    before = store.path.read_bytes()
    now[0] += 1
    invalid = history.HistoryStore(store.path, store.key_path, retention_days=retention, clock=lambda: now[0])
    invalid.activate(ACCOUNT)   # every page load
    invalid.maintain()          # the hourly timer
    with pytest.raises(history.HistoryError, match="history_configuration"):
        invalid.clear(ACCOUNT, token, device_id="lamp")   # a page opened before the restart
    with pytest.raises(history.HistoryError, match="history_configuration"):
        invalid.observe_cloud(ACCOUNT, [device("C"), other], "c", epoch=token)
    assert invalid.error == "history_configuration"
    assert store.path.read_bytes() == before
    assert len(events(store)) == 2
    invalid.purge()   # logging out still deletes history, and the setting still needs fixing
    assert not store.path.exists() and invalid.error == "history_configuration"


def test_turning_history_or_saved_data_off_deletes_it_despite_invalid_retention(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    disabled = history.HistoryStore(store.path, store.key_path, enabled=False, retention_days=0)
    assert not store.path.exists() and not store.key_path.exists()
    assert disabled.error is None
    cloud(store, [device()], "b")
    temporary = history.HistoryStore(store.path, store.key_path, persist=False, retention_days="90d")
    assert not store.path.exists() and not store.key_path.exists()
    assert temporary.error == "history_configuration"


def test_public_history_projects_fields_and_never_exports_key_values(journal):
    store, _ = journal
    cloud(store, [device()], "a")
    cloud(store, [device(key=KEY_B)], "b")
    cipher = Fernet(store.key_path.read_bytes())
    document = json.loads(cipher.decrypt(store.path.read_bytes()))
    document["events"][0]["private_metadata"] = KEY_A
    document["events"][0]["changes"][0].update(before=KEY_A, after=KEY_B, key_hash="private-hash")
    store.path.write_bytes(cipher.encrypt(json.dumps(document).encode()))
    response = json.dumps(store.view(ACCOUNT, export=True))
    assert KEY_A not in response and KEY_B not in response and "private-hash" not in response
    assert events(store, query=KEY_A) == []


@pytest.mark.parametrize("path,value", [
    (("cloud_at",), "missing"), (("clock_at",), []), (("started_at",), 10 ** 1000), (("pruned_through",), "bad"),
    (("account_id",), None), (("operations",), [None]),
    (("device_clears",), []),
    (("device_clears",), {"lamp": {"through": True, "at": 1}}),
    (("device_clears",), {"lamp": {"through": 999999, "at": 1}}),
    (("device_clears",), {"lamp": {"through": 0, "at": []}}),
    (("cloud", "lamp", "room"), []), (("cloud", "lamp", "room", "state"), []),
    (("cloud", "lamp", "field_at"), []), (("cloud", "lamp", "observed_at"), None),
    (("cloud", "lamp", "home_ids"), "bad"), (("cloud", "lamp", "key_state"), []),
    (("lan", "lamp", "protocol"), "3.3"), (("lan", "lamp", "observed_at"), float("nan")),
    (("events", 0, "changes", 0, "before"), {}), (("events", 0, "changes", 0, "kind"), []),
])
def test_structurally_invalid_decryptable_history_is_preserved_and_rejected(journal, path, value):
    store, _ = journal
    first = device(room_status="assigned", room_id="r", room_name="Study")
    cloud(store, [first], "a")
    lan(store, {"lamp": ok()}, "lan")
    cloud(store, [dict(first, name="Renamed")], "b")
    cipher = Fernet(store.key_path.read_bytes())
    document = json.loads(cipher.decrypt(store.path.read_bytes()))
    target = document
    for key in path[:-1]:
        target = target[key]
    if value == "missing":
        del target[path[-1]]
    else:
        target[path[-1]] = value
    ciphertext = cipher.encrypt(json.dumps(document).encode())
    store.path.write_bytes(ciphertext)
    with pytest.raises(history.HistoryError, match="history_invalid"):
        store.view(ACCOUNT)
    with pytest.raises(history.HistoryError, match="history_invalid"):
        cloud(store, [first], "retry")
    assert store.path.read_bytes() == ciphertext
