"""Cached, read-only release checks. No device or account data leaves the app."""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REPOSITORY = "https://github.com/vineetchoudhary/tuya-local-key"
RELEASE_API = "https://api.github.com/repos/vineetchoudhary/tuya-local-key/releases/latest"
SUPERVISOR_API = "http://supervisor/addons/self/info"
CHECK_INTERVAL = 24 * 3600
ERROR_RETRY = CHECK_INTERVAL
HOME_ASSISTANT_DELAY = 3600
MAX_STALE = 24 * 3600
MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT = 4
VERSION = re.compile(r"v?(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?", re.IGNORECASE)


class CheckUnavailable(ValueError):
    """An invalid or unavailable response, with no raw response content."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def version_parts(version):
    match = VERSION.fullmatch(version) if isinstance(version, str) else None
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _get_json(url, headers):
    # In particular, never forward a Supervisor token through a proxy or redirect.
    handlers = [_NoRedirect()]
    if url == SUPERVISOR_API:
        handlers.append(ProxyHandler({}))
    opener = build_opener(*handlers)
    with opener.open(Request(url, headers=headers), timeout=REQUEST_TIMEOUT) as response:
        if response.status != 200:
            raise CheckUnavailable()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CheckUnavailable()
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise CheckUnavailable()
    return result


def _release(data):
    tag = data.get("tag_name")
    if (data.get("draft") is not False or data.get("prerelease") is not False
            or version_parts(tag) is None):
        raise CheckUnavailable()
    published = data.get("published_at")
    if not isinstance(published, str) or len(published) > 64:
        raise CheckUnavailable()
    try:
        date = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
        published_at = date.timestamp()
    except (ValueError, OverflowError, OSError):
        raise CheckUnavailable() from None
    return {"version": tag.lstrip("vV"), "tag": tag, "published_at": published_at,
            "published": date.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "url": f"{REPOSITORY}/releases/tag/{tag}"}


class UpdateChecker:
    def __init__(self, cache_path=None):
        self._lock = threading.Lock()
        self._cache_path = Path(cache_path) if cache_path is not None else None
        self._release = None
        self._checked_at = None
        self._attempted_at = None
        self._last_check_succeeded = False
        self._generation = 0
        self._next_check = 0
        self._token = None
        self._addon_slug = None
        self._next_addon_check = 0
        self._load()

    def _load(self):
        if self._cache_path is None:
            return
        try:
            with self._cache_path.open("rb") as file:
                raw = file.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return
            saved = json.loads(raw)
            attempted = saved.get("attempted_at")
            if saved.get("schema_version") != 1 or type(attempted) not in (int, float) or not math.isfinite(attempted):
                return
            self._attempted_at = attempted
            self._next_check = time.monotonic() + max(0, attempted + CHECK_INTERVAL - time.time())
            checked = saved.get("checked_at")
            if type(checked) in (int, float) and math.isfinite(checked) and isinstance(saved.get("release"), dict):
                self._release = _release(saved["release"])
                self._checked_at = checked
            self._last_check_succeeded = saved.get("last_check_succeeded") is True
        except (OSError, ValueError, TypeError, AttributeError, OverflowError):
            pass

    def _save(self):
        if self._cache_path is None:
            return
        temporary = None
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            release = self._release
            saved = {"schema_version": 1, "attempted_at": self._attempted_at,
                     "checked_at": self._checked_at, "last_check_succeeded": self._last_check_succeeded,
                     "release": {"tag_name": release["tag"], "published_at": release["published"],
                                 "draft": False, "prerelease": False} if release else None}
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(self._cache_path.parent),
                                             prefix=".update-check-", delete=False) as file:
                temporary = file.name
                json.dump(saved, file)
            os.replace(temporary, self._cache_path)
        except OSError:
            # The in-memory cooldown still applies if persistence is unavailable.
            pass
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def status(self, current_version, home_assistant=False, supervisor_token=None, manual=False):
        generation = self._generation
        with self._lock:
            monotonic = time.monotonic()
            checked_now = False
            if (manual and generation == self._generation) or monotonic >= self._next_check:
                checked_now = True
                self._attempted_at = time.time()
                self._next_check = monotonic + CHECK_INTERVAL
                self._last_check_succeeded = False
                # Failed requests and restarts also honor the daily cooldown.
                self._save()
                try:
                    data = _get_json(RELEASE_API, {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Tuya-Local-Key-update-check",
                        "X-GitHub-Api-Version": "2022-11-28",
                    })
                    self._release = _release(data)
                    self._checked_at = time.time()
                    self._last_check_succeeded = True
                except Exception:
                    # Explicit manual checks can try again sooner.
                    pass
                self._generation += 1
                self._save()
            now = time.time()
            release = self._release if self._checked_at is not None and now - self._checked_at <= MAX_STALE else None
            current = version_parts(current_version)
            newer = bool(release and current and version_parts(release["version"]) > current)
            wait = max(0, release["published_at"] + (HOME_ASSISTANT_DELAY if home_assistant else 0) - now) if release else 0
            available = newer and wait == 0
            url = release["url"] if release else None
            if home_assistant:
                url = "https://my.home-assistant.io/redirect/updates/"
                if supervisor_token and available:
                    if self._token != supervisor_token:
                        self._token = supervisor_token
                        self._addon_slug = None
                        self._next_addon_check = 0
                    if time.monotonic() >= self._next_addon_check:
                        try:
                            info = _get_json(SUPERVISOR_API, {"Authorization": f"Bearer {supervisor_token}"})
                            data = info.get("data")
                            slug = data.get("slug") if isinstance(data, dict) else None
                            if info.get("result") != "ok" or not isinstance(slug, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", slug):
                                raise CheckUnavailable()
                            self._addon_slug = slug
                            self._next_addon_check = time.monotonic() + CHECK_INTERVAL
                        except Exception:
                            self._next_addon_check = time.monotonic() + ERROR_RETRY
                    if self._addon_slug:
                        url = f"https://my.home-assistant.io/redirect/supervisor_addon/?addon={self._addon_slug}"
            retry = max(1, math.ceil(self._next_check - time.monotonic()))
            return {
                "current_version": current_version,
                "latest_version": release["version"] if release else None,
                "available": available,
                "installation": "home_assistant" if home_assistant else "standalone",
                "release_url": release["url"] if release else None,
                "update_url": url if available else None,
                "published_at": release["published"] if release else None,
                "check_after_seconds": retry,
                "attempted_at": self._attempted_at,
                "checked_at": self._checked_at,
                "checked_now": checked_now,
                "last_check_succeeded": self._last_check_succeeded,
                "waiting_for_release": newer and wait > 0,
                "status": "ok" if release else "unavailable",
            }
