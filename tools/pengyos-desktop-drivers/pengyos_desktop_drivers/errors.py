"""Exception types for pengyos-desktop-drivers.

A single base error keeps the CLI's error handling flat: the entry point catches
:class:`PengyOSError` and prints its message (exit code 2), while any other
exception is treated as an internal bug (exit code 1).
"""

from __future__ import annotations


class PengyOSError(Exception):
    """Base class for all expected, user-facing errors."""


class NotYetImplemented(PengyOSError):
    """Raised by a module whose feature is a later unit of work.

    The message always points at the task that implements the feature, so the
    CLI surfaces a concrete next step instead of a bare ``NotImplementedError``.
    """


class LintViolation(PengyOSError):
    """Raised by :mod:`pengyos_desktop_drivers.lint` when a profile is lint-dirty.

    ``errors`` is a list of human-readable violation descriptions; the CLI prints
    them all before exiting non-zero. ``rules`` maps each rule id to whether it
    passed (``False`` for the ones that fired), so callers/tests can tell *which*
    rule rejected the profile, not just that it did.
    """

    def __init__(self, errors: list[str], rules: dict[str, bool] | None = None):
        self.errors = list(errors)
        self.rules = dict(rules) if rules else {}
        super().__init__("; ".join(self.errors) if self.errors else "lint violation")
