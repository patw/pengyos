"""Minimal systemd unit-file parser + hardening validator (US-SRV-3).

Parses INI-style ``*.service`` unit files and validates:

* required sections are present,
* required keys are present in a given section,
* a set of hardening directives are present and well-formed.

This is intentionally small and offline (stdlib only): it does not require
`systemd-analyze verify` or root. It exists so the committed exporter units can
be proven to carry the sections/keys the evaluator subset understands.

Unit-file grammar (subset)
--------------------------
* Lines are ``[Section]``, ``Key=Value``, comments (``#``/``;``), or blank.
* ``Key=Value`` — the value is everything after the first ``=``.
* A key may appear only once per section for the purpose of this validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Sections every shipped ``.service`` file must contain.
REQUIRED_SECTIONS: tuple[str, ...] = ("Unit", "Service", "Install")

#: Keys that must be present in the ``[Service]`` section.
REQUIRED_SERVICE_KEYS: tuple[str, ...] = ("ExecStart",)

#: Hardening directives (systemd.best / CIS-benchmark style) we require.
HARDENING_KEYS: tuple[str, ...] = (
    "NoNewPrivileges",
    "ProtectSystem",
    "ProtectHome",
    "PrivateTmp",
    "ProtectKernelTunables",
    "ProtectControlGroups",
    "RestrictAddressFamilies",
    "RestrictNamespaces",
    "LockPersonality",
    "RestrictSUIDSGID",
)

#: ``ProtectSystem``/``ProtectHome`` values considered adequately strict.
_PROTECT_SYSTEM_MIN = {"full", "true", "yes"}
_PROTECT_HOME_MIN = {"true", "yes", "/tmp", "/var/tmp"}

_SECTION_RE = re.compile(r"^\[([A-Za-z]+)\]\s*$")
_COMMENT_RE = re.compile(r"^\s*[#;]")
_KEYVAL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


class UnitValidationError(ValueError):
    """Raised when a unit file fails validation."""


@dataclass
class UnitSection:
    """A single ``[Section]`` block and its ``Key=Value`` pairs."""

    name: str
    keys: dict[str, str] = field(default_factory=dict)


@dataclass
class UnitFile:
    """A parsed unit file: section name -> :class:`UnitSection`."""

    path: Path
    sections: dict[str, UnitSection]

    def has_section(self, name: str) -> bool:
        return name in self.sections

    def get(self, section: str, key: str) -> str | None:
        """Return the value of ``key`` in ``section``, or ``None``."""
        sec = self.sections.get(section)
        if sec is None:
            return None
        return sec.keys.get(key)


def parse_unit(text: str) -> UnitFile:
    """Parse unit-file ``text`` into a :class:`UnitFile`.

    Raises :class:`UnitValidationError` on malformed lines.
    """
    sections: dict[str, UnitSection] = {}
    current: UnitSection | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or _COMMENT_RE.match(line):
            continue
        sec = _SECTION_RE.match(line)
        if sec:
            current = UnitSection(name=sec.group(1))
            sections[current.name] = current
            continue
        kv = _KEYVAL_RE.match(line)
        if kv is None:
            raise UnitValidationError(
                f"malformed line {lineno}: {raw!r} (expected [Section] or Key=Value)"
            )
        if current is None:
            raise UnitValidationError(
                f"key {kv.group(1)!r} at line {lineno} outside any section"
            )
        key, value = kv.group(1), kv.group(2)
        if key in current.keys:
            raise UnitValidationError(
                f"duplicate key {key!r} in [{current.name}] at line {lineno}"
            )
        current.keys[key] = value
    return UnitFile(path=Path("<parsed>"), sections=sections)


def _check_protect(value: str, minimum: set[str]) -> bool:
    """Whether a ``ProtectSystem``/``ProtectHome`` value meets the minimum."""
    token = value.split()[0].lower() if value else ""
    return token in minimum


def validate_unit(
    unit: UnitFile,
    *,
    required_sections: tuple[str, ...] = REQUIRED_SECTIONS,
    required_service_keys: tuple[str, ...] = REQUIRED_SERVICE_KEYS,
    hardening_keys: tuple[str, ...] = HARDENING_KEYS,
) -> list[str]:
    """Validate ``unit``; return a list of problem strings (empty == OK)."""
    problems: list[str] = []

    for section in required_sections:
        if not unit.has_section(section):
            problems.append(f"missing required section [{section}]")

    if unit.has_section("Service"):
        for key in required_service_keys:
            if unit.get("Service", key) is None:
                problems.append(f"missing required key {key} in [Service]")

    # Hardening directives must be present and, where relevant, well-formed.
    for key in hardening_keys:
        value = unit.get("Service", key)
        if value is None:
            problems.append(f"missing hardening directive {key}")
            continue
        if key == "ProtectSystem":
            if not _check_protect(value, _PROTECT_SYSTEM_MIN):
                problems.append(
                    f"ProtectSystem={value!r} is not sufficiently strict "
                    f"(expect full/true)"
                )
        elif key == "ProtectHome":
            if not _check_protect(value, _PROTECT_HOME_MIN):
                problems.append(
                    f"ProtectHome={value!r} is not sufficiently strict "
                    "(expect true)"
                )

    return problems


def validate_unit_file(
    path: Path | str, **kwargs: object
) -> UnitFile:
    """Load ``path`` and validate it, raising :class:`UnitValidationError`."""
    path = Path(path)
    unit = parse_unit(path.read_text(encoding="utf-8"))
    problems = validate_unit(unit, **kwargs)  # type: ignore[arg-type]
    if problems:
        raise UnitValidationError(
            f"{path.name}: " + "; ".join(problems)
        )
    return unit
