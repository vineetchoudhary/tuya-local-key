import importlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from html.parser import HTMLParser
from urllib.parse import urljoin

import pytest
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.test import Client
from werkzeug.wrappers import Response


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


class _FrontendDocument(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.assets = []
        self.config = ""
        self.reading_config = False
        self.inline_code = False
        self.inline_styles = False
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.inline_styles |= "style" in attrs or tag == "style"
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.assets.append(attrs["href"])
        if tag == "script":
            self.reading_config = attrs.get("id") == "app-config" and attrs.get("type") == "application/json"
            if "src" in attrs:
                assert attrs.get("type") == "module"
                self.assets.append(attrs["src"])
            elif not self.reading_config:
                self.inline_code = True

    def handle_data(self, data):
        if self.reading_config:
            self.config += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.reading_config = False


@pytest.mark.parametrize("prefix", ["", "/api/hassio_ingress/frontend-test"])
def test_frontend_asset_graph_loads_at_root_and_through_ingress(webapp, prefix):
    mounted = DispatcherMiddleware(webapp.app, {prefix: webapp.app}) if prefix else webapp.app
    client = Client(mounted, Response)
    headers = {"X-Ingress-Path": prefix} if prefix else {}
    response = client.get(prefix + "/", headers=headers)
    assert response.status_code == 200
    document = _FrontendDocument(response.text)
    assert not document.inline_code
    assert not document.inline_styles
    assert json.loads(document.config)["gatewayCategories"] == sorted(webapp.lan_scan.GATEWAY_CATEGORIES)
    assert document.assets
    assert all(not asset.startswith(("/", "http:", "https:")) for asset in document.assets)
    pending = [urljoin(prefix + "/", asset) for asset in document.assets]
    visited = set()
    while pending:
        asset = pending.pop()
        if asset in visited:
            continue
        visited.add(asset)
        assert asset.startswith(prefix + "/static/")
        loaded = client.get(asset, headers=headers)
        assert loaded.status_code == 200, asset
        assert "{{" not in loaded.text, asset
        if asset.endswith(".css"):
            assert loaded.mimetype == "text/css"
        else:
            assert loaded.mimetype in ("text/javascript", "application/javascript")
            imports = re.findall(r'''(?m)^import\s+[^;]+?\s+from\s+["']([^"']+)["']''', loaded.text)
            assert all(module.startswith(("./", "../")) for module in imports)
            pending.extend(urljoin(asset, module) for module in imports)
    assert any(asset.endswith(".css") for asset in visited)
    assert any("/shared/" in asset for asset in visited)


def test_frontend_configuration_is_serialized_as_inert_json(webapp, monkeypatch):
    categories = {"bridge", "gateway</script><script>alert(1)</script>"}
    monkeypatch.setattr(webapp.lan_scan, "GATEWAY_CATEGORIES", categories)
    response = webapp.app.test_client().get("/")
    document = _FrontendDocument(response.text)
    assert json.loads(document.config) == {"gatewayCategories": sorted(categories)}
    assert not document.inline_code
    assert "gateway</script>" not in response.text


@pytest.mark.parametrize("path", ["/", "/static/css/base.css", "/static/js/app.js", "/static/js/shared/model.js"])
def test_frontend_assets_follow_direct_and_ingress_authentication(tmp_path, monkeypatch, path):
    module = _reload_app(monkeypatch, tmp_path, AUTH_USERNAME="admin", AUTH_PASSWORD="secret")
    client = module.app.test_client()
    assert client.get(path).status_code == 401
    assert client.get(path, auth=("admin", "wrong")).status_code == 401
    assert client.get(path, auth=("admin", "secret")).status_code == 200
    assert client.get(path, headers={"X-Ingress-Path": "/api/hassio_ingress/test"}).status_code == 200


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


def test_dev_server_only_listens_on_this_machine(webapp, monkeypatch):
    calls = []
    monkeypatch.setattr(webapp.app, "run", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setenv("PORT", "8123")

    webapp.run_dev_server()

    assert calls == [{"host": "127.0.0.1", "port": 8123}]


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


def test_devices_cache_expires_after_ttl(webapp, monkeypatch):
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
    current_time[0] += webapp.DEVICE_CACHE_TTL_SECONDS - 1
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


# --------------------------------------------------------------------------- #
# LAN scan (see lan_scan)
# --------------------------------------------------------------------------- #
LAN_DEVICES = [
    {"id": "plug", "name": "Kitchen Plug", "local_key": "plug-key-0123456"},
    {"id": "lamp", "name": "Lamp", "local_key": "lamp-key-0123456"},
    {"id": "sensor", "name": "Door Sensor", "local_key": "gw-key-012345678",
     "sub": True, "gateway_id": "plug"},
]


def _ok(ip, version="3.3", device22=False, checked_at=1.0):
    return {"status": "ok", "ip": ip, "version": version, "device22": device22,
            "checked_at": checked_at}


def _lan_logged_in(webapp, monkeypatch, devices=None, user_code=None):
    """Log in with a loaded device list, ready to scan."""
    session = {"token_info": {}}
    if user_code:
        session["user_code"] = user_code
    webapp.core.save_session(os.environ["SESSION_FILE"], session)
    listed = [dict(d) for d in (devices or LAN_DEVICES)]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: listed)
    monkeypatch.setattr(webapp.core, "web_dict", lambda d: dict(d))
    client = webapp.app.test_client()
    assert client.get("/api/devices").status_code == 200
    return client


class FakeScan:
    """Stands in for lan_scan.scan, answering from a queue of outcomes."""

    def __init__(self, *outcomes, gate=None):
        self.outcomes = list(outcomes)
        self.calls = []
        self.gate = gate

    def __call__(self, targets, devices, known, progress=None, cancel=None, only=None,
                 routers=None):
        self.calls.append({"targets": targets, "devices": devices, "known": known,
                           "cancel": cancel, "only": only, "routers": routers})
        if progress:
            progress({"phase": "probe", "addresses": len(targets), "matched": 0})
        if self.gate is not None:
            assert self.gate.wait(5)
        results = self.outcomes.pop(0) if self.outcomes else {}
        cancelled = bool(cancel and cancel.is_set())
        return {"results": results, "summary": {
            "addresses": len(targets), "matched": sum(r["status"] == "ok" for r in results.values()),
            "devices": 2, "refused": [], "unmatched": [], "out_of_budget": [],
            "cancelled": cancelled, "duration": 0.1, "finished_at": 2.0,
        }}


def _wait_for_job(client):
    deadline = time.time() + 5
    while time.time() < deadline:
        job = client.get("/api/lan/scan").json["job"]
        if job and job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("the scan job never finished")


def _scan(client, targets="10.0.0.0/30", **body):
    body = body or {"targets": targets}
    response = client.post("/api/lan/scan", json=body)
    assert response.status_code == 202, response.json
    return _wait_for_job(client)


def test_lan_endpoints_require_a_login(webapp):
    client = webapp.app.test_client()

    assert client.get("/api/lan").status_code == 401
    assert client.post("/api/lan/scan", json={"targets": "10.0.0.1"}).status_code == 401
    assert client.get("/api/lan/scan").status_code == 401
    assert client.delete("/api/lan/scan").status_code == 401


def test_a_scan_needs_a_loaded_device_list(webapp):
    webapp.core.save_session(os.environ["SESSION_FILE"], {"token_info": {}})

    response = webapp.app.test_client().post("/api/lan/scan", json={"targets": "10.0.0.1"})

    assert response.status_code == 409
    assert response.json == {"error": "no_devices"}


@pytest.mark.parametrize("targets", ["", "8.8.8.8", "10.0.0.0/16", "nonsense"])
def test_bad_targets_are_rejected_with_a_message(webapp, monkeypatch, targets):
    client = _lan_logged_in(webapp, monkeypatch)

    response = client.post("/api/lan/scan", json={"targets": targets})

    assert response.status_code == 400
    assert response.json["error"] == "bad_targets"
    assert response.json["message"]


def test_a_scan_runs_in_the_background_and_stores_its_results(webapp, monkeypatch):
    fake = FakeScan({"plug": _ok("10.0.0.1", "3.4"),
                     "sensor": {"status": "via_gateway", "ip": "10.0.0.1", "version": "3.4",
                                "device22": False, "checked_at": 1.0, "gateway_id": "plug"}})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch)

    job = _scan(client, "10.0.0.0/30")

    assert job["state"] == "done" and job["kind"] == "scan"
    assert job["summary"]["matched"] == 1
    assert fake.calls[0]["targets"] == ["10.0.0.1", "10.0.0.2"]
    assert [d["id"] for d in fake.calls[0]["devices"]] == ["plug", "lamp", "sensor"]
    assert fake.calls[0]["only"] is None
    assert fake.calls[0]["routers"] == {"10.0.0.1"}, "the .1 starting the subnet typed"
    lan = client.get("/api/lan").json
    assert lan["results"]["plug"]["version"] == "3.4"
    assert lan["results"]["sensor"]["status"] == "via_gateway"
    assert lan["summary"]["matched"] == 1
    assert lan["targets"] == "10.0.0.0/30", "the last targets prefill the next scan"


