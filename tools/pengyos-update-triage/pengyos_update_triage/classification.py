"""Classification of advisory matches and changelog findings.

A **transparent rule engine** that takes the
evidence produced by :mod:`.matching` (advisory ↔ pin) and
:mod:`.changelog_scan` (security keywords / version bumps) and decides, for
each, a *category* — ``security`` / ``relevant`` / ``ignore`` — together with a
severity, a suggested action, and a **rationale chain** (the ordered list of
rules that fired and why). The rationale chain is the seed of the item 7
``explain`` output ("the full rationale chain").

Categories
----------
* ``security`` — an *advisory* confirms the pinned version is affected by a
  vulnerability. Carries a CVSS-derived severity (item 5) and an ``update``
  action.
* ``relevant`` — a *changelog* signals something worth a human look (a
  security-keyword finding in a release, or a version bump past the pin) but no
  advisory quantifies it. Severity is unquantified (``none``); action
  ``monitor``.
* ``ignore`` — an advisory does not apply to the pin (different component, or
  an ecosystem mismatch), or names a version the pin is *not* inside. Action
  ``noop``.

Every :class:`Classification` is a frozen dataclass carrying:

* ``category`` / ``severity`` / ``suggested_action`` / ``target_version``
* ``rationale`` — a one-line human summary
* ``rationale_chain`` — an ordered tuple of :class:`RuleStep` (the transparent
  rule trail each decision is built from)
* ``identifiers`` — the source ids (advisory id + aliases, or the CVE/GHSA ids
  the changelog references); ``evidence`` — the detailed evidence strings
* ``source`` — provenance (advisory id, or the changelog it came from)
* ``exploitability`` — the documented exploitability signal the ranking
  subtask (item 6) orders by after severity: for an advisory quantified with a
  CVSS 3.1 vector this is the spec's **Exploitability sub-score**
  (``Roundup(8.22 × AV × AC × PR × UI)``, 0.2–3.9); ``0.0`` when the advisory
  carries no computable CVSS 3.1 vector (an unquantified signal is never
  over-claimed, mirroring the severity policy). ``exploitability_source`` is
  ``"cvss"`` or ``"none"``.

This module only *classifies*. Ranking (item 6) and the ``proposals.json``
emission / ``explain`` printer (item 7) build on :class:`Classification`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .changelog_scan import ChangelogFinding, scan_entry
from .cvss import (
    CvssError,
    exploitability as _cvss_exploitability,
    exploitability_source as _cvss_exploitability_source,
    score_components as _cvss_score_components,
    severity_from_vector,
)
from .matching import AdvisoryMatch, compare_versions
from .models import (
    SEVERITY_NONE,
    Advisory,
    severity_rank,
)

__all__ = [
    "CLASSIFY_SECURITY",
    "CLASSIFY_RELEVANT",
    "CLASSIFY_IGNORE",
    "CLASSIFICATIONS",
    "ACTION_UPDATE",
    "ACTION_MONITOR",
    "ACTION_NOOP",
    "ACTIONS",
    "OUTCOME_FIRE",
    "OUTCOME_PASS",
    "OUTCOME_SKIP",
    "RuleStep",
    "Classification",
    "derive_severity",
    "classify_advisory_match",
    "classify_changelog_finding",
    "classify",
    "meets_min_severity",
]

# --- Categories -------------------------------------------------------------
CLASSIFY_SECURITY = "security"
CLASSIFY_RELEVANT = "relevant"
CLASSIFY_IGNORE = "ignore"
CLASSIFICATIONS: "tuple[str, ...]" = (CLASSIFY_SECURITY, CLASSIFY_RELEVANT, CLASSIFY_IGNORE)

# --- Suggested actions ------------------------------------------------------
ACTION_UPDATE = "update"   # patch / upgrade the pinned component
ACTION_MONITOR = "monitor" # worth a human look; no auto action
ACTION_NOOP = "noop"       # no action
ACTIONS: "tuple[str, ...]" = (ACTION_UPDATE, ACTION_MONITOR, ACTION_NOOP)

# --- Rule outcomes ----------------------------------------------------------
OUTCOME_FIRE = "fire"  # this rule decided the category
OUTCOME_PASS = "pass"  # the rule held and evaluation continued
OUTCOME_SKIP = "skip"  # the rule did not apply to this signal


@dataclass(frozen=True)
class RuleStep:
    """One step in a classification's rationale chain.

    ``rule`` is a stable identifier (so ``explain`` and tests can reference the
    rule that fired); ``outcome`` is one of :data:`OUTCOME_FIRE` / ``pass`` /
    ``skip``; ``detail`` is the human-readable explanation for that step.

    Exactly **one** rule carries :data:`OUTCOME_FIRE` per classification — the
    rule that decided the ``category`` (the rule ``explain`` highlights). Rules
    whose condition simply held (and so evaluation continued) are ``pass``;
    rules that did not apply are ``skip``.
    """

    rule: str
    outcome: str
    detail: str


@dataclass(frozen=True)
class Classification:
    """The classified outcome for one signal (an advisory match or a finding)."""

    id: str
    component: str
    category: str
    pin_version: Optional[str]
    severity: Optional[str]
    severity_source: str
    suggested_action: str
    target_version: Optional[str]
    rationale: str
    rationale_chain: Tuple[RuleStep, ...]
    identifiers: Tuple[str, ...]
    evidence: Tuple[str, ...]
    source: str
    exploitability: float = 0.0
    exploitability_source: str = "none"

    def fired_rules(self) -> "list":
        """The ordered rule ids whose outcome is :data:`OUTCOME_FIRE`.

        By construction this is the single rule that decided the ``category``
        (see :class:`RuleStep`); it is what the item 7 ``explain`` output
        highlights.
        """
        return [step.rule for step in self.rationale_chain if step.outcome == OUTCOME_FIRE]


# --- Severity derivation ----------------------------------------------------


def _is_cvss_candidate(sev_type: str, score: str) -> bool:
    t = (sev_type or "").lower()
    s = (score or "").strip()
    return "cvss" in t or s.upper().startswith("CVSS") or "/AV:" in s


def derive_severity(advisory: Advisory) -> Tuple[str, str, str]:
    """Return ``(severity, source, detail)`` for an advisory's CVSS value.

    ``severity`` is one of :data:`~.models.SEVERITY_LEVELS` or
    :data:`~.models.SEVERITY_NONE`; ``source`` is ``"cvss"`` (a CVSS value was
    quantified — a v3.0/v3.1/v4.0 vector or a raw numeric score),
    ``"unquantified"`` (a CVSS value was present but not computable) or
    ``"none"`` (no CVSS severity at all). The first quantifiable CVSS value
    wins (deterministic: advisory's own order). The reported score is the
    **BTE** score — for a CVSS v4.0 vector the full-vector score (the detail
    names the applied group, e.g. ``CVSS-BT score 5.2``; a Base-only vector
    scores as its CVSS-B), for everything else the base score.
    """
    unquantified: List[str] = []
    for sev in advisory.severities:
        if not _is_cvss_candidate(sev.type, sev.score):
            continue
        try:
            level, score = severity_from_vector(sev.score)
        except CvssError as exc:
            unquantified.append(str(exc))
            continue
        return level, "cvss", _score_detail(sev, score)
    if unquantified:
        return SEVERITY_NONE, "unquantified", "; ".join(unquantified)
    return SEVERITY_NONE, "none", "advisory carries no CVSS severity"


# Spec §1.2/§3 nomenclature for the applied metric groups of a CVSS value's
# score (v4.0 groups only; v3.x / raw values report a plain "base score").
_GROUP_NOMENCLATURE = {
    ("base",): "CVSS-B",
    ("base", "threat"): "CVSS-BT",
    ("base", "environmental"): "CVSS-BE",
    ("base", "threat", "environmental"): "CVSS-BTE",
}


def _score_detail(sev, score: float) -> str:
    """The one-line score detail carried on the rationale chain."""
    try:
        label = _GROUP_NOMENCLATURE.get(_cvss_score_components(sev.score), "base")
    except CvssError:
        label = "base"
    return f"{sev.type} {label} score {score:.1f} ({sev.score})"


def _quantified_severity(advisory: Advisory) -> "Tuple[str, float, str] | None":
    """``(level, base_score, value)`` for the first quantifiable CVSS value.

    The same "first quantifiable value wins" rule as :func:`derive_severity`
    (advisory's own order), returning the raw value string itself so the
    exploitability signal is derived from exactly the value the severity was
    derived from.
    """
    for sev in advisory.severities:
        if not _is_cvss_candidate(sev.type, sev.score):
            continue
        try:
            level, score = severity_from_vector(sev.score)
        except CvssError:
            continue
        return level, score, sev.score
    return None


# --- Advisory match → classification ----------------------------------------


def _suggested_target(match: AdvisoryMatch) -> Optional[str]:
    """The highest advisory ``fixed`` version (by ``compare_versions``)."""
    if not match.suggested_fixes:
        return None
    target = match.suggested_fixes[0]
    for v in match.suggested_fixes[1:]:
        if compare_versions(v, target) > 0:
            target = v
    return target


def classify_advisory_match(match: AdvisoryMatch) -> Classification:
    """Classify one advisory ↔ pin match (see module docstring for categories)."""
    ids = ", ".join(match.identifiers)
    pin = match.pin
    chain: List[RuleStep] = []

    # Rule 1: does the advisory even apply to this pinned component?
    if not match.matched:
        chain.append(RuleStep(
            "advisory-applicable", OUTCOME_FIRE,
            f"advisory {ids} does not name pinned '{pin.name}' (or any alias); "
            "not applicable to this pin set",
        ))
        rationale = f"IGNORE: {pin.name} {pin.version} — advisory {ids} not applicable"
        return Classification(
            id=f"advisory:{match.advisory.id}:{pin.name}",
            component=pin.name,
            category=CLASSIFY_IGNORE,
            pin_version=pin.version,
            severity=SEVERITY_NONE,
            severity_source="none",
            suggested_action=ACTION_NOOP,
            target_version=None,
            rationale=rationale,
            rationale_chain=tuple(chain),
            identifiers=tuple(match.identifiers),
            evidence=tuple(match.evidence),
            source=match.advisory.id,
        )
    chain.append(RuleStep(
        "advisory-applicable", OUTCOME_PASS,
        f"advisory {ids} applies to pinned '{pin.name}' (package '{match.package_name}')",
    ))

    # Rule 2: is the pinned version actually inside the affected set?
    if not match.affected:
        chain.append(RuleStep(
            "advisory-affected", OUTCOME_FIRE,
            f"pinned {pin.version} is NOT in the affected set: "
            + "; ".join(match.evidence),
        ))
        rationale = (
            f"IGNORE: {pin.name} {pin.version} is not affected by {ids} "
            "(already outside the vulnerable range)"
        )
        return Classification(
            id=f"advisory:{match.advisory.id}:{pin.name}",
            component=pin.name,
            category=CLASSIFY_IGNORE,
            pin_version=pin.version,
            severity=SEVERITY_NONE,
            severity_source="none",
            suggested_action=ACTION_NOOP,
            target_version=None,
            rationale=rationale,
            rationale_chain=tuple(chain),
            identifiers=tuple(match.identifiers),
            evidence=tuple(match.evidence),
            source=match.advisory.id,
        )
    chain.append(RuleStep(
        "advisory-affected", OUTCOME_FIRE,
        f"pinned {pin.version} IS affected by {ids} → security: "
        + "; ".join(match.evidence),
    ))

    # Rule 3: severity from CVSS (a refinement, not the category decision).
    severity, sev_source, sev_detail = derive_severity(match.advisory)
    quant = _quantified_severity(match.advisory)
    if quant is not None:
        _, _, vector = quant
        explo = _cvss_exploitability(vector)
        explo_src = _cvss_exploitability_source(vector)
        if explo_src == "cvss":
            chain.append(RuleStep(
                "severity", OUTCOME_PASS,
                f"{sev_detail}; exploitability sub-score {explo:.1f} ({vector})",
            ))
        else:
            # Quantified severity, but no v3-style Exploitability sub-score
            # (CVSS v4.0 vector or raw numeric score) — never over-claim it.
            chain.append(RuleStep(
                "severity", OUTCOME_PASS,
                f"{sev_detail}; no CVSS v3 Exploitability sub-score "
                f"({vector}) → exploitability 0.0",
            ))
    else:
        explo = 0.0
        explo_src = "none"
        chain.append(RuleStep("severity", OUTCOME_PASS, sev_detail))

    # Rule 4: suggested action + target (a refinement).
    target = _suggested_target(match)
    if target is not None:
        action_detail = (
            f"update {pin.name} from {pin.version} to {target} (fix for {ids})"
        )
    else:
        action_detail = (
            f"update {pin.name} from {pin.version} to a patched build "
            f"(advisory {ids} lists no fixed version)"
        )
    chain.append(RuleStep("suggested-action", OUTCOME_PASS, action_detail))

    rationale = (
        f"SECURITY: pinned {pin.name} {pin.version} is affected by {ids} "
        f"(severity {severity}); " + (f"update to {target}" if target else "update to a patched build")
    )
    return Classification(
        id=f"advisory:{match.advisory.id}:{pin.name}",
        component=pin.name,
        category=CLASSIFY_SECURITY,
        pin_version=pin.version,
        severity=severity,
        severity_source=sev_source,
        suggested_action=ACTION_UPDATE,
        target_version=target,
        rationale=rationale,
        rationale_chain=tuple(chain),
        identifiers=tuple(match.identifiers),
        evidence=tuple(match.evidence),
        source=match.advisory.id,
        exploitability=explo,
        exploitability_source=explo_src,
    )


# --- Changelog finding → classification -------------------------------------


def _changelog_ids(finding: ChangelogFinding) -> List[str]:
    """The CVE/GHSA identifiers referenced by a finding's matched entries."""
    ids: List[str] = []
    for entry in finding.entries:
        _, found = scan_entry(entry)
        for token in found:
            if token not in ids:
                ids.append(token)
    return ids


def classify_changelog_finding(
    finding: ChangelogFinding,
    corroborating_severity: Optional[str] = None,
) -> Classification:
    """Classify one changelog finding as ``relevant`` (security signal / bump).

    ``corroboring_severity`` is an *optional* severity band (from an advisory
    that shares the finding's CVE ids) supplied by an orchestrator; when given
    it is reported instead of an unquantified severity. Absent that, a changelog
    signal has no quantified severity (``none``).
    """
    pin_note = f" (pinned {finding.pin_version})" if finding.pin_version else ""
    chain: List[RuleStep] = []

    # Rule 1: what does the changelog say?
    if finding.kind == "bump":
        signal_detail = (
            f"changelog for {finding.component}{pin_note} lists release "
            f"{finding.version}, newer than pinned {finding.pin_version}"
        )
    else:
        kws = ", ".join(finding.keywords) if finding.keywords else "security terms"
        signal_detail = (
            f"changelog for {finding.component}{pin_note} lists security signals "
            f"in release {finding.version} ({kws})"
        )
    chain.append(RuleStep("changelog-signal", OUTCOME_FIRE,
                          signal_detail + " → relevant"))

    # Rule 2: is there an advisory corroborating / quantifying this signal?
    ids = _changelog_ids(finding)
    if corroborating_severity is not None:
        chain.append(RuleStep(
            "changelog-corroboration", OUTCOME_PASS,
            f"advisory corroboration raises severity to {corroborating_severity}",
        ))
        severity, sev_source = corroborating_severity, "advisory-corroborated"
    elif finding.kind == "security" and ids:
        chain.append(RuleStep(
            "changelog-corroboration", OUTCOME_SKIP,
            f"security signal only (references {', '.join(ids)}); no advisory "
            "match quantifies it — treat as relevant, verify against advisories",
        ))
        severity, sev_source = SEVERITY_NONE, "none"
    else:
        chain.append(RuleStep(
            "changelog-corroboration", OUTCOME_SKIP,
            "no advisory corroboration; severity unquantified",
        ))
        severity, sev_source = SEVERITY_NONE, "none"
    chain.append(RuleStep("severity", OUTCOME_PASS,
                          f"severity {severity} ({sev_source})"))

    # Rule 3: suggested action.
    if finding.kind == "bump":
        action_detail = (
            f"consider updating {finding.component} from {finding.pin_version} "
            f"to {finding.version} (newer release present)"
        )
    else:
        action_detail = (
            f"review release {finding.version} of {finding.component} for the "
            f"listed security changes; no auto action"
        )
    chain.append(RuleStep("suggested-action", OUTCOME_PASS, action_detail))

    if finding.kind == "bump":
        rationale = (
            f"RELEVANT: {finding.component} has a newer release "
            f"({finding.version} > pinned {finding.pin_version})"
        )
        target = finding.version
    else:
        rationale = (
            f"RELEVANT: {finding.component} release {finding.version} lists "
            f"security signals (changelog-only signal)"
        )
        target = None

    return Classification(
        id=f"changelog:{finding.component}:{finding.version}:{finding.kind}",
        component=finding.component,
        category=CLASSIFY_RELEVANT,
        pin_version=finding.pin_version,
        severity=severity,
        severity_source=sev_source,
        suggested_action=ACTION_MONITOR,
        target_version=target,
        rationale=rationale,
        rationale_chain=tuple(chain),
        identifiers=tuple(ids),
        evidence=tuple(finding.evidence),
        source=f"changelog:{finding.component}",
    )


# --- Orchestrated classification --------------------------------------------


def classify(
    matches: List[AdvisoryMatch],
    findings: List[ChangelogFinding],
) -> Tuple[List[Classification], List[Classification], List[Classification]]:
    """Classify every advisory match and changelog finding.

    Returns ``(security, relevant, ignore)`` — three deterministic lists (input
    order preserved within each). Advisory matches map to ``security`` or
    ``ignore``; changelog findings map to ``relevant``.
    """
    security: List[Classification] = []
    relevant: List[Classification] = []
    ignore: List[Classification] = []
    for match in matches:
        cls = classify_advisory_match(match)
        (security if cls.category == CLASSIFY_SECURITY
         else ignore if cls.category == CLASSIFY_IGNORE
         else relevant).append(cls)
    for finding in findings:
        cls = classify_changelog_finding(finding)
        (security if cls.category == CLASSIFY_SECURITY
         else ignore if cls.category == CLASSIFY_IGNORE
         else relevant).append(cls)
    return security, relevant, ignore


# --- Min-severity filter ----------------------------------------------------


def meets_min_severity(severity: Optional[str], min_level: str = "low") -> bool:
    """Whether a severity clears the ``--min-severity`` threshold.

    Unquantified severities (:data:`~.models.SEVERITY_NONE` / ``None``) always
    pass — an affected-but-unquantified finding must not be silently dropped.
    Quantified severities pass when their rank is at least the minimum's rank
    (:func:`~.models.severity_rank`).
    """
    if severity is None or severity == SEVERITY_NONE:
        return True
    return severity_rank(severity) >= severity_rank(min_level)
