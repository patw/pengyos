"""Exception types for pengyos-btrfs-layout.

All of these carry a human-readable message that the CLI prints verbatim on
stderr together with a non-zero exit code (see ``constants.EXIT_ERROR``). A
malformed layout, a missing snapshot, or an unsafe rollback must never surface
as a traceback.
"""

from __future__ import annotations


class BtrfsLayoutError(Exception):
    """Base class for all pengyos-btrfs-layout failures."""


class LayoutError(BtrfsLayoutError):
    """The requested subvolume layout is invalid or cannot be planned."""


class SnapshotError(BtrfsLayoutError):
    """A snapshot could not be created or resolved by name."""


class RollbackError(BtrfsLayoutError):
    """A rollback could not be planned or is unsafe without confirmation."""


class ToolNotFoundError(BtrfsLayoutError):
    """The ``btrfs`` tool is not installed."""


class BtrfsCommandError(BtrfsLayoutError):
    """A generated ``btrfs`` command was executed and exited non-zero.

    Carries the failing ``argv`` and the captured output so the caller can
    report exactly which step of a plan failed.
    """

    def __init__(self, message: str, argv=None, returncode=None, stderr: str = ""):
        super().__init__(message)
        self.argv = tuple(argv) if argv is not None else None
        self.returncode = returncode
        self.stderr = stderr
