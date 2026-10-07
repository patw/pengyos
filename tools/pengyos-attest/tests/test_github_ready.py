"""GitHub-ready contract.

Asserts that the repository is ready to push to GitHub at any commit:

* ``README.md``, ``LICENSE``, ``CHANGELOG.md`` and ``.gitignore`` exist and
  are non-empty;
* ``LICENSE`` is the MIT license, "Copyright (c) 2026 Pat Wendorf";
* the README names the tool, documents every subcommand, and cross-links
  ``spec.md``;
* ``CHANGELOG.md`` is Keep-a-Changelog style and its NEWEST
  ``## [x.y.z] - YYYY-MM-DD`` section matches the current package version
  (a version bump without a changelog section fails here — docs stay in
  sync);
* ``.gitignore`` covers the Python build/test artifacts;
* the FULL test suite is green — asserted by an opt-in ``PENGYOS_IT=1``
  test that re-runs ``pytest -q`` in a subprocess. The ordinary loop already
  runs ``pytest -q`` every iteration, so the recursive run is off by default
  (and the child process clears ``PENGYOS_IT`` so it can never recurse).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_attest import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent

GITHUB_READY_FILES = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore")


# --- the four files exist and are non-empty ---------------------------------


def test_github_ready_files_exist_and_nonempty():
    for name in GITHUB_READY_FILES:
        path = REPO_ROOT / name
        assert path.is_file(), f"missing GitHub-ready file: {name}"
        assert path.read_text(encoding="utf-8").strip(), f"{name} is empty"


# --- LICENSE ----------------------------------------------------------------


def test_license_is_mit_pat_wendorf():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.strip().startswith("MIT License")
    assert "Copyright (c) 2026 Pat Wendorf" in text
    # the standard MIT warranty disclaimer must be present
    assert "AS IS" in text


# --- README -----------------------------------------------------------------


def test_readme_documents_the_tool():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pengyos-attest" in text
    # every documented subcommand is in the usage section
    for name in ("append", "show", "verify", "export", "sign", "keygen"):
        assert name in text, f"README does not document the {name!r} subcommand"
    # docs cross-link the spec and the task breakdown
    assert "spec.md" in text


# --- CHANGELOG ----------------------------------------------------------------


def test_changelog_is_keep_a_changelog():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.lstrip().startswith("# Changelog")
    assert "Keep a Changelog" in text


def test_changelog_newest_section_matches_version():
    """The newest `## [x.y.z] - YYYY-MM-DD` section must equal __version__."""
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(
        r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$",
        text,
        re.MULTILINE,
    )
    assert versions, "no '## [x.y.z] - YYYY-MM-DD' version sections found"
    assert versions[0] == __version__, (
        f"newest CHANGELOG section {versions[0]!r} != package version "
        f"{__version__!r} — bump both together"
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
    for pattern in (
        "__pycache__/",
        "*.py[cod]",
        "*.egg-info/",
        ".pytest_cache/",
        "build/",
        "dist/",
    ):
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
