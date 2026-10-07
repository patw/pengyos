"""Command-line entry point for the pengyos-layout planner.

Usage (per spec):

    pengyos-layout plan      --target PROBE.json --edition server --out plan.json [--json]
    pengyos-layout validate    --plan plan.json [--json]
    pengyos-layout emit        --plan plan.json [--json]

The ``plan`` and ``validate`` subcommands are implemented (see
:mod:`pengyos_layout.cli`); ``emit`` reports honestly that it lands in a later
iteration.
"""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":
    sys.exit(main())
