"""Smoke tests for the pengyos-appimage scaffold.

These verify the package imports, the CLI parser wires up all four
subcommands, the (not-yet-implemented) handlers raise an honest
``NotYetImplementedError``, and the console entry point works.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_appimage import __version__, cli

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"


def test_package_has_version():
    assert isinstance(__version__, str)
    assert __version__


def test_parser_registers_all_subcommands():
    parser = cli.build_parser()
    # Each subcommand needs valid required args to parse; verify the dispatch
    # target is set for every registered command.
    cases = {
        "inspect": ["inspect", "x.AppImage"],
        "extract": ["extract", "x.AppImage", "--to", "/tmp/x"],
        "integrate": ["integrate", "/tmp/app"],
        "run": ["run", "/tmp/app"],
    }
    for cmd, argv in cases.items():
        args = parser.parse_args(argv)
        assert args.command == cmd


def test_inspect_handler_is_implemented():
    # ``inspect`` is now implemented: the handler must be the real one, not the
    # placeholder that raises ``NotYetImplementedError``.
    assert cli._HANDLERS["inspect"] is cli._cmd_inspect
    import inspect as py_inspect

    assert "NotYetImplementedError" not in py_inspect.getsource(cli._cmd_inspect)


def test_all_four_handlers_are_implemented():
    # All subcommands are now implemented: each handler is the real one, not the
    # placeholder that raises ``NotYetImplementedError``.
    import inspect as py_inspect

    expected = {
        "inspect": cli._cmd_inspect,
        "extract": cli._cmd_extract,
        "integrate": cli._cmd_integrate,
        "run": cli._cmd_run,
    }
    for command, handler in expected.items():
        assert cli._HANDLERS[command] is handler
        assert "NotYetImplementedError" not in py_inspect.getsource(handler)


def test_run_handler_builds_parser_with_sandbox_flags():
    # ``run`` gained a documented bubblewrap profile: --sandbox / --no-sandbox.
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--sandbox", "/tmp/app"])
    assert args.command == "run"
    assert args.sandbox is True
    assert args.no_sandbox is False
    args = parser.parse_args(["run", "--no-sandbox", "/tmp/app"])
    assert args.no_sandbox is True
    args = parser.parse_args(["run", "/tmp/app"])
    # Default: sandboxed.
    assert args.sandbox is False
    assert args.no_sandbox is False


def test_main_no_subcommand_prints_help():
    # ``required=True`` on subparsers means no command -> argparse exits 2.
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == cli.EXIT_USAGE


def test_missing_required_to_arg_is_usage_error():
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["extract", "x.AppImage"])
    assert exc.value.code == cli.EXIT_USAGE


def test_cli_version_flag_via_module():
    env = dict(os.environ, PYTHONPATH=str(SRC_DIR))
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_appimage", "--version"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_cli_help_flag_lists_all_subcommands():
    env = dict(os.environ, PYTHONPATH=str(SRC_DIR))
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_appimage", "--help"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    assert result.returncode == 0
    for cmd in ("inspect", "extract", "integrate", "run"):
        assert cmd in result.stdout
