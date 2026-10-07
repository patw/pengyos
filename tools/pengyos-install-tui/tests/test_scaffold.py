"""Smoke tests for the pengyos-install-tui scaffold.

The scaffold provides the CLI skeleton: package imports, the `run` / `auto`
subcommands with the spec option surface (`--answers`, `--config`, shared
`--json`), the exit-code convention, and the `NotYetImplementedError`
placeholder (kept as a convention for future phases). Both `run` and `auto`
handlers are now implemented (see `tests/test_run.py` and `tests/test_auto.py`).
Here we assert the argument surface / exit codes are wired correctly, that the
`run` handler replays a `--answers` set end-to-end, and that the
`NotYetImplementedError` shape is stable for later use.
"""

import contextlib
import importlib
import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import pengyos_install_tui
from pengyos_install_tui import __version__
from pengyos_install_tui.cli import (
    DEFAULT_CONFIG,
    ExitCode,
    NotYetImplementedError,
    build_parser,
    main,
)

ROOT = Path(__file__).resolve().parent.parent


def test_package_imports():
    assert isinstance(__version__, str)
    assert __version__
    # __main__ must import cleanly (python -m entry path).
    importlib.import_module("pengyos_install_tui.__main__")


def test_version_matches_pyproject():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == meta["project"]["version"]


def test_pyproject_stdlib_only():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    # Standard library only at runtime; pytest lives in the test extra.
    assert meta["project"]["dependencies"] == []
    assert meta["project"]["optional-dependencies"]["test"] == ["pytest>=7"]


def test_console_script_target_exists():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mod_name, fn_name = meta["project"]["scripts"]["pengyos-install-tui"].split(":")
    mod = importlib.import_module(mod_name)
    assert callable(getattr(mod, fn_name))


def test_exit_code_convention():
    assert ExitCode.OK == 0
    assert ExitCode.ERROR == 1
    assert ExitCode.USAGE == 2  # argparse's own exit code — must not collide
    assert ExitCode.NOT_IMPLEMENTED == 3


def test_default_config():
    assert DEFAULT_CONFIG == "install.toml"


def test_help_lists_subcommands():
    code, out, err = _run("--help")
    assert code == ExitCode.OK
    assert "pengyos-install-tui" in out
    assert "run" in out and "auto" in out


def test_help_shows_subcommand_options():
    code, out, err = _run("run", "--help")
    assert code == ExitCode.OK
    assert "--answers" in out
    assert "--json" in out
    code, out, err = _run("auto", "--help")
    assert code == ExitCode.OK
    assert "--config" in out
    assert "--json" in out


def test_subcommand_defaults():
    ns = build_parser().parse_args(["run", "--answers", "answers.json"])
    assert ns.command == "run"
    assert ns.answers == "answers.json"
    assert ns.json is False
    ns = build_parser().parse_args(["auto", "--config", "install.toml"])
    assert ns.command == "auto"
    assert ns.config == "install.toml"
    assert ns.json is False
    ns = build_parser().parse_args(["auto"])
    # --config defaults to the well-known install.toml.
    assert ns.config == DEFAULT_CONFIG


def test_missing_command_is_usage_error():
    code, out, err = _run()
    assert code == ExitCode.USAGE
    # argparse reports the required subcommand (the metavar "COMMAND").
    assert "COMMAND" in err or "required" in err


def test_unknown_option_is_usage_error():
    code, out, err = _run("run", "--nope")
    assert code == ExitCode.USAGE
    assert "unrecognized" in err


def test_run_is_implemented(tmp_path):
    # run is implemented (not the exit-3 placeholder): a --answers replay drives
    # the full CLI handler end-to-end without a terminal.
    answers = {
        "target": "/dev/sdb",
        "edition": "core",
        "layout": "single-root",
        "user": "alice",
        "network": "dhcp",
    }
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    code, out, err = _run("run", "--answers", str(path))
    assert code == ExitCode.OK
    assert "target:   /dev/sdb" in out
    assert "edition:  core" in out


def test_not_yet_implemented_error_shape():
    exc = NotYetImplementedError("run", "the task")
    assert "run" in str(exc) and "the task" in str(exc)


def test_version_flag():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        with pytest.raises(SystemExit) as e:
            main(["--version"])
    assert e.value.code == ExitCode.OK
    assert f"pengyos-install-tui {__version__}" in out.getvalue()


def test_python_dash_m_entrypoint():
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_install_tui", "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert "pengyos-install-tui" in proc.stdout
    assert "run" in proc.stdout and "auto" in proc.stdout


def _run(*argv: str) -> tuple[int | str, str, str]:
    """Run the CLI in-process, capturing stdout/stderr and the exit code.
    argparse failures raise SystemExit; normal returns come from main()."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()