def test_the_lan_subnet_setting_prefills_the_first_scan(webapp, monkeypatch, tmp_path):
    monkeypatch.setenv("LAN_SUBNET", "192.168.2.0/24")
    restarted = _restart(webapp)
    client = _lan_logged_in(restarted, monkeypatch)

    assert client.get("/api/lan").json["targets"] == "192.168.2.0/24"


def test_only_one_scan_runs_at_a_time(webapp, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({}, gate=gate))
    client = _lan_logged_in(webapp, monkeypatch)

    first = client.post("/api/lan/scan", json={"targets": "10.0.0.1"})
    second = client.post("/api/lan/scan", json={"targets": "10.0.0.2"})
    gate.set()
    _wait_for_job(client)

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json == {"error": "scan_running"}


def test_a_scan_can_be_cancelled(webapp, monkeypatch):
    gate = threading.Event()
    fake = FakeScan({"plug": _ok("10.0.0.1")}, gate=gate)
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch)
    client.post("/api/lan/scan", json={"targets": "10.0.0.1"})

    assert client.delete("/api/lan/scan").json == {"ok": True}
    gate.set()
    job = _wait_for_job(client)

    assert fake.calls[0]["cancel"].is_set()
    assert job["state"] == "cancelled"


def test_remembered_addresses_are_passed_to_the_next_scan(webapp, monkeypatch):
    fake = FakeScan({"plug": _ok("10.0.0.1", "3.5", True)}, {})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    _scan(client)

    assert fake.calls[1]["known"] == {"plug": {"ip": "10.0.0.1", "version": "3.5", "device22": True}}


def test_checking_one_device_uses_the_given_ip(webapp, monkeypatch):
    fake = FakeScan({"lamp": _ok("10.0.0.9", "3.3")})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch)

    job = _scan(client, device_id="lamp", ip="10.0.0.9")

    assert job["kind"] == "device" and job["result"]["ip"] == "10.0.0.9"
    assert fake.calls[0]["targets"] == ["10.0.0.9"]
    assert fake.calls[0]["only"] == ["lamp"]
    assert fake.calls[0]["routers"] is None, "an IP typed for one device is never the router"
    # The whole list goes along, so a gateway is known as one by its sub-devices.
    assert [d["id"] for d in fake.calls[0]["devices"]] == ["plug", "lamp", "sensor"]
    assert fake.calls[0]["known"] == {"lamp": {"ip": "10.0.0.9"}}
    assert client.get("/api/lan").json["summary"] is None, "a single check is not a scan"


def test_checking_one_device_falls_back_to_its_remembered_ip(webapp, monkeypatch):
    fake = FakeScan({"lamp": _ok("10.0.0.9", "3.4")}, {"lamp": _ok("10.0.0.9", "3.4")})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    _scan(client, device_id="lamp")

    assert fake.calls[1]["targets"] == ["10.0.0.9"]
    assert fake.calls[1]["known"]["lamp"]["version"] == "3.4"


@pytest.mark.parametrize("body,status,error", [
    ({"device_id": "nope", "ip": "10.0.0.1"}, 404, "unknown_device"),
    ({"device_id": "sensor", "ip": "10.0.0.1"}, 400, "sub_device"),
    ({"device_id": "lamp"}, 400, "bad_targets"),
    ({"device_id": "lamp", "ip": "10.0.0.0/30"}, 400, "bad_targets"),
])
def test_checking_one_device_validates_its_input(webapp, monkeypatch, body, status, error):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan())
    client = _lan_logged_in(webapp, monkeypatch)

    response = client.post("/api/lan/scan", json=body)

    assert response.status_code == status
    assert response.json["error"] == error


def test_a_scan_reports_version_and_ip_changes(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"plug": _ok("10.0.0.1", "3.3"), "lamp": _ok("10.0.0.2", "3.3")},
        {"plug": _ok("10.0.0.1", "3.4"), "lamp": _ok("10.0.0.7", "3.3")},
        {"plug": _ok("10.0.0.1", "3.4"), "lamp": _ok("10.0.0.7", "3.3")},
    ))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    assert client.get("/api/lan").json["changes"] is None, "nothing to compare a first scan against"

    _scan(client)
    changes = client.get("/api/lan").json["changes"]
    assert changes["version_changed"] == [{"id": "plug", "name": "Kitchen Plug", "was": "3.3", "now": "3.4"}]
    assert changes["ip_changed"] == [{"id": "lamp", "name": "Lamp", "was": "10.0.0.2", "now": "10.0.0.7"}]

    _scan(client)
    assert client.get("/api/lan").json["changes"] is None, "an unchanged scan clears the notice"


def test_a_single_check_adds_to_the_changes_instead_of_replacing_them(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"plug": _ok("10.0.0.1", "3.3"), "lamp": _ok("10.0.0.2", "3.3")},
        {"plug": _ok("10.0.0.1", "3.4"), "lamp": _ok("10.0.0.2", "3.3")},
        {"lamp": {"status": "key_mismatch", "ip": "10.0.0.2", "version": "3.3",
                  "device22": False, "checked_at": 3.0}},
    ))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    _scan(client)

    _scan(client, device_id="lamp", ip="10.0.0.2")

    changes = client.get("/api/lan").json["changes"]
    assert [e["id"] for e in changes["version_changed"]] == ["plug"]
    assert changes["local_key_failed"] == [{"id": "lamp", "name": "Lamp"}]


def test_a_result_is_dropped_when_a_refresh_changes_its_key_mid_scan(webapp, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"plug": _ok("10.0.0.1"), "lamp": _ok("10.0.0.2")}, gate=gate))
    client = _lan_logged_in(webapp, monkeypatch)
    client.post("/api/lan/scan", json={"targets": "10.0.0.0/30"})

    rotated = [dict(d, local_key="ROTATED-KEY-0000") if d["id"] == "lamp" else dict(d)
               for d in LAN_DEVICES]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: rotated)
    client.get("/api/devices?refresh=1")
    gate.set()
    _wait_for_job(client)

    assert set(client.get("/api/lan").json["results"]) == {"plug"}


def test_a_saved_connection_needs_rechecking_after_key_rotation_across_refreshes(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"lamp": _ok("10.0.0.2")}, {"lamp": _ok("10.0.0.2")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    assert client.get("/api/lan").json["results"]["lamp"]["key_current"] is True

    rotated = [dict(d, local_key="ROTATED-KEY-0000") if d["id"] == "lamp" else dict(d)
               for d in LAN_DEVICES]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: rotated)
    for _ in range(2):
        assert client.get("/api/devices?refresh=1").status_code == 200
        result = client.get("/api/lan").json["results"]["lamp"]
        assert result["key_current"] is False
        assert (result["status"], result["ip"]) == ("ok", "10.0.0.2")

    _scan(client, device_id="lamp", ip="10.0.0.2")
    assert client.get("/api/lan").json["results"]["lamp"]["key_current"] is True


