"""Render proposals for human review.

PengyOS is human-gated: nothing is auto-applied.  A proposal must be readable by
a human before it is submitted to the gate.  This module turns a
:class:`~pengyos_propose.proposal.Proposal` into human-facing text.

Two formats are planned:
* :func:`render_markdown` — compact, review-friendly Markdown (default).
* :func:`render_html` — the HTML page the gate emails to the operator.

The scaffold ships the Markdown renderer; the HTML renderer is a follow-up.
"""

from __future__ import annotations

from .proposal import Proposal, tier_label


def render_markdown(p: Proposal) -> str:
    """Render a proposal as a concise, review-ready Markdown document."""
    lines = [f"# {p.title or '(untitled)'}", ""]
    lines.append(f"- **Source:** {p.source or '—'}")
    lines.append(f"- **Type:** {p.type or '—'} · **Component:** {p.component or '—'}")
    lines.append(f"- **From → To:** `{p.from_ or '—'}` → `{p.to or '—'}`")
    lines.append(f"- **Tier:** {tier_label(p.tier)}")
    lines.append("")
    if p.rationale:
        lines.append("## Rationale")
        lines.append("")
        lines.append(p.rationale)
        lines.append("")
    if p.evidence:
        lines.append("## Evidence")
        lines.append("")
        for item in p.evidence:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(p: Proposal) -> str:  # pragma: no cover - follow-up task
    """Render a proposal as an HTML review page.

    Deferred to a later subtask.  Present so the module
    import surface is stable and the CLI can resolve the command now.
    """
    raise NotImplementedError(
        "render_html is implemented in a later subtask; use render_markdown"
    )
