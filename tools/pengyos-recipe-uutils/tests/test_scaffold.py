"""Smoke test for the repository scaffold.

Verifies the structural foundation laid down by the "Scaffold repo" subtask:
the Buildroot recipe directory, the Python project config, and the GitHub-ready
repo metadata files all exist and are well-formed. The detailed GitHub-ready
contract (LICENSE text, green suite, …) is asserted by ``test_github_ready``.
"""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_recipe_dir_exists(recipe_dir: Path) -> None:
    assert recipe_dir.is_dir(), f"missing recipe dir: {recipe_dir}"


def test_pyproject_exists_and_valid(repo_root: Path) -> None:
    pyproject = repo_root / "pyproject.toml"
    assert pyproject.is_file(), "missing pyproject.toml"
    # tomllib ships with Python 3.11+; parse to prove the file is valid TOML.
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    assert "project" in data, "pyproject.toml is missing a [project] table"
    assert data["project"]["name"], "pyproject.toml [project].name is empty"


def test_github_ready_metadata_present(repo_root: Path) -> None:
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        p = repo_root / name
        assert p.is_file(), f"missing repo file: {name}"


def test_license_not_empty_and_mit(repo_root: Path) -> None:
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")
    assert license_text.strip(), "LICENSE is empty"
    assert "MIT" in license_text, "LICENSE does not declare the MIT license"


def test_changelog_uses_keep_a_changelog(repo_root: Path) -> None:
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in changelog or "## [" in changelog, (
        "CHANGELOG.md should follow Keep-a-Changelog (## Unreleased or ## [x.y.z])"
    )
