"""Tests for the deterministic ruleset renderer (:mod:`pengyos_netctl.render`).

Covers :func:`render_ports_ruleset`, the helper render functions, and
:func:`write_ruleset`. All offline: no ``nft`` binary, no root, no network.
"""

import os

import pytest

from pengyos_netctl import (
    PortsConfig,
    mitigation_statements,
    render_allow_list,
    render_ports,
    render_ports_ruleset,
    validate_ports_ruleset_text,
    write_ruleset,
)

GOOD_PORTS = {
    "listen_port": 51820,
    "ssh_port": 22,
    "allowed_tcp": [443, 8080],
    "allowed_udp": [1194],
}


def _ports(**over):
    data = dict(GOOD_PORTS)
    data.update(over)
    return PortsConfig(data)


# ---------------------------------------------------------------- render_ports_ruleset


def test_render_is_valid_against_its_own_config():
    ports = _ports()
    text = render_ports_ruleset(ports)
    result = validate_ports_ruleset_text(text, ports)
    assert result.ok, result.errors
    assert result.root is not None


def test_render_flushes_ruleset():
    text = render_ports_ruleset(_ports())
    assert text.splitlines()[0].strip() == "#!/usr/sbin/nft -f"
    assert "flush ruleset" in text


def test_render_single_inet_table_with_three_chains():
    text = render_ports_ruleset(_ports())
    assert text.count("table inet filter") == 1
    for chain in ("input", "forward", "output"):
        assert f"chain {chain}" in text


def test_render_default_deny_input_and_forward():
    from pengyos_netctl import chain_policy, parse_nft_ruleset

    text = render_ports_ruleset(_ports())
    root = parse_nft_ruleset(text)
    table = "filter"
    # input + forward must be policy drop; output must be policy accept.
    _, in_stmts = _chain_stmts(root, table, "input")
    assert chain_policy(in_stmts) == "drop"
    _, fwd_stmts = _chain_stmts(root, table, "forward")
    assert chain_policy(fwd_stmts) == "drop"
    _, out_stmts = _chain_stmts(root, table, "output")
    assert chain_policy(out_stmts) == "accept"


def _chain_stmts(root, table, chain):
    """Tiny local helper: return the statements of a table/chain."""
    node = root["blocks"][table]["blocks"][chain]
    return node["header"], node["stmts"]


def test_render_only_declared_ports():
    """The allow list is exactly the declared ports — no more, no less."""
    ports = _ports()
    text = render_ports_ruleset(ports)
    result = validate_ports_ruleset_text(text, ports)
    assert result.ok, result.errors


def test_render_is_deterministic():
    ports = _ports()
    assert render_ports_ruleset(ports) == render_ports_ruleset(ports)


def test_render_matches_committed_fixture_ignoring_comments():
    """The reference fixture (tests/fixtures/good_rules.nft) reproduces."""
    from pengyos_netctl import load_ports_config

    cfg = load_ports_config(
        os.path.join(os.path.dirname(__file__), "..", "nftables", "ports.yaml")
    )
    rendered = render_ports_ruleset(cfg)
    fixture = open(
        os.path.join(os.path.dirname(__file__), "fixtures", "good_rules.nft"),
        encoding="utf-8",
    ).read()

    def norm(t):
        return [
            ln.strip()
            for ln in t.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]

    assert norm(rendered) == norm(fixture)


def test_render_validation_failure_raises_runtime(monkeypatch):
    """If the policy ever rejects the renderer's output, it must raise."""
    from pengyos_netctl import render as render_module

    def boom(_text, _ports):
        from pengyos_netctl import ValidationResult

        return ValidationResult(None, ["boom"])

    # Patch the name as bound in render.py (imported at module load time).
    monkeypatch.setattr(render_module, "validate_ports_ruleset_text", boom)
    with pytest.raises(RuntimeError, match="boom"):
        render_ports_ruleset(_ports())


# ---------------------------------------------------------------- render_ports (header)


def test_render_ports_lists_all_declared():
    text = render_ports(_ports())
    assert "22" in text and "443" in text and "8080" in text
    assert "1194" in text and "51820" in text


def test_render_ports_ssh_and_wireguard_labeled():
    text = render_ports(_ports())
    assert "SSH (always allowed)" in text
    assert "WireGuard (always allowed)" in text
    assert "(declared)" in text


def test_render_ports_tcp_before_udp():
    text = render_ports(_ports())
    assert text.index("tcp") < text.index("udp")


# ---------------------------------------------------------------- render_allow_list


def test_render_allow_list_ordering():
    """tcp first then udp, each ascending — byte-for-byte deterministic."""
    text = render_allow_list(_ports())
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # SSH (22) before 443 before 8080 (tcp ascending)...
    assert lines.index("        tcp dport 22 accept") < lines.index(
        "        tcp dport 443 accept"
    )
    assert lines.index("        tcp dport 443 accept") < lines.index(
        "        tcp dport 8080 accept"
    )
    # ...then udp (1194 before 51820).
    assert lines.index("        udp dport 1194 accept") < lines.index(
        "        udp dport 51820 accept"
    )
    # tcp block entirely precedes the udp block.
    assert lines[-1] == "        udp dport 51820 accept"


def test_render_allow_list_only_declared():
    text = render_allow_list(_ports(allowed_tcp=[], allowed_udp=[]))
    assert "tcp dport 22 accept" in text
    assert "udp dport 51820 accept" in text
    assert "443" not in text and "8080" not in text


# ---------------------------------------------------------------- mitigation


def test_render_contains_ssh_rate_limit_ban():
    """The rendered ruleset carries an sshguard/fail2ban-style rate-limit."""
    text = render_ports_ruleset(_ports())
    assert "limit rate over 3/minute burst 5 packets drop" in text
    assert "tcp dport 22 ct state new" in text


def test_render_mitigation_before_allow_list():
    """The ban must precede the SSH accept so overflow is dropped, not accepted."""
    text = render_ports_ruleset(_ports())
    assert text.index("limit rate over 3/minute burst 5 packets drop") < text.index(
        "tcp dport 22 accept"
    )


def test_render_mitigation_tracks_ssh_port():
    text = render_ports_ruleset(_ports(ssh_port=2222))
    assert "tcp dport 2222 ct state new limit rate" in text
    assert "tcp dport 22 ct state new" not in text


def test_render_mitigation_present_validates():
    """The mitigation does not disturb the declared-ports allow-set match."""
    ports = _ports()
    text = render_ports_ruleset(ports)
    result = validate_ports_ruleset_text(text, ports)
    assert result.ok, result.errors


def test_render_is_deterministic_with_mitigation():
    assert render_ports_ruleset(_ports()) == render_ports_ruleset(_ports())


# ---------------------------------------------------------------- mitigation_statements


def test_mitigation_statements_single_rate_limit_line():
    text = mitigation_statements(_ports())
    assert text == (
        "        tcp dport 22 ct state new "
        "limit rate over 3/minute burst 5 packets drop"
    )


def test_mitigation_statements_uses_configured_ssh_port():
    assert "tcp dport 2222 ct state new" in mitigation_statements(_ports(ssh_port=2222))


# ---------------------------------------------------------------- write_ruleset


def test_write_ruleset_creates_file_and_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "deep" / "server.nft"
    returned = write_ruleset(str(target), "flush ruleset\n")
    assert returned == str(target)
    assert target.read_text(encoding="utf-8") == "flush ruleset\n"


def test_write_ruleset_appends_trailing_newline(tmp_path):
    target = tmp_path / "server.nft"
    write_ruleset(str(target), "flush ruleset")
    assert target.read_text(encoding="utf-8").endswith("\n")
