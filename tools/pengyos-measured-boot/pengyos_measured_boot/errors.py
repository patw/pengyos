"""Domain errors for pengyos-measured-boot.

All exceptions raised intentionally by the tool derive from
:class:`PengyOSMeasuredBootError` so the CLI can map them to a clean
non-zero exit code with a human-readable message (no tracebacks).
"""

from __future__ import annotations

from dataclasses import dataclass


class PengyOSMeasuredBootError(Exception):
    """Base class for every error raised by this tool."""


class CommandNotReady(PengyOSMeasuredBootError):
    """A subcommand's backend has not been implemented yet.

    Raised by the scaffold so the suite stays green while the real
    implementation lands in a follow-up task. This is an honest
    "not wired yet" signal, never a pretend-success stub.
    """


class UsageError(PengyOSMeasuredBootError):
    """The command line was malformed or arguments were inconsistent."""


@dataclass(frozen=True)
class Mismatch:
    """A single PCR that failed policy verification.

    Attributes
    ----------
    index:
        The PCR index the policy required.
    expected:
        The value the policy required.
    actual:
        The value the boot produced, or ``None`` if the PCR was absent from
        the measurements.
    """

    index: int
    expected: int
    actual: int | None


class PolicyMismatch(PengyOSMeasuredBootError):
    """One or more PCRs did not match the expected policy values.

    Raised by :meth:`pengyos_measured_boot.policy.Policy.verify`. The message
    is specific and actionable: it names every mismatching PCR and shows the
    expected vs. actual value (or that the PCR was not measured at all).
    """

    def __init__(self, mismatches: list[Mismatch]) -> None:
        self.mismatches = list(mismatches)
        super().__init__(_format_mismatches(self.mismatches))


def _format_mismatches(mismatches: list[Mismatch]) -> str:
    """Render a specific, human-readable mismatch report."""

    lines = ["measured boot did not match policy:"]
    for m in mismatches:
        if m.actual is None:
            lines.append(f"  PCR {m.index}: required by policy, not measured")
        else:
            lines.append(
                f"  PCR {m.index}: expected {m.expected:#066x}, "
                f"got {m.actual:#066x}"
            )
    return "\n".join(lines)
