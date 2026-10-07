"""Console entry point: ``python -m pengyos-desktop-nvidia``.

The installed console script (``pengyos-desktop-nvidia``) delegates here; this
module exists so the package is runnable without installation too.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
