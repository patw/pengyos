"""Scaffold tests for pengyos-hwprofile-kernel.

These lock in the package structure, the console entry point, the ``--version``
/ ``--help`` surface, and the ``python -m`` entry point. The ``select`` /
``exclude`` / ``explain`` / ``diff`` subcommands are all wired; this suite only asserts what the scaffold provides.

No network, no root, no external tools — only in-process CLI calls and one
``python -m`` subprocess.
"""

from __future__ import annotations

import subprocess
import sys

import tomllib

from pengyos_hwprofile_kernel import __version__
from pengyos_hwprofile_kernel.cli import build_parser, main


def _subparser_action(parser, name):
    """Return the ``ArgumentParser`` action for subcommand *name*."""
    for action in parser._subparsers._actions:
        if getattr(action, "choices", None) and name in action.choices:
            return action
    raise AssertionError(f"subcommand {name!r} not found")


# ---------------------------------------------------------------------------
# Package / metadata.
# ---------------------------------------------------------------------------


def test_scaffold_version_is_semver():
    assert __version__ == "0.1.0"
    assert __version__.count(".") >= 2
    assert __version__.split(".")[0].isdigit()


def test_pyproject_version_in_sync():
    data = tomllib.loads(
        _repo_file("pyproject.toml").read_text(encoding="utf-8")
    )
    assert data["project"]["version"] == __version__


def test_pyproject_has_console_entry_point():
    data = tomllib.loads(
        _repo_file("pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = data["project"]["scripts"]
    assert scripts["pengyos-hwprofile-kernel"] == "pengyos_hwprofile_kernel.cli:main"
    # Runtime is stdlib-only; nothing to declare.
    assert data["project"]["dependencies"] == []


def _repo_file(name):
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / name


# ---------------------------------------------------------------------------
# CLI surface.
# ---------------------------------------------------------------------------


def test_scaffold_parser_has_version():
    parser = build_parser()
    help_text = parser.format_help()
    assert "--version" in help_text


def test_scaffold_version_flag():
    # --version exits 0 through the normalized path.
    assert main(["--version"]) == 0


def test_scaffold_no_command_returns_zero(capsys):
    # Bare invocation prints help (to stderr, matching the hwprofile family
    # convention) and returns 0.
    assert main([]) == 0
    assert "usage" in capsys.readouterr().err.lower()


def test_scaffold_python_m_entry_point():
    # `python -m pengyos_hwprofile_kernel --version` runs the package entry point.
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_hwprofile_kernel", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert __version__ in (result.stdout + result.stderr)
