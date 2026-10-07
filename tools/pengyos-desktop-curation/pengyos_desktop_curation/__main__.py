"""Entry point for ``python -m pengyos_desktop_curation``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
