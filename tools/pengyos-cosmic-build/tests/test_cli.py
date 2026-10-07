"""Tests for the pengyos-cosmic-build CLI framework.

These exercise the public contract of the scaffold iteration: the subcommand
surface (names, arguments, JSON mode, exit codes), clean error handling for a
missing input, and a clear, non-zero report for a subcommand whose feature body
is not yet landed. The feature bodies themselves are tested in their own
subtasks.
"""

from __future__ import annotations

import json
import subprocess
import sys

from pengyos_cosmic_build.cli import FeatureNotReady


def _code(run_cli, *args: str) -> int:
    """Run the CLI and return its exit code, capturing SystemExit."""
    try:
        return run_cli(*args)
    except SystemExit as exc:  # argparse errors raise SystemExit(2)
        return exc.code if isinstance(exc.code, int) else 0


def test_version(run_cli):
    # argparse's --version action raises SystemExit(0).
    assert _code(run_cli, "--version") == 0


def test_help_lists_all_subcommands(run_cli, capsys):
    rc = _code(run_cli, "--help")
    assert rc == 0
    out = capsys.readouterr().out
    for sub in ("vendor-all", "emit", "wire", "lint"):
        assert sub in out


def test_no_command_is_an_error(run_cli):
    # required subparser -> argparse exits non-zero (2).
    assert _code(run_cli) != 0


def test_unknown_command_is_an_error(run_cli):
    assert _code(run_cli, "bogus") != 0


def test_vendor_all_requires_inputs(run_cli):
    assert _code(run_cli, "vendor-all", "--out", "/tmp/x") != 0


def test_emit_requires_manifest_and_group(run_cli):
    assert _code(run_cli, "emit") != 0


def test_wire_requires_repo(run_cli):
    assert _code(run_cli, "wire") != 0


def test_lint_requires_repo(run_cli):
    assert _code(run_cli, "lint") != 0


def test_missing_manifest_file_reported(run_cli, capsys):
    # The manifest path does not exist -> clean "file not found", exit 1.
    rc = run_cli(
        "emit",
        "--manifest",
        "/tmp/definitely-not-here-xyz.toml",
        "--vendor",
        "/tmp/vendor",
        "--out",
        "/tmp/x",
        "--group",
        "core",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "file not found" in err


def test_missing_manifest_file_json(run_cli, capsys):
    rc = run_cli(
        "emit",
        "--manifest",
        "/tmp/definitely-not-here-xyz.toml",
        "--vendor",
        "/tmp/vendor",
        "--out",
        "/tmp/x",
        "--group",
        "core",
        "--json",
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "file not found" in payload["error"]


def test_module_invocation(run_cli, repo_root):
    # The documented `python -m pengyos_cosmic_build` entry point works.
    from pengyos_cosmic_build.cli import __version__

    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_cosmic_build", "--version"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert __version__ in proc.stdout


def test_feature_not_ready_is_exported():
    # The placeholder exception is importable and a RuntimeError.
    assert issubclass(FeatureNotReady, RuntimeError)
