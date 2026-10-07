"""The canonical input schema for pengyos-update-triage.

This module defines the *internal* data model the tool works in, independent of
the on-disk wire formats (TOML for pins, OSV JSON for advisories, plain-text
files for changelogs). The loaders in :mod:`.pins`, :mod:`.advisories` and
:mod:`.changelogs` translate their respective wire formats into these
dataclasses; the classification / ranking / emission subtasks build on top of
them.

Keeping the schema in one place (with the loader modules only doing *mapping*)
means the fixture-friendly contract is documented here and is testable in
isolation — a fixture either yields a well-typed object or raises a structured
error (see :mod:`.errors`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# --- Documented ordering scales ---------------------------------------------
# Reachability of a component, from LEAST to MOST reachable from the running
# system. This ordering feeds the ranking subtask:
# the more reachable a component, the higher a finding for it ranks, all else
# being equal.
#   * opt  — optional / developer / non-runtime component (e.g. a dev tool)
#   * std  — standard, present but not always on the critical runtime path
#   * core — core to the running system (e.g. base C library, kernel, init)
REACHABILITY_LEVELS: "tuple[str, ...]" = ("opt", "std", "core")
DEFAULT_REACHABILITY: str = "std"


def reachability_rank(reachability: str) -> int:
    """Return the 0-based rank of ``reachability`` (higher = more reachable).

    Unknown values are treated as the lowest rank (0) so that an unfamiliar
    reachability label degrades gracefully rather than crashing; the rank scale
    is the *ordering* contract the ranking subtask relies on.
    """
    try:
        return REACHABILITY_LEVELS.index(reachability)
    except ValueError:
        return 0


# --- Severity ---------------------------------------------------------------
# The severity *levels* emitted on a proposal, from lowest to highest. This is
# the fixed vocabulary the CLI's ``--min-severity`` filter accepts and the
# ranking subtask orders by. A proposal that cannot
# be quantified (an affected finding with no CVSS score) is reported at
# :data:`SEVERITY_NONE`, which sorts *below* ``low`` so it is never over-claimed
# while still always clearing the default ``low`` minimum.
SEVERITY_LEVELS: "tuple[str, ...]" = ("low", "medium", "high", "critical")
SEVERITY_NONE: str = "none"


def severity_rank(severity: "str | None") -> int:
    """Return the 0-based rank of a severity label (higher = more severe).

    :data:`SEVERITY_NONE` (or ``None`` / an unknown label) ranks below ``low``
    so an unquantified finding is never ordered above a genuinely ``low`` one.
    The scale is the *ordering* contract the ``--min-severity`` filter and the
    ranking subtask rely on.
    """
    if severity is None or severity == SEVERITY_NONE:
        return -1
    try:
        return SEVERITY_LEVELS.index(severity)
    except ValueError:
        return -1


# --- Pins -------------------------------------------------------------------


@dataclass(frozen=True)
class Pin:
    """A single pinned component from the pins manifest.

    Attributes
    ----------
    name:
        Primary component name. Used to match advisory package names.
    version:
        The exact pinned version string. Matching compares the advisory's
        affected range against this value.
    ecosystem:
        The package ecosystem the version belongs to (e.g. ``"alpine"``).
        Advisory matching prefers an ecosystem-qualified match when both the
        pin and the advisory carry an ecosystem.
    aliases:
        Alternative names the same component may appear under in an advisory
        (e.g. a distro package name vs. an upstream project name). Matching
        succeeds if the advisory's package name equals ``name`` *or* any alias.
    reachability:
        How reachable the component is from the running system; drawn from
        :data:`REACHABILITY_LEVELS`. Feeds ranking, not matching.
    note:
        Optional free-text note about why this component is pinned.
    line:
        1-based line number of the ``[[component]]`` table in the source
        manifest, for precise diagnostics; ``None`` when unknown.
    """

    name: str
    version: str
    ecosystem: str = "generic"
    aliases: List[str] = field(default_factory=list)
    reachability: str = DEFAULT_REACHABILITY
    note: str = ""
    line: Optional[int] = None

    def matches_name(self, candidate: str) -> bool:
        """True if ``candidate`` names this component (primary name or alias)."""
        return candidate == self.name or candidate in self.aliases


# --- Advisories (OSV shape) -------------------------------------------------


@dataclass(frozen=True)
class RangeEvent:
    """One event in a version range.

    OSV encodes an event as a single-key object such as ``{"introduced": "0"}``
    or ``{"fixed": "1.2.3"}``. At most one of these is normally set per event,
    but the model is tolerant: any that are absent are ``None``.
    """

    introduced: Optional[str] = None
    fixed: Optional[str] = None
    last_affected: Optional[str] = None
    vulnerable: Optional[str] = None


@dataclass
class Range:
    """A version range over a single coordinate system (``type``)."""

    type: str
    events: List[RangeEvent] = field(default_factory=list)


@dataclass
class Affected:
    """One affected package entry in an advisory.

    ``versions`` lists explicitly-affected versions (OSV allows a plain list
    alongside ``ranges``); ``ranges`` are the interval-style descriptions.
    Matching (a later subtask) considers a pinned version affected if it is
    listed in ``versions`` *or* falls inside any ``ranges`` interval.
    """

    package_name: str
    ecosystem: str
    ranges: List[Range] = field(default_factory=list)
    versions: List[str] = field(default_factory=list)
    database_specific: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Severity:
    """One severity annotation on an advisory.

    ``type`` is the scheme (e.g. ``"CVSS_V3"``, ``"CVSS_V4"``); ``score`` is the
    raw value, typically a CVSS *vector* string such as
    ``"CVSS:3.1/AV:N/AC:L/..."``. The mapping of a vector → the
    low/medium/high/critical vocabulary is a later (classification) concern.
    """

    type: str
    score: str


@dataclass
class Advisory:
    """A single security advisory, normalised from an OSV JSON document.

    The OSV ``id`` is primary; ``aliases`` carries other identifiers (notably
    CVE ids) so the tool can cross-reference. ``source_file`` records which
    fixture file the advisory came from — this is the *evidence* provenance the
    "every proposal carries source ids" acceptance criterion depends on.
    """

    id: str
    summary: str = ""
    details: str = ""
    aliases: List[str] = field(default_factory=list)
    published: Optional[str] = None
    modified: Optional[str] = None
    severities: List[Severity] = field(default_factory=list)
    affected: List[Affected] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    database_specific: Dict[str, object] = field(default_factory=dict)
    source_file: Optional[str] = None

    def all_identifiers(self) -> List[str]:
        """The advisory id plus all aliases (the full set of source ids)."""
        ids = [self.id]
        ids.extend(a for a in self.aliases if a != self.id)
        return ids


# --- Changelogs -------------------------------------------------------------


@dataclass
class ChangelogSection:
    """One version section of a changelog.

    ``version`` is the version the section describes; ``header_line`` is the raw
    line that introduced it; ``entries`` are the bullet/body lines beneath it
    up to (not including) the next section header.
    """

    version: str
    header_line: str
    entries: List[str] = field(default_factory=list)


@dataclass
class Changelog:
    """An upstream changelog for one component.

    ``component`` is the name the changelog was attributed to (via the optional
    ``component:`` directive or the file-name stem); ``sections`` is the parsed
    version-structure the changelog-scanning subtask
    will keyword-scan.
    """

    component: str
    path: str
    text: str = ""
    sections: List[ChangelogSection] = field(default_factory=list)


__all__ = [
    "REACHABILITY_LEVELS",
    "DEFAULT_REACHABILITY",
    "reachability_rank",
    "SEVERITY_LEVELS",
    "SEVERITY_NONE",
    "severity_rank",
    "Pin",
    "RangeEvent",
    "Range",
    "Affected",
    "Severity",
    "Advisory",
    "ChangelogSection",
    "Changelog",
]
