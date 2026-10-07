"""License family taxonomy for the PengyOS license-audit tool.

The freedom policy reasons about *license families*, not individual SPDX ids:
it says "permissive is fine, copyleft in the core is flagged". This module
defines the canonical vocabulary the policy and (later) classification share —
which families exist and in what order they rank for stable reporting.

The SPDX-id → family *mapping* lives in the classification subtask; this module
only defines the target families themselves, so the policy schema has a fixed
vocabulary to operate over.
"""

from __future__ import annotations

from enum import Enum


class LicenseFamily(str, Enum):
    """Canonical license families, ordered from most to least free.

    The ordering is the *freedom* ordering used for stable grouping/reporting:
    index 0 (``PERMISSIVE``) is the freest, ``UNKNOWN`` the least known.
    """

    PERMISSIVE = "permissive"
    WEAK_COPYLEFT = "weak-copyleft"
    STRONG_COPYLEFT = "strong-copyleft"
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"

    def __str__(self) -> str:  # pragma: no cover - trivial repr
        return self.value


# Canonical freedom ordering (index 0 = freest). Stable across runs.
FAMILY_ORDER: list[LicenseFamily] = list(LicenseFamily)
