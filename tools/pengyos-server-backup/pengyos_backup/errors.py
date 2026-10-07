"""Error types for the pengyos-backup planner.

The hierarchy mirrors the sibling tools in this run: a single
:class:`PengyOSBackupError` base that the CLI catches to turn into a clean
``error: ...`` message on stderr + exit code 1, plus small specialized
subclasses so callers can ``except`` precisely.
"""

from __future__ import annotations


class PengyOSBackupError(Exception):
    """Base class for all recoverable planner errors (exit code 1)."""


class ConfigError(PengyOSBackupError):
    """The backup config is missing, unreadable, or does not validate."""


class RepoError(PengyOSBackupError):
    """The restore repository is missing, unreadable, or not a valid fixture repo."""


class CommandNotImplemented(PengyOSBackupError):
    """A subcommand is wired into the CLI but not yet implemented.

    This is a *documented interface placeholder*, not a "done" stub: the
    subcommand is registered (so its argument contract is locked and tested),
    but running it raises this. Each subcommand gets its own implementation
    task, which removes this raise and marks the task done.
    """
