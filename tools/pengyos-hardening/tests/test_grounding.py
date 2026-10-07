"""Grounding: the committed baseline is the contract, the fixtures are the proof.

This is the acceptance criterion from spec.md — "the hardened fixture tree
yields zero findings" and "each single-rule mutation yields exactly that
rule's finding (and only it) — one mutation per rule" — expressed as a single
test group that is *driven by the committed baseline* rather than a hardcoded
list.

The value over the per-domain tests in ``tests/test_check.py`` is that this
suite cannot silently drift from the contract: it enumerates
``load_baseline().ids()`` and asserts, for *every* rule the baseline declares,

* a single-rule mutation fixture exists for it under
  ``tests/fixtures/mutations/<rule_id>``, and
* auditing that tree (unfiltered) yields exactly one finding, and it is the
  rule the mutation targets.

A new rule added to ``data/baseline.json`` without a matching mutation fixture
fails here — that is the whole point of grounding against the committed data.
"""

from __future__ import annotations

from pengyos_hardening.baseline import load_baseline
from pengyos_hardening.check import audit

from conftest import HARDENED_TREE, MUTATIONS_DIR, mutation_tree


def test_hardened_tree_audits_clean_with_no_skips():
    """The hardened fixture tree yields zero findings, and every rule the
    baseline declares has an evaluator (zero skipped)."""
    result = audit(HARDENED_TREE, load_baseline())
    assert result.findings == [], result.findings
    assert result.rule_ids_skipped == []
    assert result.rules_audited == len(load_baseline())


def test_every_baseline_rule_has_a_mutation_that_fires_its_named_rule():
    """Grounding (spec.md): each rule the committed baseline declares has
    exactly one single-rule mutation fixture, and auditing it unfiltered yields
    exactly one finding — the rule the mutation targets.

    This is driven by ``load_baseline().ids()`` — a rule added to the baseline
    without a matching mutation fixture fails here, not on some later run.
    """
    baseline = load_baseline()
    for rule_id in baseline.ids():
        tree = mutation_tree(rule_id)
        assert tree.is_dir(), f"no mutation fixture for {rule_id!r}"
        result = audit(tree, baseline)
        assert len(result.findings) == 1, rule_id
        assert result.findings[0].rule_id == rule_id, rule_id


def test_every_rule_id_has_a_committed_mutation_directory():
    """Every baseline rule maps to an existing mutation fixture directory, so
    the "one mutation per rule" contract is complete for the committed set."""
    baseline = load_baseline()
    for rule_id in baseline.ids():
        assert (mutation_tree(rule_id)).is_dir(), rule_id


def test_mutation_count_matches_rule_count():
    """There is exactly one mutation fixture per rule — no orphan fixtures and
    no rule left without one."""
    baseline = load_baseline()
    rule_ids = set(baseline.ids())
    fixture_roots = {p.name for p in MUTATIONS_DIR.iterdir() if p.is_dir()}
    assert fixture_roots == rule_ids


def test_hardened_tree_has_no_deviation_in_report():
    """The hardened tree renders as a clean report: the text says it is OK and
    the JSON summary reports zero findings / zero skipped."""
    from pengyos_hardening.check.report import render_json, render_text

    result = audit(HARDENED_TREE, load_baseline())
    text = render_text(result, HARDENED_TREE)
    assert "OK — no deviations" in text

    document = render_json(result, HARDENED_TREE)
    assert document["summary"]["findings"] == 0
    assert document["summary"]["passed"] == len(baseline_ids())
    assert document["summary"]["skipped"] == 0
    assert document["finding_count"] == 0


def test_each_mutation_surfaces_only_its_rule_when_unfiltered():
    """A full (unfiltered) audit of any single-rule mutation surfaces only the
    mutated rule — a one-line change must not trip an unrelated rule."""
    baseline = load_baseline()
    for rule_id in baseline.ids():
        result = audit(mutation_tree(rule_id), baseline)
        assert {f.rule_id for f in result.findings} == {rule_id}, rule_id


def test_mutation_evidence_carries_a_path():
    """Every rule's mutation finding carries a path (the file, mount, or unit
    the deviation lives in). The ``unit_enabled`` rule is a filesystem
    existence check, so it pins the path but no source line; every other
    file-backed rule pins a real ``file:line``."""
    baseline = load_baseline()
    for rule_id in baseline.ids():
        finding = audit(mutation_tree(rule_id), baseline).findings[0]
        assert finding.path, (rule_id, "finding has no path")
        # The only implemented matcher that legitimately reports no source line
        # is the systemd unit existence check.
        if finding.line is None:
            rule = baseline.by_id(rule_id)
            assert rule.matcher.kind == "unit_enabled", rule_id


# --- small helpers ---------------------------------------------------------

def baseline_ids() -> list[str]:
    """The committed baseline's rule ids (the data is fixed for the run)."""
    return load_baseline().ids()
