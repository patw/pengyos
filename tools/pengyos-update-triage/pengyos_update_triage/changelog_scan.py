"""Changelog scanning: security-keyword detection and version-bump signals.

It operates on the *parsed* structure
built by :mod:`.changelogs` (:class:`~.models.Changelog` /
:class:`~.models.ChangelogSection`) and produces a flat, deterministic list
of :class:`ChangelogFinding` objects that the classification subtask (item 5)
will turn into classified, ranked proposals.

Two kinds of findings
---------------------
``security``
    One finding **per version section** in which at least one entry line
    matches the security-keyword table (below). The finding records the
    canonical keyword names that fired (deduped, in first-seen order within
    the section), the exact entry lines that matched (bullet markers
    included, as parsed), and — for CVE/GHSA style identifiers — the
    actual ids mentioned. The ids are evidence provenance, mirroring the
    advisory side.

``bump``
    One finding per changelog, present only when the changelog is attributed
    to a *pinned* component (name or alias) **and** the changelog's latest
    version — ordered by the same stdlib :func:`.matching.compare_versions`
    comparator the advisory matching uses — is strictly newer than the pinned
    version. The finding's ``version`` is the latest release and
    ``pin_version`` is the pinned one; the evidence lists every release
    newer than the pin (in changelog order). A changelog whose latest
    release is equal to or older than the pin yields no bump finding
    ("nothing newer to update" is not a signal).

Deliberately NOT done here (later subtasks)
-------------------------------------------
* Deciding what a finding *means* (security / relevant / ignore) is item 5;
  this module only reports *what the changelog says*.
* Ranking is item 6. A security finding and a bump finding for the same
  component are separate findings on purpose: classification may weigh them
  together.
* Scanning an *unpinned* component for keywords still yields security
  findings (an unpinned CVE mention is interesting input); it simply cannot
  yield a bump finding because there is no pin to compare against.

Keyword table
-------------
Case-insensitive, word-bounded patterns; a line may match several at once.
The canonical names are stable strings (the rationale and any JSON emission
depend on them being deterministic), ordered by specificity: identifier
patterns first, then vulnerability classes, then generic terms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .matching import compare_versions
from .models import Changelog, Pin

__all__ = [
    "KIND_SECURITY",
    "KIND_BUMP",
    "FINDING_KINDS",
    "KEYWORD_TABLE",
    "scan_entry",
    "find_pin_for",
    "scan_changelog",
    "scan_changelogs",
    "ChangelogFinding",
]

KIND_SECURITY = "security"
KIND_BUMP = "bump"
FINDING_KINDS: "tuple[str, ...]" = (KIND_SECURITY, KIND_BUMP)

# (canonical name, compiled pattern). Identifier patterns are listed first
# because they are the strongest signal and appear first in deduped keyword
# lists. ``_IDS`` marks the two whose *actual matched text* is worth
# recording as evidence (the id itself, not just the keyword bucket).
_ID_PATTERNS = (
    re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE),
    re.compile(r"\bGHSA-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}\b", re.IGNORECASE),
)

KEYWORD_TABLE: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("cve", _ID_PATTERNS[0]),
    ("ghsa", _ID_PATTERNS[1]),
    ("buffer overflow", re.compile(r"\bbuffer\s+(?:overflow|overread|underrun|corruption)\b", re.IGNORECASE)),
    ("heap overflow", re.compile(r"\bheap\s+(?:overflow|corruption|use-after-free)\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"\bstack\s+overflow\b", re.IGNORECASE)),
    ("integer overflow", re.compile(r"\binteger\s+overflow\b", re.IGNORECASE)),
    ("out-of-bounds", re.compile(r"\bout[- ]of[- ]bounds\b", re.IGNORECASE)),
    ("use-after-free", re.compile(r"\buse[- ]after[- ]free\b|\bUAF\b")),
    ("denial of service", re.compile(r"\bdenial\s+of\s+service\b", re.IGNORECASE)),
    ("remote code execution", re.compile(r"\bremote\s+code\s+execution\b|\bRCE\b")),
    ("privilege escalation", re.compile(r"\bprivilege\s+escalat\w*", re.IGNORECASE)),
    ("path traversal", re.compile(r"\bpath\s+traversal\b", re.IGNORECASE)),
    ("cross-site", re.compile(r"\bcross[- ]site\b|\bXSS\b|\bCSRF\b", re.IGNORECASE)),
    ("sandbox escape", re.compile(r"\bsandbox\s+escape\b", re.IGNORECASE)),
    ("race condition", re.compile(r"\brace\s+condition\b", re.IGNORECASE)),
    ("credential leak", re.compile(r"\b(?:credential|password|secret)s?\s+leak\w*", re.IGNORECASE)),
    ("bypass", re.compile(r"\bbypass\w*\b", re.IGNORECASE)),
    ("exploit", re.compile(r"\bexploit\w*\b", re.IGNORECASE)),
    ("vulnerable", re.compile(r"\bvulnerable\b", re.IGNORECASE)),
    ("vulnerability", re.compile(r"\bvulnerab\w*", re.IGNORECASE)),
    ("malicious", re.compile(r"\bmalicious\w*\b", re.IGNORECASE)),
    ("injection", re.compile(r"\binjection\b", re.IGNORECASE)),
    ("unauthorized", re.compile(r"\bunauthoriz\w*\b", re.IGNORECASE)),
    ("hardening", re.compile(r"\bhardening\b", re.IGNORECASE)),
    ("security", re.compile(r"\bsecurity\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class ChangelogFinding:
    """One signal pulled out of a component's changelog.

    Attributes
    ----------
    component:
        The component the changelog was attributed to.
    version:
        For ``security``: the version section the entries came from. For
        ``bump``: the latest release the changelog lists.
    kind:
        One of :data:`FINDING_KINDS` (``"security"`` or ``"bump"``).
    keywords:
        Canonical keyword names that fired (``security`` findings only;
        empty for ``bump``), deduped in first-seen order within the section.
    entries:
        The exact entry lines that matched (``security`` findings only);
        these are the parsed section entries, so bullet markers are part of
        the text.
    pin_version:
        The pinned version the finding is compared against, when a pin is
        attributed to the component; ``None`` for unpinned components.
    rationale:
        One human-readable line summarising the finding — the seed of the
        item 5 rationale chain and the item 7 ``explain`` output.
    evidence:
        Always non-empty; the line-level detail behind the rationale.
    """

    component: str
    version: str
    kind: str
    keywords: List[str] = field(default_factory=list)
    entries: List[str] = field(default_factory=list)
    pin_version: Optional[str] = None
    rationale: str = ""
    evidence: List[str] = field(default_factory=list)


def scan_entry(line: str) -> "Tuple[List[str], List[str]]":
    """Scan one changelog entry line.

    Returns ``(keywords, ids)``: the canonical keyword names that matched
    (deduped, in :data:`KEYWORD_TABLE` order — a single line is scanned in
    table order) and the actual CVE/GHSA identifier strings found in the
    line (in the order they appear).

    Note the *per-finding* ``keywords`` list is deduped in first-seen order
    across the section's entries, which may differ from table order when a
    section's entries match different keyword subsets.
    """
    keywords: List[str] = []
    ids: List[str] = []
    for name, pattern in KEYWORD_TABLE:
        if name in ("cve", "ghsa"):
            found = pattern.findall(line)
            if found:
                keywords.append(name)
                for token in found:
                    if token not in ids:
                        ids.append(token)
        elif pattern.search(line):
            keywords.append(name)
    return keywords, ids


def find_pin_for(component: str, pins: "List[Pin]") -> Optional[Pin]:
    """The first pin whose name or aliases cover ``component`` (else ``None``).

    Pins are consulted in the given order, so the outcome is deterministic
    for a given manifest.
    """
    for pin in pins:
        if component == pin.name or component in pin.aliases:
            return pin
    return None


def _security_findings(changelog: Changelog, pin: Optional[Pin]) -> List[ChangelogFinding]:
    findings: List[ChangelogFinding] = []
    for section in changelog.sections:
        hit_entries: List[str] = []
        keywords: List[str] = []
        ids: List[str] = []
        for entry in section.entries:
            kws, found_ids = scan_entry(entry)
            if not kws:
                continue
            hit_entries.append(entry)
            for kw in kws:  # first-seen order across the section's entries
                if kw not in keywords:
                    keywords.append(kw)
            for token in found_ids:
                if token not in ids:
                    ids.append(token)
        if not hit_entries:
            continue
        pin_note = f" (pinned {pin.version})" if pin is not None else ""
        rationale = (
            f"{changelog.component}{pin_note}: release {section.version} "
            f"lists {len(hit_entries)} security-signal entr"
            f"{'y' if len(hit_entries) == 1 else 'ies'} "
            f"({', '.join(keywords)})"
        )
        evidence: List[str] = []
        for entry in hit_entries:
            kws, found_ids = scan_entry(entry)
            suffix = f"; references {', '.join(found_ids)}" if found_ids else ""
            evidence.append(
                f"release {section.version}: entry '{entry}' matches "
                f"{', '.join(kws)}{suffix}"
            )
        findings.append(
            ChangelogFinding(
                component=changelog.component,
                version=section.version,
                kind=KIND_SECURITY,
                keywords=keywords,
                entries=hit_entries,
                pin_version=pin.version if pin is not None else None,
                rationale=rationale,
                evidence=evidence,
            )
        )
    return findings


def _bump_finding(changelog: Changelog, pin: Pin) -> Optional[ChangelogFinding]:
    if not changelog.sections:
        return None
    # ``latest`` is the changelog's newest release by ``compare_versions``
    # (a pre-release such as ``9.0-rc1`` sorts *below* its ``9.0`` GA); on a
    # tie of notation-equivalent versions (``1.0`` vs ``1.0.0``) the FIRST
    # section wins, honouring changelogs' newest-first ordering.
    versions = [s.version for s in changelog.sections]
    latest = versions[0]
    for v in versions[1:]:
        if compare_versions(v, latest) > 0:
            latest = v
    if compare_versions(latest, pin.version) <= 0:
        return None
    newer = [v for v in versions if compare_versions(v, pin.version) > 0]
    rationale = (
        f"changelog for {changelog.component} lists release {latest}, "
        f"newer than pinned {pin.version}"
    )
    evidence = [
        f"pinned {pin.version}; latest listed in changelog {latest}",
        "newer releases (changelog order): " + ", ".join(newer),
    ]
    return ChangelogFinding(
        component=changelog.component,
        version=latest,
        kind=KIND_BUMP,
        pin_version=pin.version,
        rationale=rationale,
        evidence=evidence,
    )


def scan_changelog(changelog: Changelog, pin: Optional[Pin] = None) -> List[ChangelogFinding]:
    """Scan one parsed changelog, yielding security findings (one per hit
    section) plus, when ``pin`` is given, an optional bump finding.

    Order: security findings in section (changelog) order, then the bump
    finding — deterministic.
    """
    findings = _security_findings(changelog, pin)
    if pin is not None:
        bump = _bump_finding(changelog, pin)
        if bump is not None:
            findings.append(bump)
    return findings


def scan_changelogs(
    changelogs: List[Changelog],
    pins: "List[Pin]",
) -> List[ChangelogFinding]:
    """Scan many changelogs against a pins manifest.

    Each changelog is attributed to a pin by :func:`find_pin_for`;
    unpinned changelogs are still keyword-scanned (no bump possible).
    Deterministic: changelogs in the given order, one scan each.
    """
    out: List[ChangelogFinding] = []
    for changelog in changelogs:
        out.extend(scan_changelog(changelog, find_pin_for(changelog.component, pins)))
    return out
