"""Allow ``python -m pengyos_media``."""

import sys

from pengyos_media.cli import main

if __name__ == "__main__":
    sys.exit(main())
