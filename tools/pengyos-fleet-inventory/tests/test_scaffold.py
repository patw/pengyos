"""Scaffold smoke tests.

These verify the package skeleton, packaging metadata, and the working
``--version`` / ``--help`` CLI surface. The full feature suite lives in the
later subtasks (runner + host list, fact collection, emit, idempotency,
github-ready).
"""

from __future__ import annotations

import subprocess
import sys

import pengyos_fleet
from pengyos_fleet import __version__
from pengyos_fleet.cli import build_parser, main


def test_version_constant_matches_metadata():
    # Kept in sync with pyproject.toml on purpose.
    assert __version__ == "0.1.0"


def test_parser_builds_with_correct_prog():
    parser = build_parser()
    assert parser.prog == "pengyos-fleet"
    assert "hardware" in parser.format_help().lower()


def test_version_flag_exits_zero(capsys):
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_flag_exits_zero(capsys):
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "pengyos-fleet" in out


def test_bare_invocation_prints_help(capsys):
    # A bare call (no subcommand) prints help and exits 0.
    assert main([]) == 0
    assert "usage" in capsys.readouterr().err.lower()


def test_module_entrypoint_help():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_fleet", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "hardware" in result.stdout.lower()


def test_module_entrypoint_version():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_fleet", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout


def test_parser_advertises_collect_and_diff():
    # ``collect`` lands in the emit subtask; ``diff`` lands in the diff subtask.
    help_text = build_parser().format_help()
    assert "collect" in help_text
    assert "diff" in help_text
