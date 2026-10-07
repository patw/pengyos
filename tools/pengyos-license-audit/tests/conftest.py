"""Shared pytest fixtures for the PengyOS license-audit suite.

All fixtures are file-system-local and need no network, root, or hardware.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the committed fixtures/ directory."""
    return FIXTURES


@pytest.fixture
def pins_path() -> str:
    """Absolute path to the fixture pins manifest."""
    return str(FIXTURES / "pins.toml")


@pytest.fixture
def policy_path() -> str:
    """Absolute path to the fixture policy file."""
    return str(FIXTURES / "policy.toml")


@pytest.fixture
def run_cli(monkeypatch):
    """Return a callable that invokes the CLI in-process with a clean env.

    The working directory is pinned to the repo root so relative paths in
    fixtures resolve deterministically.
    """
    from pengyos_license_audit.cli import main

    def _run(*args: str) -> int:
        monkeypatch.chdir(REPO_ROOT)
        return main(list(args))

    return _run
