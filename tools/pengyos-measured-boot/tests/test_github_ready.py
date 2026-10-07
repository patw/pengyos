"""Assert the GitHub-ready contract (see spec.md).

The suite must be green with these files present and populated. The contract
from spec.md is:

* README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
  ``.gitignore`` present and populated.
* A full green test suite (non-empty).
* Docs are authored and kept in sync with each other and with ``pyproject.toml``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


EXPECTED_COPYRIGHT = "Copyright (c) 2026 Pat Wendorf"
PROJECT_NAME = "pengyos-measured-boot"


def test_github_ready(repo_root: Path) -> None:
    required = ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"]
    missing = [name for name in required if not (repo_root / name).is_file()]
    assert not missing, f"missing GitHub-ready files: {missing}"


def test_license_has_expected_copyright(repo_root: Path) -> None:
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")
    assert "MIT" in license_text
    assert EXPECTED_COPYRIGHT in license_text


def test_readme_not_empty(repo_root: Path) -> None:
    text = (repo_root / "README.md").read_text(encoding="utf-8").strip()
    assert text, "README.md is empty"


def test_changelog_has_entries(repo_root: Path) -> None:
    text = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [" in text, "CHANGELOG.md has no versioned entries"


def test_pyproject_declares_entry_point(repo_root: Path) -> None:
    import tomllib

    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["pengyos-measured-boot"] == "pengyos_measured_boot.cli:main"


# --- docs are authored and kept in sync ------------------------------------


def test_readme_documents_tool_and_subcommands(repo_root: Path) -> None:
    # The README is authored (not a placeholder): it names the tool and both
    # subcommands from the spec interface (``run`` / ``verify``).
    text = (repo_root / "README.md").read_text(encoding="utf-8")
    assert PROJECT_NAME in text
    assert "run" in text and "verify" in text


def test_readme_license_line_matches_license(repo_root: Path) -> None:
    # The README's own license line must agree with the LICENSE file.
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")
    assert "MIT" in readme, "README should state the license"
    assert EXPECTED_COPYRIGHT in readme, "README license line must state the copyright"
    assert "MIT" in license_text and EXPECTED_COPYRIGHT in license_text


def test_pyproject_version_matches_module(repo_root: Path) -> None:
    # pyproject version and the runtime package version must agree.
    import tomllib

    from pengyos_measured_boot import __version__

    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_version = data["project"]["version"]
    assert pyproject_version == __version__, (
        f"pyproject version {pyproject_version!r} != package version {__version__!r}"
    )


def test_changelog_latest_version_matches_pyproject(repo_root: Path) -> None:
    # The newest versioned CHANGELOG entry must match pyproject's version.
    import tomllib

    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_version = data["project"]["version"]

    # Keep a Changelog lists Unreleased first, then newest-first, so the first
    # numeric versioned heading is the latest release.
    matches = re.findall(r"## \[(\d+\.\d+\.\d+)\]", changelog)
    assert matches, "CHANGELOG has no versioned entries"
    latest_release = matches[0]
    assert latest_release == pyproject_version, (
        f"latest CHANGELOG release {latest_release!r} != pyproject version {pyproject_version!r}"
    )


def test_test_suite_is_nonempty(repo_root: Path) -> None:
    # The "full green test suite" part of the contract is a non-empty suite:
    # at least one test module, with at least one test function.
    test_files = sorted(repo_root.glob("tests/test_*.py"))
    assert test_files, "no test modules under tests/"
    total_tests = 0
    for path in test_files:
        total_tests += len(re.findall(r"^def (test_|_test_)", path.read_text(encoding="utf-8"), re.M))
    assert total_tests > 0, "the test suite has no test functions"