def test_legacy_lan_cache_has_no_current_key_proof(webapp, monkeypatch):
    client = _lan_logged_in(webapp, monkeypatch)
    session = webapp.core.load_session(webapp.SESSION_FILE)
    with webapp._lan_lock:
        webapp._save_lan(webapp._session_cache_key(session),
                         dict(webapp._empty_lan_body(), results={"plug": _ok("10.0.0.1")}))

    result = client.get("/api/lan").json["results"]["plug"]
    assert result["key_current"] is False
    assert result["status"] == "ok", "historic LAN results remain available"


def test_a_child_connection_proof_depends_on_its_gateways_key(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({
        "plug": _ok("10.0.0.1", "3.4"),
        "sensor": dict(_ok("10.0.0.1", "3.4"), status="via_gateway", gateway_id="plug"),
    }))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    results = client.get("/api/lan").json["results"]
    assert results["plug"]["key_current"] and results["sensor"]["key_current"]

    rotated = [dict(d, local_key="ROTATED-KEY-0000") if d["id"] == "plug" else dict(d)
               for d in LAN_DEVICES]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: rotated)
    client.get("/api/devices?refresh=1")
    results = client.get("/api/lan").json["results"]
    assert not results["plug"]["key_current"] and not results["sensor"]["key_current"]


def test_a_child_inherits_no_proof_when_its_gateway_key_rotates_mid_scan(webapp, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({
        "plug": _ok("10.0.0.1", "3.4"),
        "sensor": dict(_ok("10.0.0.1", "3.4"), status="via_gateway", gateway_id="plug"),
    }, gate=gate))
    client = _lan_logged_in(webapp, monkeypatch)
    client.post("/api/lan/scan", json={"targets": "10.0.0.0/30"})

    rotated = [dict(d, local_key="ROTATED-KEY-0000") if d["id"] == "plug" else dict(d)
               for d in LAN_DEVICES]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: rotated)
    client.get("/api/devices?refresh=1")
    gate.set()
    _wait_for_job(client)

    results = client.get("/api/lan").json["results"]
    assert "plug" not in results
    assert results["sensor"]["key_current"] is False


@pytest.mark.parametrize("changed", [{"gateway_id": "elsewhere"}, {"node_id": "different-node"}])
def test_a_child_connection_proof_needs_its_original_relationship(webapp, monkeypatch, changed):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({
        "plug": _ok("10.0.0.1", "3.4"),
        "sensor": dict(_ok("10.0.0.1", "3.4"), status="via_gateway", gateway_id="plug"),
    }))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    reassigned = [dict(d, **changed) if d["id"] == "sensor" else dict(d) for d in LAN_DEVICES]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: reassigned)
    client.get("/api/devices?refresh=1")
    results = client.get("/api/lan").json["results"]
    assert results["sensor"]["key_current"] is False
    assert results["plug"]["key_current"] is True


# As Tuya's sharing API lists gateways (issue #7): marked `sub`, with no key of
# their own, which sits on their sub-devices instead.
GATEWAY_DEVICES = LAN_DEVICES + [
    {"id": "gw", "name": "Gateway", "category": "wg2", "sub": True, "node_id": "0010"},
    {"id": "valve", "name": "Valve", "local_key": "valve-key-012345", "sub": True,
     "node_id": "a4c138ec2d57044b", "ip": ""},
]


def test_a_keyless_gateway_can_be_checked_with_its_sub_devices_keys(webapp, monkeypatch):
    fake = FakeScan({"gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve")})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch, devices=GATEWAY_DEVICES)

    job = _scan(client, device_id="gw", ip="10.0.0.8")

    assert job["result"]["status"] == "ok" and job["result"]["key_from"] == "valve"
    assert fake.calls[0]["only"] == ["gw"]


def test_a_borrowed_gateway_and_child_proof_follow_the_source_key(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({
        "gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve"),
        "valve": dict(_ok("10.0.0.8", "3.4"), status="via_gateway", gateway_id="gw"),
    }))
    devices = [dict(d, gateway_id="gw") if d["id"] == "valve" else dict(d)
               for d in GATEWAY_DEVICES]
    client = _lan_logged_in(webapp, monkeypatch, devices=devices)
    _scan(client)
    results = client.get("/api/lan").json["results"]
    assert results["gw"]["key_current"] and results["valve"]["key_current"]

    rotated = [dict(d, local_key="ROTATED-KEY-0000") if d["id"] == "valve" else dict(d)
               for d in devices]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: rotated)
    client.get("/api/devices?refresh=1")
    results = client.get("/api/lan").json["results"]
    assert not results["gw"]["key_current"] and not results["valve"]["key_current"]


def test_a_keyless_gateway_with_no_sub_device_keys_cant_be_checked(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan())
    client = _lan_logged_in(webapp, monkeypatch, devices=GATEWAY_DEVICES[:-1])

    response = client.post("/api/lan/scan", json={"device_id": "gw", "ip": "10.0.0.8"})

    assert (response.status_code, response.json) == (400, {"error": "no_local_key"})


def test_the_sub_device_key_a_gateway_answered_to_is_remembered(webapp, monkeypatch):
    fake = FakeScan({"gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve"),
                     "plug": _ok("10.0.0.1")}, {})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch, devices=GATEWAY_DEVICES)
    _scan(client)

    _scan(client, device_id="lamp", ip="10.0.0.2")

    known = fake.calls[1]["known"]
    assert known["gw"] == {"ip": "10.0.0.8", "version": "3.4", "device22": False, "key_from": "valve"}
    assert known["plug"]["ip"] == "10.0.0.1", "a check passes along what the rest answered to"
    assert known["lamp"] == {"ip": "10.0.0.2"}


def test_a_gateways_key_link_is_remembered_even_without_an_ip(webapp, monkeypatch):
    fake = FakeScan({"gw": dict(_ok(None), status="not_found", key_from="valve")}, {})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch, devices=GATEWAY_DEVICES)
    _scan(client)

    _scan(client)

    assert fake.calls[1]["known"]["gw"] == {"key_from": "valve"}


TWO_GATEWAY_DEVICES = GATEWAY_DEVICES + [
    {"id": "gw-2", "name": "Mesh Gateway", "category": "wg2", "sub": True, "node_id": "00d8"},
    {"id": "timer", "name": "Water Timer", "local_key": "timer-key-012345", "sub": True, "ip": ""},
]


def test_a_check_that_names_a_gateway_takes_its_key_from_the_one_that_had_it(webapp, monkeypatch):
    via = lambda ip, gateway_id: dict(_ok(ip, "3.4"), status="via_gateway", gateway_id=gateway_id)
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        # A check typed with the wrong IP took the Mesh Gateway for "gw"...
        {"gw": dict(_ok("10.0.0.9", "3.4"), key_from="timer"), "timer": via("10.0.0.9", "gw")},
        # ...and the scan after it gave the Mesh Gateway the valve's key.
        {"gw": dict(_ok("10.0.0.9", "3.4"), key_from="timer"), "timer": via("10.0.0.9", "gw"),
         "gw-2": dict(_ok("10.0.0.8", "3.4"), key_from="valve"), "valve": via("10.0.0.8", "gw-2"),
         "plug": _ok("10.0.0.1"), "sensor": via("10.0.0.1", "plug")},
        # Checked at its real IP, "gw" answers to the valve's key.
        {"gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve"), "valve": via("10.0.0.8", "gw")},
    ))
    client = _lan_logged_in(webapp, monkeypatch, devices=TWO_GATEWAY_DEVICES)
    _scan(client, device_id="gw", ip="10.0.0.9")
    _scan(client)

    _scan(client, device_id="gw", ip="10.0.0.8")

    results = client.get("/api/lan").json["results"]
    assert results["gw"]["key_from"] == "valve" and results["valve"]["gateway_id"] == "gw"
    assert "gw-2" not in results, "it waits to be named again"
    assert results["timer"]["gateway_id"] is None and results["timer"]["ip"] == "10.0.0.9"
    # Devices with keys of their own, and their sub-devices, are left as they were.
    assert results["plug"] == dict(_ok("10.0.0.1"), key_current=True)
    assert results["sensor"] == dict(via("10.0.0.1", "plug"), key_current=False)
    assert results["timer"]["key_current"] is False, "its old inferred gateway was undone"


