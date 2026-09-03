import importlib
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def webapp(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "session.json"))
    import app

    app = importlib.reload(app)
    app.app.config.update(TESTING=True)
    with app._lock:
        app._pending.clear()
    with app._devices_cache_lock:
        app._devices_cache = None
    return app


def test_api_responses_are_not_cached(webapp):
    response = webapp.app.test_client().get("/api/state")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_app_icon_serves_home_assistant_icon(webapp):
    response = webapp.app.test_client().get("/icon.png")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == (ROOT / "tuya_local_key" / "icon.png").read_bytes()


def test_app_icon_uses_ingress_relative_urls(webapp):
    response = webapp.app.test_client().get("/")

    assert response.status_code == 200
    assert 'href="icon.png"' in response.text
    assert 'src="icon.png"' in response.text
    assert 'href="/icon.png"' not in response.text
    assert 'src="/icon.png"' not in response.text


def test_qr_scheme_defaults_to_smartlife(tmp_path, monkeypatch):
    monkeypatch.delenv("QR_SCHEME", raising=False)
    monkeypatch.setenv("HASS_OPTIONS_FILE", str(tmp_path / "missing-options.json"))

    import app

    app = importlib.reload(app)

    assert app.QR_SCHEME == "smartlife"


def test_home_assistant_options_override_qr_scheme_environment(tmp_path, monkeypatch):
    options_file = tmp_path / "options.json"
    options_file.write_text('{"QR_SCHEME": "tuyaSmart"}')
    monkeypatch.setenv("HASS_OPTIONS_FILE", str(options_file))
    monkeypatch.setenv("QR_SCHEME", "smartlife")

    import app

    app = importlib.reload(app)

    assert app.QR_SCHEME == "tuyaSmart"


