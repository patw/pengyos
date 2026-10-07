"""Pytest bootstrap for the pengyos-desktop-net suite.

Two things happen here, both required for a bare ``python3 -m pytest -q`` to work
the way the verifier runs it (cwd = ``tools/pengyos-desktop-net``):

1. The repo root is added to ``sys.path`` so the ``pengyos_desktop_net`` package
   imports without ``pip install -e .``.

The suite is standard-library-only at test time, so it needs no network, root,
display, or hardware.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root = parent of this conftest.py.
ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
