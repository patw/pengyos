"""Scaffold smoke tests.

These verify the package skeleton, packaging metadata, and the working
``--version`` / ``--help`` CLI surface. The feature suite (checks, report,
fixtures, GitHub-ready) lives in the other test modules.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import pengyos_verifier
from pengyos_verifier import __version__
from pengyos_verifier.cli import PROG, build_parser, main


def test_version_constant_is_semver():
    # Kept in sync with pyproject.toml on purpose.
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_version_constant_matches_pyproject():
    # The pyproject version is the source of truth for packaging; keep
    # the package constant in lock-step so `--version` and metadata agree.
    import pathlib
    import tomllib

    pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as fh:
        meta = tomllib.load(fh)
    assert meta["project"]["version"] == __version__


def test_parser_builds_with_correct_prog():
    parser = build_parser()
    assert parser.prog == PROG == "pengyos-verifier"
    help_text = parser.format_help().lower()
    assert "witness" in help_text


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_help_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert PROG in out


def test_bare_invocation_prints_help_and_exits_zero(capsys):
    # Scaffold: no subcommands yet, so a bare call prints help and exits 0.
    assert main([]) == 0
    assert "usage" in capsys.readouterr().err.lower()


def test_module_entrypoint_help():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_verifier", "--help"],
        capture_output=True,
        text=True,
        cwd=str(_tool_root()),
    )
    assert result.returncode == 0, result.stderr
    assert PROG in result.stdout


def test_module_entrypoint_version():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_verifier", "--version"],
        capture_output=True,
        text=True,
        cwd=str(_tool_root()),
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout


def _tool_root():
    import pathlib

    return pathlib.Path(__file__).resolve().parent.parent
