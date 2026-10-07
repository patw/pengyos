"""Typed errors for ``pengyos-fleet-rollout``.

Every failure the tool can legitimately hit is a subclass of
:class:`RolloutError`, so the CLI can map them to a process exit code without
catching broad, unrelated exceptions. Standard library only.
"""

from __future__ import annotations


class RolloutError(RuntimeError):
    """Base class for every error this tool raises on purpose.

    The CLI catches this and prints ``pengyos-fleet-rollout: <message>`` to
    stderr with a non-zero exit code, so user-facing failures are actionable
    rather than tracebacks.
    """


class RolloutPlanError(RolloutError):
    """The plan could not be produced.

    Raised for an empty inventory, an unknown image, or a malformed inventory
    document — i.e. *input* problems that must be fixed before anything is
    applied.
    """


class RolloutTransportError(RolloutError):
    """A transport call failed while applying or rolling back.

    Raised when a host's apply (or rollback) step reports a non-zero exit, or
    when the transport itself cannot run. This is what a mid-rollout gate
    failure surfaces as.
    """
