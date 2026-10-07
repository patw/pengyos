"""pytest bootstrap for pengyos-netctl.

Tests run without root and without network access. Real-system integration
tests are opt-in behind the ``PENGYOS_IT=1`` environment variable and are
deselected by default.
"""

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pytest_collection_modifyitems(config, items):
    """Deselect integration tests unless PENGYOS_IT=1 is set."""
    if os.environ.get("PENGYOS_IT") == "1":
        return
    skip_it = pytest.mark.skip(reason="opt-in integration test (set PENGYOS_IT=1)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_it)
