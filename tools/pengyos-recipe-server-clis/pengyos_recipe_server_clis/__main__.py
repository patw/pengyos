"""``python -m pengyos_recipe_server_clis`` — the package entry point.

Mirrors the ``pengyos-recipe-server-clis`` console script declared in
``pyproject.toml``'s ``[project.scripts]``: both dispatch to
:func:`pengyos_recipe_server_clis.emit.main`, so the documented
``list`` / ``emit`` / ``lint`` interface works whether the package is installed
(``pengyos-recipe-server-clis …``) or run straight from the checkout
(``python -m pengyos_recipe_server_clis …``).
"""

from __future__ import annotations

from .emit import main

if __name__ == "__main__":
    raise SystemExit(main())