def _reload_app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.setenv("HASS_OPTIONS_FILE", str(tmp_path / "missing.json"))
    monkeypatch.delenv("AUTH_USERNAME", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app

    return importlib.reload(app)


def test_auth_off_unless_both_username_and_password_set(tmp_path, monkeypatch):
    for env in ({}, {"AUTH_USERNAME": "admin"}, {"AUTH_PASSWORD": "secret"}):
        app = _reload_app(monkeypatch, tmp_path, **env)
        assert app.app.test_client().get("/api/state").status_code == 200, env


def test_auth_required_when_both_set(tmp_path, monkeypatch):
    app = _reload_app(monkeypatch, tmp_path, AUTH_USERNAME="admin", AUTH_PASSWORD="secret")
    client = app.app.test_client()

    unauth = client.get("/api/state")
    assert unauth.status_code == 401
    assert unauth.headers["WWW-Authenticate"].startswith("Basic")

    assert client.get("/api/state", auth=("admin", "wrong")).status_code == 401
    assert client.get("/api/state", auth=("nope", "secret")).status_code == 401

    ok = client.get("/api/state", auth=("admin", "secret"))
    assert ok.status_code == 200
    assert ok.json == {"logged_in": False}


def test_auth_skipped_for_home_assistant_ingress(tmp_path, monkeypatch):
    app = _reload_app(monkeypatch, tmp_path, AUTH_USERNAME="admin", AUTH_PASSWORD="secret")
    client = app.app.test_client()

    # Ingress requests carry Supervisor's X-Ingress-Path and no Basic Auth credentials
    ingress = client.get(
        "/api/state", headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"}
    )
    assert ingress.status_code == 200
    assert ingress.json == {"logged_in": False}

    # Direct-port access (no ingress header) still requires credentials.
    assert client.get("/api/state").status_code == 401


def test_auth_reads_home_assistant_options(tmp_path, monkeypatch):
    options = tmp_path / "options.json"
    options.write_text('{"AUTH_USERNAME": "ha", "AUTH_PASSWORD": "ingress-pw"}')
    app = _reload_app(monkeypatch, tmp_path, HASS_OPTIONS_FILE=str(options))
    client = app.app.test_client()

    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", auth=("ha", "ingress-pw")).status_code == 200


def test_login_start_validates_user_code(webapp):
    response = webapp.app.test_client().post("/api/login/start", json={"user_code": ""})

    assert response.status_code == 400
    assert response.json["error"] == "A user code is required."


def test_login_start_stores_pending_token_and_returns_qr(webapp, monkeypatch):
    monkeypatch.setattr(webapp.core, "mint_qr_token", lambda user_code: "demo-token")
    monkeypatch.setattr(webapp.core, "qr_png_bytes", lambda content: b"png-bytes")

    response = webapp.app.test_client().post(
        "/api/login/start", json={"user_code": "user-code"}
    )

    assert response.status_code == 200
    assert response.json["token"] == "demo-token"
    assert response.json["qr"].startswith("data:image/png;base64,")
    with webapp._lock:
        assert webapp._pending["demo-token"]["user_code"] == "user-code"


def test_login_start_returns_tuya_errors(webapp, monkeypatch):
    monkeypatch.setattr(
        webapp.core,
        "mint_qr_token",
        lambda user_code: (_ for _ in ()).throw(webapp.core.LoginError("bad code")),
    )

    response = webapp.app.test_client().post(
        "/api/login/start", json={"user_code": "bad-user"}
    )

    assert response.status_code == 400
    assert response.json == {"error": "bad code"}

    monkeypatch.setattr(
        webapp.core,
        "mint_qr_token",
        lambda user_code: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    response = webapp.app.test_client().post(
        "/api/login/start", json={"user_code": "user-code"}
    )

    assert response.status_code == 502
    assert response.json == {"error": "Could not reach Tuya: offline"}


def test_login_poll_uses_post_body_not_query_string(webapp):
    client = webapp.app.test_client()

    assert client.get("/api/login/poll?token=demo-token").status_code == 405
    assert client.post("/api/login/poll", json={"token": "demo-token"}).status_code == 404


def test_login_poll_expires_old_pending_token(webapp):
    with webapp._lock:
        webapp._pending["old-token"] = {
            "user_code": "user-code",
            "created_at": time.time() - webapp.PENDING_LOGIN_TTL_SECONDS - 1,
        }

    response = webapp.app.test_client().post(
        "/api/login/poll", json={"token": "old-token"}
    )

    assert response.status_code == 404
    with webapp._lock:
        assert "old-token" not in webapp._pending


def test_login_poll_returns_pending_for_unconfirmed_login(webapp, monkeypatch):
    monkeypatch.setattr(webapp.core, "poll_login", lambda token, user_code: None)
    with webapp._lock:
        webapp._pending["demo-token"] = {
            "user_code": "user-code",
            "created_at": time.time(),
        }

    response = webapp.app.test_client().post(
        "/api/login/poll", json={"token": "demo-token"}
    )

    assert response.status_code == 200
    assert response.json == {"status": "pending"}


def test_login_poll_saves_confirmed_session(webapp, monkeypatch):
    session = {
        "user_code": "user-code",
        "terminal_id": "terminal",
        "endpoint": "endpoint",
        "token_info": {"access_token": "token"},
    }
    monkeypatch.setattr(webapp.core, "poll_login", lambda token, user_code: session)

    with webapp._lock:
        webapp._pending["demo-token"] = {
            "user_code": "user-code",
            "created_at": time.time(),
        }

    response = webapp.app.test_client().post(
        "/api/login/poll", json={"token": "demo-token"}
    )

    assert response.status_code == 200
    assert response.json == {"status": "confirmed"}
    assert webapp.core.load_session(os.environ["SESSION_FILE"]) == session
    with webapp._lock:
        assert "demo-token" not in webapp._pending


def test_login_poll_returns_json_when_session_save_fails(webapp, monkeypatch):
    session = {
        "user_code": "user-code",
        "terminal_id": "terminal",
        "endpoint": "endpoint",
        "token_info": {"access_token": "token"},
    }
    monkeypatch.setattr(webapp.core, "poll_login", lambda token, user_code: session)

    def fail_save(path, data):
        raise PermissionError("cannot write session")

    monkeypatch.setattr(webapp.core, "save_session", fail_save)
    with webapp._lock:
        webapp._pending["demo-token"] = {
            "user_code": "user-code",
            "created_at": time.time(),
        }

    response = webapp.app.test_client().post(
        "/api/login/poll", json={"token": "demo-token"}
    )

    assert response.status_code == 500
    assert response.content_type == "application/json"
    assert response.json["error"].startswith("session_save_failed:")


def test_devices_requires_session(webapp):
    response = webapp.app.test_client().get("/api/devices?refresh=1")

    assert response.status_code == 401
    assert response.json == {"error": "not_logged_in"}


def test_devices_returns_fetch_failed_for_transient_error(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})

    def fail_devices_from_session(session, session_file):
        raise RuntimeError("offline")

    monkeypatch.setattr(webapp.core, "devices_from_session", fail_devices_from_session)

    response = webapp.app.test_client().get("/api/devices")

    assert response.status_code == 502
    assert response.json == {"error": "fetch_failed"}


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("1002", "access_token is null"),
        ("1010", "token is expired"),
        ("1011", "token invalid"),
        ("1012", "token status is invalid"),
        ("1400", "token invalid"),
        ("2029", "session status is invalid"),
        ("-9999999", "sign invalid"),
    ],
)
def test_session_invalid_error_classifier_recognizes_relogin_errors(webapp, code, message):
    error = SimpleNamespace(error_code=code, error_message=message)

    assert webapp._is_session_invalid_error(error) is True


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("500", "system error, please contact the admin"),
        ("1004", "sign invalid"),
        ("1013", "request time is invalid"),
        ("1106", "permission deny"),
        ("1110", "concurrent request over limit"),
        ("1199", "your requests are too frequent"),
        ("2001", "device is offline"),
        ("2010", "device not exist"),
    ],
)
def test_session_invalid_error_classifier_ignores_retry_or_config_errors(webapp, code, message):
    error = SimpleNamespace(error_code=code, error_message=message)

    assert webapp._is_session_invalid_error(error) is False


