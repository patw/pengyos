"""PengyOS propose-client.

Formats change proposals and routes them to the human-gated propose/approve
gate.  Nothing is auto-applied: proposals flow to a human for review.

Public API surface is intentionally small; see :mod:`pengyos_propose.cli` for
the command line, :mod:`pengyos_propose.proposal` for loading/validation,
:mod:`pengyos_propose.render` for human output, :mod:`pengyos_propose.gate`
for the HTTP submit client, and :mod:`pengyos_propose.gating` for tier gating.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
