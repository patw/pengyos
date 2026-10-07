"""Shared pytest fixtures for the pengyos-cosmic-pkg suite.

All fixtures are file-system-local and need no network, root, or hardware.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    """The repository root (parent of this ``tests/`` directory)."""
    return ROOT


@pytest.fixture
def run_cli(monkeypatch):
    """Return a callable that invokes the CLI in-process with a clean cwd.

    The working directory is pinned to the repo root so fixture paths resolve
    deterministically.
    """
    from pengyos_cosmic_pkg.cli import main

    def _run(*args: str) -> int:
        monkeypatch.chdir(ROOT)
        return main(list(args))

    return _run


@pytest.fixture
def cargo_toml(tmp_path: Path) -> Path:
    """A tiny valid ``Cargo.toml`` for fixture/CLI tests."""
    path = tmp_path / "Cargo.toml"
    path.write_text(
        '[package]\n'
        'name = "fixture-app"\n'
        'version = "0.1.0"\n'
        'edition = "2021"\n',
        encoding="utf-8",
    )
    return path