def _via(ip, gateway_id=None):
    return dict(_ok(ip, "3.4"), status="via_gateway", gateway_id=gateway_id)


def test_a_check_that_names_a_gateway_also_checks_the_one_left(webapp, monkeypatch):
    fake = FakeScan(
        {"valve": _via("10.0.0.8"), "timer": _via("10.0.0.9")},   # the scan: two unnamed gateways
        {"gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve"), "valve": _via("10.0.0.8", "gw")},
        {"gw-2": dict(_ok("10.0.0.9", "3.4"), key_from="timer"), "timer": _via("10.0.0.9", "gw-2")},
    )
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch, devices=TWO_GATEWAY_DEVICES)
    _scan(client)

    job = _scan(client, device_id="gw", ip="10.0.0.8")

    follow_up = fake.calls[2]
    assert (follow_up["targets"], follow_up["only"]) == (["10.0.0.9"], ["gw-2"]), \
        "where the only key left answered"
    assert follow_up["known"]["gw"]["key_from"] == "valve", "it knows the first gateway's key now"
    assert job["result"]["key_from"] == "valve"
    assert [(a["device_id"], a["result"]["ip"]) for a in job["also"]] == [("gw-2", "10.0.0.9")]
    results = client.get("/api/lan").json["results"]
    assert results["gw-2"]["key_from"] == "timer" and results["timer"]["gateway_id"] == "gw-2"


def test_a_failed_check_of_the_gateway_left_is_reported_but_not_saved(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"valve": _via("10.0.0.8"), "timer": _via("10.0.0.9")},
        {"gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve"), "valve": _via("10.0.0.8", "gw")},
        {"gw-2": dict(_ok("10.0.0.9", "3.4"), status="busy", key_from="timer")},
    ))
    client = _lan_logged_in(webapp, monkeypatch, devices=TWO_GATEWAY_DEVICES)
    _scan(client)

    job = _scan(client, device_id="gw", ip="10.0.0.8")

    assert job["also"][0]["result"]["status"] == "busy"
    assert "gw-2" not in client.get("/api/lan").json["results"]


def test_no_gateway_is_checked_unasked_while_more_than_one_could_have_the_key(webapp, monkeypatch):
    fake = FakeScan(
        {"valve": _via("10.0.0.8"), "timer": _via("10.0.0.9")},
        {"gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve"), "valve": _via("10.0.0.8", "gw")},
    )
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    devices = TWO_GATEWAY_DEVICES + [
        {"id": "gw-3", "name": "Third Gateway", "category": "wg2", "sub": True}]
    client = _lan_logged_in(webapp, monkeypatch, devices=devices)
    _scan(client)

    job = _scan(client, device_id="gw", ip="10.0.0.8")

    assert len(fake.calls) == 2 and job["also"] == []


def test_a_scan_drops_what_3_0_stored_for_gateways_marked_sub(webapp, monkeypatch):
    # 3.0 took both gateways for sub-devices whose gateway it didn't find.
    missed = dict(_via(None), version=None)
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"gw": missed, "gw-2": missed, "valve": missed, "timer": missed, "sensor": missed},
        {"valve": _via("10.0.0.8"), "timer": _via("10.0.0.9")},   # two unnamed gateways
    ))
    client = _lan_logged_in(webapp, monkeypatch, devices=TWO_GATEWAY_DEVICES)
    _scan(client)

    _scan(client)

    results = client.get("/api/lan").json["results"]
    assert "gw" not in results and "gw-2" not in results, "so they show check needed"
    assert results["sensor"] == dict(missed, key_current=False), "a real sub-device keeps its last result"


def test_a_gateway_result_is_dropped_when_its_sub_devices_key_changes_mid_scan(webapp, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"gw": dict(_ok("10.0.0.8", "3.4"), key_from="valve"), "plug": _ok("10.0.0.1")}, gate=gate))
    client = _lan_logged_in(webapp, monkeypatch, devices=GATEWAY_DEVICES)
    client.post("/api/lan/scan", json={"targets": "10.0.0.0/28"})

    rotated = [dict(d, local_key="ROTATED-KEY-0000") if d["id"] == "valve" else dict(d)
               for d in GATEWAY_DEVICES]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: rotated)
    client.get("/api/devices?refresh=1")
    gate.set()
    _wait_for_job(client)

    assert set(client.get("/api/lan").json["results"]) == {"plug"}


