"""Domain errors for the PengyOS release tool.

Every failure the tool can legitimately hit (a missing build output, a hash
mismatch, a dangling channel pointer, ...) is a :class:`PengyOsError`
subclass. The CLI turns these into a clean ``error: <message>`` line plus a
non-zero exit code instead of a traceback.
"""

from __future__ import annotations


class PengyOsError(Exception):
    """Base class for every expected, user-facing failure."""


class BuildOutputError(PengyOsError):
    """The --build directory is missing or not a valid build output."""


class FeedError(PengyOsError):
    """The feed tree is malformed, missing, or its index is broken."""


class ManifestError(PengyOsError):
    """A manifest is missing required fields or is internally inconsistent."""


class SigningError(PengyOsError):
    """A signer could not produce or verify a signature."""


class VersionError(PengyOsError):
    """A version string is not a valid semantic version."""


class ChannelError(PengyOsError):
    """A channel name is invalid or a channel pointer is malformed."""


class RetainError(PengyOsError):
    """The retention policy cannot be applied (e.g. it would prune the live)."""
