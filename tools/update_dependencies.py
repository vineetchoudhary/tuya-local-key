#!/usr/bin/env python3
"""Look for new releases of tinytuya and tuya-device-sharing-sdk, and move to them.

They are the two libraries everything here is built on: the SDK logs in and
lists the devices, and tinytuya talks to them on the local network. The Update
dependencies workflow (.github/workflows/dependency-update.yml) runs this every
day.

    python tools/update_dependencies.py check
        Lists each one with a release newer than requirements.txt asks for.
        With --skip-proposed BRANCH, it leaves out what a pull request from
        BRANCH already proposes, or proposed and was closed without merging.
        In GitHub Actions it also writes the outputs the workflow needs.

    python tools/update_dependencies.py apply tinytuya==1.21.0 ...
        Moves those packages to those versions in requirements.txt and
        pyproject.toml. Each keeps its operator: >= stays a minimum, and ==
        stays a pin.

    python tools/update_dependencies.py verify tinytuya==1.21.0 ...
        Fails unless those are the versions installed.

apply and verify use only the standard library, so they also run inside the
app's Docker image.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (ROOT / "requirements.txt", ROOT / "pyproject.toml")
WATCHED = ("tuya-device-sharing-sdk", "tinytuya")
# Final releases only: pip never picks a pre-release here, and nothing else
# gets near a file or a shell.
VERSION = re.compile(r"\d+(\.\d+)*(\.post\d+)?")


class UpdateError(Exception):
    """Something this can't update safely. The message says what."""


def _spec(name):
    # "tinytuya>=1.20" at the start of a requirements.txt line, or inside
    # pyproject.toml's quoted dependency list.
    return re.compile(rf'(?m)(?:^|(?<=")){re.escape(name)}(\s*(?:==|>=|~=)\s*)([^\s",;#]+)')


def declared(name, files=FILES):
    """(operator, version) for name, which the files must agree on."""
    found = set()
    for path in files:
        matches = _spec(name).findall(path.read_text())
        if len(matches) != 1:
            raise UpdateError(f"{path.name} should ask for {name} once, not {len(matches)} times.")
        operator, version = matches[0]
        found.add((operator.strip(), version))
    if len(found) != 1:
        raise UpdateError(f"The files ask for different versions of {name}: {sorted(found)}.")
    return found.pop()


def latest(name):
    """The newest release pip would install for this Python: no pre-releases,
    nothing yanked, and nothing that needs a newer Python."""
    report = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--dry-run", "--no-deps", "--ignore-installed",
         "--quiet", "--disable-pip-version-check", "--report", "-", name],
        check=True, capture_output=True, text=True,
    ).stdout
    version = json.loads(report)["install"][0]["metadata"]["version"]
    if not VERSION.fullmatch(version):
        raise UpdateError(f"pip reports {name} {version!r}, which isn't a final release.")
    return version


def _version(text):
    try:
        from packaging.version import Version
    except ImportError:   # pip carries its own copy
        from pip._vendor.packaging.version import Version
    return Version(text)


def updates(files=FILES, newest=latest):
    """[(name, operator, current, new)] for each package with a newer release."""
    found = []
    for name in WATCHED:
        operator, current = declared(name, files)
        new = newest(name)
        if _version(new) > _version(current):
            found.append((name, operator, current, new))
    return found


def proposed(branch):
    """(titles of open pull requests, (name, version) pairs declined) from the
    pull requests this workflow opened from branch before."""
    listing = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "100",
         "--json", "title,state"],
        check=True, capture_output=True, text=True,
    ).stdout
    open_titles, declined = set(), set()
    for pr in json.loads(listing):
        if pr["state"] == "OPEN":
            open_titles.add(pr["title"])
        elif pr["state"] == "CLOSED":   # closed without merging: not wanted
            declined |= set(re.findall(r"([\w.-]+) to ([\w.]+)", pr["title"]))
    return open_titles, declined


def title(found):
    return "Update " + " and ".join(f"{name} to {new}" for name, _, _, new in found)


def body(found):
    rows = "\n".join(
        f"| [{name}](https://pypi.org/project/{name}/{new}/) | `{operator}{current}` | `{operator}{new}` |"
        for name, operator, current, new in found
    )
    return (
        "New releases of the libraries this app is built on.\n\n"
        "| Package | Now | Proposed |\n"
        "|---|---|---|\n"
        f"{rows}\n\n"
        "Before opening this, the Update dependencies workflow ran with these versions:\n\n"
        "- the full test suite, browser tests included\n"
        "- a Docker build for amd64 and arm64, without pushing it\n"
        "- a run of the amd64 image: it starts, serves the page, and loads both libraries\n\n"
        "Nothing was published. Merging this doesn't release it either: that still takes a version tag.\n"
    )


def write_outputs(found, path):
    delimiter = f"EOF_{uuid.uuid4().hex}"
    with open(path, "a", encoding="utf-8") as out:
        out.write(f"updates={' '.join(f'{name}=={new}' for name, _, _, new in found)}\n")
        out.write(f"title={title(found) if found else ''}\n")
        out.write(f"body<<{delimiter}\n{body(found) if found else ''}\n{delimiter}\n")


def check(branch=None, files=FILES, newest=latest, pull_requests=proposed):
    found = updates(files, newest)
    if found and branch:
        open_titles, declined = pull_requests(branch)
        for name, _, _, new in found:
            if (name, new) in declined:
                print(f"{name} {new}: a pull request proposed it and was closed without merging.")
        found = [u for u in found if (u[0], u[3]) not in declined]
        if found and title(found) in open_titles:
            print(f"Already proposed: {title(found)}")
            found = []
    for name, operator, current, new in found:
        print(f"{name}: {operator}{current} -> {operator}{new}")
    if not found:
        print("Nothing to update.")
    return found


def pins(args):
    """[(name, version)] from "name==version" arguments, checked."""
    parsed = []
    for arg in args:
        name, _, version = arg.partition("==")
        if name not in WATCHED or not VERSION.fullmatch(version):
            raise UpdateError(f"{arg!r} isn't one of {', '.join(WATCHED)} at a final release.")
        parsed.append((name, version))
    return parsed


def apply(wanted, files=FILES):
    for path in files:
        text = path.read_text()
        for name, version in wanted:
            text, count = _spec(name).subn(lambda m: f"{name}{m.group(1)}{version}", text)
            if count != 1:
                raise UpdateError(f"{path.name} should ask for {name} once, not {count} times.")
        path.write_text(text)
    for name, version in wanted:
        print(f"{name}: now {''.join(declared(name, files))}")


def verify(wanted):
    from importlib.metadata import version as installed

    wrong = [f"{name} {installed(name)} instead of {version}"
             for name, version in wanted if installed(name) != version]
    if wrong:
        raise UpdateError("Not the versions wanted: " + ", ".join(wrong) + ".")
    print("Installed: " + ", ".join(f"{name} {version}" for name, version in wanted) + ".")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    look = commands.add_parser("check", help="list the packages with a newer release")
    look.add_argument("--skip-proposed", metavar="BRANCH",
                      help="leave out what pull requests from BRANCH proposed")
    for command in ("apply", "verify"):
        commands.add_parser(command).add_argument("pins", nargs="+", metavar="NAME==VERSION")
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            found = check(args.skip_proposed)
            if os.environ.get("GITHUB_OUTPUT"):
                write_outputs(found, os.environ["GITHUB_OUTPUT"])
        elif args.command == "apply":
            apply(pins(args.pins))
        else:
            verify(pins(args.pins))
    except UpdateError as e:
        sys.exit(f"update_dependencies: {e}")


if __name__ == "__main__":
    main()
