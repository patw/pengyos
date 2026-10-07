"""Pytest bootstrap for ``pengyos-hwprofile``.

Ensures the repository root (and therefore the in-tree ``pengyos_hwprofile``
package) is importable regardless of how pytest is invoked, and registers the
``PENGYOS_IT`` integration-test marker.

The suite needs no network, no root, and no real hardware — every collector is
driven by the committed fixture tree under ``tests/fixtures/sys``. Real-system
integration (pointing a :class:`~pengyos_hwprofile.source.Source` at the live
``/sys`` and running ``lspci``/``lsusb``/``dmidecode``) is opt-in behind
``PENGYOS_IT=1`` and skipped by default.
"""

import os

import pytest


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
