"""pengyos-measured-boot.

Boot under a software TPM (swtpm), read PCRs, and verify them against an
expected event-log policy. See ``spec.md`` for the full contract.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
