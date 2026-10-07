"""Structural tests: the target directory tree exists on disk."""

from conftest import (
    FIXTURES_DIR,
    HARDENED_TREE,
    ROOT,
    TESTS_DIR,
)


def test_package_dir_exists():
    assert (ROOT / "pengyos_hardening").is_dir(), "pengyos_hardening/ must exist"


def test_tests_dir_exists():
    assert TESTS_DIR.is_dir(), "tests/ must exist"


def test_pyproject_present():
    assert (ROOT / "pyproject.toml").is_file(), "pyproject.toml must exist"


def test_fixtures_dir_exists():
    assert FIXTURES_DIR.is_dir(), "tests/fixtures/ must exist"


def test_hardened_tree_exists():
    assert HARDENED_TREE.is_dir(), "the hardened fixture tree must exist"


def test_readme_present():
    assert (ROOT / "README.md").is_file(), "README.md must exist"


def test_changelog_present():
    assert (ROOT / "CHANGELOG.md").is_file(), "CHANGELOG.md must exist"


def test_license_present():
    assert (ROOT / "LICENSE").is_file(), "LICENSE must exist"


def test_gitignore_present():
    assert (ROOT / ".gitignore").is_file(), ".gitignore must exist"
