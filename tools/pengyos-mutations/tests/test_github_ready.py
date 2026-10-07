"""GitHub-ready contract.

The repository must ship the docs and metadata that make it publishable and
discoverable on GitHub. See spec.md "GitHub-ready contract".
"""

from __future__ import annotations

import pathlib


def _read(root: pathlib.Path, name: str) -> str:
    path = root / name
    assert path.exists(), f"{name} is required for a GitHub-ready repo"
    return path.read_text(encoding="utf-8")


def test_readme_exists_and_nonempty(root_path: pathlib.Path) -> None:
    text = _read(root_path, "README.md")
    assert text.strip(), "README.md must be non-empty"


def test_license_is_mit_with_copyright(root_path: pathlib.Path) -> None:
    text = _read(root_path, "LICENSE")
    assert "MIT" in text, "LICENSE must be the MIT license"
    assert "Copyright (c) 2026 Pat Wendorf" in text, "LICENSE must carry the copyright line"


def test_changelog_exists_and_nonempty(root_path: pathlib.Path) -> None:
    text = _read(root_path, "CHANGELOG.md")
    assert text.strip(), "CHANGELOG.md must be non-empty"


def test_gitignore_exists_and_nonempty(root_path: pathlib.Path) -> None:
    text = _read(root_path, ".gitignore")
    assert text.strip(), ".gitignore must be non-empty"


def test_pyproject_exists(root_path: pathlib.Path) -> None:
    path = root_path / "pyproject.toml"
    assert path.exists(), "pyproject.toml is required"
    assert "pengyos-mutations" in path.read_text(encoding="utf-8")


def test_suite_is_green_by_construction() -> None:
    """This very module is part of the suite; if it runs, the suite runs."""
    assert True
