"""Tests for the classifier (pengyos_tiers.classifier)."""

from __future__ import annotations

import pytest

from pengyos_tiers.change import Change
from pengyos_tiers.classifier import classify, normalize_path
from pengyos_tiers.rules import _glob_to_regex, RulesFile


def _rules(*specs):
    return RulesFile.from_dict({"rule": list(specs)})


# --- normalize_path -----------------------------------------------------------


def test_normalize_collapses_dots_and_slashes():
    assert normalize_path("/etc/../etc/foo") == "/etc/foo"
    assert normalize_path("/etc//foo/.") == "/etc/foo"
    assert normalize_path("/a/b/../c") == "/a/c"


def test_normalize_rejects_relative():
    with pytest.raises(ValueError):
        normalize_path("etc/foo")


# --- glob semantics -----------------------------------------------------------


def test_star_crosses_slash():
    rx = _glob_to_regex("/etc/*")
    assert rx.match("/etc/foo")
    assert rx.match("/etc/foo/bar")
    assert not rx.match("/usr/foo")


def test_question_matches_single_char_only():
    rx = _glob_to_regex("/etc/pa?wd")
    assert rx.match("/etc/paswd")  # exactly one char in the '?' slot
    assert not rx.match("/etc/passwd")  # two chars there -> no match
    assert not rx.match("/etc/pwd")  # zero chars there -> no match


def test_char_class():
    rx = _glob_to_regex("/etc/[fp]*")
    assert rx.match("/etc/foo")
    assert rx.match("/etc/passwd")
    assert not rx.match("/etc/bar")


def test_literal_dot_is_literal():
    rx = _glob_to_regex("/a.b")
    assert rx.match("/a.b")
    assert not rx.match("/axb")  # '.' is literal, not regex any-char


# --- classify: first-match + default-deny -------------------------------------


def test_first_match_wins_by_file_order():
    rules = _rules(
        {"tier": 1, "path": "/etc/*"},
        {"tier": 2, "path": "/etc/shadow"},
    )
    change = Change.from_dict({"action": "modify", "path": "/etc/shadow"})
    result = classify(change, rules)
    # The broad T1 rule is listed first, so it wins over the later T2 rule.
    assert result.tier == 1
    assert result.matched
    assert not result.needs_human


def test_more_specific_later_rule_does_not_win():
    # Precedence is file order, not specificity: a later, more specific rule
    # cannot override an earlier match.
    rules = _rules(
        {"tier": 1, "path": "/etc/*", "name": "broad"},
        {"tier": 2, "path": "/etc/shadow", "name": "shadow"},
    )
    change = Change.from_dict({"action": "modify", "path": "/etc/shadow"})
    result = classify(change, rules)
    assert result.tier == 1
    assert result.rule_name == "broad"


def test_action_filter_respected():
    rules = _rules({"tier": 1, "path": "/etc/*", "action": ["modify"]})
    assert classify(Change.from_dict({"action": "modify", "path": "/etc/a"}), rules).tier == 1
    assert classify(Change.from_dict({"action": "delete", "path": "/etc/a"}), rules).tier == 2


def test_target_filter_respected():
    rules = _rules({"tier": 2, "path": "/etc/pengyos/*", "target": "trust-root"})
    ok = classify(
        Change.from_dict({"action": "modify", "path": "/etc/pengyos/x", "target": "trust-root"}),
        rules,
    )
    assert ok.tier == 2
    miss = classify(
        Change.from_dict({"action": "modify", "path": "/etc/pengyos/x", "target": "other"}),
        rules,
    )
    assert miss.tier == 2  # fell through to default-deny


def test_default_deny_is_tier2():
    rules = _rules({"tier": 1, "path": "/etc/*"})
    change = Change.from_dict({"action": "modify", "path": "/opt/unknown"})
    result = classify(change, rules)
    assert result.tier == 2
    assert not result.matched
    assert result.needs_human
    assert "default-deny" in result.reason


def test_needs_human_only_for_tier2():
    # An explicit Tier 0 rule → NOT needs human.
    rules0 = _rules({"tier": 0, "path": "/etc/*"})
    assert classify(Change.from_dict({"action": "modify", "path": "/etc/a"}), rules0).needs_human is False

    # An explicit Tier 1 rule → NOT needs human.
    rules1 = _rules({"tier": 1, "path": "/etc/*"})
    assert classify(Change.from_dict({"action": "modify", "path": "/etc/a"}), rules1).needs_human is False

    # A change that falls through to default-deny is Tier 2 → needs human.
    miss = classify(Change.from_dict({"action": "modify", "path": "/opt/x"}), rules1)
    assert miss.needs_human is True


def test_rule_name_echoed_in_reason():
    rules = _rules({"tier": 1, "path": "/etc/*", "name": "system config"})
    result = classify(Change.from_dict({"action": "modify", "path": "/etc/a"}), rules)
    assert result.rule_name == "system config"
    assert "system config" in result.reason


# --- Classification serialisation ---------------------------------------------


def test_classification_to_dict():
    rules = _rules({"tier": 1, "path": "/etc/*"})
    change = Change.from_dict({"action": "modify", "path": "/etc/a"})
    d = classify(change, rules).to_dict()
    assert d == {
        "tier": 1,
        "matched": True,
        "rule": None,
        "needs_human": False,
        "approvals": ["agent-auto"],
        "reason": d["reason"],
    }
    assert isinstance(d["reason"], str)


def test_normalize_then_match_on_normalized_path():
    # A change with a non-normalized path still matches the normalized form.
    rules = _rules({"tier": 1, "path": "/etc/*"})
    change = Change.from_dict({"action": "modify", "path": "/etc/../etc/secret"})
    result = classify(change, rules)
    assert result.tier == 1
