"""GitHub-ready contract.

Asserts that the repository is ready to push to GitHub at any commit:

* ``README.md``, ``LICENSE``, ``CHANGELOG.md`` and ``.gitignore`` exist and are
  non-empty *and* tracked by git (a file that exists on disk but is untracked is
  not "GitHub-ready" — it would surface in `git status` as untracked);
* ``LICENSE`` is the MIT license, "Copyright (c) 2026 Pat Wendorf";
* the README documents the recipe and cross-links ``spec.md``;
* ``CHANGELOG.md`` is Keep-a-Changelog style and its NEWEST
  ``## [x.y.z] - YYYY-MM-DD`` section matches the project version declared in
  ``pyproject.toml`` (a version bump without a changelog section fails here —
  docs stay in sync);
* ``.gitignore`` covers the Python build/test artifacts;
* the FULL test suite is green — asserted by an opt-in ``PENGYOS_IT=1`` test
  that re-runs ``pytest -q`` in a subprocess. The ordinary loop already runs
  ``pytest -q`` every iteration, so the recursive run is off by default (and
  the child process clears ``PENGYOS_IT`` so it can never recurse).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

GITHUB_READY_FILES = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore")
GITIGNORE_PATTERNS = (
    "__pycache__/",
    "*.py[cod]",
    "*.egg-info/",
    ".pytest_cache/",
    "build/",
    "dist/",
)


def _project_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _git_tracked(relpath: str) -> bool:
    """Return True when ``relpath`` (repo-root relative) is tracked by git.

    A file that merely exists on disk is not "GitHub-ready" if it is untracked:
    it would show up in `git status` as untracked and would not be pushed. This
    closes the gap where the existence checks pass but the file was never
    committed.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relpath],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        # not a git checkout (e.g. the verifier unpacks the tarball) — the
        # on-disk existence checks below still apply
        return False
    return proc.returncode == 0


# --- the four files exist and are non-empty ---------------------------------


def test_github_ready_files_exist_and_nonempty():
    for name in GITHUB_READY_FILES:
        path = REPO_ROOT / name
        assert path.is_file(), f"missing GitHub-ready file: {name}"
        assert path.read_text(encoding="utf-8").strip(), f"{name} is empty"


def test_github_ready_files_are_tracked_by_git():
    """The four GitHub-ready files must be committed, not just present on disk.

    A file that exists on disk but is untracked is not "GitHub-ready" — it would
    show up in `git status` as untracked and would not be pushed. This closes the
    gap where the existence checks pass but the file was never committed.
    """
    for name in GITHUB_READY_FILES:
        assert _git_tracked(name), f"{name} exists but is not tracked by git (not GitHub-ready)"


# --- LICENSE ----------------------------------------------------------------


def test_license_is_mit_pat_wendorf():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.strip().startswith("MIT License")
    assert "Copyright (c) 2026 Pat Wendorf" in text
    # the standard MIT warranty disclaimer must be present
    assert "AS IS" in text


# --- README -----------------------------------------------------------------


def test_readme_documents_the_recipe():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "dbus-broker" in text
    assert "package/dbus-broker" in text
    # docs cross-link the spec and the task breakdown
    assert "spec.md" in text


# --- CHANGELOG ----------------------------------------------------------------


def test_changelog_is_keep_a_changelog():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.lstrip().startswith("# Changelog")
    assert "Keep a Changelog" in text


def test_changelog_newest_section_matches_version():
    """The newest `## [x.y.z] - YYYY-MM-DD` section must equal the project version."""
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(
        r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$",
        text,
        re.MULTILINE,
    )
    assert versions, "no '## [x.y.z] - YYYY-MM-DD' version sections found"
    assert versions[0] == _project_version(), (
        f"newest CHANGELOG section {versions[0]!r} != project version "
        f"{_project_version()!r} — bump both together"
    )
    # Keep a Changelog: latest changes on top — versions appear in
    # non-increasing order
    tuples = [tuple(int(p) for p in v.split(".")) for v in versions]
    assert tuples == sorted(tuples, reverse=True), (
        f"CHANGELOG sections are not in newest-first order: {versions}"
    )


# --- .gitignore ---------------------------------------------------------------


def test_gitignore_covers_python_artifacts():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in GITIGNORE_PATTERNS:
        assert pattern in text, f".gitignore does not cover {pattern!r}"


# --- full green suite (opt-in integration check) -------------------------------


@pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1",
    reason="full-suite re-run is an integration check (set PENGYOS_IT=1)",
)
def test_full_test_suite_is_green():
    """Re-run the whole suite in a subprocess; it must exit 0."""
    env = {**os.environ, "PENGYOS_IT": ""}  # child skips this test: no recursion
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    assert proc.returncode == 0, (
        f"full test suite is NOT green (exit {proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
