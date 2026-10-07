"""Assertion runner for the PengyOS headless boot-test harness.

Given the *captured serial
console log* of a boot (see :mod:`.boot`) and a list of *checks*, evaluate each
check and report a per-check PASS/FAIL status with evidence, so that "it boots"
becomes a set of machine-checkable facts.

Two kinds of check are supported, both evaluated as plain Python regular
expressions against the accumulated serial log:

* **present** — the pattern *must* appear somewhere in the log (the "I see
  ``login:``" style assertion). PASS when a match is found; the evidence is the
  log line containing the first match.
* **absent** — the pattern *must NOT* appear anywhere in the log (the "no
  ``Kernel panic``" style assertion). PASS when no match is found; when one is
  found the check FAILS and the evidence is the line containing the first match.

Both kinds run entirely over the captured
serial log — there is no SSH/second channel (see the interface note in
``spec.md`` for why the task text's "optional SSH commands" half is dropped).

The runner is deliberately pure: it takes a log string and a list of
:class:`Assertion` and returns a list of :class:`CheckResult`. No I/O, no
processes, no QEMU — which keeps it fully unit-testable against committed log
fixtures without a network, root, display, or hardware.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "PASS_STATUS",
    "FAIL_STATUS",
    "KIND_PRESENT",
    "KIND_ABSENT",
    "MAX_EVIDENCE_CHARS",
    "Assertion",
    "CheckResult",
    "default_assertions",
    "run_assertions",
    "all_passed",
]

# --- Status / kind constants ----------------------------------------------
PASS_STATUS: str = "passed"
FAIL_STATUS: str = "failed"
KIND_PRESENT: str = "present"
KIND_ABSENT: str = "absent"

# Cap the evidence (a whole serial log line) so a pathological multi-kilobyte
# line can't bloat a report. Mirrors ``boot.MAX_EVIDENCE_CHARS``.
MAX_EVIDENCE_CHARS: int = 512


def _line_of(log_text: str, match: "re.Match[str]") -> "str | None":
    """Return the log line containing ``match`` (stripped, truncated).

    Mirrors the evidence rule used by ``boot.detect_readiness``: the evidence is
    the *line* that contains the first match, not just the matched substring,
    because a bare match (e.g. ``login:``) is unreadable out of context. The
    line is stripped and truncated to :data:`MAX_EVIDENCE_CHARS`; a line that
    is empty after stripping yields ``None``.
    """
    start = log_text.rfind("\n", 0, match.start()) + 1
    end = log_text.find("\n", match.end())
    if end == -1:
        end = len(log_text)
    line = log_text[start:end].strip()
    return line[:MAX_EVIDENCE_CHARS] or None


@dataclass(frozen=True)
class Assertion:
    """A single check to run against the serial log.

    Attributes:
        name: human-readable identifier for this check (used in reports /
            JUnit ``<testcase name=...>``).
        pattern: a regular expression searched anywhere in the log.
        kind: :data:`KIND_PRESENT` (must appear) or :data:`KIND_ABSENT`
            (must not appear).
    """

    name: str
    pattern: str
    kind: str = KIND_PRESENT

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ValueError("Assertion.name must be a non-empty string")
        if not self.pattern:
            raise ValueError(f"Assertion.pattern for {self.name!r} must be a non-empty string")
        if self.kind not in (KIND_PRESENT, KIND_ABSENT):
            raise ValueError(
                f"Assertion.kind for {self.name!r} must be {KIND_PRESENT!r} or "
                f"{KIND_ABSENT!r} (got {self.kind!r})"
            )

    # --- Convenience constructors -------------------------------------------
    @classmethod
    def present(cls, name: str, pattern: str) -> "Assertion":
        """A check that ``pattern`` *must* appear in the log."""
        return cls(name, pattern, KIND_PRESENT)

    @classmethod
    def absent(cls, name: str, pattern: str) -> "Assertion":
        """A check that ``pattern`` must *not* appear in the log."""
        return cls(name, pattern, KIND_ABSENT)


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one :class:`Assertion` against the serial log.

    Attributes:
        name: the check name (copied from the :class:`Assertion`).
        status: :data:`PASS_STATUS` or :data:`FAIL_STATUS`.
        evidence: the log line justifying the result — the line of the first
            match for a failing absent / passing present check, else ``None``
            (e.g. a passing absent check has no match line to point at).
    """

    name: str
    status: str
    evidence: "str | None" = None

    @property
    def passed(self) -> bool:
        """True when this check passed."""
        return self.status == PASS_STATUS

    def to_dict(self) -> "dict":
        """The spec's JSON shape for one check: ``{name, status, evidence}``."""
        return {
            "name": self.name,
            "status": self.status,
            "evidence": self.evidence,
        }


def default_assertions() -> "tuple[Assertion, ...]":
    """The built-in check set: "it booted to a login without panicking".

    This is what the boot flow runs when the caller does not supply its own
    checks. It is intentionally small and derived from real PengyOS serial logs:
    a successful boot ends at ``login:`` and never emits a kernel panic.
    """
    return (
        Assertion.present("reached-login", "login:"),
        Assertion.absent("no-kernel-panic", "Kernel panic"),
    )


def _evaluate(log_text: str, assertion: "Assertion") -> CheckResult:
    """Evaluate a single assertion against the log and return its result.

    Raises:
        re.error: if ``assertion.pattern`` does not compile.
    """
    compiled = re.compile(assertion.pattern)
    match = compiled.search(log_text)
    if assertion.kind == KIND_PRESENT:
        if match is None:
            return CheckResult(assertion.name, FAIL_STATUS, None)
        return CheckResult(assertion.name, PASS_STATUS, _line_of(log_text, match))
    # KIND_ABSENT
    if match is None:
        return CheckResult(assertion.name, PASS_STATUS, None)
    return CheckResult(assertion.name, FAIL_STATUS, _line_of(log_text, match))


def run_assertions(
    log_text: str,
    assertions: "tuple[Assertion, ...] | list[Assertion]",
) -> "list[CheckResult]":
    """Run every assertion in ``assertions`` against ``log_text``.

    Args:
        log_text: the captured serial console log (may be empty — then no
            present check passes and every absent check passes).
        assertions: the checks to run. An empty list is allowed and yields an
            empty result list.

    Returns:
        One :class:`CheckResult` per assertion, in input order.

    Raises:
        re.error: if any assertion's pattern does not compile.
        ValueError: if any assertion is malformed (see :class:`Assertion`).
    """
    return [_evaluate(log_text, a) for a in assertions]


def all_passed(results: "list[CheckResult] | tuple[CheckResult, ...]") -> bool:
    """True when every result passed. An empty result set passes vacuously."""
    return all(r.passed for r in results)
