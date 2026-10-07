"""Tests for the pengyos-cosmic-pkg CLI framework.

These exercise the public contract of the scaffold iteration: the subcommand
surface (names, arguments, JSON mode, exit codes), clean error handling for a
missing manifest, and a clear, non-zero report for a subcommand whose feature
body is not yet landed. The feature bodies themselves are tested in their own
subtasks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_cosmic_pkg.cli import FeatureNotReady, main


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
    for sub in ("vendor", "recipe", "lint"):
        assert sub in out


def test_no_command_is_an_error(run_cli):
    # required subparser -> argparse exits non-zero (2).
    assert _code(run_cli) != 0


def test_unknown_command_is_an_error(run_cli):
    assert _code(run_cli, "bogus") != 0


def test_vendor_requires_manifest(run_cli):
    assert _code(run_cli, "vendor", "--out", "/tmp/x") != 0


def test_recipe_requires_manifest_and_out(run_cli):
    assert _code(run_cli, "recipe") != 0


def test_lint_requires_dir(run_cli):
    assert _code(run_cli, "lint") != 0


def test_lint_reports_violations_cleanly(run_cli, capsys, tmp_path):
    # lint is implemented; a package dir with problems exits 1 with a clear
    # message (never a traceback) — a reported condition, not a crash.
    pkgdir = tmp_path / "pkg"
    pkgdir.mkdir()
    rc = run_cli("lint", "--dir", str(pkgdir))
    assert rc == 1
    err = capsys.readouterr().err
    assert "lint" in err or "violation" in err.lower()


def test_lint_json_mode_reports_violations(run_cli, capsys, tmp_path):
    pkgdir = tmp_path / "pkg"
    pkgdir.mkdir()
    rc = run_cli("lint", "--dir", str(pkgdir), "--json")
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "lint"
    assert payload["ok"] is False
    assert "violations" in payload


def test_manifest_error_reported_cleanly(run_cli, capsys, tmp_path):
    # A manifest that fails semantic validation (here: an empty manifest with no
    # required fields) is reported cleanly (exit 1, no traceback) by the feature
    # module's ManifestError, not the CLI framework. vendor now needs a
    # --cargo-lock too, so a real one is supplied.
    manifest = tmp_path / "m.toml"
    manifest.write_text("# empty manifest\n", encoding="utf-8")
    lock = tmp_path / "Cargo.lock"
    lock.write_text('version = "3"\n', encoding="utf-8")
    rc = run_cli(
        "vendor",
        "--manifest",
        str(manifest),
        "--cargo-lock",
        str(lock),
        "--out",
        str(tmp_path),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "vendor" in err
    assert "required field" in err


def test_missing_manifest_file_reported(run_cli, capsys, repo_root):
    # The manifest path does not exist -> clean "file not found", exit 1. A
    # real --cargo-lock is supplied so the (now-required) argument is satisfied.
    lock = repo_root / "tests" / "fixtures" / "workspace" / "Cargo.lock"
    rc = run_cli(
        "vendor",
        "--manifest",
        "/tmp/definitely-not-here-xyz.toml",
        "--cargo-lock",
        str(lock),
        "--out",
        "/tmp/x",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "file not found" in err


def test_missing_manifest_file_json(run_cli, capsys, repo_root):
    lock = repo_root / "tests" / "fixtures" / "workspace" / "Cargo.lock"
    rc = run_cli(
        "vendor",
        "--manifest",
        "/tmp/definitely-not-here-xyz.toml",
        "--cargo-lock",
        str(lock),
        "--out",
        "/tmp/x",
        "--json",
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "file not found" in payload["error"]


def test_module_invocation(run_cli, repo_root):
    # The documented `python -m pengyos_cosmic_pkg` entry point works.
    from pengyos_cosmic_pkg.cli import __version__

    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_cosmic_pkg", "--version"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert __version__ in proc.stdout


def test_feature_not_ready_is_exported():
    # The placeholder exception is importable and a RuntimeError.
    assert issubclass(FeatureNotReady, RuntimeError)
