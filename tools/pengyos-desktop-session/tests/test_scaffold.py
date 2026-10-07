"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, subcommand dispatch,
  bad usage),
* the CLI argument surface (``emit`` / ``check``) is registered with the
  documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
from pathlib import Path

import pytest

import pengyos_desktop_session as pkg
from pengyos_desktop_session import cli
from pengyos_desktop_session import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- package metadata -------------------------------------------------------


def test_package_exposes_semver_string():
    assert isinstance(pkg.__version__, str)
    parts = pkg.__version__.split(".")
    # At least a numeric MAJOR.MINOR.PATCH core.
    assert len(parts) >= 3
    assert parts[0].isdigit()
    assert parts[1].isdigit()
    assert parts[2][:1].isdigit()


def test_version_matches_pyproject():
    """__version__ must not drift from the declared build metadata."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text
    assert 'name = "pengyos-desktop-session"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does ``pip install -e .``), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-desktop-session")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-desktop-session is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-desktop-session = "pengyos_desktop_session.cli:main"' in text


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = cli.main(["--version"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert __version__ in out
    assert "pengyos-desktop-session" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "pengyos-desktop-session" in out
    # All documented subcommands are advertised.
    for name in ("emit", "check"):
        assert name in out


def test_emit_dispatch_now_implements_and_writes(tmp_path, capsys):
    # emit is now implemented: it writes the five
    # artifacts under --out and returns EXIT_OK (not EXIT_NOT_IMPLEMENTED).
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out)])
    capsys.readouterr()
    assert rc == cli.EXIT_OK
    for name in (
        "greetd.toml",
        "cosmic.desktop",
        "cosmic-session@.service",
        "cosmic-session-launch.sh",
        "cosmic-session.env",
    ):
        assert (out / name).is_file(), f"{name} not written"


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser is checked before subcommand
    # dispatch. A valid subcommand invocation is passed so argparse does not
    # reject the subcommand's own required flags before ``main`` returns.
    rc = cli.main(["--version", "emit", "--out", "o"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def test_emit_parser_full():
    args = cli.build_parser().parse_args(["emit", "--out", "/tmp/out"])
    assert args.command == "emit"
    assert args.out == "/tmp/out"
    assert args.json is False


def test_emit_out_required():
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["emit"])
    assert exc.value.code == 2


def test_emit_json():
    args = cli.build_parser().parse_args(["emit", "--out", "o", "--json"])
    assert args.command == "emit"
    assert args.json is True


def test_check_log_parser():
    args = cli.build_parser().parse_args(["check", "--log", "FILE"])
    assert args.command == "check"
    assert args.log == "FILE"
    assert args.ssh is None
    assert args.json is False


def test_check_ssh_parser():
    args = cli.build_parser().parse_args(["check", "--ssh", "host"])
    assert args.command == "check"
    assert args.ssh == "host"
    assert args.log is None


def test_check_log_required_when_no_ssh():
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["check"])
    assert exc.value.code == 2


def test_check_log_and_ssh_mutually_exclusive():
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["check", "--log", "L", "--ssh", "h"])
    assert exc.value.code == 2


def test_check_json():
    args = cli.build_parser().parse_args(["check", "--log", "L", "--json"])
    assert args.command == "check"
    assert args.json is True
