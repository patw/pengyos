"""Tests for the Markdown renderer."""

from __future__ import annotations

from pengyos_propose.proposal import Proposal
from pengyos_propose.render import render_markdown


def test_render_markdown_includes_title_and_tier(sample_proposal):
    out = render_markdown(sample_proposal)
    assert "Bump systemd in the base image" in out
    assert "Tier 1 (agent-mutable)" in out
    assert "`systemd 256` → `systemd 257`" in out


def test_render_markdown_includes_rationale_and_evidence(sample_proposal):
    out = render_markdown(sample_proposal)
    assert "Security fix backported from upstream" in out
    assert "CVE-2026-4321" in out


def test_render_markdown_marks_tier2(sample_proposal_path, tier2_proposal_path):
    from pengyos_propose.proposal import load_proposal_file

    p = load_proposal_file(tier2_proposal_path)
    out = render_markdown(p)
    assert "Tier 2 (human-signed)" in out


def test_render_markdown_handles_empty_proposal():
    out = render_markdown(Proposal())
    assert "(untitled)" in out
    assert out.endswith("\n")
