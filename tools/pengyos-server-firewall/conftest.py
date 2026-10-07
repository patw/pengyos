"""Root conftest — ensure the repo root is importable.

Running ``python -m pytest`` puts the CWD on ``sys.path`` already, but the
bare ``pytest`` console script does not. Inserting the repo root here makes
``import pengyos_netctl`` work no matter how the suite is launched.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
