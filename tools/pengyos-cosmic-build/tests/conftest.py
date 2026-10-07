"""Shared pytest fixtures for the pengyos-cosmic-build suite.

Two things happen here, both required for a bare ``python3 -m pytest -q`` to
work the way the verifier runs it (cwd = ``tools/pengyos-cosmic-build``):

1. The repo root is added to ``sys.path`` so the ``pengyos_cosmic_build``
   package imports without ``pip install -e .``.
2. The **engine + sibling lanes** are added to ``sys.path`` so their
   importable packages resolve to the pinned in-tree source rather than some
   other copy. They are:

   * the ``pengyos-cosmic-pkg`` engine (the vendoring/recipe engine),
   * the ``pengyos-cosmic-recipes`` recipes lane (owns the manifest + emit),
   * the ``pengyos-cosmic-native-deps`` native-dep lane (owns the native deps).

   These are the external dependencies of this repo, so all of their import
   machinery lives in one place, here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent

# The sibling tools this repo wires together, keyed by their tools/ dir name.
_SIBLINGS = (
    "pengyos-cosmic-pkg",
    "pengyos-cosmic-recipes",
    "pengyos-cosmic-native-deps",
)


def _add_sibling(name: str) -> None:
    """Prepend the sibling's ``tools/<name>`` dir to ``sys.path`` once."""
    sibling = ROOT.parent / name
    if (
        (sibling / "pengyos_cosmic_pkg").is_dir()
        or (sibling / "pengyos_cosmic_recipes").is_dir()
        or (sibling / "pengyos_cosmic_native_deps").is_dir()
    ):
        path = str(sibling)
        if path not in sys.path:
            sys.path.insert(0, path)


if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Resolve every sibling engine/lane from the in-tree tools/ dir.
for _name in _SIBLINGS:
    _add_sibling(_name)


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
    from pengyos_cosmic_build.cli import main

    def _run(*args: str) -> int:
        monkeypatch.chdir(ROOT)
        return main(list(args))

    return _run
