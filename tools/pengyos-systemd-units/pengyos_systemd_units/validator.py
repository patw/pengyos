"""pengyos_systemd_units.validator — INI-style systemd unit validator.

A small, standard-library-only validator for systemd unit files. It parses the
INI-style key/value format systemd consumes and enforces a rule table:

- required sections are present (``[Unit]`` always, plus ``[Service]``/``[Socket]``
  for the kinds that run or listen),
- ``[Section]`` names are spelled correctly (no typos),
- ``ExecStart`` is present for units that run one (``.service``),
- ``ExecStart`` is absent on ``.target`` units,
- ``.socket`` units declare ``ListenStream`` and ``Service`` in ``[Socket]``,
- any ``[Install]`` section declares ``WantedBy`` (``WantedBy`` sanity).

The rules are encoded as data (see :data:`RULES`) so they can be exercised as
table-driven tests in the follow-up task "Encode rules as data".

Public surface
--------------
- :func:`parse_unit` — parse a unit file into a :class:`Unit`.
- :class:`Finding` / :class:`Rule` — structured findings and the rule table.
- :func:`validate_unit` — validate a single unit file into :class:`Validation`.
- :func:`validate_dir` — validate every unit in a directory.
- :func:`validate_iter` — validate an iterable of paths, collecting findings.
"""

from __future__ import annotations

import configparser
import json
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Iterable, Iterator

__all__ = [
    "Severity",
    "Rule",
    "Unit",
    "Finding",
    "Validation",
    "UnitKind",
    "RULES",
    "VALID_SECTION_NAMES",
    "parse_unit",
    "validate_unit",
    "validate_iter",
    "validate_dir",
]


# --------------------------------------------------------------------------- #
# Parsed model
# --------------------------------------------------------------------------- #
class UnitKind(str, Enum):
    """The systemd unit kind, derived from the file suffix."""

    SERVICE = "service"
    TARGET = "target"
    SOCKET = "socket"
    UNKNOWN = "unknown"

    @property
    def suffix(self) -> str:
        return f".{self.value}"

    @classmethod
    def from_suffix(cls, suffix: str) -> "UnitKind":
        """Map a file suffix (``".service"`` or ``"service"``) to a kind.

        Unknown suffixes map to :attr:`UNKNOWN` rather than raising.
        """
        key = suffix.lower().lstrip(".")
        try:
            return cls(key)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class Unit:
    """A parsed systemd unit file.

    ``sections`` maps ``"[Name]"`` -> ``{key: value}`` preserving key case,
    matching systemd's case-sensitive key semantics.
    """

    name: str
    kind: UnitKind
    sections: dict[str, dict[str, str]]
    path: Path | None = None

    def has_section(self, section: str) -> bool:
        return section in self.sections

    def keys(self, section: str) -> set[str]:
        """Return the set of keys declared in ``[section]`` (empty if absent)."""
        return set(self.sections.get(section, {}))

    def value(self, section: str, key: str) -> str | None:
        return self.sections.get(section, {}).get(key)


# --------------------------------------------------------------------------- #
# Rule table (data-driven)
# --------------------------------------------------------------------------- #
class Severity(str, Enum):
    """How serious a finding is."""

    ERROR = "error"
    WARNING = "warning"

    @property
    def code(self) -> int:
        """Exit-code contribution: a run with any error is non-zero."""
        return 1 if self is Severity.ERROR else 0


class Rule(str, Enum):
    """The identifier of a rule in the validation rule table."""

    REQUIRED_UNIT = "required[Unit]"
    SECTION_NAME = "section_name"
    REQUIRED_SERVICE = "required[Service]"
    EXEC_START = "execStart"
    TARGET_NO_EXEC_START = "target-no-execStart"
    SOCKET_LISTEN_STREAM = "socket[ListenStream]"
    SOCKET_SERVICE = "socket[Service]"
    WANTEDBY = "install[WantedBy]"


@dataclass(frozen=True)
class RuleSpec:
    """Static description of a rule: its severity and the kinds it constrains."""

    rule: Rule
    severity: Severity
    # ``None`` means "applies to every unit kind"; otherwise a set of kinds.
    applies_to: frozenset[UnitKind] | None = None


