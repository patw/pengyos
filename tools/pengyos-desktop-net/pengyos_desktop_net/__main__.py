"""Entry point for ``python -m pengyos_desktop_net``."""

from __future__ import annotations

import sys

from pengyos_desktop_net.cli import main

if __name__ == "__main__":
    sys.exit(main())
