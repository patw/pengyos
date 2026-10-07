"""Scaffold tests for pengyos-cosmic-recipes.

Asserts the repo is importable as a package with the expected shape (package
dir, ``pyproject.toml`` metadata) and that the GitHub-ready docs exist and are
non-empty. The fuller GitHub-ready contract (LICENSE text, CHANGELOG version,
README cross-links) is covered by ``test_github_ready`` in a later iteration.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pengyos_cosmic_recipes

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


def test_package_imports():
    """The ``pengyos_cosmic_recipes`` package imports from the repo root."""
    assert pengyos_cosmic_recipes.__version__ == "0.1.0"


def test_package_has_init():
    """The package directory holds an ``__init__.py``."""
    assert (ROOT / "pengyos_cosmic_recipes" / "__init__.py").is_file()


def test_pyproject_metadata():
    """``pyproject.toml`` declares this project with the expected identity."""
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file()
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["name"] == "pengyos-cosmic-recipes"
    assert data["project"]["version"] == "0.1.0"
    # stdlib-only at test time.
    assert data["project"]["dependencies"] == []


def test_github_ready_docs_present_nonempty():
    """The four GitHub-ready docs exist and are non-empty at scaffold time."""
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        path = ROOT / name
        assert path.is_file(), f"{name} is missing"
        assert path.stat().st_size > 0, f"{name} is empty"
