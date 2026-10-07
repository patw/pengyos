"""pengyos-backup — a backup planner for PengyOS server restore (US-SRV-4).

The planner takes a ``backup.yaml`` and:

* emits a systemd timer + oneshot service that runs restic/rustic (``plan``),
* proves a documented restore reproduces a known tree via a tree-hash check
  (``verify-restore``), and
* renders the plan as JSON (``render``).

See ``spec.md`` for the authoritative contract.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
