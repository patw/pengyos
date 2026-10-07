"""Scaffold smoke tests.

These verify the package skeleton, packaging metadata, and the working
``--version`` / ``--help`` CLI surface. The full feature suite lives in the
later subtasks (schema, collectors, CLI, diff, github-ready).
"""

from __future__ import annotations

import subprocess
import sys

import pengyos_hwprofile
from pengyos_hwprofile import __version__
from pengyos_hwprofile.cli import build_parser, main


def test_version_constant_matches_metadata():
    # Kept in sync with pyproject.toml on purpose.
    assert __version__ == "0.4.0"


def test_parser_builds_with_correct_prog():
    parser = build_parser()
    assert parser.prog == "pengyos-hwprofile"
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
    assert "pengyos-hwprofile" in out


def test_bare_invocation_prints_help(capsys):
    # Scaffold: no subcommand yet, so a bare call prints help and exits 0.
    assert main([]) == 0
    assert "usage" in capsys.readouterr().err.lower()


def test_module_entrypoint_help():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_hwprofile", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "hardware" in result.stdout.lower()


def test_module_entrypoint_version():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_hwprofile", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout
