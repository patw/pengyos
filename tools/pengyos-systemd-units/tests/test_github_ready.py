"""GitHub-ready contract.

The repository must always ship with the files and metadata a consumer expects
to find on GitHub: a README, a correct MIT LICENSE, a CHANGELOG, a `.gitignore`,
and a green test suite (this very file collecting and passing).

These checks are intentionally strict so the "GitHub-ready" invariant can never
rot silently.
"""

from pathlib import Path

import pytest

# Project root = parent of this tests/ directory.
ROOT = Path(__file__).resolve().parent.parent

LICENSE_COPYRIGHT = "Copyright (c) 2026 Pat Wendorf"


def test_readme_present() -> None:
    assert (ROOT / "README.md").is_file(), "README.md is missing"


def test_license_present_and_mit() -> None:
    license_file = ROOT / "LICENSE"
    assert license_file.is_file(), "LICENSE is missing"

    text = license_file.read_text(encoding="utf-8")
    assert "MIT" in text, "LICENSE must be the MIT license"
    assert LICENSE_COPYRIGHT in text, (
        f"LICENSE must carry {LICENSE_COPYRIGHT!r}"
    )


def test_changelog_present() -> None:
    assert (ROOT / "CHANGELOG.md").is_file(), "CHANGELOG.md is missing"


def test_gitignore_present() -> None:
    assert (ROOT / ".gitignore").is_file(), ".gitignore is missing"


def test_pyproject_declares_project() -> None:
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file(), "pyproject.toml is missing"
    assert "pengyos-systemd-units" in pyproject.read_text(encoding="utf-8")


def test_package_layout() -> None:
    pkg = ROOT / "pengyos_systemd_units"
    assert pkg.is_dir(), "package directory pengyos_systemd_units is missing"
    assert (pkg / "__init__.py").is_file(), "package __init__.py is missing"


def test_github_ready_contract() -> None:
    """Aggregated contract check: every artifact a GitHub repo should ship."""
    required = ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore"]
    for name in required:
        assert (ROOT / name).is_file(), f"{name} is missing from the repo"
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "pengyos_systemd_units" / "__init__.py").is_file()
    assert "MIT" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert LICENSE_COPYRIGHT in (ROOT / "LICENSE").read_text(encoding="utf-8")
