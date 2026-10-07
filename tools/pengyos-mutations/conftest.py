"""Root conftest for pengyos-mutations.

Ensures the repository root is importable so ``import pengyos_mutations`` works
whether or not the package is installed, and exposes a couple of shared
fixtures.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest  # noqa: E402


@pytest.fixture
def root_path() -> pathlib.Path:
    """Absolute path to the repository root."""
    return ROOT
