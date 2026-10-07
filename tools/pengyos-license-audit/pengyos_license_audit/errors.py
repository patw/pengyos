"""Error types for the PengyOS license-audit tool.

All errors raised by the library derive from :class:`AuditError` so the CLI can
map them to deterministic exit codes without leaking internals.
"""

from __future__ import annotations


class AuditError(Exception):
    """Base class for every error raised by this package."""


class ValidationError(AuditError):
    """A malformed input: bad TOML, missing required field, impossible value."""


class ManifestError(AuditError):
    """The pins/manifest could not be loaded or is internally inconsistent."""


class PolicyError(AuditError):
    """The policy file could not be loaded or is invalid."""
