"""Test configuration for the pengyos-debx suite.

Package imports (``from pengyos_debx import ...``) are handled by the
``pythonpath = ["src"]`` setting in ``pyproject.toml``. This module only
provides reusable fixtures.
"""

import os

import pytest


@pytest.fixture
def project_root():
    """Absolute path to the tool directory (conftest.py lives at the root)."""
    from pathlib import Path

    return Path(__file__).resolve().parent


@pytest.fixture
def fixtures_dir(project_root):
    """Absolute path to ``tests/fixtures``."""
    return project_root / "tests" / "fixtures"
