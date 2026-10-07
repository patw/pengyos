"""Required approvals per tier.

Every tier gates a change behind exactly one approval type (SPEC §1.5):

- **Tier 0 (base / image):** nobody mutates this at runtime. A change here is
  resolved by the reproducible-build pipeline — the image is rebuilt,
  verified, signed, and *measured* (TPM2) — and a build **witness** attests
  to that verified build. Approval: ``witness``.
- **Tier 1 (agent-mutable):** Pengy may change this freely, but every
  mutation is snapshotted, journaled, and attested (and always revertible).
  Approval: ``agent-auto``.
- **Tier 2 (critical surface):** bootloader binaries/keys, the kernel,
  crypto/identity, the agent's own privilege, the update trust root. A
  **human** must sign before it crosses the security boundary. Approval:
  ``human-sign``.

The approval is a pure function of the tier, so ``Classification.approvals``
can expose it directly and the CLI can gate on it.
"""

from __future__ import annotations

import enum
from typing import Mapping

class Approval(str, enum.Enum):
    """A required approval type. Subclasses ``str`` so it serializes as its value."""

    WITNESS = "witness"          # Tier 0 — verified/measured build witness
    AGENT_AUTO = "agent-auto"    # Tier 1 — agent applies, snapshotted + attested
    HUMAN_SIGN = "human-sign"    # Tier 2 — human signs across the security boundary

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


# The authoritative tier → approval map. Tier 2 (and anything unknown) is the
# fail-safe: it always requires a human.
APPROVALS_BY_TIER: Mapping[int, tuple[Approval, ...]] = {
    0: (Approval.WITNESS,),
    1: (Approval.AGENT_AUTO,),
    2: (Approval.HUMAN_SIGN,),
}


def approvals_for(tier: int) -> tuple[Approval, ...]:
    """Return the required approval(s) for ``tier``.

    Unknown tiers fail safe to ``human-sign``: an unrecognised tier is treated
    as requiring a human rather than auto-approving.
    """

    return APPROVALS_BY_TIER.get(tier, (Approval.HUMAN_SIGN,))
