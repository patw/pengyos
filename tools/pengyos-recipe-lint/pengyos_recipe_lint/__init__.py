"""PengyOS recipe-lint — linter for Buildroot package recipes.

The package is organised in thin layers so each can be unit-tested in
isolation:

* ``recipes``  — filesystem discovery of ``.mk`` / ``.hash`` recipe pairs.
* ``recipe_parser`` — tolerant parsing of Buildroot ``.mk`` variable
  assignments (``_VERSION``, ``_SITE``, ``_SOURCE``, ``_LICENSE``,
  ``_LICENSE_FILES``).
* ``rules``    — the lint rules (version pinned, hash present, license
  declared, no unverified downloads).
* ``report``   — the violation model + stable ordering.
* ``cli``      — the ``pengyos-recipe-lint`` command-line entry point.

Only the layers needed for a given subtask are implemented; the others are
intentionally absent until their subtask lands.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
