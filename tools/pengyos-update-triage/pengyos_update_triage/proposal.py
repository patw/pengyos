"""Emission of ``proposals.json`` + a human summary, and ``explain`` (item 7).

This is the terminal stage of the pipeline and the piece that turns the
ranked, classified findings into the tool's *output*:

* :func:`run_command` — read the pins manifest, advisory and changelog
  fixtures, drive matching → changelog scan → classification → ranking, apply
  the ``--min-severity`` filter, write ``proposals.json`` to ``--out`` and print
  a short human summary to stdout.
* :func:`explain_command` — given a proposal id, print the *full* rationale
  chain for that proposal (the ordered rule steps with the deciding rule
  highlighted), the evidence, and the source ids.

The two command functions return process exit codes and never raise on good
use (matching the CLI contract in :mod:`.cli`): ``0`` success, ``1`` ran-but-
failed (bad input, missing files, unknown proposal id), ``2`` usage (handled by
argparse upstream), ``3`` reserved. A ``run`` that produces *zero* proposals is
still a success (``0``) — "nothing to update" is a valid outcome, not an error.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from . import __version__
from .advisories import load_advisories
from .changelog_scan import scan_changelogs
from .changelogs import load_changelogs
from .classification import (
    ACTION_MONITOR,
    ACTION_NOOP,
    ACTION_UPDATE,
    CLASSIFY_IGNORE,
    CLASSIFY_RELEVANT,
    CLASSIFY_SECURITY,
    OUTCOME_FIRE,
    meets_min_severity,
    classify,
)
from .errors import InputError
from .matching import match_all
from .models import Pin
from .pins import load_pins
from .ranking import rank

__all__ = [
    "PROPOSALS_SCHEMA",
    "Proposal",
    "ProposalSet",
    "build_proposal_set",
    "proposals_to_dict",
    "write_proposals",
    "render_summary",
    "explain_proposal",
    "load_proposals",
    "run_command",
    "explain_command",
]

# The on-disk schema tag for proposals.json (bump the suffix when the shape
# changes incompatibly).
PROPOSALS_SCHEMA: str = "pengyos.proposals/v1"

# Exit codes (mirrored from :mod:`.cli`; kept local so this module does not
# import the CLI, which would be a cycle).
EXIT_OK: int = 0
EXIT_FAIL: int = 1

# Categories that become *actionable* proposals (something to update or
# monitor). ``ignore`` findings are triage *decisions* kept for transparency,
# not proposals; they are listed separately and are still explainable.
_PROPOSAL_CATEGORIES = (CLASSIFY_SECURITY, CLASSIFY_RELEVANT)


# --- Data model -------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """One emitted proposal: a ranked, classified finding, fully serializable.

    Carries everything a human (or ``explain``) needs without re-running the
    pipeline: the category/severity/action, the version range, the one-line
    rationale, the *full* rationale chain, the evidence, and the source ids.
    """

    id: str
    component: str
    category: str
    severity: str
    severity_source: str
    action: str
    pin_version: Optional[str]
    target_version: Optional[str]
    rationale: str
    identifiers: Tuple[str, ...]
    evidence: Tuple[str, ...]
    source: str
    exploitability: float
    exploitability_source: str
    order: int
    rank_signals: Dict[str, object]
    rank_rationale: str
    fired_rule: str
    rationale_chain: Tuple[Dict[str, str], ...] = field(default_factory=tuple)

    @staticmethod
    def _chain_steps(cls) -> "Tuple[Dict[str, str], ...]":
        return tuple(
            {"rule": step.rule, "outcome": step.outcome, "detail": step.detail}
            for step in cls.rationale_chain
        )

    @classmethod
    def from_ranked(cls, rc, order: int) -> "Proposal":
        """Build a :class:`Proposal` from a ranked classification."""
        c = rc.classification
        fired = c.fired_rules()
        return cls(
            id=c.id,
            component=c.component,
            category=c.category,
            severity=c.severity if c.severity else "none",
            severity_source=c.severity_source,
            action=c.suggested_action,
            pin_version=c.pin_version,
            target_version=c.target_version,
            rationale=c.rationale,
            identifiers=tuple(c.identifiers),
            evidence=tuple(c.evidence),
            source=c.source,
            exploitability=c.exploitability,
            exploitability_source=c.exploitability_source,
            order=order,
            rank_signals={
                "severity_rank": rc.severity_rank,
                "exploitability": rc.exploitability,
                "reachability": rc.reachability,
                "reachability_rank": rc.reachability_rank,
            },
            rank_rationale=rc.rank_rationale,
            fired_rule=fired[0] if fired else "",
            rationale_chain=cls._chain_steps(c),
        )

    @classmethod
    def from_dict(cls, doc: Dict[str, object]) -> "Proposal":
        """Rebuild a :class:`Proposal` from its serialized form (for ``explain``)."""
        chain = doc.get("rationale_chain") or []
        return cls(
            id=doc["id"],
            component=doc["component"],
            category=doc["category"],
            severity=doc.get("severity", "none"),
            severity_source=doc.get("severity_source", ""),
            action=doc["action"],
            pin_version=doc.get("pin_version"),
            target_version=doc.get("target_version"),
            rationale=doc["rationale"],
            identifiers=tuple(doc.get("identifiers") or ()),
            evidence=tuple(doc.get("evidence") or ()),
            source=doc.get("source", ""),
            exploitability=float(doc.get("exploitability", 0.0)),
            exploitability_source=doc.get("exploitability_source", "none"),
            order=int(doc.get("order", 0)),
            rank_signals=dict(doc.get("rank_signals") or {}),
            rank_rationale=doc.get("rank_rationale", ""),
            fired_rule=doc.get("fired_rule", ""),
            rationale_chain=tuple(chain),
        )

    def to_dict(self) -> Dict[str, object]:
        """A plain-JSON-serializable dict for this proposal."""
        return {
            "id": self.id,
            "component": self.component,
            "category": self.category,
            "severity": self.severity,
            "severity_source": self.severity_source,
            "action": self.action,
            "pin_version": self.pin_version,
            "target_version": self.target_version,
            "rationale": self.rationale,
            "identifiers": list(self.identifiers),
            "evidence": list(self.evidence),
            "source": self.source,
            "exploitability": self.exploitability,
            "exploitability_source": self.exploitability_source,
            "order": self.order,
            "rank_signals": self.rank_signals,
            "rank_rationale": self.rank_rationale,
            "fired_rule": self.fired_rule,
            "rationale_chain": list(self.rationale_chain),
        }

    def version_range(self) -> str:
        """A human ``affected → target`` string for the summary line."""
        pin = self.pin_version if self.pin_version is not None else "?"
        if self.action == ACTION_UPDATE:
            target = self.target_version or "a patched build"
            return f"{pin} -> {target}"
        if self.action == ACTION_MONITOR:
            if self.target_version:
                return f"{pin} -> {self.target_version} (newer release)"
            return f"{pin} (release review)"
        return f"{pin} (no action)"


@dataclass
class ProposalSet:
    """The full output of one ``run``: actionable proposals + ignored findings."""

    proposals: List[Proposal] = field(default_factory=list)
    ignored: List[Proposal] = field(default_factory=list)
    min_severity: str = "low"
    input_counts: Dict[str, int] = field(default_factory=dict)

    def counts(self) -> Dict[str, int]:
        security = sum(1 for p in self.proposals if p.category == CLASSIFY_SECURITY)
        relevant = sum(1 for p in self.proposals if p.category == CLASSIFY_RELEVANT)
        return {
            "security": security,
            "relevant": relevant,
            "ignore": len(self.ignored),
            "proposals": len(self.proposals),
        }

    def find(self, proposal_id: str) -> Optional[Proposal]:
        """Look up a proposal (or ignored finding) by id, else ``None``."""
        for collection in (self.proposals, self.ignored):
            for p in collection:
                if p.id == proposal_id:
                    return p
        return None

    def to_dict(self) -> Dict[str, object]:
        return proposals_to_dict(self)

    @classmethod
    def from_dict(cls, doc: Dict[str, object]) -> "ProposalSet":
        """Rebuild a :class:`ProposalSet` from a serialized ``proposals.json``."""
        return cls(
            proposals=[Proposal.from_dict(p) for p in doc.get("proposals") or []],
            ignored=[Proposal.from_dict(p) for p in doc.get("ignored") or []],
            min_severity=doc.get("min_severity", "low"),
            input_counts=dict(doc.get("inputs") or {}),
        )


# --- Building the set -------------------------------------------------------


def build_proposal_set(
    pins: List[Pin],
    advisories: List[object],
    changelogs: List[object],
    min_severity: str = "low",
) -> ProposalSet:
    """Drive the full pipeline and return the emitted :class:`ProposalSet`.

    Steps (each a pure, independently-tested module):
    1. ``match_all`` every advisory against every pin;
    2. ``scan_changelogs`` for security / bump signals;
    3. ``classify`` every match and finding (security / relevant / ignore);
    4. ``rank`` the *whole* classified set (ranking never drops);
    5. split into actionable proposals (``security``/``relevant`` that clear
       ``min_severity``) and ``ignore`` findings.

    The split is deterministic: proposals appear in the global rank order,
    re-indexed ``1..N``.
    """
    matches = match_all(list(pins), list(advisories))
    findings = scan_changelogs(list(changelogs), list(pins))
    security, relevant, ignored = classify(matches, findings)
    all_classifications = list(security) + list(relevant) + list(ignored)
    ranked = rank(all_classifications, list(pins))

    proposals: List[Proposal] = []
    ignored_list: List[Proposal] = []
    for order, rc in enumerate(ranked, start=1):
        cls = rc.classification
        if cls.category in _PROPOSAL_CATEGORIES and meets_min_severity(
            cls.severity, min_severity
        ):
            proposals.append(Proposal.from_ranked(rc, order=len(proposals) + 1))
        elif cls.category == CLASSIFY_IGNORE:
            ignored_list.append(Proposal.from_ranked(rc, order=len(ignored_list) + 1))

    return ProposalSet(
        proposals=proposals,
        ignored=ignored_list,
        min_severity=min_severity,
        input_counts={
            "pins": len(list(pins)),
            "advisories": len(list(advisories)),
            "changelogs": len(list(changelogs)),
        },
    )


def proposals_to_dict(pset: ProposalSet) -> Dict[str, object]:
    """The JSON document structure written to ``proposals.json``."""
    return {
        "schema": PROPOSALS_SCHEMA,
        "generator": f"pengyos-update-triage {__version__}",
        "min_severity": pset.min_severity,
        "counts": pset.counts(),
        "inputs": pset.input_counts,
        "proposals": [p.to_dict() for p in pset.proposals],
        "ignored": [p.to_dict() for p in pset.ignored],
    }


def write_proposals(pset: ProposalSet, out_path: Union[str, Path]) -> Path:
    """Serialize ``pset`` to ``out_path`` as pretty JSON (UTF-8, trailing newline)."""
    path = Path(out_path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = proposals_to_dict(pset)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


# --- Human summary ----------------------------------------------------------


def _summary_block(proposal: Proposal) -> List[str]:
    line = (
        f"  {proposal.order:>2}. [{proposal.category}] {proposal.component} "
        f"({', '.join(proposal.identifiers) if proposal.identifiers else proposal.source})"
    )
    meta = (
        f"     severity {proposal.severity}  action {proposal.action}  "
        f"{proposal.version_range()}"
    )
    return [line, meta, f"     {proposal.rationale}"]


def render_summary(pset: ProposalSet) -> str:
    """A short, per-proposal human summary for stdout.

    Lists each proposal's id-relevant line, severity, action, the affected
    version → suggested target, and the one-line rationale; then a compact
    tally of the ignored findings (each still explainable by id).
    """
    counts = pset.counts()
    lines: List[str] = []
    lines.append(
        f"PengyOS update-triage: {counts['proposals']} proposal(s) "
        f"(min severity: {pset.min_severity}) — "
        f"{counts['security']} security, {counts['relevant']} relevant, "
        f"{counts['ignore']} ignored"
    )
    if pset.proposals:
        for p in pset.proposals:
            lines.extend(_summary_block(p))
    else:
        lines.append("  (no actionable proposals — nothing to update)")
    lines.append("")
    lines.append(
        f"Ignored {len(pset.ignored)} finding(s) (no action); explainable by id:"
    )
    if pset.ignored:
        for p in pset.ignored:
            lines.append(f"  - [{p.category}] {p.id}: {p.rationale}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


# --- explain ----------------------------------------------------------------


def explain_proposal(proposal: Proposal) -> str:
    """The full, human-readable rationale chain for one proposal.

    Every rule step is printed; the step whose outcome is ``fire`` — the rule
    that decided the category — is explicitly highlighted (the spec's
    "explain output includes the rule that fired").
    """
    out: List[str] = []
    out.append(f"Proposal: {proposal.id}")
    out.append(f"  component:       {proposal.component}")
    out.append(f"  category:        {proposal.category}")
    out.append(
        f"  severity:        {proposal.severity} (source: {proposal.severity_source})"
    )
    out.append(f"  action:          {proposal.action}")
    out.append(f"  version:         {proposal.version_range()}")
    out.append(
        f"  exploitability:  {proposal.exploitability:.1f} "
        f"(source: {proposal.exploitability_source})"
    )
    out.append(f"  rank:            #{proposal.order} — {proposal.rank_rationale}")
    out.append(f"  source:          {proposal.source}")
    if proposal.identifiers:
        out.append(f"  identifiers:     {', '.join(proposal.identifiers)}")
    out.append("")
    out.append("Rationale (one line):")
    out.append(f"  {proposal.rationale}")
    out.append("")
    out.append("Rationale chain (the rule that decided the category is marked FIRED):")
    if proposal.rationale_chain:
        for i, step in enumerate(proposal.rationale_chain, start=1):
            marker = "FIRED" if step["outcome"] == OUTCOME_FIRE else step["outcome"]
            out.append(f"  {i}. [{marker}] {step['rule']}: {step['detail']}")
    else:
        out.append("  (no chain recorded)")
    out.append("")
    out.append("Evidence:")
    if proposal.evidence:
        for ev in proposal.evidence:
            out.append(f"  - {ev}")
    else:
        out.append("  (none)")
    return "\n".join(out)


def load_proposals(path: Union[str, Path]) -> ProposalSet:
    """Read a previously written ``proposals.json`` into a :class:`ProposalSet`."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"proposals file is not a JSON object: {path}")
    return ProposalSet.from_dict(doc)


