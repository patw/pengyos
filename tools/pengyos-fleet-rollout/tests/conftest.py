"""Pytest bootstrap for ``pengyos-fleet-rollout``.

Ensures the repository root (and therefore the in-tree ``pengyos_fleet_rollout``
package) is importable regardless of how pytest is invoked, and registers the
``PENGYOS_IT`` integration-test marker.

The suite needs no network, no root, no real display, and no real hardware —
the transport is injectable and the inventory is a committed fixture. Real
system integration is opt-in behind ``PENGYOS_IT=1`` and skipped by default.
"""

import os
import sys
from pathlib import Path

import pytest  # noqa: F401  (imported for the fixtures_dir fixture above)

# The sibling ``pengyos-fleet-inventory`` package describes the fleet inventory
# this tool consumes. Make it importable for the inventory-conformance test when
# present, so the "if importable" branch exercises the real loader. Safe when
# absent: the sibling is only added if its package directory exists.
_fleet = Path(__file__).parent.parent.parent / "pengyos-fleet-inventory"
if (_fleet / "pengyos_fleet").is_dir():
    sys.path.insert(0, str(_fleet))


@pytest.fixture
def fixtures_dir() -> Path:
    """The directory of committed fixtures (inventory / plan / journal)."""

    return Path(__file__).parent / "fixtures"


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
