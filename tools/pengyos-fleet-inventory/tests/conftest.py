"""Pytest bootstrap for ``pengyos-fleet``.

Ensures the repository root (and therefore the in-tree ``pengyos_fleet``
package) is importable regardless of how pytest is invoked, and registers the
``PENGYOS_IT`` integration-test marker.

The suite needs no network, no root, no real display, and no real hardware —
fact collection is driven by a deterministic mock runner backed by committed
fixtures. Real integration (live SSH, real hardware commands) is opt-in behind
``PENGYOS_IT=1`` and skipped by default.
"""

import os
import sys
from pathlib import Path

import pytest

# The hwprofile package (the schema source of truth) lives in a sibling tool in
# this monorepo. Make it importable for the schema-conformance test when present,
# so the "if importable" branch of fact collection exercises the real schema.
# Safe when absent: the sibling is only added if its package directory exists.
_hwprofile = Path(__file__).parent.parent.parent / "pengyos-hwprofile"
if (_hwprofile / "pengyos_hwprofile").is_dir():
    sys.path.insert(0, str(_hwprofile))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests requiring a real system (PENGYOS_IT=1)",
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
