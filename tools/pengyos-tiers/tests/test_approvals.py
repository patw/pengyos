"""Tests for required approvals (pengyos_tiers.approvals) and their wiring
into ``Classification.approvals`` / ``Classification.to_dict``."""

from __future__ import annotations

import json

from pengyos_tiers.approvals import Approval, approvals_for
from pengyos_tiers.change import Change
from pengyos_tiers.classifier import classify
from pengyos_tiers.rules import RulesFile


def _rules(*specs):
    return RulesFile.from_dict({"rule": list(specs)})


# --- approvals_for -------------------------------------------------------------


def test_tier0_requires_witness():
    assert approvals_for(0) == (Approval.WITNESS,)


def test_tier1_requires_agent_auto():
    assert approvals_for(1) == (Approval.AGENT_AUTO,)


def test_tier2_requires_human_sign():
    assert approvals_for(2) == (Approval.HUMAN_SIGN,)


def test_unknown_tier_fails_safe_to_human_sign():
    # A tier outside 0..2 must never auto-approve; it requires a human.
    assert approvals_for(-1) == (Approval.HUMAN_SIGN,)
    assert approvals_for(3) == (Approval.HUMAN_SIGN,)
    assert approvals_for(99) == (Approval.HUMAN_SIGN,)


def test_approval_values_are_the_spec_strings():
    # The three approval strings are fixed by the task/SPEC.
    assert Approval.WITNESS.value == "witness"
    assert Approval.AGENT_AUTO.value == "agent-auto"
    assert Approval.HUMAN_SIGN.value == "human-sign"
    # str-enum: compares equal to its value.
    assert Approval.WITNESS == "witness"


# --- wired into Classification -------------------------------------------------


def test_classification_approvals_for_each_tier():
    base = {"action": "modify", "path": "/etc/x"}
    for tier in (0, 1, 2):
        rules = _rules({"tier": tier, "path": "/etc/*"})
        cls = classify(Change.from_dict(base), rules)
        assert cls.approvals == approvals_for(tier)
        assert cls.needs_human == (tier == 2)


def test_default_deny_classification_requires_human_sign():
    # A change that falls through to default-deny is Tier 2 → human-sign.
    rules = _rules({"tier": 1, "path": "/etc/*"})
    cls = classify(Change.from_dict({"action": "modify", "path": "/opt/unknown"}), rules)
    assert cls.approvals == (Approval.HUMAN_SIGN,)
    assert cls.needs_human is True


def test_to_dict_includes_approvals_as_strings():
    rules = _rules({"tier": 1, "path": "/etc/*"})
    cls = classify(Change.from_dict({"action": "modify", "path": "/etc/a"}), rules)
    d = cls.to_dict()
    assert d["approvals"] == ["agent-auto"]
    # JSON-serialisable (the enum subclasses str).
    assert json.loads(json.dumps(d))["approvals"] == ["agent-auto"]


def test_tier0_classification_exposes_witness():
    rules = _rules({"tier": 0, "path": "/usr/lib/modules/*"})
    cls = classify(Change.from_dict({"action": "install", "path": "/usr/lib/modules/kernel"}), rules)
    assert cls.approvals == (Approval.WITNESS,)
    assert cls.needs_human is False
