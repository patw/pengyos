"""Allow ``python -m pengyos_desktop_session`` to run the CLI."""

import sys

from . import cli

if __name__ == "__main__":
    sys.exit(cli.main())
