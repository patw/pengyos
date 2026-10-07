"""Structured error types for input handling.

Every loader raises one of these (or a subclass) when its input does not satisfy
the documented schema, so that callers can distinguish a *bad-input* failure
(→ exit code 1, "ran but failed") from a *usage* failure (→ exit code 2, argparse).
The messages are intentionally human-readable because they are surfaced verbatim
on stderr; each carries the machine-friendly fields a caller (or a later
subcommand) needs to report precisely what went wrong and where.
"""

from __future__ import annotations


class InputError(Exception):
    """Base class for all structured input/schema errors.

    Attributes
    ----------
    path:
        The path of the offending file, if known (may be ``None`` for
        directory-level problems such as a missing advisories directory).
    """

    path: "str | None"

    def __init__(self, message: str, path: "str | None" = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path

    def __str__(self) -> str:  # pragma: no cover - exercised implicitly
        if self.path:
            return f"{self.path}: {self.message}"
        return self.message


class PinsError(InputError):
    """The pins manifest is missing, unreadable, or does not match the schema."""


class PinsFormatError(PinsError):
    """The pins manifest could not be parsed as TOML / did not decode."""


class PinsSchemaError(PinsError):
    """The pins manifest parsed, but its structure violated the documented schema."""


class AdvisoryError(InputError):
    """An advisory fixture is missing, malformed, or does not match the schema."""


class AdvisoryFormatError(AdvisoryError):
    """An advisory fixture could not be parsed as JSON."""


class AdvisorySchemaError(AdvisoryError):
    """An advisory fixture parsed, but did not match the OSV schema."""


class ChangelogError(InputError):
    """A changelog fixture is missing, unreadable, or does not match the schema."""
