"""Test configuration for the pengyos-appimage suite.

Package imports (``from pengyos_appimage import ...``) are handled by the
``pythonpath = ["src"]`` setting in ``pyproject.toml``. This module only
provides reusable fixtures.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_xdg(tmp_path_factory):
    """Point XDG dirs at a temp location so integration helpers never touch
    the real user home during tests."""
    os.environ["XDG_DATA_HOME"] = str(tmp_path_factory.mktemp("xdg_data"))
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path_factory.mktemp("xdg_config"))
    os.environ["XDG_RUNTIME_DIR"] = str(tmp_path_factory.mktemp("xdg_runtime"))


@pytest.fixture
def project_root():
    """Absolute path to the repository root (conftest.py lives at the root)."""
    from pathlib import Path

    return Path(__file__).resolve().parent


@pytest.fixture
def fixtures_dir(project_root):
    """Absolute path to ``tests/fixtures``."""
    return project_root / "tests" / "fixtures"


@pytest.fixture
def sample_appimage(fixtures_dir):
    """Path to the committed sample AppImage fixture (has a poison AppRun)."""
    return fixtures_dir / "sample-appimage"
