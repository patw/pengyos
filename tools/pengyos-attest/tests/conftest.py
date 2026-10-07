"""Test configuration for the pengyos-attest suite.

This conftest makes the repository root (which contains the ``pengyos_attest``
package) importable without a prior ``pip install``. That keeps the suite
self-sufficient when a bare ``pytest -q`` runs from the repo
root, and it lets the same suite also run against an installed copy.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
