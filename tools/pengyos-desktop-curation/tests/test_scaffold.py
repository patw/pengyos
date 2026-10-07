"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, the not-yet-implemented
  subcommands, bad usage),
* the CLI argument surface (``list`` / ``coverage`` / ``emit``) is registered
  with the documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
from pathlib import Path

import pytest

import pengyos_desktop_curation as pkg
from pengyos_desktop_curation import __version__
from pengyos_desktop_curation import cli
from pengyos_desktop_curation.cli import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    SUBCOMMANDS,
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
    assert 'name = "pengyos-desktop-curation"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does `pip install -e .`), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-desktop-curation")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-desktop-curation is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-desktop-curation = "pengyos_desktop_curation.cli:main"' in text


def test_python_module_entry_point_exists():
    """``python -m pengyos_desktop_curation`` is wired to the same entry point."""
    text = (REPO_ROOT / "pengyos_desktop_curation" / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from .cli import main" in text
    assert "sys.exit(main())" in text


def test_subcommands_match_documented_surface():
    """The documented CLI surface (spec Interface line) is exactly what the
    parser advertises, in help order."""
    text = (REPO_ROOT / "spec.md").read_text(encoding="utf-8")
    for token in ("list", "coverage", "emit", "lint"):
        assert token in text
    assert SUBCOMMANDS == ("list", "coverage", "emit", "lint")


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out
    assert "pengyos-desktop-curation" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "pengyos-desktop-curation" in out
    # All documented subcommands are advertised.
    for token in SUBCOMMANDS:
        assert token in out


def test_main_subcommands_not_yet_implemented(capsys, tmp_path):
    """The not-yet-implemented body (``list``) reports a clean exit 1 with a
    stable message, not a usage error and not a crash.

    ``coverage`` (item 3) and ``emit`` (item 4) are implemented; ``list`` is
    the only remaining not-implemented body. ``emit`` still needs its ``--out``
    flag, so exercise it with ``--out`` (otherwise argparse errors, exit 2).
    """
    capsys.readouterr()
    rc = main(["list"])
    assert rc == EXIT_FAIL, "list should report not-implemented"
    err = capsys.readouterr().err
    assert "not implemented yet" in err


def test_main_coverage_is_implemented(capsys):
    """``coverage`` is implemented: the committed
    set is complete, so it exits 0 and prints a pass verdict."""
    capsys.readouterr()
    rc = main(["coverage"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "complete" in out


def test_main_emit_without_out_is_usage_error():
    """``emit`` requires ``--out``; without it, an argparse usage error (exit 2)."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["emit"])
    assert exc.value.code == EXIT_USAGE


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch.
    rc = main(["--version", "list"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def _require(exc_type, argv):
    """Assert ``parse_args(argv)`` raises ``exc_type``; return the ExceptionInfo."""
    with pytest.raises(exc_type) as exc:
        build_parser().parse_args(argv)
    return exc


def test_list_parser_takes_json_flag(tmp_path):
    args = build_parser().parse_args(["list", "--json"])
    assert args.command == "list"
    assert args.json is True


def test_list_json_defaults_false(tmp_path):
    args = build_parser().parse_args(["list"])
    assert args.command == "list"
    assert args.json is False


def test_coverage_parser_takes_json_flag(tmp_path):
    args = build_parser().parse_args(["coverage", "--json"])
    assert args.command == "coverage"
    assert args.json is True


def test_coverage_json_defaults_false(tmp_path):
    args = build_parser().parse_args(["coverage"])
    assert args.command == "coverage"
    assert args.json is False


def test_emit_parser_requires_out(tmp_path):
    exc = _require(SystemExit, ["emit"])
    assert exc.value.code == EXIT_USAGE


def test_emit_parser_takes_out(tmp_path):
    args = build_parser().parse_args(["emit", "--out", str(tmp_path)])
    assert args.command == "emit"
    assert args.out == str(tmp_path)


def test_emit_parser_takes_json_flag(tmp_path):
    args = build_parser().parse_args(["emit", "--out", str(tmp_path), "--json"])
    assert args.command == "emit"
    assert args.out == str(tmp_path)
    assert args.json is True


def test_emit_json_defaults_false(tmp_path):
    args = build_parser().parse_args(["emit", "--out", str(tmp_path)])
    assert args.json is False


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == EXIT_USAGE
