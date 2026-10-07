"""Tests for the committed-baseline submodule (:mod:`pengyos_hardening.baseline`).

These are the structural tests for the very first implementation subtask: the
baseline is *data*, and the loader must validate it. A malformed rule (missing
``id``, unknown ``severity``/``domain``, or a matcher that does not conform to
the schema) is a hard error, so the contract can never silently degrade.
"""

from __future__ import annotations

import json

import pytest

from pengyos_hardening.baseline import (
    KNOWN_DOMAINS,
    KNOWN_MATCHER_KINDS,
    KNOWN_SEVERITIES,
    Baseline,
    Matcher,
    Rule,
    load_baseline,
    load_baseline_from_json,
)
from pengyos_hardening.errors import BaselineError


# --- the committed baseline loads and is well-formed ----------------------

def test_committed_baseline_loads():
    baseline = load_baseline()
    assert baseline.baseline_id == "pengyos-hardening-1"
    assert baseline.schema_version == 1
    assert len(baseline) == 18


def test_every_rule_has_the_four_contract_fields():
    baseline = load_baseline()
    for rule in baseline.rules:
        assert rule.id, "rule id must be non-empty"
        assert rule.severity in KNOWN_SEVERITIES, "severity must be known"
        assert rule.domain in KNOWN_DOMAINS, "domain must be known"
        assert isinstance(rule.matcher, Matcher)
        assert rule.matcher.kind in KNOWN_MATCHER_KINDS


def test_rule_ids_are_stable_kebab_case():
    import re

    baseline = load_baseline()
    pattern = re.compile(r"^[a-z][a-z0-9-]*$")
    for rule in baseline.rules:
        assert pattern.match(rule.id), f"{rule.id!r} is not a stable id"


def test_all_five_domains_are_covered():
    """The contract names five domains; the baseline must exercise each."""
    baseline = load_baseline()
    covered = {rule.domain for rule in baseline.rules}
    assert KNOWN_DOMAINS == covered, "every declared domain must have a rule"


def test_by_id_lookup():
    baseline = load_baseline()
    rule = baseline.by_id("ssh-permit-root-login")
    assert rule.severity == "critical"
    assert rule.domain == "ssh"
    assert rule.matcher.kind == "key_value"


def test_by_id_unknown_raises_keyerror():
    baseline = load_baseline()
    with pytest.raises(KeyError):
        baseline.by_id("does-not-exist")


def test_by_domain_sorted_severity_first():
    baseline = load_baseline()
    ssh = baseline.by_domain("ssh")
    assert [r.id for r in ssh] == [
        "ssh-empty-passwords",  # critical
        "ssh-password-auth",    # critical
        "ssh-permit-root-login",  # critical
        "ssh-pubkey-auth",      # high
        "ssh-x11-forwarding",   # medium
        "ssh-tcp-forwarding",   # low
    ]


def test_duplicate_rule_id_rejected():
    text = json.dumps(
        {
            "schema_version": 1,
            "baseline_id": "dup",
            "rules": [
                {"id": "a", "severity": "low", "domain": "ssh", "matcher": {
                    "kind": "key_value",
                    "params": {"file": "x", "key": "K", "expected": "v"},
                }},
                {"id": "a", "severity": "low", "domain": "ssh", "matcher": {
                    "kind": "key_value",
                    "params": {"file": "x", "key": "K", "expected": "v"},
                }},
            ],
        }
    )
    with pytest.raises(BaselineError, match="duplicate rule id"):
        load_baseline_from_json(text)


# --- structural rejection: the data is the contract -----------------------

def test_missing_id_rejected():
    text = json.dumps(
        {
            "rules": [
                {"severity": "low", "domain": "ssh", "matcher": {
                    "kind": "key_value",
                    "params": {"file": "x", "key": "K", "expected": "v"},
                }},
            ]
        }
    )
    with pytest.raises(BaselineError, match="missing a non-empty 'id'"):
        load_baseline_from_json(text)


def test_non_kebab_id_rejected():
    text = json.dumps(
        {
            "rules": [
                {"id": "SSH_Permit", "severity": "low", "domain": "ssh", "matcher": {
                    "kind": "key_value",
                    "params": {"file": "x", "key": "K", "expected": "v"},
                }},
            ]
        }
    )
    with pytest.raises(BaselineError, match="not a stable kebab-case id"):
        load_baseline_from_json(text)


def test_unknown_domain_rejected():
    text = json.dumps(
        {
            "rules": [
                {"id": "r1", "severity": "low", "domain": "network", "matcher": {
                    "kind": "key_value",
                    "params": {"file": "x", "key": "K", "expected": "v"},
                }},
            ]
        }
    )
    with pytest.raises(BaselineError, match="unknown domain"):
        load_baseline_from_json(text)


