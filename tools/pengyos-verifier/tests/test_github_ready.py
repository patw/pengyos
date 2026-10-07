"""GitHub-ready contract (spec.md, "Verification").

Asserts the repo is publishable as-is:

* README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"),
  CHANGELOG.md, and .gitignore all exist and are non-empty;
* the packaging metadata (pyproject.toml) declares the MIT license,
  the console entry point, and a README that exists;
* the docs are kept in sync: the CHANGELOG covers the current
  version, the README names all five checks and the CLI usage, and
  every repo path the README references actually exists;
* the required files are trackable by git (not silently ignored);
* the rest of the test suite is green (run as a subprocess, with
  this file excluded so the assertion does not recurse into itself).

Offline and root-free: a fresh ``git clone`` + ``pip install -e '.[dev]'``
+ ``pytest -q`` must make every test here pass.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import List

import pytest

from pengyos_verifier import __version__

TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_ROOT_FILES = ("README.md", "LICENSE", "CHANGELOG.md",
                       ".gitignore", "pyproject.toml")


def _root(name: str) -> str:
    return os.path.join(TOOL_ROOT, name)


def _read(name: str) -> str:
    with open(_root(name), encoding="utf-8") as fh:
        return fh.read()


# ----------------------------------------------------------------------
# required files exist and are authored (non-empty)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("name", REQUIRED_ROOT_FILES)
def test_required_root_file_exists_and_is_nonempty(name: str):
    path = _root(name)
    assert os.path.isfile(path), f"missing required file: {name}"
    assert os.path.getsize(path) > 0, f"{name} is empty — not authored"


def test_license_is_mit_with_expected_copyright():
    text = _read("LICENSE")
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text
    # The MIT grant and no-warranty disclaimers are the substance of
    # an MIT license — a renamed or truncated file must not pass.
    assert "Permission is hereby granted" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


# ----------------------------------------------------------------------
# packaging metadata
# ----------------------------------------------------------------------

def test_pyproject_declares_mit_license_and_entrypoint():
    import tomllib

    with open(_root("pyproject.toml"), "rb") as fh:
        meta = tomllib.load(fh)
    project = meta["project"]
    assert project["name"] == "pengyos-verifier"
    assert project["version"] == __version__  # lockstep, pinned in test_scaffold
    assert project["license"] == {"text": "MIT"}
    assert "License :: OSI Approved :: MIT License" in project["classifiers"]
    # README is declared as packaging metadata and must exist.
    assert project["readme"] == "README.md"
    assert os.path.isfile(_root(project["readme"]))
    # The console script is the deliverable name from the spec.
    assert project["scripts"]["pengyos-verifier"] == "pengyos_verifier.cli:main"


def test_changelog_covers_current_version():
    """The CHANGELOG must document the version being shipped."""
    text = _read("CHANGELOG.md")
    assert f"[{__version__}]" in text, (
        f"CHANGELOG.md has no entry for the current version "
        f"{__version__} — keep it in sync when bumping")


# ----------------------------------------------------------------------
# docs kept in sync: README
# ----------------------------------------------------------------------

def test_readme_documents_all_checks_and_cli_usage():
    from pengyos_verifier import checks

    text = _read("README.md")
    for check_id in checks.CHECK_IDS:
        assert check_id in text, f"README does not mention check {check_id!r}"
    # The deliverable CLI line, with the required --build argument.
    assert "pengyos-verifier check --build" in text
    # The exit-code contract (spec: 0 pass / 1 fail / 2 usage-IO).
    for code in ("0 = all pass", "1 = any check failed", "2 = usage/IO error"):
        assert code in text, f"README exit-code contract missing: {code!r}"
    # The License section matches the LICENSE file.
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_readme_references_only_existing_repo_paths():
    """Every in-repo path the README points at must exist on disk.

    A docs example that references a moved/renamed file is the
    classic stale-GitHub failure mode; catch it at the prefix level
    (``tests/...`` and ``pengyos_verifier/...``) rather than parsing
    prose.
    """
    text = _read("README.md")
    paths = re.findall(r"(?:tests|pengyos_verifier)(?:/[A-Za-z0-9_.\-]+)+/?",
                       text)
    assert paths, "README references no in-repo paths at all — suspicious"
    missing = sorted({p for p in paths if not os.path.exists(_root(p))})
    assert not missing, (
        f"README references paths that do not exist: {missing}")


# ----------------------------------------------------------------------
# trackability: nothing required is git-ignored
# ----------------------------------------------------------------------

def test_required_files_are_not_git_ignored():
    """A file git ignores never reaches a clone — GitHub must get it.

    Same regression class as the fixture ``build/`` incident: the
    working tree looks complete, but a fresh clone would not be.
    """
    tracked = list(REQUIRED_ROOT_FILES) + [
        "pengyos_verifier/__init__.py",
        "conftest.py",
    ]
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(tracked),
            capture_output=True, text=True, cwd=TOOL_ROOT,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git is not available in this environment")
    if result.returncode not in (0, 1):
        pytest.skip(f"not a git checkout ({result.stderr.strip()})")
    ignored = result.stdout.splitlines()
    assert not ignored, (
        "git ignores required repo files — they would be missing from "
        f"a fresh clone: {ignored}")


# ----------------------------------------------------------------------
# the full green test suite (self-exclusion avoids recursion)
# ----------------------------------------------------------------------

def test_rest_of_suite_is_green():
    """Run the suite (minus this file) and require zero failures.

    The spec's "full green test suite" is asserted from the inside by
    shelling out to ``pytest -q`` on everything else in ``tests/`` —
    this file's own pass is established by the outer run that invoked
    it.
    """
    cmd = [sys.executable, "-m", "pytest", "-q",
           "--ignore=tests/test_github_ready.py"]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=TOOL_ROOT, timeout=600)
    assert result.returncode == 0, (
        "the test suite is not green:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
