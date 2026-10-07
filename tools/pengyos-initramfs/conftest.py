"""Pytest bootstrap: make the ``pengyos_initramfs`` package importable.

``python3 -m pytest`` already puts the current directory on ``sys.path`` (which
is what ``verify.toml`` uses), but this makes plain ``pytest`` work too.
"""

import sys
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))
