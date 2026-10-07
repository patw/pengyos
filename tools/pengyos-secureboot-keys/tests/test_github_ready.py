"""GitHub-ready contract.

Verifies the repository carries everything the spec's GitHub-ready contract
requires: `README.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"),
`CHANGELOG.md`, `.gitignore`, a `pyproject.toml` that declares the
`pengyos-sbkeys` script, an importable package and a non-empty test suite.

This test itself is part of that contract, so it must stay green.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore"]


def _repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return _repo_root()


def test_required_files_present(repo_root: Path):
    for name in REQUIRED_FILES:
        path = repo_root / name
        assert path.is_file(), f"{name} is missing"
        assert path.stat().st_size > 0, f"{name} is empty"


def test_pyproject_declares_console_script(repo_root: Path):
    pyproject = repo_root / "pyproject.toml"
    assert pyproject.is_file()
    text = pyproject.read_text(encoding="utf-8")
    assert "[project.scripts]" in text
    assert "pengyos-sbkeys" in text
    assert "pengyos_sbkeys.cli:main" in text


def test_license_is_mit_with_copyright(repo_root: Path):
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


def test_changelog_has_entries(repo_root: Path):
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"^## \[", changelog, re.MULTILINE) is not None


def test_gitignore_covers_python_and_keys(repo_root: Path):
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert "__pycache__" in gitignore
    # Real key material must never be committed.
    assert "/keys/" in gitignore


def test_package_is_importable():
    from pengyos_sbkeys import __version__  # noqa: F401

    assert __version__


def test_test_suite_is_nonempty(repo_root: Path):
    tests_dir = repo_root / "tests"
    assert tests_dir.is_dir()
    files = list(tests_dir.glob("test_*.py"))
    assert files, "no test_*.py files found under tests/"


def test_readme_documents_all_subcommands(repo_root: Path):
    # Docs are authored and kept in sync: every documented command appears in
    # the README so a reader can discover the interface.
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    for command in ("init", "enroll-commands", "sign"):
        assert command in readme, f"README does not document the {command} command"


def test_readme_documents_security_posture(repo_root: Path):
    # The core security contract must be documented: no key leak + perm refusal.
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    lowered = readme.lower()
    assert "key material" in lowered or "key" in lowered
    assert "permission" in lowered or "0600" in lowered
    assert (
        "refus" in lowered or "fails closed" in lowered or "refuse" in lowered
    ), "README should document the perm-refusal / fail-closed behaviour"


def test_changelog_has_unreleased_and_versioned_sections(repo_root: Path):
    # A Keep-a-Changelog style file: an [Unreleased] section plus at least one
    # released version section.
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"^## \[Unreleased\]", changelog, re.MULTILINE) is not None
    assert (
        re.search(r"^## \[\d+\.\d+\.\d+\]", changelog, re.MULTILINE) is not None
    ), "CHANGELOG must contain at least one versioned section"
