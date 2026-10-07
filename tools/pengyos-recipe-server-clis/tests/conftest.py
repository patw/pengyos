"""Shared pytest fixtures for the PengyOS recipe-server-clis suite.

All paths are resolved relative to this file so the suite runs from any
working directory and needs no network, root, display, or real hardware.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT
