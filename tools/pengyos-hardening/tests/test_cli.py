"""Tests for the command-line dispatch surface (pengyos_hardening/cli.py)."""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from pengyos_hardening import __version__
from pengyos_hardening.cli import build_parser, main


def test_parser_has_baseline_and_check_subcommands():
    parser = build_parser()
    choices = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choices = set(action.choices)
    assert "baseline" in choices
    assert "check" in choices


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    assert __version__ in out


def test_no_command_errors_with_usage(capsys):
    """A bare invocation must fail with a usage error (exit code 2)."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_check_without_tree_errors(capsys):
    """`check` requires --tree; omitting it is a usage error (exit code 2)."""
    with pytest.raises(SystemExit) as exc:
        main(["check"])
    assert exc.value.code == 2


def test_module_invocation_reports_version():
    """`python3 -m pengyos_hardening --version` prints the version and exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_hardening", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout
