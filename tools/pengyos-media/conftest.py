"""Pytest configuration for pengyos-media.

Ensures the tool directory (where ``pengyos_media`` lives) is importable
regardless of where pytest is invoked from, then enables opt-in integration
tests behind ``PENGYOS_IT=1``.
"""

import os
import sys

import pytest

# Make the package importable when running `pytest` from the tool directory.
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)


def integration_enabled() -> bool:
    """Integration tests are opt-in behind ``PENGYOS_IT=1``."""
    return os.environ.get("PENGYOS_IT") == "1"


# Exposed so tests can query the flag without importing os themselves.
INTEGRATION_ENABLED = integration_enabled()


@pytest.fixture
def integration_enabled_flag() -> bool:
    """Whether opt-in integration tests are enabled (``PENGYOS_IT=1``)."""
    return integration_enabled()
