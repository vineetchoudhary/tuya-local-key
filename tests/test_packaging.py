import re
import struct
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_home_assistant_metadata_matches_release_image():
    config = yaml.safe_load((ROOT / "tuya_local_key" / "config.yaml").read_text())
    repository = yaml.safe_load((ROOT / "repository.yaml").read_text())

    assert repository["name"] == "Tuya Local Key"
    assert config["slug"] == "tuya_local_key"
    assert config["version"] == "1.7"
    assert config["image"] == "ghcr.io/vineetchoudhary/tuya-local-key"
    assert "legacy" not in config
    assert config["arch"] == ["aarch64", "amd64"]
    assert config["ingress"] is True
    assert config["ingress_port"] == 8000
    assert config["ports"]["8000/tcp"] is None
    assert config["environment"]["SESSION_FILE"] == "/data/session.json"
    assert config["environment"]["QR_SCHEME"] == "smartlife"
    assert config["environment"]["PORT"] == "8000"
    assert config["options"]["QR_SCHEME"] == "smartlife"
    assert config["schema"]["QR_SCHEME"] == "list(smartlife|tuyaSmart)"
    assert config["schema"]["AUTH_USERNAME"] == "str?"
    assert config["schema"]["AUTH_PASSWORD"] == "password?"


def test_home_assistant_app_icon_exists_and_is_square_png():
    icon = ROOT / "tuya_local_key" / "icon.png"
    data = icon.read_bytes()

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert icon.name == "icon.png"
    assert struct.unpack(">II", data[16:24]) == (128, 128)


def test_dockerfile_has_home_assistant_labels_and_runtime_contract():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert 'ARG BUILD_VERSION=dev' in dockerfile
    assert 'io.hass.version="${BUILD_VERSION}"' in dockerfile
    assert 'io.hass.type="app"' in dockerfile
    assert 'io.hass.arch="aarch64|amd64"' in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert "USER appuser" not in dockerfile
    assert "SESSION_FILE=/data/session.json" in dockerfile
    assert "waitress-serve --listen=0.0.0.0:${PORT:-8000}" in dockerfile


def test_github_workflow_runs_tests_and_only_publishes_on_tags():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text()
    )

    assert workflow[True]["push"] == {"tags": ["v*"]}
    assert "branches" not in workflow[True]["push"]
    assert workflow[True]["pull_request"] == {"branches": ["main"]}

    text = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text()
    assert "pytest" in text
    assert "value=${GITHUB_REF_NAME#v}" in text
    assert "type=raw,value=${{ steps.version.outputs.value }}" in text
    assert "BUILD_VERSION=${{ steps.version.outputs.value }}" in text
    assert "push: false" in text
    assert "push: true" in text


def test_readme_screenshot_paths_exist():
    readme = (ROOT / "README.md").read_text()
    paths = set(re.findall(r'(?:src|srcset)="([^"]+)"', readme))

    assert paths
    for path in paths:
        if path.startswith("docs/screenshots/"):
            assert (ROOT / path).is_file(), path
