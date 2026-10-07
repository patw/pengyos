"""Tests for the structural nftables validator (:mod:`pengyos_netctl.nft`).

Covers two layers:

* the vendored structural *parser* (``parse_nft_ruleset`` etc.) — the
  policy-neutral nft grammar walk, and
* our declared-ports *policy* layer
  (``validate_ports_ruleset_text``): default-deny input/forward,
  ``flush ruleset`` present, and the inbound allow list equal to the
  declared ports.

Tests are offline: no ``nft`` binary, no root, no network.
"""

import os

import pytest

from pengyos_netctl import (
    NftSyntaxError,
    Port,
    PortsConfig,
    ValidationResult,
    chain_policy,
    chain_statements,
    dport_accepts,
    find_rate_limit_drop,
    parse_nft_ruleset,
    strip_comments,
    tokenize,
    validate_ports_ruleset,
    validate_ports_ruleset_text,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_RULES = os.path.join(REPO_ROOT, "tests", "fixtures", "good_rules.nft")


def _ports(**over):
    data = {
        "listen_port": 51820,
        "ssh_port": 22,
        "allowed_tcp": [443, 8080],
        "allowed_udp": [1194],
    }
    data.update(over)
    return PortsConfig(data)


# ---------------------------------------------------------------- parser

# A full default-deny ruleset whose inbound allow list is exactly the
# ports declared by _ports() (tcp 22/443/8080, udp 1194/51820). It also
# carries the sshguard/fail2ban-style SSH rate-limit/ban the policy
# requires, so it is a fully valid ruleset for the declared-ports checks.
GOOD = """
flush ruleset

table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;
        iif "lo" accept
        ct state established,related accept
        ct state invalid drop
        tcp dport 22 ct state new limit rate over 3/minute burst 5 packets drop
        tcp dport 22 accept
        tcp dport 443 accept
        tcp dport 8080 accept
        udp dport 1194 accept
        udp dport 51820 accept
    }
    chain forward {
        type filter hook forward priority 0; policy drop;
    }
    chain output {
        type filter hook output priority 0; policy accept;
    }
}
"""


def test_strip_comments_removes_hashes_and_keeps_quoted():
    out = strip_comments('tcp dport 22 accept  # a comment\niif "a#b" accept')
    assert "comment" not in out
    assert '"a#b"' in out  # the '#' inside the string is preserved


def test_strip_comments_unterminated_block_raises():
    with pytest.raises(NftSyntaxError, match="unterminated"):
        strip_comments("flush ruleset /* no close")


def test_tokenize_does_not_glue_punctuation():
    toks = tokenize("0;")
    assert toks == ["0", ";"]


def test_tokenize_string_is_one_token():
    toks = tokenize('iif "lo" accept')
    assert '"lo"' in toks


def test_parse_roundtrip_structure():
    root = parse_nft_ruleset(GOOD)
    assert root["header"] is None
    assert "flush ruleset" in root["stmts"]
    assert "filter" in root["blocks"]
    for chain in ("input", "forward", "output"):
        assert chain in root["blocks"]["filter"]["blocks"]


def test_parse_unbalanced_brace_raises():
    with pytest.raises(NftSyntaxError, match="unbalanced"):
        parse_nft_ruleset("table inet filter {")


def test_parse_duplicate_block_raises():
    text = (
        "table inet filter {}\n"
        "table inet filter {}\n"
    )
    with pytest.raises(NftSyntaxError, match="duplicate block"):
        parse_nft_ruleset(text)


def test_chain_statements_missing_table_raises_keyerror():
    root = parse_nft_ruleset(GOOD)
    with pytest.raises(KeyError):
        chain_statements(root, "nope", "input")


def test_chain_policy_extracts_drop():
    root = parse_nft_ruleset(GOOD)
    _, stmts = chain_statements(root, "filter", "input")
    assert chain_policy(stmts) == "drop"


def test_chain_policy_unrecognised_raises():
    with pytest.raises(NftSyntaxError, match="unrecognised policy"):
        chain_policy(["policy maybe"])


def test_chain_policy_missing_raises():
    with pytest.raises(NftSyntaxError, match="no policy"):
        chain_policy(["ct state accept"])


def test_dport_accepts_single_and_combined():
    root = parse_nft_ruleset(GOOD)
    _, stmts = chain_statements(root, "filter", "input")
    assert dport_accepts(stmts) == {
        ("tcp", "22"), ("tcp", "443"), ("tcp", "8080"),
        ("udp", "1194"), ("udp", "51820"),
    }


def test_dport_accepts_combined_range():
    found = dport_accepts(["tcp dport 22, 80 accept"])
    assert found == {("tcp", "22"), ("tcp", "80")}


def test_dport_accepts_bad_value_raises():
    with pytest.raises(NftSyntaxError, match="dport without value"):
        dport_accepts(["tcp dport accept"])


# ---------------------------------------------------------- policy layer


def test_port_equality_hash_str():
    assert Port("tcp", "22") == Port("tcp", "22")
    assert Port("tcp", "22") != Port("tcp", "23")
    assert hash(Port("tcp", "22")) == hash(Port("tcp", "22"))
    assert str(Port("udp", "51820")) == "udp/51820"


def test_validation_result_ok_and_repr():
    result = ValidationResult({}, [])
    assert result.ok
    assert bool(result)
    assert "OK" in repr(result)
    assert not ValidationResult({}, ["x"]).ok
    assert "FAIL" in repr(ValidationResult({}, ["x"]))


def test_validate_good_ruleset_passes():
    with open(GOOD_RULES, encoding="utf-8") as fh:
        text = fh.read()
    result = validate_ports_ruleset_text(text, _ports())
    assert result.ok, result.errors
    assert result.root is not None


def test_validate_committed_fixture_matches_config():
    """The committed fixture must satisfy nftables/ports.yaml."""
    from pengyos_netctl import load_ports_config

    cfg = load_ports_config(os.path.join(REPO_ROOT, "nftables", "ports.yaml"))
    result = validate_ports_ruleset(GOOD_RULES, cfg)
    assert result.ok, result.errors


def test_validate_missing_flush_ruleset():
    text = GOOD.replace("flush ruleset\n", "")
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("flush ruleset" in e for e in result.errors)


def test_validate_input_must_be_default_deny():
    text = GOOD.replace(
        "type filter hook input priority 0; policy drop;",
        "type filter hook input priority 0; policy accept;",
    )
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("chain 'input' must be default-deny" in e for e in result.errors)


def test_validate_forward_must_be_default_deny():
    text = GOOD.replace(
        "type filter hook forward priority 0; policy drop;",
        "type filter hook forward priority 0; policy accept;",
    )
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("chain 'forward' must be default-deny" in e for e in result.errors)


def test_validate_extra_port_rejected():
    text = GOOD.replace("tcp dport 22 accept", "tcp dport 9999 accept")
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("extra ['tcp/9999']" in e for e in result.errors)


def test_validate_missing_port_rejected():
    text = GOOD.replace("tcp dport 22 accept\n", "")
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("missing ['tcp/22']" in e for e in result.errors)


def test_validate_declared_ports_reflected_in_error():
    # A different config declares only SSH; the ruleset exposing more
    # must fail with the *declared* set named in the message.
    text = GOOD
    result = validate_ports_ruleset_text(text, _ports(allowed_tcp=[], allowed_udp=[]))
    assert not result.ok
    assert any("tcp/22" in e and "udp/51820" in e for e in result.errors)


def test_validate_no_inet_table():
    text = "table ip filter {\n  chain input {\n    policy drop;\n  }\n}\n"
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("table inet" in e for e in result.errors)


def test_validate_syntax_error_lands_in_result_not_raised():
    result = validate_ports_ruleset_text("table inet filter {", _ports())
    assert not result.ok
    assert result.root is None
    assert any("syntax" in e for e in result.errors)


def test_validate_ports_ruleset_reads_file(tmp_path):
    p = tmp_path / "r.nft"
    p.write_text(GOOD, encoding="utf-8")
    result = validate_ports_ruleset(str(p), _ports())
    assert result.ok, result.errors


# ---------------------------------------------------- mitigation (rate-limit/ban)


def test_find_rate_limit_drop_finds_ban():
    stmt = "tcp dport 22 ct state new limit rate over 3/minute burst 5 packets drop"
    assert find_rate_limit_drop([stmt]) == stmt


def test_find_rate_limit_drop_absent_returns_none():
    assert find_rate_limit_drop(["tcp dport 22 accept"]) is None
    assert find_rate_limit_drop(["ct state invalid drop"]) is None
    assert find_rate_limit_drop([]) is None


def test_find_rate_limit_drop_requires_all_three():
    # Needs `drop` AND `limit` AND `rate` — a plain drop or a plain
    # rate-limit (missing the other) is not a ban.
    assert find_rate_limit_drop(["ct state invalid drop"]) is None
    assert find_rate_limit_drop(["limit rate over 3/minute"]) is None


def test_validate_good_ruleset_has_mitigation():
    # The committed fixture now carries the rate-limit/ban, so it is valid.
    with open(GOOD_RULES, encoding="utf-8") as fh:
        text = fh.read()
    result = validate_ports_ruleset_text(text, _ports())
    assert result.ok, result.errors


def test_validate_without_mitigation_fails():
    # A ruleset identical to GOOD but missing the rate-limit/ban is rejected.
    text = GOOD.replace(
        "        tcp dport 22 ct state new limit rate over 3/minute burst 5 packets drop\n",
        "",
    )
    # The ban is gone; the allow set is unchanged, so only the mitigation
    # check should fire.
    assert "limit rate" not in text
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("rate-limit/ban" in e for e in result.errors)


def test_validate_with_rate_limit_ban_passes():
    # Adding a rate-limit ban (before the SSH accept) keeps it valid.
    text = GOOD.replace(
        "        tcp dport 22 accept",
        "        tcp dport 22 ct state new limit rate over 3/minute burst 5 packets drop\n"
        "        tcp dport 22 accept",
    )
    result = validate_ports_ruleset_text(text, _ports())
    assert result.ok, result.errors


def test_validate_no_inet_table_has_no_mitigation_check():
    # With no inet table the input chain is absent, so no mitigation error.
    text = "table ip filter {\n  chain input {\n    policy drop;\n  }\n}\n"
    result = validate_ports_ruleset_text(text, _ports())
    assert not result.ok
    assert any("table inet" in e for e in result.errors)
    assert not any("rate-limit/ban" in e for e in result.errors)
