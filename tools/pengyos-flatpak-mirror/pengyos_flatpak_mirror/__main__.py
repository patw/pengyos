"""Entry point for ``python -m pengyos_flatpak_mirror``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
