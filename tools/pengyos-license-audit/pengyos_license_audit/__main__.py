"""Enable ``python -m pengyos_license_audit``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
