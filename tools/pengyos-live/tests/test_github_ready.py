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
* the README starts with a top-level ``# `` heading (it renders as the
  repository title on GitHub);
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

from pengyos_live import __version__

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


def test_license_year_tracks_newest_changelog_year():
    """The copyright year in the LICENSE must not fall behind the newest
    CHANGELOG year — a repo whose docs advance past the copyright year is a
    tell-tale sign of an unkept GitHub-ready tree."""
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    license_year = int(re.search(r"Copyright \(c\) (\d{4})", license_text).group(1))
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    years = [
        int(y)
        for y in re.findall(r"^## \[\d+\.\d+\.\d+\] - (\d{4})", changelog, re.MULTILINE)
    ]
    assert years, "no '## [x.y.z] - YYYY' sections in CHANGELOG"
    assert max(years) <= license_year, (
        f"CHANGELOG newest year {max(years)} is past the LICENSE copyright year "
        f"{license_year} — bump the copyright year"
    )


# --- README -----------------------------------------------------------------


def test_readme_has_top_level_heading():
    """A README without a top-level ``# `` heading renders poorly on GitHub;
    every GitHub-ready repo ships one."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    assert first_line.startswith("# "), (
        "README.md should start with a top-level '# ' heading (it renders as the "
        "repository title on GitHub)"
    )


def test_readme_documents_the_tool():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pengyos-live" in text
    # every documented subcommand is in the usage section
    for name in ("emit", "lint"):
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


# --- build metadata (the sdist/wheel must build on GitHub Actions) -------------


def test_pyproject_readme_field_points_to_existing_file():
    """``pyproject.toml`` declares the README as the project readme; the file
    it points to must exist or the build fails — a GitHub-ready tree must build."""
    try:
        import tomllib  # stdlib, Python 3.11+
    except ImportError:
        pytest.skip("tomllib not available (Python < 3.11)")

    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        cfg = tomllib.load(fh)
    readme_path = cfg["project"].get("readme")
    assert readme_path, "pyproject.toml does not declare a project 'readme'"
    assert (REPO_ROOT / readme_path).is_file(), (
        f"pyproject.toml readme {readme_path!r} does not exist — the build would fail"
    )


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
