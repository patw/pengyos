"""Pytest bootstrap for ``pengyos-hwprofile-kernel``.

Ensures the repository root (and therefore the in-tree ``pengyos_hwprofile_kernel``
package) is importable regardless of how pytest is invoked, and registers the
``PENGYOS_IT`` integration-test marker.

The kernel-fragments library (the fragment-format source of truth) lives in a
sibling tool in this monorepo. It is added to ``sys.path`` when present so the
fragment-format tests can import the real ``pengyos_kernel_fragments`` package;
this is safe when the sibling is absent (only added if its package directory
exists).
"""

import os
import sys
from pathlib import Path

import pytest

# The sibling tools live in this monorepo (they are not pip packages). Make
# them importable for the tests that exercise the real library / schema, so the
# tests drive the actual pengyos-kernel-fragments fragment grammar and the
# pengyos-hwprofile schema rather than a reimplementation. Both are safe when
# absent: a sibling is only added if its package directory exists.

# The hwprofile schema (the profile schema source of truth).
_hwprofile = Path(__file__).resolve().parents[1] / "pengyos-hwprofile"
if (_hwprofile / "pengyos_hwprofile").is_dir():
    sys.path.insert(0, str(_hwprofile))

# The kernel-fragments library (the fragment-format source of truth).
_kernel_fragments = Path(__file__).resolve().parents[1] / "pengyos-kernel-fragments"
if (_kernel_fragments / "pengyos_kernel_fragments").is_dir():
    sys.path.insert(0, str(_kernel_fragments))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests requiring real sibling tooling / a full "
        "build chain (PENGYOS_IT=1)",
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
