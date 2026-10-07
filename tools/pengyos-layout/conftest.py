"""Pytest bootstrap for the src-layout package.

The verifier runs ``python3 -m pytest -q`` from this tool's directory without an
editable install (the host Python is externally managed, PEP 668). Adding the
``src`` tree to ``sys.path`` here lets ``import pengyos_layout`` resolve for the
whole suite without touching the system environment.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
