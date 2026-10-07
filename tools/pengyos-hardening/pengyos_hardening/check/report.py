"""Rendering: turn an :class:`AuditResult` into stable text or JSON.

The report is the third submodule (spec.md). It is intentionally the *only*
place that decides how an audit looks: the auditor owns the findings, the
report owns the layout. Two contracts are served:

* **Text** — human-readable, deterministic. A fixed header, a one-line summary
  (rules audited / findings / skipped / passed, plus a per-severity breakdown),
  then the findings (each with its ``file:line`` evidence), then the skipped
  rules. The layout never depends on dict iteration order or wall-clock time, so
  it is golden-able.
* **JSON** — the machine-readable form consumed by CI. Stable key order, sorted
  findings, and a ``summary`` block mirroring the text header.

Both are golden-able for a given tree (see ``tests/test_report.py``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from ..errors import BaselineError
from .audit import AuditResult

__all__ = [
    "render_text",
    "render_json",
    "write_json",
    "write_text",
    "summary",
]


def summary(result: AuditResult) -> dict[str, Any]:
    """The headline counts that head both the text and JSON reports.

    * ``rules_audited`` — every rule the baseline declares.
    * ``findings`` — total findings across all locations.
    * ``rules_with_findings`` — distinct rules that fired at least once.
    * ``passed`` — rules audited, minus skipped, minus rules that fired
      (i.e. rules that audited clean).
    * ``skipped`` — distinct rules whose matcher is not yet implemented.
    * ``by_severity`` — the finding count per severity, in
      :data:`pengyos_hardening.baseline.SEVERITY_ORDER` order (always all five,
      zero-filled) so the breakdown is stable even when a severity is absent.
    * ``by_domain`` — the finding count per domain, in declaration order.
    """
    from ..baseline import SEVERITY_ORDER, load_baseline

    rules = load_baseline().rules
    by_severity = {level: 0 for level in SEVERITY_ORDER}
    rules_with_findings: set[str] = set()
    for finding in result.findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        rules_with_findings.add(finding.rule_id)

    # Findings per domain, resolved against the baseline's domain declaration
    # order (stable by construction) so the breakdown never depends on the
    # order findings happened to arrive in.
    rules_by_id = {rule.id: rule for rule in rules}
    by_domain: dict[str, int] = {rule.domain: 0 for rule in rules}
    for finding in result.findings:
        rule = rules_by_id.get(finding.rule_id)
        domain = rule.domain if rule is not None else "unknown"
        by_domain[domain] = by_domain.get(domain, 0) + 1

    return {
        "rules_audited": result.rules_audited,
        "findings": len(result.findings),
        "rules_with_findings": len(rules_with_findings),
        "passed": result.rules_audited
        - len(result.rule_ids_skipped)
        - len(rules_with_findings),
        "skipped": len(result.rule_ids_skipped),
        "by_severity": by_severity,
        "by_domain": by_domain,
    }


# --- text -------------------------------------------------------------------

def render_text(result: AuditResult, tree: Path) -> str:
    """Render the audit as stable, deterministic text.

    The layout is fixed: a title line underlined by dashes, a one-line summary
    (rules audited · findings · skipped · passed, plus a per-severity breakdown),
    the findings (each with its ``file:line`` evidence), and finally the skipped
    rules. The exact bytes are golden in ``tests/test_report.py``.
    """
    from ..baseline import SEVERITY_ORDER

    lines: list[str] = []
    header = f"pengyOS hardening audit — {tree}"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append("")

    if not result.locations:
        lines.append(
            "no auditable locations found (no */rootfs-overlay/ or *_defconfig)"
        )
        lines.append("")
        return "\n".join(lines)

    # The one-line summary: counts + a per-severity breakdown of findings.
    summ = summary(result)
    breakdown = ", ".join(
        f"{summ['by_severity'][level]} {level}" for level in SEVERITY_ORDER
    )
    lines.append(
        f"{summ['rules_audited']} rule(s) audited · "
        f"{summ['findings']} finding(s) · "
        f"{summ['skipped']} skipped · "
        f"{summ['passed']} passed"
    )
    lines.append(f"by severity: {breakdown}")
    lines.append("")

    if not result.findings:
        lines.append("OK — no deviations from the baseline.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"Findings ({summ['findings']}):")
    for finding in result.sorted_findings():
        loc = finding.path
        if finding.line is not None:
            loc = f"{loc}:{finding.line}"
        lines.append(
            f"  [{finding.severity:<8}] {finding.rule_id}  —  {finding.message}"
        )
        if loc:
            lines.append(f"      {loc}")
    lines.append("")

    skipped = result.rule_ids_skipped
    if skipped:
        lines.append("Skipped (matcher not yet implemented):")
        lines.append(f"  {', '.join(skipped)}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# --- JSON -------------------------------------------------------------------

def render_json(result: AuditResult, tree: Path) -> dict[str, object]:
    """Serialise the audit as a JSON-ready dict (stable key order)."""
    return {
        "tree": str(tree),
        "summary": summary(result),
        "rules_audited": result.rules_audited,
        "finding_count": len(result.findings),
        "skipped": result.rule_ids_skipped,
        "findings": [f.to_dict() for f in result.sorted_findings()],
    }


# --- writers ----------------------------------------------------------------

def write_json(result: AuditResult, tree: Path, out: str | None = None) -> None:
    """Emit JSON findings.

    * ``out is None`` — write to stdout.
    * ``out == '-'` — write to stdout.
    * ``out == '<path>'` — write to that file.
    """
    document = render_json(result, tree)
    payload = json.dumps(document, indent=2, sort_keys=True)
    if out is None or out == "-":
        print(payload)
    else:
        Path(out).write_text(payload + "\n", encoding="utf-8")


def write_text(result: AuditResult, tree: Path, stream: TextIO | None = None) -> None:
    """Render and print the text report to ``stream`` (stdout by default)."""
    text = render_text(result, tree)
    if stream is None:
        print(text)
    else:
        print(text, file=stream)


def main(args: "Any") -> int:  # type: ignore[name-defined]  # noqa: F821
    """CLI entry point for ``pengyos-hardening check``.

    Reads ``--tree``/``--rule``/``--json`` from the incoming argparse namespace
    and dispatches. Exits ``0`` always (a tree with deviations is not a crash —
    it is a valid audit report); the findings themselves carry the signal.
    """
    import argparse

    from ..baseline import load_baseline
    from ..errors import BaselineError
    from .audit import audit

    tree = Path(args.tree)
    if not tree.is_dir():
        print(f"pengyos-hardening: --tree {args.tree!r} is not a directory", file=sys.stderr)
        return 2

    try:
        baseline = load_baseline()
    except BaselineError as exc:
        print(f"pengyos-hardening: baseline is malformed: {exc}", file=sys.stderr)
        return 3

    result = audit(tree, baseline, rule=getattr(args, "rule", None))

    json_out = getattr(args, "json", None)
    if json_out is not None:
        write_json(result, tree, out=(json_out if isinstance(json_out, str) else None))
        return 0

    write_text(result, tree)
    return 0
