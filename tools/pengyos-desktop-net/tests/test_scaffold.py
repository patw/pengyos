"""Scaffold tests for pengyos-desktop-net.

Asserts the repo is importable as a package with the expected shape (package
dir, ``pyproject.toml`` metadata) and that the GitHub-ready docs exist and are
non-empty. The fuller GitHub-ready contract (LICENSE text, CHANGELOG version,
README cross-links) is covered by ``test_github_ready`` in a later iteration.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pengyos_desktop_net

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


def test_package_imports():
    """The ``pengyos_desktop_net`` package imports from the repo root."""
    assert pengyos_desktop_net.__version__ == "0.5.0"


def test_package_has_modules():
    """The package ships the emit and lint lanes."""
    from pengyos_desktop_net import emit  # noqa: F401
    from pengyos_desktop_net import lint  # noqa: F401
    from pengyos_desktop_net import profile  # noqa: F401


def test_package_has_init():
    """The package directory holds an ``__init__.py``."""
    assert (ROOT / "pengyos_desktop_net" / "__init__.py").is_file()


def test_pyproject_metadata():
    """``pyproject.toml`` declares this project with the expected identity."""
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file()
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["name"] == "pengyos-desktop-net"
    assert data["project"]["version"] == "0.5.0"
    # stdlib-only at test time.
    assert data["project"]["dependencies"] == []
    assert data["project"]["license"]["text"] == "MIT"


def test_github_ready_docs_present_nonempty():
    """The four GitHub-ready docs exist and are non-empty at scaffold time."""
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        path = ROOT / name
        assert path.is_file(), f"{name} is missing"
        assert path.stat().st_size > 0, f"{name} is empty"
