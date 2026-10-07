"""Tests for the ``rules`` CLI command (:mod:`pengyos_netctl.cli`)."""

import os
import subprocess
import sys

import pytest

from pengyos_netctl.cli import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTS = os.path.join(REPO_ROOT, "nftables", "ports.yaml")


def _write_ports(tmp_path, **over):
    data = {
        "listen_port": 51820,
        "ssh_port": 22,
        "allowed_tcp": [443, 8080],
        "allowed_udp": [1194],
    }
    data.update(over)
    path = tmp_path / "ports.yaml"
    path.write_text(
        "listen_port: {listen_port}\n"
        "ssh_port: {ssh_port}\n"
        "allowed_tcp: {allowed_tcp}\n"
        "allowed_udp: {allowed_udp}\n".format(**data),
        encoding="utf-8",
    )
    return str(path)


def test_rules_writes_valid_ruleset(capsys, tmp_path):
    config = _write_ports(tmp_path)
    out = str(tmp_path / "server.nft")
    assert main(["rules", "--config", config, "--out", out]) == 0
    assert os.path.isfile(out)
    written = open(out, encoding="utf-8").read()
    assert "flush ruleset" in written
    assert "policy drop" in written
    # The written ruleset must validate against the same config.
    from pengyos_netctl import load_ports_config, validate_ports_ruleset_text

    cfg = load_ports_config(config)
    result = validate_ports_ruleset_text(written, cfg)
    assert result.ok, result.errors


def test_rules_reports_exposed_ports(capsys, tmp_path):
    config = _write_ports(tmp_path)
    out = str(tmp_path / "server.nft")
    assert main(["rules", "--config", config, "--out", out]) == 0
    printed = capsys.readouterr().out
    assert "OK: wrote" in printed
    for port in ("tcp/22", "tcp/443", "tcp/8080", "udp/1194", "udp/51820"):
        assert port in printed


def test_rules_output_is_deterministic(tmp_path):
    config = _write_ports(tmp_path)
    out1 = str(tmp_path / "a.nft")
    out2 = str(tmp_path / "b.nft")
    assert main(["rules", "--config", config, "--out", out1]) == 0
    assert main(["rules", "--config", config, "--out", out2]) == 0
    assert open(out1, encoding="utf-8").read() == open(out2, encoding="utf-8").read()


def test_rules_emits_ssh_rate_limit_ban(capsys, tmp_path):
    """The `rules` output carries the sshguard/fail2ban-style ban."""
    config = _write_ports(tmp_path)
    out = str(tmp_path / "server.nft")
    assert main(["rules", "--config", config, "--out", out]) == 0
    written = open(out, encoding="utf-8").read()
    assert "tcp dport 22 ct state new limit rate over 3/minute burst 5 packets drop" in written


def test_rules_reflects_declared_ports(capsys, tmp_path):
    # A config declaring no extra ports exposes only SSH + WireGuard.
    config = _write_ports(tmp_path, allowed_tcp=[8080], allowed_udp=[])
    out = str(tmp_path / "server.nft")
    assert main(["rules", "--config", config, "--out", out]) == 0
    written = open(out, encoding="utf-8").read()
    # SSH + the single declared extra (8080) are present...
    assert "tcp dport 22 accept" in written
    assert "tcp dport 8080 accept" in written
    # ...and the default extra (443) is absent.
    assert "tcp dport 443 accept" not in written


def test_rules_missing_config_exits_two(capsys, tmp_path):
    out = str(tmp_path / "server.nft")
    assert (
        main(["rules", "--config", "/nonexistent/ports.yaml", "--out", out]) == 2
    )
    err = capsys.readouterr().err
    assert "spec not found" in err
    assert not os.path.exists(out)


def test_rules_requires_config_flag():
    with pytest.raises(SystemExit) as exc:
        main(["rules", "--out", "server.nft"])
    assert exc.value.code == 2


def test_rules_requires_out_flag():
    with pytest.raises(SystemExit) as exc:
        main(["rules", "--config", PORTS])
    assert exc.value.code == 2


def test_rules_help_lists_config_and_out(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["rules", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--config" in out
    assert "--out" in out


def test_rules_missing_out_dir_is_created(tmp_path):
    config = _write_ports(tmp_path)
    out = str(tmp_path / "does" / "not" / "exist" / "server.nft")
    assert main(["rules", "--config", config, "--out", out]) == 0
    assert os.path.isfile(out)


def test_rules_invoked_as_module(capsys, tmp_path):
    config = _write_ports(tmp_path)
    out = str(tmp_path / "server.nft")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pengyos_netctl",
            "rules",
            "--config",
            config,
            "--out",
            out,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK: wrote" in proc.stdout
    assert os.path.isfile(out)
