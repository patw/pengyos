"""Scaffold smoke tests.

These verify the package skeleton, packaging metadata, and the working
``--version`` / ``--help`` CLI surface. The full feature suite (plan, apply,
rollback, the transport, the journal) lives in the later subtasks.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import pengyos_fleet_rollout
from pengyos_fleet_rollout import __version__
from pengyos_fleet_rollout.cli import build_parser, main


def test_version_constant_matches_metadata():
    # Kept in sync with pyproject.toml on purpose.
    assert __version__ == "0.1.0"


def test_parser_builds_with_correct_prog():
    parser = build_parser()
    assert parser.prog == "pengyos-fleet-rollout"
    assert "rollout" in parser.format_help().lower()


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "pengyos-fleet-rollout" in capsys.readouterr().out


def test_bare_invocation_prints_help(capsys):
    # A bare call (no subcommand) prints help to stderr and exits 0.
    assert main([]) == 0
    assert "usage" in capsys.readouterr().err.lower()


def test_module_entrypoint_help():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_fleet_rollout", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "pengyos-fleet-rollout" in result.stdout


def test_module_entrypoint_version():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_fleet_rollout", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout


def test_parser_advertises_plan_apply_rollback():
    # The spec fixes the interface up front; the parser advertises all three.
    help_text = build_parser().format_help()
    for cmd in ("plan", "apply", "rollback"):
        assert cmd in help_text, f"{cmd} is not advertised by the CLI"


def test_plan_subparser_has_required_args():
    # The documented ``plan`` interface: --image + --inventory (+ optional
    # --batch-size / --json). The handler that fills these in lands later.
    parser = build_parser()
    args = parser.parse_args(
        ["plan", "--image", "img-1", "--inventory", "inv.toml"]
    )
    assert args.command == "plan"
    assert args.image == "img-1"
    assert args.inventory == "inv.toml"
    assert args.batch_size == 1
    assert args.json is False


def test_apply_subparser_defaults_to_dry_run():
    # The documented ``apply`` interface defaults to a dry run (commit=False);
    # --commit flips it to actually run. The handler that fills this in lands
    # later.
    parser = build_parser()
    dry = parser.parse_args(["apply", "--plan", "p.json", "--journal", "j.json"])
    assert dry.command == "apply"
    assert dry.transport == "noop"
    assert dry.commit is False  # dry run by default

    committed = parser.parse_args(
        ["apply", "--plan", "p.json", "--journal", "j.json", "--commit"]
    )
    assert committed.commit is True

    explicit_dry = parser.parse_args(
        ["apply", "--plan", "p.json", "--journal", "j.json", "--dry-run"]
    )
    assert explicit_dry.commit is False


def test_rollback_subparser_defaults_to_dry_run():
    parser = build_parser()
    args = parser.parse_args(["rollback", "--journal", "j.json"])
    assert args.command == "rollback"
    assert args.transport == "noop"
    assert args.commit is True
