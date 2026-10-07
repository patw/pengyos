"""Smoke and dispatch tests for the ``pengyos-secureboot-enroll`` CLI dispatcher.

These tests exercise the plumbing built in the *Scaffold repo* subtask: argument
parsing, subcommand help, error handling and routing to handlers. The command
bodies are intentionally not implemented yet, so they raise
:class:`pengyos_secureboot_enroll.cli.NotYetImplemented`.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_secureboot_enroll import __version__
from pengyos_secureboot_enroll import cli

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_version_is_set():
    assert isinstance(__version__, str)
    assert __version__.count(".") >= 2


def test_build_parser_lists_all_subcommands():
    parser = cli.build_parser()
    # The help text should mention every documented subcommand.
    help_text = parser.format_help()
    for command in ("keys", "sign-chain", "enroll", "lint"):
        assert command in help_text


def test_no_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_keys_requires_out():
    with pytest.raises(SystemExit) as exc:
        cli.main(["keys"])
    assert exc.value.code == 2


def test_sign_chain_requires_esp_and_keys():
    with pytest.raises(SystemExit) as exc:
        cli.main(["sign-chain", "--keys", "keys"])
    assert exc.value.code == 2


def test_enroll_requires_mode():
    with pytest.raises(SystemExit) as exc:
        cli.main(["enroll", "--keys", "keys"])
    assert exc.value.code == 2


def test_lint_requires_esp_and_keys():
    with pytest.raises(SystemExit) as exc:
        cli.main(["lint", "--keys", "keys"])
    assert exc.value.code == 2


def test_dispatch_routes_to_handler(monkeypatch):
    called = {}

    def fake_keys(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(cli, "cmd_keys", fake_keys)
    rc = cli.main(["keys", "--out", "keys"])
    assert rc == 0
    assert called["args"].out == "keys"


def test_not_yet_implemented_returns_clean_exit_code(capsys, monkeypatch):
    # The NotYetImplemented -> exit-code mapping is the dispatch safety net.
    # Raise it explicitly and confirm `main` turns it into the clean exit code.
    def boom(args):
        raise cli.NotYetImplemented("probe")

    monkeypatch.setattr(cli, "cmd_keys", boom)
    rc = cli.main(["keys", "--out", "keys"])
    assert rc == cli.EXIT_NOT_YET_IMPLEMENTED
    captured = capsys.readouterr()
    assert "error:" in captured.err
    # Key material must never be printed.
    assert "keys" not in captured.out


def test_module_invocation_help_exits_zero():
    # `python -m pengyos_secureboot_enroll --help` must work without installing.
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_secureboot_enroll", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for command in ("keys", "sign-chain", "enroll", "lint"):
        assert command in result.stdout


def test_module_invocation_unknown_command_fails():
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_secureboot_enroll", "bogus"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
