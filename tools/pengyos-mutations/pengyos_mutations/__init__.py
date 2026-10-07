"""PengyOS mutations — journaled, reversible system changes.

Snapshot → apply → record → revert → converge.

Every system change Pengy makes is snapshotted, attested, revertible, and
learned (see spec §1.5). This package provides the CLI and the pluggable
backend machinery that makes that possible.
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
