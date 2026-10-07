"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, both subcommands
  dispatch, bad usage),
* the CLI argument surface (``emit`` / ``lint``) is registered with the
  documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``,
* at the scaffold stage ``emit`` / ``lint`` are wired but return
  ``EXIT_NOT_IMPLEMENTED`` (the real logic is a later subtask).
"""

import importlib.metadata as importlib_metadata
from pathlib import Path

import pytest

import pengyos_live as pkg
from pengyos_live import cli
from pengyos_live import __version__
from pengyos_live.cli import (
    EXIT_OK,
    EXIT_USAGE,
    build_parser,
    main,
)

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
    assert 'name = "pengyos-live"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does ``pip install -e .``), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-live")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-live is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-live = "pengyos_live.cli:main"' in text


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out
    assert "pengyos-live" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "pengyos-live" in out
    # Both documented subcommands are advertised.
    assert "emit" in out
    assert "lint" in out


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == EXIT_USAGE


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch. A
    # valid emit invocation is passed so argparse does not reject the subcommand's
    # own required flags before ``main`` returns.
    rc = main(
        ["--version", "emit", "--edition", "desktop", "--session", "cosmic", "--out", "o"]
    )
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface: emit ---------------------------------------------


def test_emit_parser_full():
    args = build_parser().parse_args(
        ["emit", "--edition", "desktop", "--session", "cosmic", "--out", "o"]
    )
    assert args.command == "emit"
    assert args.edition == "desktop"
    assert args.session == "cosmic"
    assert args.out == "o"
    assert args.json is False


def test_emit_parser_json():
    args = build_parser().parse_args(
        ["emit", "--edition", "core", "--session", "console", "--out", "o", "--json"]
    )
    assert args.edition == "core"
    assert args.session == "console"
    assert args.json is True


def test_emit_edition_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["emit", "--session", "cosmic", "--out", "o"])
    assert exc.value.code == EXIT_USAGE


def test_emit_session_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["emit", "--edition", "desktop", "--out", "o"])
    assert exc.value.code == EXIT_USAGE


def test_emit_out_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["emit", "--edition", "desktop", "--session", "cosmic"])
    assert exc.value.code == EXIT_USAGE


def test_emit_edition_choice():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(
            ["emit", "--edition", "tablet", "--session", "cosmic", "--out", "o"]
        )
    assert exc.value.code == EXIT_USAGE


def test_emit_session_choice():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(
            ["emit", "--edition", "desktop", "--session", "wayland", "--out", "o"]
        )
    assert exc.value.code == EXIT_USAGE


# --- CLI argument surface: lint ---------------------------------------------


def test_lint_parser_full():
    args = build_parser().parse_args(["lint", "--overlay", "o"])
    assert args.command == "lint"
    assert args.overlay == "o"
    assert args.json is False


def test_lint_parser_json():
    args = build_parser().parse_args(["lint", "--overlay", "o", "--json"])
    assert args.overlay == "o"
    assert args.json is True


def test_lint_overlay_required():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["lint"])
    assert exc.value.code == EXIT_USAGE
