"""Ranking of classified findings.

This is a **documented, no-ML heuristic**: :func:`rank` orders a list of
:class:`~.classification.Classification` objects by the lexicographic key

    (severity, exploitability signal, component reachability)

highest first, with two deterministic tie-breakers so the ordering is a *total*
order for any input:

1. **severity** — the classification's severity band on the fixed scale
   :data:`~.models.SEVERITY_LEVELS` (``critical > high > medium > low``).
   An unquantified severity (``none``) sorts below ``low`` — an affected-but-
   unquantified finding is never ordered above a genuinely ``low`` one
   (:func:`~.models.severity_rank`).
2. **exploitability signal** — the CVSS v3 **Exploitability sub-score**
   (``Roundup(8.22 × AV × AC × PR × UI)``, 0.2–3.9) recorded on the
   classification by the item 5 rule engine (see
   :func:`~.cvss.exploitability`). Within one severity band, the finding an
   attacker can reach more easily ranks first (e.g. network/low-complexity/
   no-privileges 3.9 before a local-privilege one at 1.9). A CVSS v4.0 vector
   or a raw numeric score has no v3-style sub-score, and an unquantified
   signal likewise: both carry ``0.0`` (source ``"none"``) and are never
   over-claimed.
3. **component reachability** — the pin's reachability on
   :data:`~.models.REACHABILITY_LEVELS` (``core > std > opt``). A finding for
   a component not present in the pins manifest (an unpinned changelog
   signal) degrades to rank 0, labelled ``"unpinned"`` — the same graceful
   degradation as :func:`~.models.reachability_rank` for unknown labels.

Tie-breakers (only when all three signals are equal, for total determinism):
4. **category** — ``security`` before ``relevant`` before ``ignore``; and
5. **id** — the classification's id string, lexicographically. Input order
   therefore has no effect on the output order.

The result is a list of :class:`RankedClassification`, each carrying the
underlying classification, its 1-based ``position``, the three ranked signals
as plain values (so the item 7 emitter can serialize them), and a
``rank_rationale`` — a one-line human-readable account of *why* it got its
position. This module only *orders*; it never drops, merges or re-categorises
its inputs, and the item 7 emission / ``explain`` printer builds on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .changelog_scan import find_pin_for
from .classification import (
    CLASSIFY_IGNORE,
    CLASSIFY_RELEVANT,
    CLASSIFY_SECURITY,
    Classification,
)
from .models import Pin, reachability_rank, severity_rank

__all__ = [
    "CATEGORY_PRIORITY",
    "RANKED_UNPINNED",
    "RankedClassification",
    "rank_key",
    "rank",
]

# The category tie-break order (lower ranks earlier) — applied only after the
# three documented signals are all equal.
CATEGORY_PRIORITY = {
    CLASSIFY_SECURITY: 0,
    CLASSIFY_RELEVANT: 1,
    CLASSIFY_IGNORE: 2,
}

# The reachability label reported for a finding whose component has no pin.
RANKED_UNPINNED: str = "unpinned"


@dataclass(frozen=True)
class RankedClassification:
    """One classification together with the signals that ranked it.

    ``position`` is the 1-based slot in the ranked list; the signal fields
    mirror what the sort key consumed so a downstream emitter (item 7) can
    serialize *why* a proposal sits where it does without re-deriving anything.
    """

    classification: Classification
    position: int
    severity_rank: int
    exploitability: float
    reachability: str
    reachability_rank: int
    rank_rationale: str


def rank_key(
    classification: Classification,
    reachability_label: str,
    reachability_rank: int,
) -> "tuple":
    """The total, deterministic sort key for one classification.

    Exposed (and tested) on its own so the ordering contract is inspectable
    independently of :func:`rank`; smaller keys sort earlier.
    """
    return (
        -severity_rank(classification.severity),
        -classification.exploitability,
        -reachability_rank,
        CATEGORY_PRIORITY.get(classification.category, len(CATEGORY_PRIORITY)),
        classification.id,
    )


def _pin_reachability(component: str, pins: List[Pin]):
    """``(label, rank)`` for ``component``: the covering pin's reachability,
    or ``(RANKED_UNPINNED, 0)`` when no pin covers it."""
    pin: Optional[Pin] = find_pin_for(component, pins)
    if pin is None:
        return RANKED_UNPINNED, 0
    return pin.reachability, reachability_rank(pin.reachability)


def rank(
    classifications: List[Classification],
    pins: List[Pin],
) -> List[RankedClassification]:
    """Rank ``classifications`` by (severity, exploitability, reachability).

    See the module docstring for the full heuristic. The output is a new list,
    deterministically ordered regardless of input order; the input list is
    never mutated and every input appears in the output exactly once.
    """
    pins = list(pins)
    decorated = [
        (cls, *_pin_reachability(cls.component, pins))
        for cls in classifications
    ]
    decorated.sort(key=lambda t: rank_key(t[0], t[1], t[2]))

    ranked: List[RankedClassification] = []
    for position, (cls, label, rrank) in enumerate(decorated, start=1):
        sev = cls.severity if cls.severity else "none"
        ranked.append(RankedClassification(
            classification=cls,
            position=position,
            severity_rank=severity_rank(cls.severity),
            exploitability=cls.exploitability,
            reachability=label,
            reachability_rank=rrank,
            rank_rationale=(
                f"ranked #{position} of {len(decorated)}: "
                f"severity {sev}; exploitability "
                f"{cls.exploitability:.1f} ({cls.exploitability_source}); "
                f"reachability {label}"
            ),
        ))
    return ranked
