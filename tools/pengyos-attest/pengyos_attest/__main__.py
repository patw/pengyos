"""Enable ``python -m pengyos_attest`` to invoke the CLI directly.

The installed console script (``pengyos-attest``) and this module share the
same entry point, so both paths are tested identically.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
