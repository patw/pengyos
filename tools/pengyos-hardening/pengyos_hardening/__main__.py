"""Enable ``python3 -m pengyos_hardening`` as an alias for the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
