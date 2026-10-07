"""Proposal schema and loading.

A *proposal* is a single change request flowing from triage / upstream-watch
toward the human-gated propose/approve gate.  It carries enough context for a
human reviewer to decide: what is changing, why, and how sensitive it is.

This module owns the data model and JSON (de)serialization.  It is the single
source of truth for the proposal shape; :mod:`pengyos_propose.render`,
:mod:`pengyos_propose.gate` and :mod:`pengyos_propose.gating` all consume a
:class:`Proposal` rather than raw dicts.

The loader enforces *required-field* presence up front: a proposal must carry
at least one human-usable field (:data:`HUMAN_USABLE_FIELDS`, i.e. a non-empty
``title``, ``component`` or ``tier``) or :meth:`Proposal.from_dict` raises
``ValueError`` — which the CLI surfaces as a clear exit-1 error before `render`
or `submit` do anything.  ``tier`` is still parsed leniently so a malformed
value surfaces at render/gate time rather than crashing the loader; validating
that a ``tier`` is *known* is a separate, later subtask.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# The tiers a change can be classified into.  Tier 2 is the human-signed band:
# it must never be submitted without an explicit human-approval flag.
TIER_0 = "tier0"      # image / baseline
TIER_1 = "tier1"      # agent-mutable
TIER_2 = "tier2"      # human-signed / human-approval required

VALID_TIERS = (TIER_0, TIER_1, TIER_2)

# Fields that make a proposal human-usable.  A proposal must carry at least one
# of them, or the loader rejects it (see the "required fields at load" task).
# Presence is what matters here — a *valid* tier value is a separate concern
# (a later task) and is not checked by this predicate.
HUMAN_USABLE_FIELDS = ("title", "component", "tier")

_TIER_LABELS = {
    TIER_0: "Tier 0 (image)",
    TIER_1: "Tier 1 (agent-mutable)",
    TIER_2: "Tier 2 (human-signed)",
}


def tier_label(tier: str) -> str:
    """Human label for a tier id, falling back to the raw id."""
    return _TIER_LABELS.get(tier, tier)


def _has_human_field(data: dict[str, Any]) -> bool:
    """True iff the source carries at least one non-empty human-usable field.

    A proposal needs at least one of :data:`HUMAN_USABLE_FIELDS` to be usable by
    a human reviewer; a missing *or* blank field does not count.  Only presence
    is checked here — a *valid* tier value is a separate concern.
    """
    for key in HUMAN_USABLE_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


@dataclass
class Proposal:
    """A single change proposal.

    Fields mirror the shape agreed in the spec: a human-readable ``title``,
    where the change came from (``source``), the classification ``type`` and
    ``component``, the ``from``/``to`` delta, the ``rationale``, collected
    ``evidence`` and the sensitivity ``tier``.
    """

    title: str = ""
    source: str = ""
    type: str = ""
    component: str = ""
    from_: str = ""
    to: str = ""
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)
    tier: str = TIER_1

    def is_tier2(self) -> bool:
        """True iff this proposal sits in the human-signed band."""
        return self.tier == TIER_2

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (``from_`` -> ``from`` for the API)."""
        data = asdict(self)
        data["from"] = data.pop("from_")
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Proposal":
        """Build a :class:`Proposal` from a mapping.

        Unknown keys are ignored; missing keys fall back to defaults.  A
        proposal missing *all* human-usable fields (:data:`HUMAN_USABLE_FIELDS`)
        is rejected with ``ValueError``.  ``tier`` is validated against
        :data:`VALID_TIERS` and defaults to :data:`TIER_1` when absent or
        unparseable (strict tier *validity* is enforced later).
        """
        if not isinstance(data, dict):
            raise ValueError("proposal payload must be a JSON object")

        # Required-fields-at-load enforcement: a proposal must carry at least one
        # human-usable field, or it is unusable by a reviewer.  Rejected here, at
        # load, so both `render` and `submit` refuse it before doing anything.
        if not _has_human_field(data):
            raise ValueError(
                "proposal is missing all human-usable fields: "
                f"at least one of {', '.join(HUMAN_USABLE_FIELDS)} is required"
            )

        tier = data.get("tier", TIER_1)
        if tier not in VALID_TIERS:
            # Lenient now; task 2 makes this strict.  Keep a sane default.
            tier = TIER_1

        evidence = data.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        evidence = list(evidence)

        return cls(
            title=str(data.get("title", "") or ""),
            source=str(data.get("source", "") or ""),
            type=str(data.get("type", "") or ""),
            component=str(data.get("component", "") or ""),
            from_=str(data.get("from") if "from" in data else data.get("from_", "") or ""),
            to=str(data.get("to", "") or ""),
            rationale=str(data.get("rationale", "") or ""),
            evidence=evidence,
            tier=tier,
        )

    @classmethod
    def load(cls, text: str) -> "Proposal":
        """Parse a proposal from a JSON string."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"proposal is not valid JSON: {e}") from e
        return cls.from_dict(data)


def load_proposal_file(path: str) -> Proposal:
    """Load a :class:`Proposal` from a JSON file on disk."""
    with open(path, "r", encoding="utf-8") as f:
        return load_proposal(f.read())


def load_proposal(text: str) -> Proposal:
    """Parse a :class:`Proposal` from a JSON string."""
    return Proposal.load(text)
