"""Validate a directory of kernel config fragments.

Implements the ``validate`` command. Three checks,
in order:

1. **Syntax** — every malformed line recorded by the tolerant parser
   (:mod:`pengyos_kernel_fragments.fragments`) is surfaced as an error finding.
2. **Conflicts** — the same :data:`CONFIG_` symbol assigned different values
   (e.g. ``=y`` in one place and ``=n`` in another, including within a single
   file) is reported as a conflict.
3. **Required symbols** — rules declared as data (see :data:`REQUIRED_RULES`)
   fire when a required symbol is absent or disabled (set to ``=n``).

See spec.md "Interface" and "Verification".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .fragments import Fragment, load_directory


class Severity(str, Enum):
    """How serious a :class:`Finding` is.

    Members compare equal to their string value so ``Severity.ERROR == "error"``.
    """

    ERROR = "error"
    WARNING = "warning"


# Stable, human-facing error codes.
CODE_SYNTAX = "SYNTAX"
CODE_CONFLICT = "CONFLICT"
CODE_MISSING_REQUIRED = "MISSING_REQUIRED"


@dataclass(frozen=True)
class Finding:
    """A single validation problem (syntax error, conflict, or missing rule)."""

    severity: Severity
    code: str
    message: str
    source: str
    line_no: int | None = None
    symbol: str | None = None

    def format(self) -> str:
        loc = self.source
        if self.line_no is not None:
            loc = f"{self.source}:{self.line_no}"
        return f"{self.severity.value}: [{self.code}] {loc}: {self.message}"


@dataclass(frozen=True)
class Conflict:
    """A symbol assigned more than one distinct value.

    :ivar values: every assignment, in load order, as ``(source, value, line_no)``.
    """

    symbol: str
    values: tuple[tuple[str, str, int], ...]


@dataclass
class ValidationReport:
    """The outcome of validating a set of fragments."""

    fragment_names: tuple[str, ...]
    conflicts: tuple[Conflict, ...]
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        """``True`` when there are no conflicts and no findings.

        (Conflict findings live inside :attr:`findings`, so a clean report has
        neither.)
        """
        return not self.conflicts and not self.findings

    @property
    def exit_code(self) -> int:
        """Process exit code: 0 when clean, 1 when any problem was found."""
        return 0 if self.ok else 1

    @property
    def problems(self) -> int:
        """Total number of reported findings (conflicts are findings too)."""
        return len(self.findings)


@dataclass(frozen=True)
class RequiredRule:
    """A required-symbol rule, declared as data (spec.md "Verification").

    A rule fires when any of :attr:`symbols` is missing from the fragments or
    present but disabled (``=n``).
    """

    name: str
    symbols: tuple[str, ...]

    @property
    def message(self) -> str:
        joined = " and ".join(self.symbols) if self.symbols else "a symbol"
        return f"{self.name} requires {joined} enabled"


# The required-symbol rules. Deliberately small and data-driven so new profiles
# can be added without code changes. ``systemd`` mirrors the spec's example.
REQUIRED_RULES: tuple[RequiredRule, ...] = (
    RequiredRule(
        name="systemd",
        symbols=("CONFIG_CGROUPS", "CONFIG_EPOLL", "CONFIG_INOTIFY"),
    ),
)


def _severity_rank(severity: Severity) -> int:
    return 0 if severity is Severity.ERROR else 1


def validate_fragments(
    fragments: Iterable[Fragment],
    rules: Iterable[RequiredRule] = REQUIRED_RULES,
) -> ValidationReport:
    """Validate an iterable of :class:`Fragment` objects.

    Parameters:
        fragments: The fragments to validate.
        rules: The required-symbol rules to apply (defaults to
            :data:`REQUIRED_RULES`).

    Returns a :class:`ValidationReport` with findings sorted deterministically:
    severity (error first), then source, then symbol, then line number.
    """
    frag_list = sorted(fragments, key=lambda f: f.name)
    names = tuple(f.name for f in frag_list)
    rules = tuple(rules)

    findings: list[Finding] = []

    # 1. Syntax: surface every malformed line recorded by the parser.
    for frag in frag_list:
        for err in frag.errors:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code=CODE_SYNTAX,
                    message=err.reason,
                    source=frag.name,
                    line_no=err.line_no,
                )
            )

    # 2. Conflicts: the same symbol assigned different values.
    assignments: dict[str, list[tuple[str, str, int]]] = {}
    for frag in frag_list:
        for entry in frag.entries:
            assignments.setdefault(entry.symbol, []).append(
                (frag.name, entry.value, entry.line_no)
            )

    conflicts: list[Conflict] = []
    for symbol in sorted(assignments):
        values = {value for (_, value, _) in assignments[symbol]}
        if len(values) > 1:
            assigned = ", ".join(
                f"{source}={value}@{line_no}"
                for (source, value, line_no) in assignments[symbol]
            )
            conflicts.append(
                Conflict(symbol=symbol, values=tuple(assignments[symbol]))
            )
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code=CODE_CONFLICT,
                    message=f"symbol {symbol} is set to more than one value: {assigned}",
                    source=symbol,
                    symbol=symbol,
                )
            )

    # 3. Required symbols: a symbol is 'enabled' if any fragment sets it to a
    #    value other than 'n'.
    enabled: dict[str, bool] = {}
    for frag in frag_list:
        for entry in frag.entries:
            enabled[entry.symbol] = enabled.get(entry.symbol, False) or (
                entry.value != "n"
            )

    for rule in rules:
        for symbol in rule.symbols:
            if not enabled.get(symbol, False):
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        code=CODE_MISSING_REQUIRED,
                        message=f"{rule.message} ({symbol})",
                        source=rule.name,
                        symbol=symbol,
                    )
                )

    findings.sort(
        key=lambda f: (
            _severity_rank(f.severity),
            f.source,
            f.symbol or "",
            f.line_no or 0,
        )
    )

    return ValidationReport(
        fragment_names=names,
        conflicts=tuple(conflicts),
        findings=tuple(findings),
    )


def validate_directory(
    directory: Path | str,
    rules: Iterable[RequiredRule] = REQUIRED_RULES,
) -> ValidationReport:
    """Load ``*.fragment`` files from *directory* and validate them."""
    return validate_fragments(load_directory(directory), rules=rules)


def select_rules(
    rules: Iterable[RequiredRule], profile: str | None
) -> tuple[RequiredRule, ...]:
    """Filter *rules* to *profile*.

    With no profile, every rule is returned. With a profile, only rules whose
    name matches (case-insensitively) are returned; an unknown profile name
    raises :class:`ValueError`.
    """
    rules = tuple(rules)
    if not profile:
        return rules
    wanted = profile.lower()
    matched = [r for r in rules if r.name.lower() == wanted]
    if not matched:
        available = ", ".join(r.name for r in rules) or "(none)"
        raise ValueError(
            f"unknown profile {profile!r}; available rules: {available}"
        )
    return tuple(matched)


def format_report(report: ValidationReport) -> str:
    """Render a report as the text the ``validate`` command prints."""
    lines = [f.format() for f in report.findings]
    if report.problems:
        lines.append(
            f"{report.problems} problem(s) across "
            f"{len(report.fragment_names)} fragment(s)."
        )
    else:
        lines.append(f"OK: {len(report.fragment_names)} fragment(s) clean.")
    return "\n".join(lines)
