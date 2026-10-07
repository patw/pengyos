"""``python -m pengyos_upstream_watch`` entry point.

Wired to the same ``main`` as the ``pengyos-upstream-watch`` console script so
the two invocation styles are interchangeable.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