def test_lan_results_survive_a_restart_and_are_encrypted(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1", "3.4")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    blob = Path(webapp.LAN_CACHE_FILE).read_bytes()
    restarted = _restart(webapp)

    assert b"10.0.0.1" not in blob and b"plug" not in blob
    assert restarted.app.test_client().get("/api/lan").json["results"]["plug"]["version"] == "3.4"
    assert restarted.app.test_client().get("/api/lan").json["results"]["plug"]["key_current"] is True


def test_lan_results_are_ignored_after_switching_accounts(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    webapp.core.save_session(os.environ["SESSION_FILE"], {"user_code": "someone-else", "token_info": {}})
    response = _restart(webapp).app.test_client().get("/api/lan")

    assert response.json["results"] == {}


def test_logout_removes_the_lan_results(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    assert os.path.exists(webapp.LAN_CACHE_FILE)

    client.post("/api/logout")

    assert not os.path.exists(webapp.LAN_CACHE_FILE)
    client = _lan_logged_in(webapp, monkeypatch)
    assert client.get("/api/lan").json["results"] == {}


def test_logging_in_again_clears_the_lan_results(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    monkeypatch.setattr(
        webapp.core, "poll_login", lambda token, user_code: {"token_info": {}})
    with webapp._lock:
        webapp._pending["t"] = {"user_code": "u", "created_at": time.time()}

    assert client.post("/api/login/poll", json={"token": "t"}).json == {"status": "confirmed"}

    assert not os.path.exists(webapp.LAN_CACHE_FILE)
    assert client.get("/api/lan").json["results"] == {}


def test_an_invalid_session_with_no_saved_list_clears_the_lan_results(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    with webapp._devices_cache_lock:
        webapp._devices_cache = None
    os.remove(webapp.DEVICE_CACHE_FILE)
    monkeypatch.setattr(webapp.core, "devices_from_session",
                        lambda s, p: (_ for _ in ()).throw(KeyError("t")))

    assert client.get("/api/devices").status_code == 401

    assert not os.path.exists(webapp.LAN_CACHE_FILE)


def test_a_scan_that_finishes_after_logout_saves_nothing(webapp, monkeypatch):
    gate = threading.Event()
    fake = FakeScan({"plug": _ok("10.0.0.1")}, gate=gate)
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch)
    client.post("/api/lan/scan", json={"targets": "10.0.0.1"})

    client.post("/api/logout")
    client = _lan_logged_in(webapp, monkeypatch)   # the same account, straight back in
    gate.set()
    for thread in threading.enumerate():
        if thread.name == "lan-scan":
            thread.join(5)

    assert fake.calls[0]["cancel"].is_set()
    assert client.get("/api/lan").json["results"] == {}
    assert client.get("/api/lan/scan").json["job"] is None
    assert not os.path.exists(webapp.LAN_CACHE_FILE)


def test_device_cache_off_keeps_lan_results_in_memory_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.setenv("HASS_OPTIONS_FILE", str(tmp_path / "missing-options.json"))
    monkeypatch.setenv("DEVICE_CACHE", "off")
    import app

    app = importlib.reload(app)
    app.app.config.update(TESTING=True)
    monkeypatch.setattr(app.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1")}))
    client = _lan_logged_in(app, monkeypatch)
    _scan(client)

    assert client.get("/api/lan").json["results"]["plug"]["status"] == "ok"
    assert not os.path.exists(app.LAN_CACHE_FILE)


def test_no_key_appears_in_the_lan_responses(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1")}))
    client = _lan_logged_in(webapp, monkeypatch)
    started = client.post("/api/lan/scan", json={"targets": "10.0.0.1"}).json
    _wait_for_job(client)

    text = json.dumps([started, client.get("/api/lan").json, client.get("/api/lan/scan").json])
    for device in LAN_DEVICES:
        assert device["local_key"] not in text
        assert webapp._key_digest(device["local_key"]) not in text


def test_the_app_starts_and_lists_devices_without_tinytuya(webapp, monkeypatch):
    monkeypatch.setitem(sys.modules, "tinytuya", None)   # import now raises ImportError
    restarted = _restart(webapp)
    client = _lan_logged_in(restarted, monkeypatch)

    assert client.get("/api/devices").json["devices"][0]["local_key"] == LAN_DEVICES[0]["local_key"]
    assert client.get("/api/lan").status_code == 200

    response = client.post("/api/lan/scan", json={"targets": "10.0.0.1"})
    assert response.status_code == 503
    assert response.json == {"error": "scanner_unavailable"}
    assert client.get("/api/lan/scan").json["job"] is None


def test_a_scanner_failure_mid_scan_keeps_the_stored_results(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1", "3.4")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    def broken(*args, **kwargs):
        raise webapp.lan_scan.ScannerUnavailable("tinytuya failed on every probe")

    monkeypatch.setattr(webapp.lan_scan, "scan", broken)
    client.post("/api/lan/scan", json={"targets": "10.0.0.1"})
    job = _wait_for_job(client)

    assert job["state"] == "failed" and job["error"] == "scanner_unavailable"
    assert client.get("/api/lan").json["results"]["plug"]["version"] == "3.4"
    assert client.get("/api/devices").status_code == 200


def test_an_unexpected_scan_error_is_contained_in_the_job(webapp, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("anything at all")

    monkeypatch.setattr(webapp.lan_scan, "scan", crash)
    client = _lan_logged_in(webapp, monkeypatch)
    client.post("/api/lan/scan", json={"targets": "10.0.0.1"})

    job = _wait_for_job(client)
    assert job["state"] == "failed" and job["error"] == "scan_failed"
    assert client.get("/api/devices").status_code == 200


def test_a_device_without_a_local_key_cant_be_checked(webapp, monkeypatch):
    fake = FakeScan()
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    client = _lan_logged_in(webapp, monkeypatch,
                            devices=LAN_DEVICES + [{"id": "ble", "name": "Bluetooth Lock"}])

    response = client.post("/api/lan/scan", json={"device_id": "ble", "ip": "10.0.0.9"})

    assert response.status_code == 400
    assert response.json == {"error": "no_local_key"}
    assert fake.calls == []


def _unreachable(ip, checked_at=2.0):
    return dict(_ok(ip, checked_at=checked_at), status="unreachable")


def test_a_failed_check_at_a_new_ip_keeps_the_saved_result(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"lamp": _ok("10.0.0.2")}, {"lamp": _unreachable("10.0.0.99")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    job = _scan(client, device_id="lamp", ip="10.0.0.99")   # a typo

    assert job["result"]["status"] == "unreachable", "the check still says what it found"
    stored = client.get("/api/lan").json["results"]["lamp"]
    assert (stored["status"], stored["ip"]) == ("ok", "10.0.0.2")


def test_a_failed_check_at_the_known_ip_is_saved(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"lamp": _ok("10.0.0.2")}, {"lamp": dict(_ok("10.0.0.2"), status="busy")}))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)

    _scan(client, device_id="lamp", ip="10.0.0.2")

    assert client.get("/api/lan").json["results"]["lamp"]["status"] == "busy"


def test_a_passing_check_clears_its_key_failed_entry(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan(
        {"plug": _ok("10.0.0.1"), "lamp": _ok("10.0.0.2")},
        {"plug": _ok("10.0.0.1", "3.4"), "lamp": dict(_ok("10.0.0.2"), status="key_mismatch")},
        {"lamp": _ok("10.0.0.2")},
    ))
    client = _lan_logged_in(webapp, monkeypatch)
    _scan(client)
    _scan(client)
    before = client.get("/api/lan").json
    assert before["changes"]["local_key_failed"] == [{"id": "lamp", "name": "Lamp"}]

    _scan(client, device_id="lamp", ip="10.0.0.2")

    after = client.get("/api/lan").json
    assert after["changes"]["local_key_failed"] == []
    assert [e["id"] for e in after["changes"]["version_changed"]] == ["plug"], "the rest stays"
    assert after["changes_at"] == before["changes_at"], "nothing new, so a dismissed notice stays dismissed"


@pytest.mark.parametrize("path", ["/api/lan/scan", "/api/login/start", "/api/login/poll"])
def test_a_json_body_that_isnt_an_object_is_refused_not_a_crash(webapp, monkeypatch, path):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan())
    client = _lan_logged_in(webapp, monkeypatch)

    response = client.post(path, json=["10.0.0.1"])

    assert 400 <= response.status_code < 500


def test_a_failure_while_saving_doesnt_leave_the_job_running(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({"plug": _ok("10.0.0.1")}, {}))
    client = _lan_logged_in(webapp, monkeypatch)

    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(webapp, "_merge_lan", broken)
    job = _scan(client)

    assert job["state"] == "failed" and job["error"] == "scan_failed"
    assert client.post("/api/lan/scan", json={"targets": "10.0.0.1"}).status_code == 202, "not stuck"
    _wait_for_job(client)


def test_a_scan_that_gets_no_thread_isnt_left_running(webapp, monkeypatch):
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({}))
    client = _lan_logged_in(webapp, monkeypatch)

    class NoThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    with monkeypatch.context() as m:
        m.setattr(webapp.threading, "Thread", NoThread)
        response = client.post("/api/lan/scan", json={"targets": "10.0.0.1"})

    assert response.status_code == 503 and response.json == {"error": "scan_failed"}
    assert client.get("/api/lan/scan").json["job"]["state"] == "failed"
    assert client.post("/api/lan/scan", json={"targets": "10.0.0.1"}).status_code == 202
    _wait_for_job(client)


def test_the_job_says_when_a_cancel_is_under_way(webapp, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(webapp.lan_scan, "scan", FakeScan({}, gate=gate))
    client = _lan_logged_in(webapp, monkeypatch)
    client.post("/api/lan/scan", json={"targets": "10.0.0.1"})
    assert client.get("/api/lan/scan").json["job"]["cancelling"] is False

    client.delete("/api/lan/scan")
    job = client.get("/api/lan/scan").json["job"]
    gate.set()

    assert job["state"] == "running" and job["cancelling"] is True
    assert _wait_for_job(client)["cancelling"] is False


PREFERENCE_SESSION = {"client_id": "client-id", "user_code": "account-a",
                      "endpoint": "https://openapi.tuyaeu.com", "terminal_id": "terminal-a",
                      "token_info": {"access_token": "secret"}}


def _exclusion_logged_in(webapp, monkeypatch, session=None, load_devices=True):
    session = dict(PREFERENCE_SESSION if session is None else session)
    webapp.core.save_session(webapp.SESSION_FILE, session)
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda s, p: [
        {"id": "plug", "name": "Plug", "local_key": "plug-key"},
        {"id": "lamp", "name": "Lamp", "local_key": "lamp-key"}])
    monkeypatch.setattr(webapp.core, "web_dict", lambda d: dict(d))
    client = webapp.app.test_client()
    if load_devices:
        assert client.get("/api/devices").status_code == 200
    return client


def _cloud_unavailable(*args, **kwargs):
    raise AssertionError("Device preferences must not contact Tuya")


def test_exclusions_require_a_saved_session(webapp):
    client = webapp.app.test_client()
    for method in (client.get, client.patch):
        response = method("/api/device-exclusions")
        assert response.status_code == 401 and response.json == {"error": "not_logged_in"}
        assert response.headers["Cache-Control"] == "no-store"


def test_exclusions_need_a_complete_account_identity(webapp):
    webapp.core.save_session(webapp.SESSION_FILE, {"token_info": {}})
    client = webapp.app.test_client()
    assert client.get("/api/device-exclusions").status_code == 409
    response = client.patch("/api/device-exclusions", json={"exclude": ["plug"]})
    assert response.status_code == 409 and response.json["error"] == "account_identity_unavailable"
    assert not Path(webapp.DEVICE_PREFERENCES_FILE).exists()


def test_preferences_read_without_a_snapshot_or_cloud_call(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    monkeypatch.setattr(webapp.core, "devices_from_session", _cloud_unavailable)
    response = client.get("/api/device-exclusions")
    assert response.status_code == 200
    assert response.json == {"excluded_ids": [], "revision": 0,
        "account_id": webapp.device_preferences.account_id(PREFERENCE_SESSION)}
    assert not Path(webapp.DEVICE_PREFERENCES_FILE).exists()


def test_exclusion_deltas_are_idempotent_and_never_filter_server_devices(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    digest = client.get("/api/device-exclusions").json["account_id"]
    first = client.patch("/api/device-exclusions", json={"exclude": ["plug", "plug"], "account_id": digest})
    assert first.status_code == 200
    assert first.json == {"excluded_ids": ["plug"], "revision": 1, "account_id": digest}
    assert client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).json == first.json
    second = client.patch("/api/device-exclusions", json={"exclude": ["lamp"], "restore": ["plug"]})
    assert second.json == {"excluded_ids": ["lamp"], "revision": 2, "account_id": digest}
    assert client.patch("/api/device-exclusions", json={}).json == second.json
    assert [d["id"] for d in client.get("/api/devices").json["devices"]] == ["plug", "lamp"]
    assert [d["id"] for d in webapp._session_devices(PREFERENCE_SESSION)] == ["plug", "lamp"]


@pytest.mark.parametrize("payload", [None, [], "plug", {"exclude": "plug"}, {"restore": None},
    {"exclude": [""]}, {"exclude": ["  "]}, {"exclude": [True]}, {"exclude": [123]},
    {"exclude": [{}]}, {"exclude": ["plug\n"]}, {"exclude": ["\ud800"]}, {"exclude": ["x" * 257]},
    {"exclude": ["plug"], "restore": ["plug"]}, {"excluded_ids": ["plug"]},
    {"restore_all": 1}, {"restore_all": "true"}, {"restore_all": True, "exclude": ["plug"]},
    {"restore_all": True, "restore": ["plug"]},
    {"exclude": ["plug"] * 2001}])
def test_invalid_exclusion_batches_fail_atomically(webapp, monkeypatch, payload):
    client = _exclusion_logged_in(webapp, monkeypatch)
    saved = client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).json
    response = client.patch("/api/device-exclusions", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 400 and response.json["error"] == "invalid_exclusions"
    assert client.get("/api/device-exclusions").json == saved


@pytest.mark.parametrize("data, content_type", [("{", "application/json"),
    ('{"exclude": ["plug"]}', "text/plain"), (" " * (256 * 1024 + 1), "application/json"),
    ("[" * 1100 + "]" * 1100, "application/json")])
def test_invalid_or_oversized_exclusion_requests_are_bounded(webapp, monkeypatch, data, content_type):
    client = _exclusion_logged_in(webapp, monkeypatch)
    response = client.patch("/api/device-exclusions", data=data, content_type=content_type)
    assert response.status_code == 400 and response.json["error"] == "invalid_exclusions"
    assert client.get("/api/device-exclusions").json["revision"] == 0


def test_exclude_requires_an_account_snapshot_and_rejects_unknown_ids(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    response = client.patch("/api/device-exclusions", json={"exclude": ["plug"]})
    assert response.status_code == 409 and response.json["error"] == "device_snapshot_required"
    assert client.get("/api/devices").status_code == 200
    response = client.patch("/api/device-exclusions", json={"exclude": ["plug", "unknown"]})
    assert response.status_code == 400
    assert response.json == {"error": "unknown_device_ids", "unknown_ids": ["unknown"]}
    assert client.get("/api/device-exclusions").json["excluded_ids"] == []


def test_restore_allows_ids_missing_from_snapshot_and_needs_no_snapshot(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    client.patch("/api/device-exclusions", json={"exclude": ["plug", "lamp"]})
    webapp._clear_devices_cache()
    monkeypatch.setattr(webapp.core, "devices_from_session", _cloud_unavailable)
    response = client.patch("/api/device-exclusions", json={"restore": ["plug", "lamp", "absent"]})
    assert response.status_code == 200 and response.json["excluded_ids"] == []
    assert response.json["revision"] == 2


def test_preferences_and_account_context_are_current_for_cached_and_stale_devices(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    before = client.get("/api/devices").json
    saved = client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).json
    assert before["account_id"] == saved["account_id"]
    assert client.get("/api/devices").json == before
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    stale = client.get("/api/devices?refresh=1")
    assert stale.json["stale"] is True and stale.json["account_id"] == saved["account_id"]
    assert client.get("/api/device-exclusions").json == saved
    assert "account_id" not in webapp._devices_cache["body"]
    assert "changes" not in client.get("/api/devices").json
    assert "excluded_ids" not in webapp._devices_cache["body"]


def test_preferences_survive_restart_cache_off_and_qr_renewal(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    saved = client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).json
    client.post("/api/logout")
    assert Path(webapp.DEVICE_PREFERENCES_FILE).exists()
    monkeypatch.setenv("DEVICE_CACHE", "off")
    restarted = _restart(webapp)
    renewed = dict(PREFERENCE_SESSION, terminal_id="new-terminal", token_info={"access_token": "new-token"})
    client = _exclusion_logged_in(restarted, monkeypatch, renewed)
    assert client.get("/api/device-exclusions").json == saved
    assert not Path(restarted.DEVICE_CACHE_FILE).exists()
    assert not Path(restarted.DEVICE_CACHE_KEY_FILE).exists()
    assert _restart(webapp).app.test_client().get("/api/device-exclusions").json == saved


def test_exclusions_are_separate_for_accounts_and_stale_tab_writes_are_rejected(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    saved_a = client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).json
    other = dict(PREFERENCE_SESSION, user_code="account-b")
    client = _exclusion_logged_in(webapp, monkeypatch, other)
    saved_b = client.get("/api/device-exclusions").json
    assert saved_b["excluded_ids"] == [] and saved_b["account_id"] != saved_a["account_id"]
    response = client.patch("/api/device-exclusions", json={"exclude": ["lamp"], "account_id": saved_a["account_id"]})
    assert response.status_code == 409 and response.json["error"] == "account_changed"
    assert client.patch("/api/device-exclusions", json={"exclude": ["lamp"]}).json["excluded_ids"] == ["lamp"]
    client = _exclusion_logged_in(webapp, monkeypatch)
    assert client.get("/api/device-exclusions").json == saved_a


def test_preferences_error_never_reports_a_successful_save_or_empty_set(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    saved = client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).json

    def fail(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(webapp.device_preferences.core, "atomic_write", fail)
        response = client.patch("/api/device-exclusions", json={"exclude": ["lamp"]})
    assert response.status_code == 503 and response.json["error"] == "preferences_unavailable"
    assert client.get("/api/device-exclusions").json == saved
    Path(webapp.DEVICE_PREFERENCES_FILE).write_text("corrupt")
    assert client.get("/api/device-exclusions").status_code == 503
    assert client.patch("/api/device-exclusions", json={"restore": ["plug"]}).status_code == 503
    assert Path(webapp.DEVICE_PREFERENCES_FILE).read_text() == "corrupt"


def test_two_tabs_delta_updates_do_not_lose_either_selection(webapp, monkeypatch):
    _exclusion_logged_in(webapp, monkeypatch)
    barrier = threading.Barrier(2)
    responses = []

    def exclude(dev_id):
        barrier.wait(2)
        responses.append(webapp.app.test_client().patch("/api/device-exclusions", json={"exclude": [dev_id]}))

    threads = [threading.Thread(target=exclude, args=(dev_id,)) for dev_id in ("plug", "lamp")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert [response.status_code for response in responses] == [200, 200]
    final = webapp.app.test_client().get("/api/device-exclusions").json
    assert final["excluded_ids"] == ["lamp", "plug"] and final["revision"] == 2


def test_preferences_are_available_while_a_cloud_refresh_is_running(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    started, release = threading.Event(), threading.Event()

    def blocking(*args):
        started.set()
        assert release.wait(3)
        return []

    monkeypatch.setattr(webapp.core, "devices_from_session", blocking)
    thread = threading.Thread(target=lambda: webapp.app.test_client().get("/api/devices?refresh=1"))
    thread.start()
    assert started.wait(2)
    try:
        assert client.get("/api/device-exclusions").status_code == 200
        assert client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).status_code == 200
    finally:
        release.set()
        thread.join(3)


def test_logout_cannot_race_a_preference_save_into_a_new_account(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    saving, release = threading.Event(), threading.Event()
    original_update = webapp._device_preferences.update

    def blocking_update(*args, **kwargs):
        saving.set()
        assert release.wait(3)
        return original_update(*args, **kwargs)

    monkeypatch.setattr(webapp._device_preferences, "update", blocking_update)
    results = {}
    writer = threading.Thread(target=lambda: results.update(save=webapp.app.test_client().patch(
        "/api/device-exclusions", json={"exclude": ["plug"]})))
    writer.start()
    assert saving.wait(2)
    logout_done = threading.Event()

    def logout():
        results["logout"] = webapp.app.test_client().post("/api/logout")
        logout_done.set()

    transition = threading.Thread(target=logout)
    transition.start()
    assert not logout_done.wait(0.05)
    release.set()
    writer.join(3)
    transition.join(3)
    assert results["save"].status_code == 200 and results["logout"].status_code == 200
    assert client.get("/api/device-exclusions").status_code == 401
    client = _exclusion_logged_in(webapp, monkeypatch, dict(PREFERENCE_SESSION, user_code="account-b"))
    assert client.get("/api/device-exclusions").json["excluded_ids"] == []
    client = _exclusion_logged_in(webapp, monkeypatch)
    assert client.get("/api/device-exclusions").json["excluded_ids"] == ["plug"]


def test_an_inflight_login_cannot_resurrect_a_logged_out_session(webapp, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def blocking_login(*args):
        started.set()
        assert release.wait(3)
        return dict(PREFERENCE_SESSION)

    monkeypatch.setattr(webapp.core, "poll_login", blocking_login)
    with webapp._lock:
        webapp._pending["token"] = {"user_code": "account-a", "created_at": time.time()}
    responses = []
    thread = threading.Thread(target=lambda: responses.append(webapp.app.test_client().post(
        "/api/login/poll", json={"token": "token"})))
    thread.start()
    assert started.wait(2)
    assert webapp.app.test_client().post("/api/logout").status_code == 200
    release.set()
    thread.join(3)
    assert responses[0].status_code == 409
    assert not Path(webapp.SESSION_FILE).exists()


def test_real_qr_relogin_retains_only_the_same_accounts_preferences(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    saved = client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).json
    sessions = [dict(PREFERENCE_SESSION, terminal_id="qr-terminal"),
                dict(PREFERENCE_SESSION, user_code="account-b")]

    for index, session in enumerate(sessions):
        monkeypatch.setattr(webapp.core, "poll_login", lambda *args: session)
        token = f"token-{index}"
        with webapp._lock:
            webapp._pending[token] = {"user_code": session["user_code"], "created_at": time.time()}
        assert client.post("/api/login/poll", json={"token": token}).json == {"status": "confirmed"}
        assert client.get("/api/device-exclusions").json["excluded_ids"] == (["plug"] if index == 0 else [])
    assert webapp._device_preferences.get(saved["account_id"]) == saved


def test_logout_waits_for_sdk_token_save_then_removes_the_old_session(webapp, monkeypatch):
    _exclusion_logged_in(webapp, monkeypatch)
    fetching, release, logged_out = threading.Event(), threading.Event(), threading.Event()
    responses = []

    def sdk_refresh(session, path):
        fetching.set()
        assert release.wait(3)
        webapp.core.save_session(path, dict(session, token_info={"access_token": "renewed"}))
        return []

    monkeypatch.setattr(webapp.core, "devices_from_session", sdk_refresh)
    fetch = threading.Thread(target=lambda: responses.append(webapp.app.test_client().get("/api/devices?refresh=1")))
    fetch.start()
    assert fetching.wait(2)

    def logout():
        responses.append(webapp.app.test_client().post("/api/logout"))
        logged_out.set()

    transition = threading.Thread(target=logout)
    transition.start()
    assert not logged_out.wait(0.05)
    release.set()
    fetch.join(3)
    transition.join(3)
    assert len(responses) == 2 and all(response.status_code == 200 for response in responses)
    assert not Path(webapp.SESSION_FILE).exists()
    assert webapp.app.test_client().get("/api/device-exclusions").status_code == 401


def test_restore_all_clears_large_sets_without_a_device_snapshot_or_cloud(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    digest = client.get("/api/device-exclusions").json["account_id"]
    webapp._device_preferences.update(digest, exclude=[f"device-{number}" for number in range(2100)])
    monkeypatch.setattr(webapp.core, "devices_from_session", _cloud_unavailable)
    result = client.patch("/api/device-exclusions", json={"restore_all": True, "account_id": digest})
    assert result.status_code == 200
    assert result.json == {"excluded_ids": [], "revision": 2, "account_id": digest}
    assert client.patch("/api/device-exclusions", json={"restore_all": True}).json == result.json


def test_lan_response_identifies_its_account_for_client_snapshot_pairing(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    account_a = client.get("/api/device-exclusions").json["account_id"]
    assert client.get("/api/lan").json["account_id"] == account_a
    client = _exclusion_logged_in(webapp, monkeypatch, dict(PREFERENCE_SESSION, user_code="account-b"))
    account_b = client.get("/api/devices").json["account_id"]
    response = client.get("/api/lan")
    assert response.json["account_id"] == account_b and account_b != account_a


def test_scan_mutations_check_account_and_return_account_context(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    digest = client.get("/api/device-exclusions").json["account_id"]
    gate = threading.Event()
    fake = FakeScan({}, gate=gate)
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)
    response = client.post("/api/lan/scan", json={"targets": "10.0.0.1", "account_id": "stale-account"})
    assert response.status_code == 409 and response.json["error"] == "account_changed"
    assert fake.calls == []
    started = client.post("/api/lan/scan", json={"targets": "10.0.0.1", "account_id": digest})
    try:
        assert started.status_code == 202 and started.json["account_id"] == digest
        progress = client.get("/api/lan/scan")
        assert progress.json["account_id"] == digest
        job = webapp._lan_job
        refusal = client.delete("/api/lan/scan", json={"account_id": "stale-account"})
        assert refusal.status_code == 409 and refusal.json["error"] == "account_changed"
        assert not job["cancel"].is_set()
        cancelled = client.delete("/api/lan/scan", json={"account_id": digest})
        assert cancelled.status_code == 200 and cancelled.json["account_id"] == digest
        assert job["cancel"].is_set()
    finally:
        gate.set()
        _wait_for_job(client)


def test_scan_preparation_rechecks_account_before_registering_a_job(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    digest = client.get("/api/device-exclusions").json["account_id"]
    fake = FakeScan({})
    monkeypatch.setattr(webapp.lan_scan, "scan", fake)

    def switch_account():
        with webapp._account_lock:
            webapp.core.save_session(webapp.SESSION_FILE, dict(PREFERENCE_SESSION, user_code="account-b"))

    monkeypatch.setattr(webapp.lan_scan, "require_scanner", switch_account)
    response = client.post("/api/lan/scan", json={"targets": "10.0.0.1", "account_id": digest})
    assert response.status_code == 409 and response.json["error"] == "account_changed"
    assert webapp._lan_job is None and fake.calls == []


def _devices_with_homes(webapp):
    return webapp.core.Devices([
        {"id": "plug", "name": "Plug", "local_key": "plug-key", "owner_id": "not-a-home"},
        {"id": "lamp", "name": "Lamp", "local_key": "lamp-key"}],
        homes=[{"id": 101, "name": "Main home"}, {"id": "202", "name": "Other home"},
               {"id": "303", "name": "Empty home"}],
        home_ids={"plug": [101, "202"], "lamp": ["202"]})


def test_devices_adds_authoritative_home_memberships_and_keeps_full_snapshot(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    fetched = _devices_with_homes(webapp)
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: fetched)
    response = client.get("/api/devices")

    assert response.status_code == 200
    assert response.json["homes"] == [{"id": "101", "name": "Main home"},
        {"id": "202", "name": "Other home"}, {"id": "303", "name": "Empty home"}]
    plug, lamp = response.json["devices"]
    assert plug["home_ids"] == ["101", "202"] and plug["home_names"] == ["Main home", "Other home"]
    assert lamp["home_ids"] == ["202"] and lamp["home_names"] == ["Other home"]
    assert "home_ids" not in fetched[0], "web overlays leave source device records unchanged"
    assert [device["id"] for device in webapp._session_devices(PREFERENCE_SESSION)] == ["plug", "lamp"]
    assert client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).status_code == 200
    assert len(client.get("/api/devices").json["devices"]) == 2


def test_home_metadata_survives_cache_hits_and_restart(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    fetched = _devices_with_homes(webapp)
    calls = []

    def fetch(*args):
        calls.append(1)
        return fetched

    monkeypatch.setattr(webapp.core, "devices_from_session", fetch)
    first = client.get("/api/devices")
    assert client.get("/api/devices").json == first.json
    restarted = _restart(webapp)
    assert restarted.app.test_client().get("/api/devices").json == first.json
    assert calls == [1]


def test_failed_home_refresh_retains_the_complete_cached_membership(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: _devices_with_homes(webapp))
    first = client.get("/api/devices").json

    def failed_fetch(*args):
        raise RuntimeError("Home device request failed")

    monkeypatch.setattr(webapp.core, "devices_from_session", failed_fetch)
    response = client.get("/api/devices?refresh=1")
    assert response.status_code == 200 and response.json["stale"] is True
    assert response.json["homes"] == first["homes"] and response.json["devices"] == first["devices"]
    assert response.json["refresh_failed"] is True


def test_old_cached_lists_have_no_home_metadata_until_explicit_refresh(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch)
    old = client.get("/api/devices").json
    assert "homes" not in old and all("home_ids" not in d for d in old["devices"])
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: _devices_with_homes(webapp))
    assert client.get("/api/devices").json == old
    refreshed = client.get("/api/devices?refresh=1").json
    assert len(refreshed["homes"]) == 3
    assert all(device["home_ids"] for device in refreshed["devices"])
    assert "changes" not in refreshed, "adding home metadata does not invent device changes"


def test_known_empty_homes_are_distinct_from_unavailable_home_metadata(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: webapp.core.Devices(
        [], homes=[{"id": "empty", "name": "An empty home"}]))
    response = client.get("/api/devices").json
    assert response["devices"] == [] and response["homes"] == [{"id": "empty", "name": "An empty home"}]
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: webapp.core.Devices([]))
    refreshed = client.get("/api/devices?refresh=1").json
    assert refreshed["devices"] == [] and refreshed["homes"] == []


def _devices_with_rooms(webapp):
    fetched = _devices_with_homes(webapp)
    return webapp.core.Devices(list(fetched) + [{"id": "sensor", "name": "Sensor"}],
        homes=fetched.homes, home_ids=fetched.home_ids,
        device_rooms={"plug": {"id": 123, "name": "Kitchen", "display_order": 2}, "lamp": None})


def test_rooms_distinguish_assigned_unassigned_and_failed_lookups_without_mutating_source(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    fetched = _devices_with_rooms(webapp)
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: fetched)

    body = client.get("/api/devices").json

    assert body["rooms_loaded"] is True
    plug, lamp, sensor = body["devices"]
    assert (plug["room_status"], plug["room_id"], plug["room_name"], plug["room_display_order"]) == (
        "assigned", "123", "Kitchen", 2)
    assert lamp["room_status"] == "unassigned" and lamp["room_id"] is None
    assert sensor["room_status"] == "unknown" and "room_id" not in sensor
    assert all("room_status" not in device for device in fetched)
    assert client.patch("/api/device-exclusions", json={"exclude": ["plug"]}).status_code == 200
    assert len(client.get("/api/devices").json["devices"]) == 3
    assert len(webapp._session_devices(PREFERENCE_SESSION)) == 3, "room filters cannot change scan inputs"


def test_rooms_survive_cache_hits_restart_and_failed_refresh_without_new_lookups(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    calls = []

    def fetch(*args):
        calls.append(1)
        return _devices_with_rooms(webapp)

    monkeypatch.setattr(webapp.core, "devices_from_session", fetch)
    first = client.get("/api/devices").json
    assert client.get("/api/devices").json == first
    restarted = _restart(webapp)
    assert restarted.app.test_client().get("/api/devices").json == first
    assert calls == [1]

    def fail(*args):
        raise RuntimeError("Tuya offline")

    monkeypatch.setattr(restarted.core, "devices_from_session", fail)
    snapshot = restarted.app.test_client().get("/api/devices?refresh=1").json
    assert snapshot["stale"] is True and snapshot["refresh_failed"] is True
    assert snapshot["rooms_loaded"] is True and snapshot["devices"] == first["devices"]


def test_legacy_home_cache_needs_explicit_refresh_for_rooms_without_inventing_changes(webapp, monkeypatch):
    client = _exclusion_logged_in(webapp, monkeypatch, load_devices=False)
    fetched = _devices_with_homes(webapp)
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: fetched)
    first = client.get("/api/devices").json
    assert "rooms_loaded" not in first and all("room_status" not in device for device in first["devices"])
    enriched = webapp.core.Devices(fetched, homes=fetched.homes, home_ids=fetched.home_ids,
        device_rooms={"plug": {"id": "123", "name": "Kitchen"}, "lamp": None})
    monkeypatch.setattr(webapp.core, "devices_from_session", lambda *args: enriched)
    assert client.get("/api/devices").json == first

    refreshed = client.get("/api/devices?refresh=1").json

    assert refreshed["rooms_loaded"] is True
    assert "changes" not in refreshed, "room enrichment alone must not report key/device changes"
