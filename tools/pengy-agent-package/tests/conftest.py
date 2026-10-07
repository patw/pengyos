"""Pytest bootstrap for the pengy-agent-package suite.

Adds the tool root to ``sys.path`` so a bare ``python3 -m pytest -q`` works
without ``pip install -e .`` (the verifier runs exactly that, cwd =
``tools/pengy-agent-package``). The recipe-check helpers live in
``tests.recipe_check`` so they can be imported by both the scaffold and the
recipe test modules.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
