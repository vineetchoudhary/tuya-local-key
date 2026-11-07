import copy
import json

import pytest

import support_report


DEVICE = {
    "id": "private-device-id", "name": "Private bedroom lamp", "uuid": "private-uuid",
    "local_key": "private-local-key", "uid": "private-user", "owner_id": "private-owner",
    "node_id": "private-node", "ip": "203.0.113.27", "lat": 12.9716, "lon": 77.5946,
    "room_name": "Private room", "category": "dj", "product_id": "shared-product",
    "product_name": "Colour lamp", "model": "L100", "online": True, "support_local": True,
    "status": {"switch_led": True, "colour": "private-reading"},
    "status_range": {"switch_led": {"type": "Boolean", "values": "{}"},
                     "temperature": {"type": "Integer", "values": '{"min":0,"max":1000,"unit":"K","scale":1}'}},
    "function": {"switch_led": {"type": "Boolean", "name": "Private switch description"}},
    "local_strategy": {"1": {"status_code": "switch_led"}},
    "future_secret_field": {"token": "private-future-secret"},
}
RESULT = {"status": "ok", "ip": "172.16.20.13", "version": "3.4", "checked_at": 1752000100,
          "key_current": True, "private_payload": "private-raw-reply"}


def report(device=None, result=None, **kwargs):
    device = copy.deepcopy(DEVICE) if device is None else device
    return support_report.build([device], {device["id"]: RESULT if result is None else result},
                                device["id"], 1752000000, now=1752000200, **kwargs)


def test_report_preserves_useful_definitions_and_connection_evidence_without_private_data():
    output = report()
    encoded = json.dumps(output, allow_nan=False)
    assert output["schema_version"] == 1
    assert output["device"]["product_id"] == DEVICE["product_id"]
    assert output["device"]["connection"]["key_verified"] is True
    assert output["device"]["connection"]["checked_at"].endswith("+00:00")
    definitions = {item["code"]: item for item in output["data_points"]["definitions"]}
    assert definitions["switch_led"]["dp_id"] == "1"
    assert definitions["switch_led"]["readable"] is True
    assert definitions["switch_led"]["writable"] is True
    assert definitions["temperature"]["definitions"] == {"min": 0, "max": 1000, "scale": 1, "unit": "K"}
    for value in ("private-", DEVICE["name"], DEVICE["ip"], RESULT["ip"], str(DEVICE["lat"]), str(DEVICE["lon"])):
        assert value not in encoded
    assert "current values" in output["privacy"]["excluded"]


def test_private_strings_are_also_removed_when_embedded_in_allowlisted_fields():
    device = copy.deepcopy(DEVICE)
    device["product_name"] = f'Lamp for {device["name"]} with {device["local_key"]}'
    device["status_range"]["temperature"]["values"] = json.dumps({
        "unit": DEVICE["uuid"], "range": [DEVICE["id"], "person@example.com", "10.0.0.14", "token-secret"],
        "unknown": "private-extra-definition", "credentials": {"password": "private-password"},
    })
    encoded = json.dumps(report(device, session={"token_info": {"access_token": "token-secret"}}))
    for value in (DEVICE["name"], DEVICE["local_key"], DEVICE["uuid"], DEVICE["id"],
                  "person@example.com", "10.0.0.14", "token-secret", "private-extra-definition", "private-password"):
        assert value not in encoded
    assert "[redacted]" in encoded


@pytest.mark.parametrize("version,current,status,verified", [
    ("3.1", True, "ok", False), ("3.4", False, "ok", False),
    ("3.4", True, "key_mismatch", False), ("3.4", True, "ok", True),
])
def test_key_verification_does_not_overstate_plaintext_or_stale_results(version, current, status, verified):
    connection = report(result=dict(RESULT, version=version, key_current=current, status=status))["device"]["connection"]
    assert connection["key_verified"] is verified


def test_gateway_verification_and_child_online_report_stay_separate():
    child = dict(DEVICE, sub=True, gateway_id="private-gateway")
    gateway = {"id": "private-gateway", "name": "Private gateway name", "category": "wg2", "local_key": "private-gateway-key"}
    results = {child["id"]: dict(RESULT, status="via_gateway", gateway_id=gateway["id"], sub_online=False),
               gateway["id"]: RESULT}
    output = support_report.build([child, gateway], results, child["id"], 1)
    assert output["device"]["role"] == "sub_device"
    assert output["device"]["connection"]["verification_scope"] == "gateway"
    assert output["device"]["connection"]["sub_device_online"] is False
    assert output["gateway"]["role"] == "gateway"
    assert "private-" not in json.dumps(output)


def test_report_handles_missing_keys_connections_and_malformed_definitions():
    device = {"id": "private-empty", "status_range": {
        "broken": {"values": "{bad json"}, "list": {"values": "[]"},
        "large": {"type": "Integer", "values": {"min": 10 ** 500, "max": float("nan"), "unit": []}},
    }, "function": None, "local_strategy": []}
    output = report(device, {})
    assert output["device"]["details_available"]["local_key"] is False
    assert output["device"]["connection"]["status"] == "not_checked"
    assert all(item["definitions"] == {} for item in output["data_points"]["definitions"])
    json.dumps(output, allow_nan=False)


def test_network_job_keeps_outcomes_and_timings_but_no_targets_ids_errors_or_other_devices():
    job = {"id": "private-job", "kind": "device", "state": "cancelled", "device_id": DEVICE["id"],
           "targets": "172.16.20.0/24", "started_at": 1752000000, "finished_at": 1752000100,
           "result": RESULT, "error": "private-error-with-key", "also": [{"id": "private-other-device"}],
           "summary": {"addresses": 1, "duration": 1.5, "routers": ["private-router"],
                       "unmatched": ["private-other-address"]}}
    output = report(job=job)
    check = output["last_network_check"]
    assert check["state"] == "cancelled"
    assert check["summary"] == {"addresses": 1, "duration": 1.5}
    assert check["error"] == "unknown_error"
    assert "private-" not in json.dumps(output)
    assert "172.16.20" not in json.dumps(output)
    assert "last_network_check" not in report(job=dict(job, device_id="unrelated"))


def test_definition_limit_keeps_large_reports_bounded():
    device = {"id": "private-large", "status": {f"point_{i}": "private-value" for i in range(1100)}}
    output = report(device)
    assert output["data_points"]["count"] == 1100
    assert len(output["data_points"]["definitions"]) == support_report.MAX_DATA_POINTS
    assert output["data_points"]["truncated"] is True
    assert "private-value" not in json.dumps(output)


def test_large_enum_definitions_are_limited_by_size_as_well_as_count():
    values = json.dumps({"range": ["enum-label-" * 8] * 256})
    device = {"id": "private-large", "status_range": {f"point_{i}": {"values": values} for i in range(20)}}
    output = report(device)
    assert output["data_points"]["truncated"] is True
    assert len(json.dumps(output)) < support_report.MAX_DEFINITION_BYTES + 16000


def test_redaction_happens_before_long_fields_are_truncated():
    device = copy.deepcopy(DEVICE)
    device["name"] = "private-long-name" * 100
    device["product_name"] = "Product for " + device["name"]
    output = report(device)
    assert "private-long-name" not in json.dumps(output)
    assert output["device"]["product_name"] == "Product for [redacted]"
