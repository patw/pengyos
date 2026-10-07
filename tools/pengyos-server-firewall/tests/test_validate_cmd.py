"""Tests for the ``validate`` CLI command (:mod:`pengyos_netctl.cli`)."""

import os
import sys

import pytest

from pengyos_netctl.cli import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_RULES = os.path.join(REPO_ROOT, "tests", "fixtures", "good_rules.nft")
PORTS = os.path.join(REPO_ROOT, "nftables", "ports.yaml")


def test_validate_good_ruleset_exits_zero(capsys):
    assert main(["validate", "--rules", GOOD_RULES, "--config", PORTS]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "default-deny" in out


def test_validate_bad_ruleset_exits_two(capsys, tmp_path):
    bad = tmp_path / "bad.nft"
    bad.write_text(
        open(GOOD_RULES, encoding="utf-8").read()
        .replace("tcp dport 22 accept", "tcp dport 65535 accept"),
        encoding="utf-8",
    )
    assert main(["validate", "--rules", str(bad), "--config", PORTS]) == 2
    err = capsys.readouterr().err
    assert "ruleset failed validation" in err
    assert "65535" in err


def test_validate_missing_required_arg_exits_two():
    # argparse rejects a missing required subcommand arg with SystemExit(2).
    with pytest.raises(SystemExit) as exc:
        main(["validate", "--rules", GOOD_RULES])
    assert exc.value.code == 2


def test_validate_missing_rules_file_exits_two(capsys):
    assert (
        main(["validate", "--rules", "/nonexistent/rules.nft", "--config", PORTS])
        == 2
    )
    err = capsys.readouterr().err
    assert "cannot read ruleset" in err


def test_validate_requires_rules_flag():
    with pytest.raises(SystemExit) as exc:
        main(["validate", "--config", PORTS])
    assert exc.value.code == 2


def test_validate_help_lists_rules_and_config(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["validate", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--rules" in out
    assert "--config" in out


def test_validate_invoked_as_module(capsys):
    """`python -m pengyos_netctl validate ...` must work (no install)."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_netctl", "validate",
         "--rules", GOOD_RULES, "--config", PORTS],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
