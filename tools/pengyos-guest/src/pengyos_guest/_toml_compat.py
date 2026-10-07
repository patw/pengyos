"""Backfill :mod:`tomllib` for Python < 3.11.

The tool depends only on the standard library. :mod:`tomllib` ships with
Python 3.11+, so on 3.10 we import the third-party ``tomli`` package *if* it is
installed and raise a clear error otherwise. We never add ``tomli`` as a hard
runtime dependency -- it is an optional dev/test convenience.
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    from tomllib import loads  # type: ignore[no-redef]
else:  # pragma: no cover - exercised only on Python 3.10
    try:
        from tomli import loads  # type: ignore[no-redef]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "pengyos-guest needs tomllib (Python 3.11+) or the 'tomli' "
            "package on Python 3.10. Install it with: pip install tomli"
        ) from exc

__all__ = ["loads"]
