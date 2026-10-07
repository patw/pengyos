"""Scaffold tests for pengyos-sbom.

These lock in the *CLI* contract: the package is importable with a
version, the argument structure for every subcommand is correct, usage
errors exit with the argparse code, and missing-file/invalid inputs exit
with the generic error code. All four subcommands are fully implemented
(see test_spdx.py, test_validate.py, test_sign_verify.py for the
functional coverage).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

from pengyos_sbom import __version__, cli, constants  # noqa: E402


def run_cli(*args, env_extra=None):
    """Invoke the CLI as a subprocess exactly as an end user would."""
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "pengyos_sbom", *map(str, args)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- Package ------------------------------------------------------------
def test_package_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_constants_are_stable():
    assert constants.EXIT_OK == 0
    assert constants.EXIT_ERROR == 1
    assert constants.EXIT_USAGE == 2
    assert constants.EXIT_NOT_IMPLEMENTED == 3
    assert constants.SIG_EXT == ".sig"
    assert constants.SPDX_VERSION == "2.3"
    assert constants.CYCLONEDX_SPEC_VERSION == "1.5"


# --- Help / version -----------------------------------------------------
def test_help_lists_all_subcommands():
    res = run_cli("--help")
    assert res.returncode == 0
    for name in ("generate", "validate", "sign", "verify"):
        assert name in res.stdout


def test_help_shows_program_name():
    res = run_cli("--help")
    assert res.returncode == 0
    assert "pengyos-sbom" in res.stdout


def test_version_flag():
    res = run_cli("--version")
    assert res.returncode == 0
    assert __version__ in res.stdout
    assert "pengyos-sbom" in res.stdout


# --- Usage errors (argparse, exit code 2) -------------------------------
def test_no_command_is_usage_error():
    res = run_cli()
    assert res.returncode == constants.EXIT_USAGE
    assert "COMMAND" in res.stderr or "usage" in res.stderr.lower()


def test_unknown_subcommand_is_usage_error():
    res = run_cli("frobnicate")
    assert res.returncode == constants.EXIT_USAGE
    assert "invalid choice" in res.stderr


def test_generate_requires_out():
    res = run_cli("generate", "--legal-info", "x", "--show-info", "y")
    assert res.returncode == constants.EXIT_USAGE
    assert "--out" in res.stderr


def test_generate_requires_all_required_flags():
    res = run_cli("generate")
    assert res.returncode == constants.EXIT_USAGE


def test_validate_format_choices_enforced():
    res = run_cli("validate", "somefile.json", "--format", "bogus")
    assert res.returncode == constants.EXIT_USAGE
    assert "invalid choice" in res.stderr


def test_sign_requires_key():
    res = run_cli("sign", "somefile.json")
    assert res.returncode == constants.EXIT_USAGE
    assert "--key" in res.stderr


# --- Dispatch: no subcommand falls through to a scaffold path ------------
def test_no_subcommand_is_unimplemented():
    # All four subcommands are implemented; running them with bad input must
    # produce a real result (error/usage), never the removed scaffold code.
    argvs = [
        ["validate", "does-not-exist.json"],
        ["sign", "does-not-exist.json", "--key", "does-not-exist.key"],
        ["verify", "does-not-exist.json", "--key", "does-not-exist.key"],
        ["generate", "--legal-info", "x", "--show-info", "y", "--out", "z"],
    ]
    for argv in argvs:
        rc = cli.main(argv)
        assert rc != constants.EXIT_NOT_IMPLEMENTED, argv
        assert rc in (constants.EXIT_OK, constants.EXIT_ERROR, constants.EXIT_USAGE)


# --- Direct main() invocation (no subprocess) --------------------------
def test_main_returns_zero_for_help():
    assert cli.main(["--help"]) == constants.EXIT_OK


def test_main_returns_zero_for_version():
    assert cli.main(["--version"]) == constants.EXIT_OK


def test_main_returns_error_code_for_missing_file():
    assert cli.main(["validate", "does-not-exist.json"]) == constants.EXIT_ERROR
    assert (
        cli.main(["sign", "does-not-exist.json", "--key", "does-not-exist.key"])
        == constants.EXIT_ERROR
    )
    assert (
        cli.main(["verify", "does-not-exist.json", "--key", "does-not-exist.key"])
        == constants.EXIT_ERROR
    )


def test_main_returns_usage_code_for_bad_choice():
    assert cli.main(["nope"]) == constants.EXIT_USAGE


# --- Console-script declaration ----------------------------------------
def test_console_script_is_declared():
    pyproject = (REPO / "pyproject.toml").read_text()
    assert "pengyos-sbom = " in pyproject
    assert "pengyos_sbom.cli:main" in pyproject


def test_generate_honours_source_date_epoch(tmp_path):
    """Two runs with one SOURCE_DATE_EPOCH write byte-identical documents."""
    fix = REPO / "tests" / "fixtures"
    outs = []
    for i in range(2):
        spdx_out, cdx_out = tmp_path / f"{i}.spdx.json", tmp_path / f"{i}.cdx.json"
        res = run_cli(
            "generate", "--legal-info", fix / "legal-info",
            "--show-info", fix / "show-info.json",
            "--out", spdx_out, "--cdx", cdx_out,
            env_extra={"SOURCE_DATE_EPOCH": "1790978063"},
        )
        assert res.returncode == 0, res.stderr
        outs.append((spdx_out.read_bytes(), cdx_out.read_bytes()))
    assert outs[0] == outs[1]
    assert b"2026-10-02T21:54:23+00:00" in outs[0][0]
