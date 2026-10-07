"""Error types for :mod:`pengyos_netctl`.

All failures that the CLI is expected to surface to the user (bad spec,
unresolvable key, invalid output path, ...) derive from
:class:`NetctlError` so :func:`pengyos_netctl.cli.main` can turn them into
a clean ``exit 2`` with a one-line message instead of a traceback.
"""

__all__ = [
    "NetctlError",
    "SpecError",
    "KeyAlreadyExistsError",
    "KeyFormatError",
    "OutputError",
    "NftValidationError",
]


class NetctlError(Exception):
    """Base class for all user-facing pengyos-netctl errors."""


class SpecError(NetctlError):
    """A spec/config file is missing, unreadable, or structurally invalid."""


class NftValidationError(NetctlError):
    """A rendered ruleset fails the structural/declared-ports policy check."""


class KeyAlreadyExistsError(NetctlError):
    """A peer key already exists and must not be overwritten (see spec)."""


class KeyFormatError(NetctlError):
    """A key file is present but not a valid base64 WireGuard key."""


class OutputError(NetctlError):
    """The rendered output cannot be written (e.g. the destination exists)."""
