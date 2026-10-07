"""Console entry point: ``python -m pengyos_desktop_drivers``.

The installed console script (``pengyos-desktop-drivers``) delegates here; this
module exists so the package is runnable without installation too.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