class TokenExpiredError(Exception):
    error_code = "-9999999"
    error_message = "sign invalid"


def test_devices_returns_session_invalid_when_there_is_no_snapshot(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})

    def fail_devices_from_session(session, session_file):
        raise TokenExpiredError()

    monkeypatch.setattr(webapp.core, "devices_from_session", fail_devices_from_session)

    response = webapp.app.test_client().get("/api/devices?refresh=1")

    assert response.status_code == 401
    assert response.json == {"error": "session_invalid"}
    assert webapp._devices_cache is None


def test_expired_session_still_serves_the_snapshot(webapp, monkeypatch):
    """Local keys outlive the login, so an expired session shows the saved list."""
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    with webapp._devices_cache_lock:
        webapp._devices_cache = {
            "body": {"devices": [{"name": "Old Device"}]},
            "cached_at": time.time(),
            "session_key": webapp._session_cache_key({"token_info": {}}),
        }

    def fail_devices_from_session(session, session_file):
        raise TokenExpiredError()

    monkeypatch.setattr(webapp.core, "devices_from_session", fail_devices_from_session)

    response = webapp.app.test_client().get("/api/devices?refresh=1")

    assert response.status_code == 200
    assert response.json["devices"] == [{"name": "Old Device"}]
    assert response.json["stale"] is True
    assert response.json["stale_reason"] == "session_invalid"
    assert response.json["refresh_failed"] is True
    assert webapp._devices_cache is not None, "the saved keys are still good"


