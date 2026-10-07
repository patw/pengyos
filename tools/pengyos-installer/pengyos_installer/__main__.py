"""Allow ``python -m pengyos_installer``."""

import sys

from pengyos_installer.cli import main

if __name__ == "__main__":
    sys.exit(main())
