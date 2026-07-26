import importlib
import os
import time

import pytest


@pytest.fixture()
def webapp(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "session.json"))
    import app

    app = importlib.reload(app)
    app.app.config.update(TESTING=True)
    with app._lock:
        app._pending.clear()
    return app


def test_api_responses_are_not_cached(webapp):
    response = webapp.app.test_client().get("/api/state")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


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


def test_logout_clears_session_and_pending_logins(webapp):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})
    with webapp._lock:
        webapp._pending["demo-token"] = {
            "user_code": "user-code",
            "created_at": time.time(),
        }

    response = webapp.app.test_client().post("/api/logout")

    assert response.status_code == 200
    assert response.json == {"ok": True}
    assert not os.path.exists(os.environ["SESSION_FILE"])
    assert webapp._pending == {}
