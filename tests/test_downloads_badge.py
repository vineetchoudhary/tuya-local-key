"""Run the badge writer against local Git repositories, including shallow clones."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *map(str, args)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-q")
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Badge test")
    git(source, "config", "user.email", "badge@example.com")
    git(source, "checkout", "-q", "-b", "main")
    (source / "README.md").write_text("Main branch content\n")
    git(source, "add", "README.md")
    git(source, "commit", "-q", "-m", "Initial main commit")
    git(source, "push", "-q", remote, "main")
    return remote, source


def run_badge(tmp_path, remote, count, before_push=""):
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "downloads-badge.yml").read_text()
    )
    script = next(
        step["run"] for step in workflow["jobs"]["update"]["steps"]
        if "COUNT" in step.get("env", {})
    )
    if before_push:
        script = script.replace("\ngit push ", f"\n{before_push}\ngit push ")
    # Rewrite only this test URL. File transport keeps --depth 1 effective,
    # and all other protocols are disabled so these tests cannot use the network.
    env = dict(os.environ, COUNT=count, TOKEN="test-token", GITHUB_REPOSITORY="test/repo",
               BADGE_REMOTE=str(remote), GIT_CONFIG_COUNT="3",
               GIT_CONFIG_KEY_0=f"url.{remote.as_uri()}.insteadOf",
               GIT_CONFIG_VALUE_0="https://x-access-token:test-token@github.com/test/repo.git",
               GIT_CONFIG_KEY_1="protocol.allow", GIT_CONFIG_VALUE_1="never",
               GIT_CONFIG_KEY_2="protocol.file.allow", GIT_CONFIG_VALUE_2="always")
    workspace = tmp_path / f"run-{len(list(tmp_path.glob('run-*')))}"
    workspace.mkdir()
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        cwd=workspace, env=env, capture_output=True, text=True,
    )


def assert_badge(remote, count, main):
    assert git(remote, "rev-list", "--count", "badges") == "1"
    assert git(remote, "ls-tree", "--name-only", "badges") == "downloads.json"
    assert json.loads(git(remote, "show", "badges:downloads.json")) == {
        "schemaVersion": 1, "label": "downloads", "message": count, "color": "2f6fed",
    }
    assert git(remote, "rev-parse", "main") == main


def test_first_run_and_updates_keep_one_commit_and_unchanged_runs_do_nothing(tmp_path, repositories):
    remote, _ = repositories
    main = git(remote, "rev-parse", "main")
    for count in ("1K", "2K"):
        result = run_badge(tmp_path, remote, count)
        assert result.returncode == 0, result.stderr
        assert_badge(remote, count, main)

    previous = git(remote, "rev-parse", "badges")
    result = run_badge(tmp_path, remote, "2K")
    assert result.returncode == 0, result.stderr
    assert "Unchanged at 2K." in result.stdout
    assert git(remote, "rev-parse", "badges") == previous


@pytest.mark.parametrize("count", ["2K", "3K"])
def test_existing_history_is_removed_even_when_the_count_is_unchanged(tmp_path, repositories, count):
    remote, source = repositories
    main = git(remote, "rev-parse", "main")
    git(source, "switch", "-q", "--orphan", "badges")
    for old_count in ("1K", "2K"):
        (source / "downloads.json").write_text(json.dumps({
            "schemaVersion": 1, "label": "downloads", "message": old_count, "color": "2f6fed",
        }) + "\n")
        git(source, "add", "downloads.json")
        git(source, "commit", "-q", "-m", f"Update downloads badge to {old_count}")
    git(source, "push", "-q", remote, "badges")
    assert git(remote, "rev-list", "--count", "badges") == "2"

    result = run_badge(tmp_path, remote, count)
    assert result.returncode == 0, result.stderr
    assert_badge(remote, count, main)


def test_a_concurrent_branch_update_is_not_overwritten(tmp_path, repositories):
    remote, source = repositories
    result = run_badge(tmp_path, remote, "1K")
    assert result.returncode == 0, result.stderr
    git(source, "fetch", "-q", remote, "badges")
    git(source, "checkout", "-q", "-b", "badges", "FETCH_HEAD")
    (source / "downloads.json").write_text('{"message": "concurrent update"}\n')
    git(source, "commit", "-q", "-a", "-m", "Concurrent update")
    # Transfer its objects without moving badges until the workflow is pushing.
    git(source, "push", "-q", remote, "HEAD:concurrent")
    concurrent = git(remote, "rev-parse", "concurrent")

    result = run_badge(tmp_path, remote, "2K", before_push=(
        'git --git-dir="$BADGE_REMOTE" update-ref refs/heads/badges refs/heads/concurrent'
    ))
    assert result.returncode != 0
    assert "stale info" in result.stderr
    assert git(remote, "rev-parse", "badges") == concurrent
