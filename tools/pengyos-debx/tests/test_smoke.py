"""Smoke tests for the pengyos-debx scaffold.

These verify the package imports, the CLI parser wires up all three
subcommands, the (not-yet-implemented) handlers raise an honest
``NotYetImplementedError``, and the console entry point works.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_debx import __version__, cli

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
        "extract": ["extract", "x.deb", "--into", "/tmp/x"],
        "analyse": ["analyse", "/tmp/x"],
        "list-scripts": ["list-scripts", "x.deb"],
    }
    for cmd, argv in cases.items():
        args = parser.parse_args(argv)
        assert args.command == cmd


def test_extract_requires_into():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["extract", "x.deb"])


def test_list_scripts_handler_is_implemented():
    # ``list-scripts`` is now implemented (lists scripts, never runs them); it
    # must NOT be an honest ``NotYetImplementedError`` placeholder.
    import inspect as py_inspect

    assert "NotYetImplementedError" not in py_inspect.getsource(cli._cmd_list_scripts)
    assert "list_scripts_from_path" in py_inspect.getsource(cli._cmd_list_scripts)


def test_extract_handler_is_implemented():
    # ``extract`` is now implemented (writes the payload to a prefix); it must
    # NOT be an honest ``NotYetImplementedError`` placeholder.
    import inspect as py_inspect

    assert "NotYetImplementedError" not in py_inspect.getsource(cli._cmd_extract)
    assert "extract_path" in py_inspect.getsource(cli._cmd_extract)


def test_analyse_handler_is_implemented():
    # ``analyse`` is now implemented (scans DT_NEEDED by pure ELF parsing); it
    # must NOT be an honest ``NotYetImplementedError`` placeholder.
    import inspect as py_inspect

    assert "NotYetImplementedError" not in py_inspect.getsource(cli._cmd_analyse)
    assert "analyse_path" in py_inspect.getsource(cli._cmd_analyse)


def test_main_no_subcommand_prints_help():
    # ``required=True`` on subparsers means no command -> argparse exits 2.
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == cli.EXIT_USAGE


def test_main_placeholder_handler_returns_exit_code():
    # A placeholder handler is turned into exit code 3, not a crash. We prove the
    # mapping directly by pointing a handler at a NotYetImplementedError-raising
    # function (all real handlers are now implemented, so no natural placeholder
    # remains to test against).
    def _placeholder(*_args):
        raise cli.NotYetImplementedError("placeholder")

    cli._HANDLERS["analyse"] = _placeholder
    try:
        rc = cli.main(["analyse", "/tmp/x"])
    finally:
        cli._HANDLERS["analyse"] = cli._cmd_analyse
    assert rc == cli.EXIT_NOT_IMPLEMENTED


def test_cli_version_flag_via_module():
    env = dict(os.environ, PYTHONPATH=str(SRC_DIR))
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_debx", "--version"],
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
        [sys.executable, "-m", "pengyos_debx", "--help"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    assert result.returncode == 0
    for cmd in ("extract", "analyse", "list-scripts"):
        assert cmd in result.stdout
