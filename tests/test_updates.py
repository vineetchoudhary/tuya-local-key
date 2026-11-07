from datetime import datetime, timezone
import json
import threading

import pytest

import updates

NOW = 1_800_000_000


def release(version="v3.6", age=7200, **fields):
    return {"tag_name": version, "draft": False, "prerelease": False,
            "published_at": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(), **fields}


@pytest.fixture
def clock(monkeypatch):
    value = [NOW]
    monkeypatch.setattr(updates.time, "time", lambda: value[0])
    monkeypatch.setattr(updates.time, "monotonic", lambda: value[0])
    return value


@pytest.mark.parametrize("current,newest,expected", [
    ("3.5.0", "v3.5", False), ("3.5", "v3.5.0", False), ("3.5.0", "v3.5.1", True),
    ("3.5.0", "v3.6", True), ("3.10.0", "v3.9", False), ("3.9.0", "v3.10", True),
    ("4.0.0", "v3.6", False), ("3.6.0rc1", "v3.6", False), ("unavailable", "v3.6", False),
])
def test_only_numerically_newer_stable_versions_offer_an_update(monkeypatch, clock, current, newest, expected):
    monkeypatch.setattr(updates, "_get_json", lambda *args: release(newest))
    result = updates.UpdateChecker().status(current)
    assert result["available"] is expected
    assert result["update_url"] == (f"{updates.REPOSITORY}/releases/tag/{newest}" if expected else None)


@pytest.mark.parametrize("fields", [
    {"draft": True}, {"prerelease": True}, {"tag_name": "v3.6-beta.1"},
    {"tag_name": "javascript:alert(1)"}, {"tag_name": "v3.6/../../bad"}, {"tag_name": None},
    {"published_at": None}, {"published_at": "2026-11-07T13:00:00"},
    {"published_at": "not a timestamp"}, {"draft": "false"},
])
def test_unusable_release_data_never_shows_a_badge(monkeypatch, clock, fields):
    monkeypatch.setattr(updates, "_get_json", lambda *args: release(**fields))
    result = updates.UpdateChecker().status("3.5.0")
    assert result["available"] is False and result["status"] == "unavailable"
    assert result["update_url"] is None


@pytest.mark.parametrize("age,available,retry", [(3599, False, 86400), (3600, True, 86400), (3601, True, 86400)])
def test_home_assistant_waits_a_full_hour_from_publication(monkeypatch, clock, age, available, retry):
    monkeypatch.setattr(updates, "_get_json", lambda *args: release(age=age))
    result = updates.UpdateChecker().status("3.5.0", home_assistant=True)
    assert result["available"] is available
    assert result["check_after_seconds"] == retry
    assert result["update_url"] == ("https://my.home-assistant.io/redirect/updates/" if available else None)


def test_standalone_shows_a_new_release_immediately_and_ignores_untrusted_release_links(monkeypatch, clock):
    monkeypatch.setattr(updates, "_get_json", lambda *args: release(age=0, html_url="https://attacker.test/update"))
    result = updates.UpdateChecker().status("3.5.0")
    assert result["available"] is True
    assert result["update_url"] == f"{updates.REPOSITORY}/releases/tag/v3.6"


def test_cached_young_release_becomes_eligible_without_another_github_request(monkeypatch, clock):
    calls = []

    def get(url, headers):
        calls.append(url)
        return release(age=3500)

    monkeypatch.setattr(updates, "_get_json", get)
    checker = updates.UpdateChecker()
    assert checker.status("3.5.0", home_assistant=True)["available"] is False
    clock[0] += 100
    assert checker.status("3.5.0", home_assistant=True)["available"] is True
    assert calls == [updates.RELEASE_API]


def test_home_assistant_links_to_its_own_app_and_keeps_the_token_private(monkeypatch, clock):
    calls = []

    def get(url, headers):
        calls.append((url, headers))
        return release() if url == updates.RELEASE_API else {"result": "ok", "data": {"slug": "a1b2c3d4_tuya_local_key"}}

    monkeypatch.setattr(updates, "_get_json", get)
    checker = updates.UpdateChecker()
    result = checker.status("3.5.0", home_assistant=True, supervisor_token="private-supervisor-token")
    assert result["update_url"] == "https://my.home-assistant.io/redirect/supervisor_addon/?addon=a1b2c3d4_tuya_local_key"
    assert "private-supervisor-token" not in json.dumps(result)
    assert "Authorization" not in calls[0][1]
    assert calls[1][1] == {"Authorization": "Bearer private-supervisor-token"}
    checker.status("3.5.0", home_assistant=True, supervisor_token="private-supervisor-token")
    assert len(calls) == 2


@pytest.mark.parametrize("info", [None, {"result": "ok", "data": {"slug": "../../bad"}},
                                       {"result": "error"}, {"result": "ok", "data": {"slug": "x?token=secret"}}])
def test_unknown_home_assistant_slug_falls_back_to_its_updates_page(monkeypatch, clock, info):
    def get(url, headers):
        if url == updates.RELEASE_API:
            return release()
        if info is None:
            raise OSError("private token")
        return info

    monkeypatch.setattr(updates, "_get_json", get)
    result = updates.UpdateChecker().status("3.5.0", home_assistant=True, supervisor_token="private-token")
    assert result["available"] is True
    assert result["update_url"] == "https://my.home-assistant.io/redirect/updates/"


def test_release_requests_are_cached_and_outages_retry_without_raw_errors(monkeypatch, clock):
    calls = []

    def get(*args):
        calls.append(args)
        raise OSError("private account and token")

    monkeypatch.setattr(updates, "_get_json", get)
    checker = updates.UpdateChecker()
    first = checker.status("3.5.0")
    assert first["status"] == "unavailable" and first["available"] is False
    assert "private" not in json.dumps(first)
    checker.status("3.5.0")
    assert len(calls) == 1
    clock[0] += updates.ERROR_RETRY
    checker.status("3.5.0")
    assert len(calls) == 2


