"""PengyOS license-audit.

Verify each PengyOS component's license claim and enforce the freedom policy.

This package is the runtime library behind the ``pengyos-license-audit`` CLI.
It is intentionally stdlib-only: no third-party runtime dependencies.
"""

from .classification import classify, classify_expression
from .errors import (
    AuditError,
    ManifestError,
    PolicyError,
    ValidationError,
)
from .families import FAMILY_ORDER, LicenseFamily
from .model import Verdict, VerdictResult

__all__ = [
    "AuditError",
    "FAMILY_ORDER",
    "LicenseFamily",
    "ManifestError",
    "PolicyError",
    "ValidationError",
    "Verdict",
    "VerdictResult",
    "classify",
    "classify_expression",
]

__version__ = "0.1.0"
