"""Allow ``python -m pengyos_live`` to run the CLI."""

import sys

from . import cli

if __name__ == "__main__":
    sys.exit(cli.main())
