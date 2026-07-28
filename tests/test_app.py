import importlib
import os
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
    response = webapp.app.test_client().get("/api/devices")

    assert response.status_code == 401
    assert response.json == {"error": "not_logged_in"}


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
