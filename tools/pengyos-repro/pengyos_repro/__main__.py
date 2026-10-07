"""Enable ``python -m pengyos_repro`` to invoke the CLI directly.

The installed console script (``pengyos-repro``) and this module share the same
entry point, so both paths are tested identically.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
