"""Exception types for pengyos-sbom.

All of these carry a human-readable message that the CLI prints verbatim on
stderr together with a non-zero exit code (see ``constants.EXIT_ERROR``).
Malformed Buildroot output must never surface as a traceback.
"""

from __future__ import annotations


class SBOMError(Exception):
    """Base class for all pengyos-sbom failures."""


class LegalInfoError(SBOMError):
    """The legal-info directory is missing or its manifests are malformed."""


class ShowInfoError(SBOMError):
    """The show-info JSON file is missing or malformed."""
