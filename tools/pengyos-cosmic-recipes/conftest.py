"""Pytest bootstrap for the pengyos-cosmic-recipes suite.

Two things happen here, both required for a bare ``python3 -m pytest -q`` to
work the way the verifier runs it (cwd = ``tools/pengyos-cosmic-recipes``):

1. The repo root is added to ``sys.path`` so the ``pengyos_cosmic_recipes``
   package imports without ``pip install -e .``.
2. The **pengyos-cosmic-pkg engine** is added to ``sys.path`` so
   ``import pengyos_cosmic_pkg`` resolves to the pinned engine source rather
   than some other copy. The engine is a sibling tool whose importable package
   lives at ``<engine>/tools/pengyos-cosmic-pkg/pengyos_cosmic_pkg``; this file
   locates that directory and prepends it.

The engine is the single external dependency of this repo, so all of its
import machinery lives in one place, here.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root = parent of this conftest.py.
ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _engine_tools_dir() -> Path | None:
    """Return the engine's ``tools/pengyos-cosmic-pkg`` dir, or ``None``.

    Resolution order (cheapest robust first):

    1. The **in-tree sibling engine** under this repo's ``tools/`` — the
       canonical source. This is present in every checkout, so the verifier resolves the *same* pinned
       engine the repo ships (never a stale external copy).
    2. The location of the already-importable engine package (covers a
       pip/pipx-installed copy) by walking up from ``pengyos_cosmic_pkg``.

    Returns ``None`` when neither resolves, so a missing engine is a
    *reported* import error (below), not a silent no-op.
    """
    # 1. In-tree sibling engine: <repo>/tools/pengyos-cosmic-pkg.
    repo_sibling = ROOT.parent / "pengyos-cosmic-pkg"
    if (repo_sibling / "pengyos_cosmic_pkg" / "__init__.py").is_file():
        return repo_sibling

    # 2. The installed/importable engine (pip/pipx copy).
    try:
        import pengyos_cosmic_pkg as _pkg  # noqa: F401

        return Path(_pkg.__file__).resolve().parent.parent
    except ImportError:
        pass

    return None


_ENGINE_DIR = _engine_tools_dir()
if _ENGINE_DIR is not None and str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
