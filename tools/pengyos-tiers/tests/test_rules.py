"""Tests for the declarative rules file (pengyos_tiers.rules)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from pengyos_tiers.errors import RulesError
from pengyos_tiers.rules import DEFAULT_DENY_TIER, Rule, RulesFile

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "tiers.toml"


def _load_inline(toml_text: str) -> RulesFile:
    return RulesFile.from_dict(tomllib.loads(toml_text))


# --- committed sample ruleset -------------------------------------------------


def test_committed_ruleset_loads():
    rules = RulesFile.load(COMMITTED)
    assert len(rules) >= 2  # the two starter rules
    assert rules.deny_tier == DEFAULT_DENY_TIER == 2


def test_committed_ruleset_is_valid_toml():
    # Guard against a silently broken committed file.
    assert RulesFile.load(COMMITTED).source == str(COMMITTED)


# --- schema validation --------------------------------------------------------


def test_missing_rule_array():
    with pytest.raises(RulesError, match="no \\[\\[rule\\]\\]"):
        _load_inline("")


def test_rule_not_a_table():
    # A rule entry that is not a table (built directly, since [[rule]] always
    # yields a table in well-formed TOML).
    with pytest.raises(RulesError, match="must be a TOML table"):
        RulesFile.from_dict({"rule": [1]})


def test_missing_tier():
    with pytest.raises(RulesError, match="tier is required"):
        _load_inline('[[rule]]\npath = "/etc/*"\n')


def test_tier_out_of_range():
    with pytest.raises(RulesError, match="one of"):
        _load_inline('[[rule]]\ntier = 3\npath = "/etc/*"\n')


def test_tier_bool_rejected():
    with pytest.raises(RulesError, match="tier is required"):
        _load_inline('[[rule]]\ntier = true\npath = "/etc/*"\n')


def test_missing_path():
    with pytest.raises(RulesError, match="at least one"):
        _load_inline('[[rule]]\ntier = 1\n')


def test_path_not_absolute():
    with pytest.raises(RulesError, match="absolute"):
        _load_inline('[[rule]]\ntier = 1\npath = "etc/*"\n')


def test_action_filter_string():
    rules = _load_inline('[[rule]]\ntier = 1\npath = "/etc/*"\naction = "modify"\n')
    assert rules.rules[0].action == ("modify",)


def test_bad_action_identifier():
    with pytest.raises(RulesError, match="lowercase identifier"):
        _load_inline('[[rule]]\ntier = 1\npath = "/etc/*"\naction = "Modify"\n')


def test_bad_target():
    with pytest.raises(RulesError, match="non-empty string"):
        _load_inline('[[rule]]\ntier = 1\npath = "/etc/*"\ntarget = ""\n')


def test_bad_name_type():
    with pytest.raises(RulesError, match="must be a string"):
        _load_inline('[[rule]]\ntier = 1\npath = "/etc/*"\nname = 5\n')


def test_array_of_paths():
    rules = _load_inline(
        '[[rule]]\ntier = 1\npath = ["/etc/*", "/var/lib/*"]\n'
    )
    assert rules.rules[0].paths == ("/etc/*", "/var/lib/*")


def test_from_dict_not_a_table():
    with pytest.raises(RulesError, match="must be a TOML table"):
        RulesFile.from_dict([1, 2, 3])


def test_rule_not_array_of_tables():
    # `rule` present but a single table rather than an array of tables.
    with pytest.raises(RulesError, match="array of tables"):
        RulesFile.from_dict({"rule": {"tier": 1, "path": "/etc/*"}})


def test_empty_rule_list():
    with pytest.raises(RulesError, match="no valid"):
        RulesFile.from_dict({"rule": []})


def test_load_missing_file():
    with pytest.raises(RulesError, match="not found"):
        RulesFile.load(ROOT / "does-not-exist.toml")


def test_load_invalid_toml(tmp_path: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text('[[rule]\ntier = 1\n')  # unterminated table key
    with pytest.raises(RulesError, match="invalid TOML"):
        RulesFile.load(bad)


# --- Rule.matches -------------------------------------------------------------


def test_rule_matches_path_glob():
    rule = Rule(tier=1, paths=("/etc/*",))
    assert rule.matches("modify", "/etc/foo")
    assert not rule.matches("modify", "/usr/foo")


def test_rule_matches_action_filter():
    rule = Rule(tier=1, paths=("/etc/*"), action=("modify",))
    assert rule.matches("modify", "/etc/foo")
    assert not rule.matches("delete", "/etc/foo")


def test_rule_matches_target_filter():
    rule = Rule(tier=2, paths=("/etc/pengyos/*"), target="update-trust-root")
    assert rule.matches("modify", "/etc/pengyos/x", target="update-trust-root")
    assert not rule.matches("modify", "/etc/pengyos/x", target="other")


def test_rule_matches_any_path():
    rule = Rule(tier=1, paths=("/etc/*", "/var/lib/*"))
    assert rule.matches("modify", "/var/lib/x")
