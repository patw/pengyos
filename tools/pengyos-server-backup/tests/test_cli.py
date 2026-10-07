"""Tests for the pengyos-backup CLI (argparse dispatch, version, errors)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from pengyos_backup import __version__
from pengyos_backup.cli import build_parser, main


# --- version -----------------------------------------------------------------


def test_version_command_prints_version(run_cli_fn):
    proc = run_cli_fn("version")
    assert proc.returncode == 0
    assert proc.stdout.strip() == __version__


def test_version_flag(run_cli_fn):
    proc = run_cli_fn("--version")
    assert proc.returncode == 0
    # The --version flag prints "<prog> <version>", so the version is the
    # trailing token.
    assert proc.stdout.strip().endswith(__version__)


def test_main_version_returns_zero():
    assert main(["version"]) == 0


# --- no-args help ------------------------------------------------------------


def test_no_args_prints_help_to_stderr_and_exits_zero(capsys):
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.err
    # The known subcommands are advertised in the help text.
    for name in ("plan", "verify-restore", "render"):
        assert name in captured.err


# --- argument contract (spec.md Interface) -----------------------------------


def test_all_subcommands_registered():
    parser = build_parser()
    # Each subcommand parses with its required args and sets its dispatch func
    # (handlers raise CommandNotImplemented until their dedicated task; we only
    # assert the contract is wired).
    plan = parser.parse_args(["plan", "--config", "c.yaml", "--out", "o"])
    assert plan.command == "plan"
    assert plan.func is not None
    verify = parser.parse_args(
        ["verify-restore", "--repo", "r", "--target", "t"]
    )
    assert verify.command == "verify-restore"
    assert verify.func is not None
    render = parser.parse_args(["render", "--config", "c.yaml"])
    assert render.command == "render"
    assert render.func is not None


def test_plan_requires_config_and_out():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["plan"])  # missing --config/--out


def test_plan_defaults_binary_to_restic():
    parser = build_parser()
    args = parser.parse_args(["plan", "--config", "c.yaml", "--out", "o"])
    assert args.binary == "restic"


def test_plan_accepts_rustic_binary():
    parser = build_parser()
    args = parser.parse_args(
        ["plan", "--config", "c.yaml", "--out", "o", "--binary", "rustic"]
    )
    assert args.binary == "rustic"


def test_verify_restore_accepts_dry_run():
    parser = build_parser()
    args = parser.parse_args(
        ["verify-restore", "--repo", "r", "--target", "t", "--dry-run"]
    )
    assert args.dry_run is True


def test_verify_restore_requires_repo_and_target():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["verify-restore", "--repo", "r"])


def test_error_message_goes_to_stderr(capsys):
    assert main(["plan", "--config", "c.yaml", "--out", "o"]) == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err


# --- entry point via module --------------------------------------------------


def test_module_entrypoint_runs_version(root):
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_backup", "version"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == __version__
