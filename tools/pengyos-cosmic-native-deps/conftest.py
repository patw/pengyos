"""Pytest bootstrap for the ``pengyos-cosmic-native-deps`` suite.

Ensures the tool directory (where ``pengyos_cosmic_native_deps`` lives) is
importable regardless of where pytest is invoked from, and registers the
``integration`` marker.

Integration tests (``tests/test_integration.py``) are opt-in behind
``PENGYOS_IT=1``: the full-suite re-run in
``tests/test_github_ready.py`` is the only test that reads that env var, and it
clears it for the child process so the suite never recurses. A test that needs a
real Buildroot checkout is skipped rather than failed when none is present, so
the opt-in suite is green on a host that has not checked one out.
"""

from __future__ import annotations

import os
import sys

import pytest

# Make the package importable when running `pytest` from the tool directory.
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: marks tests that run against a real Buildroot checkout "
        "(PENGYOS_IT=1)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items) -> None:
    if os.environ.get("PENGYOS_IT") not in ("1", "true", "True"):
        skip = pytest.mark.skip(
            reason="integration tests disabled (set PENGYOS_IT=1)",
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)