def test_devices_response_keeps_field_order(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    monkeypatch.setattr(
        webapp.core,
        "devices_from_session",
        lambda session, session_file: [SimpleNamespace(
            name="Kitchen Plug", id="device-1", local_key="key", online=True,
            update_time=1_752_000_000, status={"switch_1": True},
        )],
    )

    response = webapp.app.test_client().get("/api/devices")

    # jsonify sorts keys by default; the UI and CSV export rely on web_dict()'s order.
    assert list(response.json["devices"][0]) == [
        "name", "id", "local_key", "online", "update_time", "status", "epochs",
    ]


def test_devices_ignores_cache_for_different_session(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    with webapp._devices_cache_lock:
        webapp._devices_cache = {
            "body": {"devices": [{"name": "Old Device"}]},
            "cached_at": time.time(),
            "session_key": ("different-session",),
        }
    monkeypatch.setattr(
        webapp.core,
        "devices_from_session",
        lambda session, session_file: [SimpleNamespace(name="Fresh Device")],
    )
    monkeypatch.setattr(webapp.core, "web_dict", lambda device: {"name": device.name})

    response = webapp.app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert response.json["devices"] == [{"name": "Fresh Device"}]


def test_devices_fetch_runs_outside_cache_lock(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})

    def fake_devices_from_session(session, session_file):
        assert webapp._devices_cache_lock.acquire(blocking=False)
        webapp._devices_cache_lock.release()
        return [SimpleNamespace(name="Fresh Device")]

    monkeypatch.setattr(webapp.core, "devices_from_session", fake_devices_from_session)
    monkeypatch.setattr(webapp.core, "web_dict", lambda device: {"name": device.name})

    response = webapp.app.test_client().get("/api/devices")

    assert response.status_code == 200


def test_concurrent_device_fetches_are_single_flighted(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    calls = []
    in_fetch = threading.Event()
    release = threading.Event()

    def blocking_fetch(session, session_file):
        calls.append(session_file)
        in_fetch.set()
        release.wait(2)
        return [SimpleNamespace(name="Fresh Device")]

    monkeypatch.setattr(webapp.core, "devices_from_session", blocking_fetch)
    monkeypatch.setattr(webapp.core, "web_dict", lambda device: {"name": device.name})

    results = {}

    def call(key):
        resp = webapp.app.test_client().get("/api/devices?refresh=1")
        results[key] = (resp.status_code, resp.get_json())

    first = threading.Thread(target=call, args=("first",))
    first.start()
    assert in_fetch.wait(2)
    second = threading.Thread(target=call, args=("second",))
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(2)
    second.join(2)

    assert len(calls) == 1
    assert results["first"][0] == 200
    assert results["second"][0] == 200
    assert results["first"][1]["devices"] == [{"name": "Fresh Device"}]
    assert results["second"][1]["devices"] == [{"name": "Fresh Device"}]


def test_devices_response_is_cached_until_refresh(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    calls = []

    def fake_devices_from_session(session, session_file):
        calls.append(session_file)
        return [SimpleNamespace(name=f"Device {len(calls)}")]

    monkeypatch.setattr(webapp.core, "devices_from_session", fake_devices_from_session)
    monkeypatch.setattr(webapp.core, "web_dict", lambda device: {"name": device.name})
    client = webapp.app.test_client()

    first = client.get("/api/devices")
    second = client.get("/api/devices")
    refreshed = client.get("/api/devices?refresh=1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert refreshed.status_code == 200
    assert first.json["devices"] == [{"name": "Device 1"}]
    assert second.json["devices"] == [{"name": "Device 1"}]
    assert refreshed.json["devices"] == [{"name": "Device 2"}]
    assert len(calls) == 2


def test_devices_refresh_failure_returns_stale_cache(webapp, monkeypatch):
    session = {"token_info": {}}
    webapp.core.save_session(os.environ["SESSION_FILE"], session)
    cached_body = {
        "devices": [{"name": "Cached Device"}],
        "cached_at": 1_000.0,
        "cache_expires_at": 1_000.0 + webapp.DEVICE_CACHE_TTL_SECONDS,
    }
    with webapp._devices_cache_lock:
        webapp._devices_cache = {
            "body": cached_body,
            "cached_at": 1_000.0,
            "session_key": webapp._session_cache_key(session),
        }

    def fail_devices_from_session(session, session_file):
        raise RuntimeError("offline")

    monkeypatch.setattr(webapp.core, "devices_from_session", fail_devices_from_session)

    response = webapp.app.test_client().get("/api/devices?refresh=1")

    assert response.status_code == 200
    assert response.json == {
        **cached_body, "stale": True, "stale_reason": "fetch_failed", "refresh_failed": True,
    }
    assert webapp._devices_cache["body"] == cached_body


def test_devices_uses_fetch_completion_time_for_cache(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    # Request start, then fetch completion; anything after (the cache write
    # stamps its own token) keeps the last reading.
    times = [1_000.0, 1_005.0]
    monkeypatch.setattr(webapp.time, "time", lambda: times.pop(0) if len(times) > 1 else times[0])
    monkeypatch.setattr(
        webapp.core,
        "devices_from_session",
        lambda session, session_file: [SimpleNamespace(name="Fresh Device")],
    )
    monkeypatch.setattr(webapp.core, "web_dict", lambda device: {"name": device.name})

    response = webapp.app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert response.json["cached_at"] == 1_005.0


def test_devices_cache_expires_after_24_hours(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    current_time = [1_000.0]
    calls = []

    def fake_devices_from_session(session, session_file):
        calls.append(session_file)
        return [SimpleNamespace(name=f"Device {len(calls)}")]

    monkeypatch.setattr(webapp.time, "time", lambda: current_time[0])
    monkeypatch.setattr(webapp.core, "devices_from_session", fake_devices_from_session)
    monkeypatch.setattr(webapp.core, "web_dict", lambda device: {"name": device.name})
    client = webapp.app.test_client()

    first = client.get("/api/devices")
    current_time[0] += (24 * 60 * 60) - 1
    cached = client.get("/api/devices")
    current_time[0] += 2
    expired = client.get("/api/devices")

    assert first.json["devices"] == [{"name": "Device 1"}]
    assert cached.json["devices"] == [{"name": "Device 1"}]
    assert expired.json["devices"] == [{"name": "Device 2"}]
    assert len(calls) == 2


def test_logout_clears_session_and_pending_logins(webapp):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    with webapp._lock:
        webapp._pending["demo-token"] = {
            "user_code": "user-code",
            "created_at": time.time(),
        }
    with webapp._devices_cache_lock:
        webapp._devices_cache = {
            "body": {"devices": []},
            "cached_at": time.time(),
            "session_key": ("demo",),
        }

    response = webapp.app.test_client().post("/api/logout")

    assert response.status_code == 200
    assert response.json == {"ok": True}
    assert not os.path.exists(os.environ["SESSION_FILE"])
    assert webapp._pending == {}
    assert webapp._devices_cache is None


def test_logout_ok_when_session_file_is_missing(webapp):
    response = webapp.app.test_client().post("/api/logout")

    assert response.status_code == 200
    assert response.json == {"ok": True}


# --------------------------------------------------------------------------- #
# The device list on disk (see device_cache)
# --------------------------------------------------------------------------- #
def _serve_devices(webapp, monkeypatch, calls, name="Kitchen Plug", key="s3cret-key"):
    """Log the fixture in and count how often the list is fetched from Tuya."""
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})

    def fake_devices_from_session(session, session_file):
        calls.append(session_file)
        return [SimpleNamespace(name=name, local_key=key)]

    monkeypatch.setattr(webapp.core, "devices_from_session", fake_devices_from_session)
    monkeypatch.setattr(
        webapp.core, "web_dict", lambda d: {"name": d.name, "local_key": d.local_key}
    )


def _restart(webapp):
    """Reload the module, so only what reached disk survives."""
    restarted = importlib.reload(webapp)
    restarted.app.config.update(TESTING=True)
    return restarted


def test_devices_survive_a_restart(webapp, monkeypatch):
    calls = []
    _serve_devices(webapp, monkeypatch, calls)
    first = webapp.app.test_client().get("/api/devices")

    second = _restart(webapp).app.test_client().get("/api/devices")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json == first.json
    assert len(calls) == 1, "the stored list should serve the restart"


def test_stored_devices_are_encrypted_at_rest(webapp, monkeypatch):
    _serve_devices(webapp, monkeypatch, [])
    webapp.app.test_client().get("/api/devices")

    blob = Path(webapp.DEVICE_CACHE_FILE).read_bytes()

    assert b"s3cret-key" not in blob
    assert b"Kitchen Plug" not in blob


def test_stored_devices_are_ignored_after_switching_accounts(webapp, monkeypatch):
    calls = []
    _serve_devices(webapp, monkeypatch, calls)
    webapp.app.test_client().get("/api/devices")
    assert os.path.exists(webapp.DEVICE_CACHE_FILE)

    webapp.core.save_session(
        os.environ["SESSION_FILE"], {"user_code": "somebody-else", "token_info": {}}
    )
    response = _restart(webapp).app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert len(calls) == 2, "another account's list must not be served"


def test_unreadable_stored_devices_fall_back_to_a_fetch(webapp, monkeypatch):
    calls = []
    _serve_devices(webapp, monkeypatch, calls)
    webapp.app.test_client().get("/api/devices")
    assert os.path.exists(webapp.DEVICE_CACHE_FILE)
    Path(webapp.DEVICE_CACHE_FILE).write_bytes(b"not a fernet token")

    response = _restart(webapp).app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert response.json["devices"] == [{"name": "Kitchen Plug", "local_key": "s3cret-key"}]
    assert len(calls) == 2


def test_stored_devices_expire_after_24_hours(webapp, monkeypatch):
    calls = []
    now = [1_000.0]
    monkeypatch.setattr(webapp.time, "time", lambda: now[0])
    _serve_devices(webapp, monkeypatch, calls)
    webapp.app.test_client().get("/api/devices")
    assert os.path.exists(webapp.DEVICE_CACHE_FILE)

    now[0] += webapp.DEVICE_CACHE_TTL_SECONDS + 1
    restarted = _restart(webapp)
    monkeypatch.setattr(restarted.time, "time", lambda: now[0])
    response = restarted.app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert len(calls) == 2, "a stored list past its TTL must be refetched"


def test_logout_removes_the_stored_devices_and_their_key(webapp, monkeypatch):
    _serve_devices(webapp, monkeypatch, [])
    webapp.app.test_client().get("/api/devices")
    assert os.path.exists(webapp.DEVICE_CACHE_FILE)

    response = webapp.app.test_client().post("/api/logout")

    assert response.status_code == 200
    assert not os.path.exists(webapp.DEVICE_CACHE_FILE)
    assert not os.path.exists(webapp.DEVICE_CACHE_KEY_FILE)


def test_confirmed_login_removes_the_previous_stored_devices(webapp, monkeypatch):
    _serve_devices(webapp, monkeypatch, [])
    webapp.app.test_client().get("/api/devices")
    assert os.path.exists(webapp.DEVICE_CACHE_FILE)

    monkeypatch.setattr(
        webapp.core, "poll_login",
        lambda token, user_code: {"user_code": user_code, "token_info": {}},
    )
    with webapp._lock:
        webapp._pending["demo-token"] = {
            "user_code": "user-code",
            "created_at": time.time(),
        }

    response = webapp.app.test_client().post(
        "/api/login/poll", json={"token": "demo-token"}
    )

    assert response.json == {"status": "confirmed"}
    assert not os.path.exists(webapp.DEVICE_CACHE_FILE)
    assert not os.path.exists(webapp.DEVICE_CACHE_KEY_FILE)


def test_device_cache_off_keeps_the_list_in_memory_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.setenv("HASS_OPTIONS_FILE", str(tmp_path / "missing-options.json"))
    monkeypatch.setenv("DEVICE_CACHE", "off")
    import app

    app = importlib.reload(app)
    app.app.config.update(TESTING=True)
    calls = []
    _serve_devices(app, monkeypatch, calls)

    client = app.app.test_client()
    first = client.get("/api/devices")
    second = client.get("/api/devices")

    assert first.status_code == 200
    assert second.json == first.json
    assert len(calls) == 1, "the in-memory cache still applies"
    assert not os.path.exists(app.DEVICE_CACHE_FILE)
    assert not os.path.exists(app.DEVICE_CACHE_KEY_FILE)


def test_turning_the_device_cache_off_removes_an_existing_one(webapp, monkeypatch, tmp_path):
    _serve_devices(webapp, monkeypatch, [])
    webapp.app.test_client().get("/api/devices")
    assert os.path.exists(webapp.DEVICE_CACHE_FILE)

    monkeypatch.setenv("DEVICE_CACHE", "off")
    restarted = _restart(webapp)
    _serve_devices(restarted, monkeypatch, [])
    response = restarted.app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert not os.path.exists(restarted.DEVICE_CACHE_FILE)
    assert not os.path.exists(restarted.DEVICE_CACHE_KEY_FILE)


def test_device_cache_paths_default_beside_the_session(webapp, tmp_path):
    assert webapp.DEVICE_CACHE_FILE == str(tmp_path / "devices.cache")
    assert webapp.DEVICE_CACHE_KEY_FILE == str(tmp_path / "cache.key")


def test_session_cache_key_does_not_leak_the_user_code(webapp):
    key = webapp._session_cache_key({"user_code": "abcdef123456", "token_info": {}})

    assert "abcdef123456" not in key
    assert key != webapp._session_cache_key({"user_code": "other", "token_info": {}})


# --------------------------------------------------------------------------- #
# Change detection and the offline snapshot
# --------------------------------------------------------------------------- #
def _serve_changing_devices(webapp, monkeypatch, rounds):
    """Return a different device list on each fetch, from `rounds`."""
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    remaining = list(rounds)

    def fake_devices_from_session(session, session_file):
        return [SimpleNamespace(**d) for d in remaining.pop(0)]

    monkeypatch.setattr(webapp.core, "devices_from_session", fake_devices_from_session)
    monkeypatch.setattr(
        webapp.core, "web_dict",
        lambda d: {"name": d.name, "id": d.id, "local_key": d.local_key},
    )


def test_first_fetch_reports_no_changes(webapp, monkeypatch):
    _serve_changing_devices(webapp, monkeypatch, [
        [{"id": "a", "name": "Plug", "local_key": "k1"}],
    ])

    response = webapp.app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert "changes" not in response.json, "nothing to compare a first list against"


def test_refresh_reports_a_rotated_local_key(webapp, monkeypatch):
    _serve_changing_devices(webapp, monkeypatch, [
        [{"id": "a", "name": "Kitchen Plug", "local_key": "k1"}],
        [{"id": "a", "name": "Kitchen Plug", "local_key": "ROTATED"}],
    ])
    client = webapp.app.test_client()
    client.get("/api/devices")

    response = client.get("/api/devices?refresh=1")

    assert response.json["changes"]["key_changed"] == [
        {"id": "a", "name": "Kitchen Plug"}
    ]
    assert "ROTATED" not in json.dumps(response.json["changes"])


def test_refresh_reports_added_removed_and_renamed(webapp, monkeypatch):
    _serve_changing_devices(webapp, monkeypatch, [
        [{"id": "a", "name": "Lamp", "local_key": "k1"},
         {"id": "b", "name": "Gone", "local_key": "k2"}],
        [{"id": "a", "name": "Bedroom Lamp", "local_key": "k1"},
         {"id": "c", "name": "New Sensor", "local_key": "k3"}],
    ])
    client = webapp.app.test_client()
    client.get("/api/devices")

    changes = client.get("/api/devices?refresh=1").json["changes"]

    assert changes["added"] == [{"id": "c", "name": "New Sensor"}]
    assert changes["removed"] == [{"id": "b", "name": "Gone"}]
    assert changes["renamed"] == [{"id": "a", "name": "Bedroom Lamp", "was": "Lamp"}]
    assert changes["key_changed"] == []


def test_an_unchanged_refresh_reports_nothing(webapp, monkeypatch):
    same = [{"id": "a", "name": "Plug", "local_key": "k1"}]
    _serve_changing_devices(webapp, monkeypatch, [same, list(same)])
    client = webapp.app.test_client()
    client.get("/api/devices")

    assert "changes" not in client.get("/api/devices?refresh=1").json


def test_changes_are_compared_against_the_stored_list_across_a_restart(webapp, monkeypatch):
    _serve_changing_devices(webapp, monkeypatch, [
        [{"id": "a", "name": "Kitchen Plug", "local_key": "k1"}],
        [{"id": "a", "name": "Kitchen Plug", "local_key": "ROTATED"}],
    ])
    webapp.app.test_client().get("/api/devices")

    restarted = _restart(webapp)
    response = restarted.app.test_client().get("/api/devices?refresh=1")

    assert response.json["changes"]["key_changed"] == [
        {"id": "a", "name": "Kitchen Plug"}
    ]


def test_changes_survive_a_reload_of_the_page(webapp, monkeypatch):
    """The notice outlives the response that discovered it, until the next refresh."""
    _serve_changing_devices(webapp, monkeypatch, [
        [{"id": "a", "name": "Kitchen Plug", "local_key": "k1"}],
        [{"id": "a", "name": "Kitchen Plug", "local_key": "ROTATED"}],
    ])
    client = webapp.app.test_client()
    client.get("/api/devices")
    client.get("/api/devices?refresh=1")

    assert client.get("/api/devices").json["changes"]["key_changed"]


def test_switching_accounts_does_not_report_every_device_as_added(webapp, monkeypatch):
    _serve_changing_devices(webapp, monkeypatch, [
        [{"id": "a", "name": "Plug", "local_key": "k1"}],
        [{"id": "z", "name": "Other Account Plug", "local_key": "k9"}],
    ])
    webapp.app.test_client().get("/api/devices")

    webapp.core.save_session(
        os.environ["SESSION_FILE"], {"user_code": "somebody-else", "token_info": {}}
    )
    response = _restart(webapp).app.test_client().get("/api/devices")

    assert response.status_code == 200
    assert "changes" not in response.json


def test_an_unreachable_tuya_serves_the_snapshot_instead_of_a_502(webapp, monkeypatch):
    calls = []
    _serve_devices(webapp, monkeypatch, calls)
    first = webapp.app.test_client().get("/api/devices")

    restarted = _restart(webapp)
    monkeypatch.setattr(
        restarted.core, "devices_from_session",
        lambda session, path: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    response = restarted.app.test_client().get("/api/devices?refresh=1")

    assert response.status_code == 200
    assert response.json["devices"] == first.json["devices"]
    assert response.json["stale"] is True
    assert response.json["stale_reason"] == "fetch_failed"


def test_an_expired_snapshot_is_still_served_when_tuya_is_unreachable(webapp, monkeypatch):
    """Past the TTL and offline: a stale list beats no list at all."""
    now = [1_000.0]
    monkeypatch.setattr(webapp.time, "time", lambda: now[0])
    _serve_devices(webapp, monkeypatch, [])
    first = webapp.app.test_client().get("/api/devices")

    now[0] += webapp.DEVICE_CACHE_TTL_SECONDS + 1
    restarted = _restart(webapp)
    monkeypatch.setattr(restarted.time, "time", lambda: now[0])
    monkeypatch.setattr(
        restarted.core, "devices_from_session",
        lambda session, path: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    response = restarted.app.test_client().get("/api/devices")  # no refresh flag

    assert response.status_code == 200
    assert response.json["devices"] == first.json["devices"]
    assert response.json["stale"] is True
    assert "refresh_failed" not in response.json, "the user didn't ask for a refresh"


def test_a_fetch_failure_without_a_snapshot_still_fails(webapp, monkeypatch):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    monkeypatch.setattr(
        webapp.core, "devices_from_session",
        lambda session, path: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    response = webapp.app.test_client().get("/api/devices")

    assert response.status_code == 502
    assert response.json == {"error": "fetch_failed"}


def test_a_served_snapshot_is_never_written_back_as_fresh(webapp, monkeypatch):
    _serve_devices(webapp, monkeypatch, [])
    webapp.app.test_client().get("/api/devices")

    restarted = _restart(webapp)
    monkeypatch.setattr(
        restarted.core, "devices_from_session",
        lambda session, path: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    restarted.app.test_client().get("/api/devices?refresh=1")

    stored = restarted.device_cache.load(
        restarted.DEVICE_CACHE_FILE, restarted.DEVICE_CACHE_KEY_FILE
    )
    assert "stale" not in stored["body"]
    assert "refresh_failed" not in stored["body"]
