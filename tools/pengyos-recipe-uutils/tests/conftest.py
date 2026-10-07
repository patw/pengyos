"""Shared pytest fixtures for the PengyOS uutils-coreutils recipe suite.

All paths are resolved relative to this file so the suite runs from any
working directory and needs no network, root, display, or real hardware.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE_DIR = REPO_ROOT / "package" / "uutils-coreutils"


def read_recipe_file(name: str) -> str:
    """Read a recipe file by name, raising a clear error if it is missing."""
    path = RECIPE_DIR / name
    if not path.is_file():
        raise AssertionError(f"recipe file not found: {path}")
    return path.read_text(encoding="utf-8")


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture
def recipe_dir() -> Path:
    """Absolute path to the Buildroot recipe directory."""
    return RECIPE_DIR
