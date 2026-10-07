"""Smoke and dispatch tests for the ``pengyos-sbkeys`` CLI dispatcher.

These tests exercise the plumbing built in the *Scaffold repo* subtask: argument
parsing, subcommand help, error handling and routing to handlers. The command
bodies are intentionally not implemented yet, so they raise
:class:`pengyos_sbkeys.cli.NotYetImplemented`.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_sbkeys import __version__
from pengyos_sbkeys import cli

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_version_is_set():
    assert isinstance(__version__, str)
    assert __version__.count(".") >= 2


def test_build_parser_lists_all_subcommands():
    parser = cli.build_parser()
    # The help text should mention every documented subcommand.
    help_text = parser.format_help()
    for command in ("init", "enroll-commands", "sign"):
        assert command in help_text


def test_no_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_init_requires_out():
    with pytest.raises(SystemExit) as exc:
        cli.main(["init"])
    assert exc.value.code == 2


def test_sign_requires_key():
    with pytest.raises(SystemExit) as exc:
        cli.main(["sign", "uki.efi"])
    assert exc.value.code == 2


def test_dispatch_routes_to_handler(monkeypatch):
    called = {}

    def fake_init(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(cli, "cmd_init", fake_init)
    rc = cli.main(["init", "--out", "keys"])
    assert rc == 0
    assert called["args"].out == "keys"


def test_not_yet_implemented_returns_clean_exit_code(capsys):
    # `init`, `enroll-commands` and `sign` (command generation) are now
    # implemented; exercise the NotYetImplemented path on the next
    # unimplemented behaviour, `sign --execute` (actually running the tools).
    rc = cli.main(["sign", "uki.efi", "--key", "keys/DB.key", "--execute"])
    assert rc == cli.EXIT_NOT_YET_IMPLEMENTED
    captured = capsys.readouterr()
    assert "error:" in captured.err
    # Key material must never be printed.
    assert "DB.key" not in captured.out


def test_module_invocation_help_exits_zero():
    # `python -m pengyos_sbkeys --help` must work without installing.
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_sbkeys", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for command in ("init", "enroll-commands", "sign"):
        assert command in result.stdout


def test_module_invocation_unknown_command_fails():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_sbkeys", "bogus"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
