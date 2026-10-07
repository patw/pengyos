"""Allow ``python -m pengyos_install_tui``."""

import sys

from pengyos_install_tui.cli import main

if __name__ == "__main__":
    sys.exit(main())
