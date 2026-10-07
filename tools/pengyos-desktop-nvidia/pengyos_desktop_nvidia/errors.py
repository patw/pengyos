"""Exception types for pengyos-desktop-nvidia.

The CLI catches :class:`PengyOSError` and prints its message (exit code 2);
any other exception is an internal bug (exit code 1).
"""

from __future__ import annotations


class PengyOSError(Exception):
    """Base class for all expected, user-facing errors."""


class LintViolation(PengyOSError):
    """One or more lint rules failed.

    ``errors`` lists every human-readable violation; ``rules`` maps each rule id
    to whether it passed, so callers can tell *which* rule fired.
    """

    def __init__(self, errors: list[str], rules: dict[str, bool] | None = None):
        self.errors = list(errors)
        self.rules = dict(rules) if rules else {}
        super().__init__("; ".join(self.errors) if self.errors else "lint violation")