# --- Commands ----------------------------------------------------------------


def run_command(
    pins_path: Union[str, Path],
    advisories_dir: Union[str, Path],
    changelogs_dir: Union[str, Path],
    out_path: Union[str, Path],
    min_severity: str = "low",
) -> int:
    """Run the full pipeline and write ``proposals.json`` + print a summary.

    Returns ``0`` on success (including a zero-proposal run) and ``1`` when the
    inputs cannot be read/parsed or the output cannot be written.
    """
    try:
        pins = load_pins(pins_path).pins
        advisories = load_advisories(advisories_dir)
        changelogs = load_changelogs(changelogs_dir)
        pset = build_proposal_set(pins, advisories, changelogs, min_severity)
        write_proposals(pset, out_path)
    except InputError as exc:
        sys.stderr.write(f"pengyos-update-triage: {exc}\n")
        return EXIT_FAIL
    except OSError as exc:
        sys.stderr.write(f"pengyos-update-triage: {exc}\n")
        return EXIT_FAIL

    print(render_summary(pset))
    sys.stderr.write(
        f"pengyos-update-triage: wrote {len(pset.proposals)} proposal(s) to {out_path}\n"
    )
    return EXIT_OK


def explain_command(
    proposals_path: Union[str, Path], proposal_id: str
) -> int:
    """Print the full rationale chain for ``proposal_id`` from a proposals file.

    Returns ``0`` on success; ``1`` if the file is missing/unreadable or no
    proposal (or ignored finding) has that id.
    """
    path = Path(proposals_path)
    if not path.exists():
        sys.stderr.write(f"pengyos-update-triage: proposals file not found: {path}\n")
        return EXIT_FAIL
    try:
        pset = load_proposals(path)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"pengyos-update-triage: cannot read {path}: {exc}\n")
        return EXIT_FAIL

    proposal = pset.find(proposal_id)
    if proposal is None:
        sys.stderr.write(
            f"pengyos-update-triage: no proposal with id {proposal_id!r} in {path}\n"
        )
        return EXIT_FAIL

    print(explain_proposal(proposal))
    return EXIT_OK
