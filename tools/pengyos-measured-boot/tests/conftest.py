"""Shared pytest fixtures for the pengyos-measured-boot test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root (the parent of ``tests/``)."""

    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def integration(monkeypatch: "pytest.MonkeyPatch") -> bool:
    """Return True iff integration tests are enabled (``PENGYOS_IT=1``).

    Integration tests should guard on this and skip otherwise.
    """

    return os.environ.get("PENGYOS_IT") == "1"
