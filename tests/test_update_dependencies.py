"""tools/update_dependencies.py and the Update dependencies workflow that runs it."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import update_dependencies as ud  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "dependency-update.yml"


def files(tmp_path, tinytuya="tinytuya>=1.20", sdk="tuya-device-sharing-sdk>=0.2.15"):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"{sdk}\nqrcode[pil]>=7.4\n{tinytuya}\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        f'[project]\ndependencies = [\n    "{sdk}",\n    "qrcode[pil]>=7.4",\n    "{tinytuya}",\n]\n')
    return requirements, pyproject


def newest(**versions):
    return lambda name: versions[name.replace("-", "_")]


# --------------------------------------------------------------------------- #
# Reading and moving versions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ud.WATCHED)
def test_the_repository_asks_for_each_library_the_same_way_in_both_files(name):
    operator, version = ud.declared(name)

    assert operator in (">=", "==") and ud.VERSION.fullmatch(version)


def test_apply_moves_both_files_and_keeps_each_operator(tmp_path):
    paths = files(tmp_path, tinytuya="tinytuya==1.20.0")

    ud.apply([("tinytuya", "1.21.0"), ("tuya-device-sharing-sdk", "0.2.16")], paths)

    requirements, pyproject = (p.read_text() for p in paths)
    assert requirements == "tuya-device-sharing-sdk>=0.2.16\nqrcode[pil]>=7.4\ntinytuya==1.21.0\n"
    assert '"tuya-device-sharing-sdk>=0.2.16",' in pyproject and '"tinytuya==1.21.0",' in pyproject
    assert '"qrcode[pil]>=7.4",' in pyproject, "the rest stays as it was"


def test_apply_refuses_a_file_that_doesnt_ask_for_the_package_once(tmp_path):
    requirements, pyproject = files(tmp_path)
    pyproject.write_text(pyproject.read_text().replace('"tinytuya>=1.20",', ""))

    with pytest.raises(ud.UpdateError, match="pyproject.toml should ask for tinytuya once"):
        ud.apply([("tinytuya", "1.21.0")], (requirements, pyproject))


def test_files_that_disagree_are_refused(tmp_path):
    requirements, pyproject = files(tmp_path)
    requirements.write_text(requirements.read_text().replace("tinytuya>=1.20", "tinytuya>=1.19"))

    with pytest.raises(ud.UpdateError, match="different versions of tinytuya"):
        ud.declared("tinytuya", (requirements, pyproject))


@pytest.mark.parametrize("arg", [
    "requests==2.32.0",              # not one of the two
    "tinytuya==1.21.0rc1",           # a pre-release
    "tinytuya==1.21.0;rm -rf /",     # anything that isn't a version
    "tinytuya>=1.21.0",
    "tinytuya==",
])
def test_only_the_two_libraries_at_a_final_release_are_accepted(arg):
    with pytest.raises(ud.UpdateError):
        ud.pins([arg])


def test_verify_says_which_installed_version_is_wrong(monkeypatch):
    installed = {"tinytuya": "1.20.0", "tuya-device-sharing-sdk": "0.2.16"}
    monkeypatch.setattr("importlib.metadata.version", installed.get)

    ud.verify([("tuya-device-sharing-sdk", "0.2.16")])
    with pytest.raises(ud.UpdateError, match="tinytuya 1.20.0 instead of 1.21.0"):
        ud.verify([("tinytuya", "1.21.0")])


# --------------------------------------------------------------------------- #
# Finding updates
# --------------------------------------------------------------------------- #
def test_check_lists_only_newer_releases(tmp_path):
    paths = files(tmp_path)

    found = ud.updates(paths, newest(tinytuya="1.21.0", tuya_device_sharing_sdk="0.2.15"))

    assert found == [("tinytuya", ">=", "1.20", "1.21.0")]
    assert ud.updates(paths, newest(tinytuya="1.20.0", tuya_device_sharing_sdk="0.2.15")) == [], \
        "1.20.0 is the 1.20 asked for"


def test_check_leaves_out_what_a_pull_request_already_proposed(tmp_path, capsys):
    paths = files(tmp_path)
    releases = newest(tinytuya="1.21.0", tuya_device_sharing_sdk="0.2.16")

    open_pr = ud.check("dependency-update", paths, releases, lambda branch: (
        {"Update tuya-device-sharing-sdk to 0.2.16 and tinytuya to 1.21.0"}, set()))
    declined = ud.check("dependency-update", paths, releases, lambda branch: (
        set(), {("tinytuya", "1.21.0")}))

    assert open_pr == []
    assert declined == [("tuya-device-sharing-sdk", ">=", "0.2.15", "0.2.16")]
    out = capsys.readouterr().out
    assert "Already proposed" in out and "closed without merging" in out


def test_open_and_declined_pull_requests_are_told_apart(monkeypatch):
    listing = [
        {"title": "Update tinytuya to 1.22.0", "state": "OPEN"},
        {"title": "Update tinytuya to 1.21.0 and tuya-device-sharing-sdk to 0.2.16", "state": "CLOSED"},
        {"title": "Update tinytuya to 1.20.0", "state": "MERGED"},
    ]
    monkeypatch.setattr(ud.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout=json.dumps(listing)))

    open_titles, declined = ud.proposed("dependency-update")

    assert open_titles == {"Update tinytuya to 1.22.0"}
    assert declined == {("tinytuya", "1.21.0"), ("tuya-device-sharing-sdk", "0.2.16")}


def test_the_workflow_gets_the_updates_a_title_and_a_body(tmp_path):
    output = tmp_path / "output"
    found = [("tuya-device-sharing-sdk", ">=", "0.2.15", "0.2.16"), ("tinytuya", ">=", "1.20", "1.21.0")]

    ud.write_outputs(found, output)

    text = output.read_text()
    assert "updates=tuya-device-sharing-sdk==0.2.16 tinytuya==1.21.0\n" in text
    assert "title=Update tuya-device-sharing-sdk to 0.2.16 and tinytuya to 1.21.0\n" in text
    body = ud.body(found)
    assert body in text and "`>=0.2.15` | `>=0.2.16`" in body
    assert ";" not in body and "—" not in body, "the house style for text people read"


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #
def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(job):
    return _workflow()["jobs"][job]["steps"]


def test_the_workflow_never_pushes_an_image():
    builds = [step for job in _workflow()["jobs"] for step in _steps(job)
              if str(step.get("uses", "")).startswith("docker/build-push-action")]

    assert builds and all(step["with"]["push"] is False for step in builds)
    assert not any("docker/login-action" in str(step.get("uses", ""))
                   for job in _workflow()["jobs"] for step in _steps(job))


def test_new_releases_only_run_where_nothing_can_be_written():
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {}
    assert jobs["check"]["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert jobs["test"]["permissions"] == {"contents": "read"}
    for job in ("check", "test"):
        assert _steps(job)[0]["with"]["persist-credentials"] is False
    # The job that can write never installs or runs anything new.
    commands = " ".join(step.get("run", "") for step in _steps("pull-request"))
    assert "pip" not in commands and "pytest" not in commands and "docker" not in commands
    assert jobs["pull-request"]["needs"] == ["check", "test"]


def test_the_workflow_runs_daily_and_commits_as_the_bot():
    workflow = _workflow()
    schedule = workflow[True]["schedule"][0]["cron"]   # YAML 1.1 reads "on" as true
    commands = " ".join(step.get("run", "") for step in _steps("pull-request"))

    minute, hour, *rest = schedule.split()
    assert rest == ["*", "*", "*"] and minute.isdigit() and hour.isdigit()
    assert 'user.name "Developer Insider Bot"' in commands
    assert 'user.email "bot@developerinsider.co"' in commands
