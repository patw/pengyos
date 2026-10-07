"""Enable ``python -m pengyos_measured_boot``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