def test_cached_release_expires_after_an_extended_outage(monkeypatch, clock):
    monkeypatch.setattr(updates, "_get_json", lambda *args: release())
    checker = updates.UpdateChecker()
    assert checker.status("3.5.0")["available"] is True

    def fail(*args):
        raise TimeoutError()

    monkeypatch.setattr(updates, "_get_json", fail)
    clock[0] += updates.CHECK_INTERVAL
    assert checker.status("3.5.0")["available"] is True
    clock[0] += updates.MAX_STALE
    result = checker.status("3.5.0")
    assert result["available"] is False and result["status"] == "unavailable"


def test_automatic_checks_remain_a_full_day_apart(monkeypatch, clock):
    calls = []
    monkeypatch.setattr(updates, "_get_json", lambda *args: calls.append(args) or release())
    checker = updates.UpdateChecker()
    assert checker.status("3.5.0")["checked_now"] is True
    clock[0] += 24 * 3600 - 1
    assert checker.status("3.5.0")["checked_now"] is False
    assert len(calls) == 1
    clock[0] += 1
    assert checker.status("3.5.0")["checked_now"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("fails", [False, True])
def test_automatic_cooldown_survives_restart_even_after_a_failed_check(monkeypatch, clock, tmp_path, fails):
    calls = []

    def get(*args):
        calls.append(args)
        if fails:
            raise OSError("offline")
        return release()

    monkeypatch.setattr(updates, "_get_json", get)
    path = tmp_path / "update-check.json"
    updates.UpdateChecker(path).status("3.5.0")
    saved = json.loads(path.read_text())
    assert saved["attempted_at"] == NOW
    clock[0] += 86400 - 1
    restarted = updates.UpdateChecker(path)
    assert restarted.status("3.5.0")["checked_now"] is False
    assert len(calls) == 1
    clock[0] += 1
    assert restarted.status("3.5.0")["checked_now"] is True
    assert len(calls) == 2


def test_manual_check_bypasses_the_cooldown_and_restarts_the_daily_window(monkeypatch, clock):
    calls = []
    monkeypatch.setattr(updates, "_get_json", lambda *args: calls.append(args) or release("v3.7" if len(calls) > 1 else "v3.6"))
    checker = updates.UpdateChecker()
    assert checker.status("3.5.0")["latest_version"] == "3.6"
    clock[0] += 60
    manual = checker.status("3.5.0", manual=True)
    assert manual["latest_version"] == "3.7" and manual["checked_now"] is True
    assert manual["last_check_succeeded"] is True
    assert len(calls) == 2
    clock[0] += 86399
    assert checker.status("3.5.0")["checked_now"] is False
    assert len(calls) == 2
    clock[0] += 1
    assert checker.status("3.5.0")["checked_now"] is True


def test_manual_failure_is_distinguished_from_a_cached_success(monkeypatch, clock):
    checker = updates.UpdateChecker()
    monkeypatch.setattr(updates, "_get_json", lambda *args: release())
    checker.status("3.5.0")

    def fail(*args):
        raise OSError("offline")

    monkeypatch.setattr(updates, "_get_json", fail)
    result = checker.status("3.5.0", manual=True)
    assert result["available"] is True, "a known update is still useful during a brief outage"
    assert result["last_check_succeeded"] is False


def test_persistent_update_state_contains_no_supervisor_credentials(monkeypatch, clock, tmp_path):
    def get(url, headers):
        return release() if url == updates.RELEASE_API else {"result": "ok", "data": {"slug": "private-instance-slug"}}

    monkeypatch.setattr(updates, "_get_json", get)
    path = tmp_path / "update-check.json"
    updates.UpdateChecker(path).status("3.5.0", home_assistant=True, supervisor_token="private-supervisor-token")
    saved = path.read_text()
    assert "private-supervisor-token" not in saved and "private-instance-slug" not in saved
    assert "releases/tag" not in saved, "release URLs are reconstructed from validated metadata"


def test_simultaneous_clients_share_one_github_check(monkeypatch, clock):
    calls, results = [], []
    entered, finish = threading.Event(), threading.Event()

    def get(*args):
        calls.append(args)
        entered.set()
        assert finish.wait(2)
        return release()

    monkeypatch.setattr(updates, "_get_json", get)
    checker = updates.UpdateChecker()
    threads = [threading.Thread(target=lambda: results.append(checker.status("3.5.0"))) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        assert entered.wait(2)
    finally:
        finish.set()
        for thread in threads:
            thread.join(2)
    assert len(results) == 3 and all(result["available"] for result in results)
    assert len(calls) == 1


def test_http_requests_are_bounded_and_supervisor_credentials_cannot_be_redirected(monkeypatch):
    calls = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            calls.append(limit)
            return b'{}'

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return Response()

    def opener(*handlers):
        calls.extend(handlers)
        return Opener()

    monkeypatch.setattr(updates, "build_opener", opener)
    assert updates._get_json(updates.SUPERVISOR_API, {"Authorization": "Bearer secret"}) == {}
    assert any(isinstance(handler, updates.ProxyHandler) and handler.proxies == {} for handler in calls)
    no_redirect = next(handler for handler in calls if isinstance(handler, updates._NoRedirect))
    assert no_redirect.redirect_request(None, None, 302, None, None, "https://other.test") is None
    assert (updates.SUPERVISOR_API, updates.REQUEST_TIMEOUT) in calls
    assert updates.MAX_RESPONSE_BYTES + 1 in calls