# The canonical systemd section names (freedesktop.org unit file spec).
VALID_SECTION_NAMES: frozenset[str] = frozenset(
    {
        "Unit",
        "Service",
        "Install",
        "Socket",
        "Timer",
        "Mount",
        "Automount",
        "Swap",
        "Path",
        "Slice",
        "Scope",
        "Target",
    }
)

# The rule table. Each rule carries its severity and the unit kinds it applies
# to. This is the single source of truth exercised by the table-driven tests.
RULES: tuple[RuleSpec, ...] = (
    RuleSpec(Rule.REQUIRED_UNIT, Severity.ERROR),
    RuleSpec(Rule.SECTION_NAME, Severity.ERROR),
    RuleSpec(
        Rule.REQUIRED_SERVICE,
        Severity.ERROR,
        frozenset({UnitKind.SERVICE}),
    ),
    RuleSpec(
        Rule.EXEC_START,
        Severity.ERROR,
        frozenset({UnitKind.SERVICE}),
    ),
    RuleSpec(
        Rule.TARGET_NO_EXEC_START,
        Severity.ERROR,
        frozenset({UnitKind.TARGET}),
    ),
    RuleSpec(
        Rule.SOCKET_LISTEN_STREAM,
        Severity.ERROR,
        frozenset({UnitKind.SOCKET}),
    ),
    RuleSpec(
        Rule.SOCKET_SERVICE,
        Severity.ERROR,
        frozenset({UnitKind.SOCKET}),
    ),
    RuleSpec(
        Rule.WANTEDBY,
        Severity.ERROR,
    ),
)


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Finding:
    """A single validation finding against one unit."""

    rule: Rule
    severity: Severity
    message: str
    section: str | None = None
    key: str | None = None

    def as_dict(self) -> dict[str, str]:
        data: dict[str, str] = {
            "rule": self.rule.value,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.section is not None:
            data["section"] = self.section
        if self.key is not None:
            data["key"] = self.key
        return data


@dataclass
class Validation:
    """The result of validating a single unit: its findings and pass/fail."""

    unit: Unit
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """A unit is OK when it carries no error-level findings."""
        return not self.errors


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _parse_unit_file(path: Path) -> Unit:
    """Parse ``path`` into a :class:`Unit`, preserving key case.

    Raises :class:`configparser.Error` when the file is not parseable as INI
    (systemd keys are case-sensitive, so ``optionxform`` is a no-op).
    """
    parser = configparser.ConfigParser(strict=False, allow_no_value=True)
    parser.optionxform = str  # do NOT lowercase keys (systemd semantics)
    with path.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    sections = {name: dict(parser[name]) for name in parser.sections()}
    return Unit(
        name=path.name,
        kind=UnitKind.from_suffix(path.suffix),
        sections=sections,
        path=path,
    )


def parse_unit(path: str | Path) -> Unit:
    """Parse a unit file into a :class:`Unit`.

    Alias for :func:`_parse_unit_file` with the public signature. Raises
    :class:`configparser.Error` on malformed INI.
    """
    return _parse_unit_file(Path(path))


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _findings_for_unit(unit: Unit) -> list[Finding]:
    """Compute the findings for a single parsed :class:`Unit`.

    This is the content-level rule engine. It never raises for a malformed unit
    (parsing is the caller's responsibility) — it simply reports what it sees.
    """
    findings: list[Finding] = []

    # Rule: every unit needs a [Unit] section.
    if not unit.has_section("Unit"):
        findings.append(
            Finding(
                rule=Rule.REQUIRED_UNIT,
                severity=Severity.ERROR,
                message=f"{unit.name}: missing required [Unit] section",
            )
        )

    # Rule: section names must be spelled correctly.
    for section_name in unit.sections:
        if section_name not in VALID_SECTION_NAMES:
            findings.append(
                Finding(
                    rule=Rule.SECTION_NAME,
                    severity=Severity.ERROR,
                    message=f"{unit.name}: unknown section name [{section_name}]",
                    section=section_name,
                )
            )

    # Per-kind rules.
    if unit.kind is UnitKind.SERVICE:
        if not unit.has_section("Service"):
            findings.append(
                Finding(
                    rule=Rule.REQUIRED_SERVICE,
                    severity=Severity.ERROR,
                    message=f"{unit.name}: [Service] section is missing",
                    section="Service",
                )
            )
        elif "ExecStart" not in unit.keys("Service"):
            findings.append(
                Finding(
                    rule=Rule.EXEC_START,
                    severity=Severity.ERROR,
                    message=f"{unit.name}: [Service] is missing ExecStart",
                    section="Service",
                    key="ExecStart",
                )
            )

    if unit.kind is UnitKind.TARGET:
        for key in unit.keys("Service"):
            if key == "ExecStart":
                findings.append(
                    Finding(
                        rule=Rule.TARGET_NO_EXEC_START,
                        severity=Severity.ERROR,
                        message=f"{unit.name}: target must not define ExecStart",
                        section="Service",
                        key=key,
                    )
                )

    if unit.kind is UnitKind.SOCKET:
        if not unit.has_section("Socket"):
            findings.append(
                Finding(
                    rule=Rule.SOCKET_LISTEN_STREAM,
                    severity=Severity.ERROR,
                    message=f"{unit.name}: [Socket] section is missing",
                    section="Socket",
                )
            )
        else:
            if "ListenStream" not in unit.keys("Socket"):
                findings.append(
                    Finding(
                        rule=Rule.SOCKET_LISTEN_STREAM,
                        severity=Severity.ERROR,
                        message=f"{unit.name}: [Socket] is missing ListenStream",
                        section="Socket",
                        key="ListenStream",
                    )
                )
            if "Service" not in unit.keys("Socket"):
                findings.append(
                    Finding(
                        rule=Rule.SOCKET_SERVICE,
                        severity=Severity.ERROR,
                        message=f"{unit.name}: [Socket] is missing Service",
                        section="Socket",
                        key="Service",
                    )
                )

    # Rule: any [Install] section must declare WantedBy (WantedBy sanity).
    if unit.has_section("Install"):
        if "WantedBy" not in unit.keys("Install"):
            findings.append(
                Finding(
                    rule=Rule.WANTEDBY,
                    severity=Severity.ERROR,
                    message=f"{unit.name}: [Install] is missing WantedBy",
                    section="Install",
                    key="WantedBy",
                )
            )

    return findings


def validate_unit(path: str | Path) -> Validation:
    """Validate a single unit file, returning a :class:`Validation`.

    Raises :class:`configparser.Error` when the file is not parseable as INI.
    """
    unit = parse_unit(path)
    return Validation(unit=unit, findings=_findings_for_unit(unit))


def validate_iter(paths: Iterable[str | Path]) -> Iterator[Validation]:
    """Validate an iterable of unit paths, yielding one :class:`Validation`."""
    for path in paths:
        yield validate_unit(path)


def validate_dir(directory: str | Path) -> list[Validation]:
    """Validate every unit in ``directory``, sorted by name.

    Only files whose suffix is a recognised unit kind are validated; anything
    else (notably ``README.md``) is skipped.
    """
    directory = Path(directory)
    units = sorted(
        child
        for child in directory.iterdir()
        if child.is_file() and child.suffix.lstrip(".").lower() in {k.value for k in UnitKind}
    )
    return [validate_unit(unit) for unit in units]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def summary(validations: Iterable[Validation]) -> dict[str, int]:
    """Collapse a collection of :class:`Validation` into pass/fail counts."""
    total = 0
    passed = 0
    errored = 0
    for validation in validations:
        total += 1
        if validation.ok:
            passed += 1
        else:
            errored += 1
    return {"units": total, "passed": passed, "errored": errored}


def report(validations: Iterable[Validation]) -> str:
    """Render a human-readable report over a collection of validations."""
    lines: list[str] = []
    for validation in validations:
        unit = validation.unit
        if validation.ok:
            lines.append(f"OK    {unit.name}")
            continue
        for finding in validation.errors:
            lines.append(f"FAIL  {unit.name}: {finding.message}")
    return "\n".join(lines)


def to_json(validations: Iterable[Validation]) -> str:
    """Render a collection of validations as a JSON array of findings."""
    payload = [
        {
            "unit": validation.unit.name,
            "kind": validation.unit.kind.value,
            "ok": validation.ok,
            "findings": [f.as_dict() for f in validation.findings],
        }
        for validation in validations
    ]
    return json.dumps(payload, indent=2, sort_keys=True)
