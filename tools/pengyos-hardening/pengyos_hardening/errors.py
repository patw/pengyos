"""Error types for :mod:`pengyos_hardening`.

A single, small exception hierarchy keeps the loader's failure surface explicit
and testable: the CLI can map :class:`BaselineError` to a clean exit code
instead of dumping a traceback.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "PengyOSHardeningError",
    "BaselineError",
    "RuleError",
    "Finding",
]


class PengyOSHardeningError(Exception):
    """Base class for every error raised by this package."""


class BaselineError(PengyOSHardeningError):
    """The committed baseline data is malformed (structural validation).

    Raised by the baseline loader when a rule is missing a required field,
    references an unknown severity/domain, or carries a matcher that does not
    conform to the schema. The data is the contract, so a broken contract is a
    hard error rather than a silently-tolerated one.
    """


class RuleError(PengyOSHardeningError):
    """A single rule failed evaluation against a file.

    Carries the finding itself (rule id, severity, file:line evidence, message)
    so callers can render it directly without re-deriving it.
    """

    def __init__(
        self,
        rule_id: str,
        severity: str,
        message: str,
        path: str | None = None,
        line: int | None = None,
    ) -> None:
        self.rule_id = rule_id
        self.severity = severity
        self.message = message
        self.path = path
        self.line = line
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        """Serialise the finding to a plain dict (used by the report layer)."""
        finding: dict[str, object] = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path is not None:
            finding["path"] = self.path
        if self.line is not None:
            finding["line"] = self.line
        return finding


@dataclass(frozen=True)
class Finding:
    """A single rule evaluation: a deviation with ``file:line`` evidence.

    Produced by a matcher when a rule fails against a file. The audit collects
    these; the report renders them. ``path``/``line`` are the evidence location
    (``None`` when the deviation is not tied to a single line, e.g. a missing
    file the rule expected to find).
    """

    rule_id: str
    severity: str
    message: str
    path: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialise the finding to a plain dict (used by the report layer)."""
        finding: dict[str, object] = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path is not None:
            finding["path"] = self.path
        if self.line is not None:
            finding["line"] = self.line
        return finding
