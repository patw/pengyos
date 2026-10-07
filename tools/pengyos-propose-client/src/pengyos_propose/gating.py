"""Tier gating — the human-approval gate.

PengyOS classifies changes into three bands (see the `pengyos-tiers` tool):

* **Tier 0** — image / baseline.
* **Tier 1** — agent-mutable (the AI may act).
* **Tier 2** — human-signed.  Never crosses the security boundary without an
  explicit human decision.

This module owns the *model* of gating: :func:`needs_human_approval` (the pure
predicate) and :class:`GateError` (the signal the CLI raises when a proposal is
refused).  *Enforcing* the gate — wiring the refusal into the submit path and
proving it with a test — is a distinct, later subtask.
"""

from __future__ import annotations

from .proposal import Proposal


class GateError(Exception):
    """Raised when a proposal is refused by the human gate."""


def needs_human_approval(p: Proposal) -> bool:
    """Return True iff the proposal sits in the human-signed (Tier 2) band."""
    return p.tier == "tier2"
