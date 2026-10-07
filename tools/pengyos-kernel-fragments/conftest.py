"""Pytest bootstrap.

Ensures the repository root (and therefore the in-tree ``pengyos_kernel_fragments``
package) is importable regardless of how pytest is invoked, and registers the
``PENGYOS_IT`` integration-test marker.
"""

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests requiring real system integration "
        "(PENGYOS_IT=1)",
    )


def pytest_collection_modifyitems(config, items):
    # Drop integration tests unless explicitly enabled.
    if os.environ.get("PENGYOS_IT") not in ("1", "true", "True"):
        skip = pytest.mark.skip(
            reason="integration tests disabled (set PENGYOS_IT=1)"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)
