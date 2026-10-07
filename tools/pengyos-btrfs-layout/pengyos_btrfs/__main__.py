"""Enable ``python -m pengyos_btrfs``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
