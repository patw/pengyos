"""Tests for the CLI framework (:mod:`pengyos_netctl.cli`)."""

import subprocess
import sys

import pytest

from pengyos_netctl import __version__
from pengyos_netctl.cli import COMMANDS, main
from pengyos_netctl.errors import NetctlError


def test_main_no_command_prints_help_and_exits_zero(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "pengyos-netctl" in out
    assert "usage" in out.lower()


def test_main_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_main_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_main_reports_netctl_error_as_exit_2(monkeypatch, capsys):
    def boom(_args):
        raise NetctlError("boom")

    monkeypatch.setitem(COMMANDS, "boomcmd", (boom, "boom"))
    try:
        assert main(["boomcmd"]) == 2
    finally:
        monkeypatch.delitem(COMMANDS, "boomcmd", raising=False)
    err = capsys.readouterr().err
    assert "error: boom" in err


def test_main_unknown_command_exits_two(capsys):
    # argparse rejects an unrecognised subcommand with exit code 2.
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2


def test_cli_module_invocation_version():
    """`python -m pengyos_netctl --version` must work (no install needed)."""
    repo_root = subprocess.run(
        [sys.executable, "-m", "pengyos_netctl", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in repo_root.stdout