def test_unknown_severity_rejected():
    text = json.dumps(
        {
            "rules": [
                {"id": "r1", "severity": "sev-ere", "domain": "ssh", "matcher": {
                    "kind": "key_value",
                    "params": {"file": "x", "key": "K", "expected": "v"},
                }},
            ]
        }
    )
    with pytest.raises(BaselineError, match="severity must be one of"):
        load_baseline_from_json(text)


def test_missing_matcher_rejected():
    text = json.dumps(
        {
            "rules": [
                {"id": "r1", "severity": "low", "domain": "ssh"},
            ]
        }
    )
    with pytest.raises(BaselineError, match="matcher"):
        load_baseline_from_json(text)


def test_unknown_matcher_kind_rejected():
    text = json.dumps(
        {
            "rules": [
                {"id": "r1", "severity": "low", "domain": "ssh", "matcher": {
                    "kind": "telepathy",
                    "params": {"file": "x", "key": "K", "expected": "v"},
                }},
            ]
        }
    )
    with pytest.raises(BaselineError, match="unknown matcher kind"):
        load_baseline_from_json(text)


def test_matcher_missing_param_rejected():
    text = json.dumps(
        {
            "rules": [
                {"id": "r1", "severity": "low", "domain": "ssh", "matcher": {
                    "kind": "key_value",
                    "params": {"file": "x", "key": "K"},  # no "expected"
                }},
            ]
        }
    )
    with pytest.raises(BaselineError, match="missing param"):
        load_baseline_from_json(text)


def test_non_json_rejected():
    with pytest.raises(BaselineError, match="not valid JSON"):
        load_baseline_from_json("{not json")


def test_missing_rules_list_rejected():
    with pytest.raises(BaselineError, match="rules must be a list"):
        load_baseline_from_json(json.dumps({"baseline_id": "x"}))


# --- custom path override -------------------------------------------------

def test_load_from_custom_path(tmp_path):
    custom = tmp_path / "baseline.json"
    custom.write_text(
        json.dumps(
            {
                "schema_version": 7,
                "baseline_id": "custom",
                "rules": [
                    {"id": "only-rule", "severity": "high", "domain": "units",
                     "matcher": {"kind": "unit_enabled",
                                 "params": {"wants_dir": "d", "unit": "u.service"}}},
                ],
            }
        ),
        encoding="utf-8",
    )
    baseline = load_baseline(custom)
    assert baseline.schema_version == 7
    assert baseline.baseline_id == "custom"
    assert baseline.rules[0].id == "only-rule"


# --- serialisation --------------------------------------------------------

def test_to_dict_round_trips():
    baseline = load_baseline()
    data = baseline.to_dict()
    assert data["baseline_id"] == "pengyos-hardening-1"
    assert data["rule_count"] == len(baseline) == len(data["rules"])
    # Re-loading the serialised form must validate cleanly (self-describing).
    restored = load_baseline_from_json(json.dumps(data))
    assert restored.ids() == baseline.ids()


def test_rule_to_dict_shape():
    rule = load_baseline().by_id("firewall-input-policy-deny")
    d = rule.to_dict()
    assert set(d) == {"id", "severity", "domain", "description", "matcher"}
    assert d["matcher"] == {
        "kind": "nft_policy",
        "params": {
            "file": "etc/nftables.conf",
            "chain": "input",
            "expected": "deny",
        },
    }


def test_severity_rank_ordering():
    rule = load_baseline().by_id("ssh-permit-root-login")
    assert rule.severity_rank == 0  # critical
    assert load_baseline().by_id("ssh-tcp-forwarding").severity_rank > 0


# --- CLI: baseline subcommand ---------------------------------------------

def test_cli_baseline_exits_zero(capsys):
    from pengyos_hardening.cli import main

    rc = main(["baseline"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rules=18" in out
    assert "ssh-permit-root-login" in out


def test_cli_baseline_json_is_valid_json(capsys):
    from pengyos_hardening.cli import main

    rc = main(["baseline", "--json"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    document = json.loads(out)  # must parse
    assert document["baseline_id"] == "pengyos-hardening-1"
    assert document["rule_count"] == 18


def test_cli_baseline_malformed_exits_three(tmp_path, capsys):
    """A malformed baseline reported via --path exits 3 with a clean message."""
    from pengyos_hardening.cli import main

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"rules": [{"severity": "low"}]}), encoding="utf-8")
    rc = main(["baseline", "--path", str(bad)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "malformed" in err
