"""PengyOS systemd-units.

A small, standard-library-only package providing a validator for INI-style
systemd unit files (required sections, ``ExecStart`` presence, section-name
spelling, and ``WantedBy`` sanity).

The validator lives in :mod:`pengyos_systemd_units.validator`; the common names
are re-exported here for convenience::

    from pengyos_systemd_units import validate_unit, parse_unit

See :mod:`pengyos_systemd_units.validator` for the full API and rule table.
"""

from __future__ import annotations

from .validator import (
    Finding,
    Rule,
    RuleSpec,
    Severity,
    Unit,
    UnitKind,
    Validation,
    RULES,
    VALID_SECTION_NAMES,
    parse_unit,
    report,
    summary,
    to_json,
    validate_dir,
    validate_iter,
    validate_unit,
)

__all__ = [
    "Finding",
    "Rule",
    "RuleSpec",
    "Severity",
    "Unit",
    "UnitKind",
    "Validation",
    "RULES",
    "VALID_SECTION_NAMES",
    "parse_unit",
    "report",
    "summary",
    "to_json",
    "validate_dir",
    "validate_iter",
    "validate_unit",
]

__version__ = "0.1.0"
