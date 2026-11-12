"""Exercise the local runner without installing packages or changing Docker."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
PYTHON_STUB = '''
import json, os, sys
from pathlib import Path

VERSION = {version!r}
IS_VENV = {venv!r}
args = sys.argv[1:]
with open(os.environ["RUNNER_LOG"], "a") as log:
    log.write(json.dumps({{"kind": "python", "path": sys.argv[0], "args": args}}) + "\\n")
if args[0] == "-c":
    sys.version_info = VERSION + (0, "final", 0)
    sys.prefix = "/test-venv" if IS_VENV else sys.base_prefix
    exec(args[1])
elif args == ["--version"]:
    print("Python %s.%s.0" % VERSION)
elif args[:2] == ["-m", "venv"]:
    target = Path(args[2]) / "bin" / "python"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).read_text().replace("IS_VENV = " + repr(False), "IS_VENV = " + repr(True))
    target.write_text(source)
    target.chmod(0o755)
elif args[:2] == ["-m", "pytest"]:
    sys.exit(int(os.environ.get("RUNNER_PYTEST_EXIT", "0")))
elif args[:2] != ["-m", "pip"]:
    raise AssertionError(args)
'''


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)
    return path


def python_at(path, version=(3, 12), venv=False):
    return executable(path, PYTHON_STUB.format(version=version, venv=venv))


@pytest.fixture
def runner(tmp_path):
    project = tmp_path / "local project"
    (project / "tests").mkdir(parents=True)
    shutil.copyfile(ROOT / "tests" / "test.sh", project / "tests" / "test.sh")
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "dirname").symlink_to(shutil.which("dirname"))
    log = tmp_path / "commands.jsonl"
    executable(commands / "docker", '''
import json, os, sys
with open(os.environ["RUNNER_LOG"], "a") as log:
    log.write(json.dumps({"kind": "docker", "args": sys.argv[1:]}) + "\\n")
sys.exit(1 if sys.argv[1:3] in (["container", "inspect"], ["image", "inspect"]) else 0)
''')
    for command in ("curl", "open"):
        executable(commands / command, "pass\n")
    env = dict(os.environ, PATH=str(commands), PYTHON_BIN="", RUNNER_LOG=str(log))
    return SimpleNamespace(project=project, commands=commands, log=log, env=env)


def run(runner, *args, **env):
    result = subprocess.run(
        ["/bin/bash", str(runner.project / "tests" / "test.sh"), *args],
        env=dict(runner.env, **env), capture_output=True, text=True, timeout=15,
    )
    calls = [json.loads(line) for line in runner.log.read_text().splitlines()] if runner.log.exists() else []
    return result, calls


def test_old_project_and_system_python_use_a_supported_python_in_a_test_environment(runner):
    old = python_at(runner.project / ".venv" / "bin" / "python", (3, 9), venv=True)
    original = old.read_text()
    python_at(runner.commands / "python3", (3, 9))
    python_at(runner.commands / "python3.12")

    result, calls = run(runner)

    assert result.returncode == 0, result.stderr
    test_python = runner.project / ".venv-test" / "3.12" / "bin" / "python"
    installs = [call for call in calls if call["kind"] == "python" and call["args"][:2] == ["-m", "pip"]]
    tests = [call for call in calls if call["kind"] == "python" and call["args"][:2] == ["-m", "pytest"]]
    assert len(installs) == len(tests) == 1
    assert installs[0]["path"] == tests[0]["path"] == str(test_python)
    assert "Using Python 3.12" in result.stdout
    assert old.read_text() == original


@pytest.mark.parametrize("explicit", [False, True])
def test_compatible_virtual_environment_is_used_directly(runner, explicit):
    project_python = python_at(runner.project / ".venv" / "bin" / "python", venv=True)
    chosen = python_at(runner.project / "chosen-env" / "bin" / "python", (3, 14), venv=True) if explicit else project_python

    result, calls = run(runner, **({"PYTHON_BIN": str(chosen)} if explicit else {}))

    assert result.returncode == 0, result.stderr
    install = next(call for call in calls if call["kind"] == "python" and call["args"][:2] == ["-m", "pip"])
    assert install["path"] == str(chosen)
    assert not (runner.project / ".venv-test").exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_incompatible_python_fails_before_package_installation_or_docker(runner, explicit):
    old = python_at(runner.commands / "python3", (3, 9))

    result, calls = run(runner, **({"PYTHON_BIN": str(old)} if explicit else {}))

    assert result.returncode != 0
    assert "Python 3.10 or newer is required" in result.stderr
    assert not [call for call in calls if call["kind"] == "docker" or call["args"][:1] == ["-m"]]


def test_skip_tests_needs_no_python(runner):
    result, calls = run(runner, "--skip-tests", PYTHON_BIN="missing-python")

    assert result.returncode == 0, result.stderr
    assert not [call for call in calls if call["kind"] == "python"]
    assert any(call["args"][:1] == ["build"] for call in calls)
    assert any(call["args"][:1] == ["run"] for call in calls)


def test_failed_tests_leave_docker_untouched(runner):
    python_at(runner.project / ".venv" / "bin" / "python", venv=True)

    result, calls = run(runner, RUNNER_PYTEST_EXIT="1")

    assert result.returncode != 0
    assert not [call for call in calls if call["kind"] == "docker"]
