"""Shared pytest fixtures for the pengyos-backup test suite.

pytest adds the directory holding ``conftest.py`` to ``sys.path`` (prepend
import mode, no ``tests/__init__.py``), so the package is importable from a
checkout without an editable install. Shared helpers are exposed as fixtures so
test modules avoid cross-file imports.

Integration tests (those that shell out to a real restic/rustic binary) are
opt-in behind ``PENGYOS_IT=1`` and are otherwise skipped, so the default suite
needs no network, root, display, or real hardware.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run the CLI via ``python -m pengyos_backup`` without installing it.

    ``python -m`` puts the working directory on ``sys.path[0]`` so the package
    is importable from a checkout, exercising the console-script entry point
    end-to-end without ``pip install``.
    """

    return subprocess.run(
        [sys.executable, "-m", "pengyos_backup", *args],
        cwd=cwd or str(ROOT),
        capture_output=True,
        text=True,
    )


@pytest.fixture
def run_cli_fn():
    """Return the :func:`run_cli` helper bound to the repo root."""

    return run_cli


@pytest.fixture
def root() -> Path:
    """The repo root (parent of ``tests/``)."""

    return ROOT


@pytest.fixture
def integration() -> bool:
    """True only when integration tests are enabled (PENGYOS_IT=1)."""

    return os.environ.get("PENGYOS_IT") == "1"
