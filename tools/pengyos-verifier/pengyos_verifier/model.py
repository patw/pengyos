"""Shared report vocabulary for the verifier.

A check produces :class:`Finding` records; a :class:`Report` aggregates
them into the pass/fail verdict. The vocabulary is intentionally boring
(frozen dataclasses with deterministic ``to_dict``/``to_json``) so the
machine-readable JSON report is stable across runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Tuple


class Status(str, Enum):
    """Outcome of a single check."""

    PASS = "pass"
    FAIL = "fail"

    def __str__(self) -> str:  # ``str(Status.FAIL)`` -> ``"fail"``
        return self.value


@dataclass(frozen=True)
class Finding:
    """The outcome of running one check against one build.

    Fields:
        check:    stable check id (see ``checks.CHECK_IDS``).
        status:   :class:`Status.PASS` or :class:`Status.FAIL`.
        message:  one-line, human-readable summary of the outcome.
        evidence: ordered, immutable tuple of concrete evidence strings
                  that pin down the outcome (paths, seq numbers, field
                  values, digest hex). A FAIL finding must always carry
                  at least one piece of evidence naming what went wrong
                  and where; a PASS finding may carry none (or the
                  confirming facts, e.g. a hash or a score).
    """

    check: str
    status: Status
    message: str
    evidence: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.check, str) or not self.check:
            raise ValueError("check id must be a non-empty string")
        if not isinstance(self.status, Status):
            try:
                object.__setattr__(self, "status", Status(self.status))
            except ValueError:
                raise ValueError(
                    f"status must be a Status, got {self.status!r}"
                ) from None
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("message must be a non-empty string")
        # A bare string here would silently become a per-CHARACTER
        # "evidence" tuple downstream (tuple(s for s in "abc")) — the
        # caller almost certainly meant a one-element tuple. Reject it
        # so the report can never carry char-split evidence.
        if isinstance(self.evidence, str):
            raise ValueError(
                "evidence must be a sequence of evidence strings, not a "
                "bare string (wrap it in a tuple: (evidence,))"
            )
        object.__setattr__(
            self, "evidence", tuple(str(e) for e in self.evidence)
        )
        if self.status is Status.FAIL and not self.evidence:
            raise ValueError(
                f"a FAIL finding for check {self.check!r} must carry "
                "evidence pinning down the failure"
            )

    @property
    def ok(self) -> bool:
        return self.status is Status.PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status.value,
            "message": self.message,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class Report:
    """The aggregate pass/fail verdict over a set of findings.

    ``build`` and ``attest`` are the labels of the inspected build
    output dir and attest log (as given by the caller), so the report
    is self-describing when written to ``--json OUT``.
    """

    build: str
    attest: str
    findings: Tuple[Finding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))

    @property
    def failed(self) -> Tuple[Finding, ...]:
        """The FAIL findings, in report order."""
        return tuple(f for f in self.findings if not f.ok)

    @property
    def ok(self) -> bool:
        """True iff no check failed."""
        return not self.failed

    @property
    def exit_code(self) -> int:
        """0 when every check passed, 1 when any check failed.

        A check that could not inspect its target (missing file) is
        still a FAIL finding — a witness that cannot see the evidence
        cannot pass it — so this mapping never grows a third report
        value. Exit code 2 stays reserved for usage errors (argparse).
        """
        return 0 if self.ok else 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verifier": "pengyos-verifier",
            "build": self.build,
            "attest": self.attest,
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self) -> str:
        """Deterministic, machine-readable JSON (sorted keys, 2-space indent)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def render(self) -> str:
        """A human-readable pass/fail report (the ``check`` command prints
        this to stdout; :meth:`to_json` is the machine-readable twin).

        Every finding is listed in report order with its status tag and
        one-line message; each piece of evidence is indented beneath its
        finding so a FAIL always shows *what* went wrong and *where*.
        """
        lines = [
            "PengyOS verifier report",
            f"  build:   {self.build}",
            f"  attest:  {self.attest or '(none)'}",
            f"  verdict: {'PASS' if self.ok else 'FAIL'} "
            f"({len(self.failed)}/{len(self.findings)} checks failed)",
            "",
        ]
        for f in self.findings:
            tag = "PASS" if f.ok else "FAIL"
            lines.append(f"  [{tag}] {f.check} - {f.message}")
            for ev in f.evidence:
                lines.append(f"         {ev}")
        return "\n".join(lines) + "\n"


def findings_from_mapping(data: Mapping[str, Any]) -> "Finding":
    """Inverse of :meth:`Finding.to_dict` (round-trips the JSON shape)."""
    return Finding(
        check=data["check"],
        status=data["status"],
        message=data["message"],
        evidence=tuple(data.get("evidence", ())),
    )
