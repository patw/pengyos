"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, not-yet-implemented
  subcommands, bad usage),
* the CLI argument surface (``run`` / ``explain``) is registered with the
  documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
from pathlib import Path

import pytest

import pengyos_update_triage as pkg
from pengyos_update_triage import __version__
from pengyos_update_triage import cli
from pengyos_update_triage.cli import (
    DEFAULT_MIN_SEVERITY,
    EXIT_FAIL,
    EXIT_OK,
    SEVERITY_LEVELS,
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
    assert 'name = "pengyos-update-triage"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does `pip install -e .`), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-update-triage")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-update-triage is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-update-triage = "pengyos_update_triage.cli:main"' in text


def test_python_module_entry_point_exists():
    """``python -m pengyos_update_triage`` is wired to the same entry point."""
    text = (REPO_ROOT / "pengyos_update_triage" / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from .cli import main" in text
    assert "sys.exit(main())" in text


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out
    assert "pengyos-update-triage" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "pengyos-update-triage" in out
    # Both documented subcommands are advertised.
    assert "run" in out
    assert "explain" in out


def test_main_run_missing_input_is_ran_but_failed(capsys, tmp_path):
    # ``run`` is now real (item 7). Pointing it at inputs that do not exist is
    # a *ran-but-failed* outcome (exit 1), not a usage error and not the
    # scaffold's not-implemented code (3).
    rc = main(
        [
            "run",
            "--pins",
            str(tmp_path / "pins.toml"),  # does not exist
            "--advisories",
            str(tmp_path / "adv"),
            "--changelogs",
            str(tmp_path / "chg"),
            "--out",
            str(tmp_path / "proposals.json"),
        ]
    )
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "not implemented" not in err
    assert "pengyos-update-triage" in err


def test_main_explain_missing_file_is_ran_but_failed(capsys):
    # ``explain`` is now real (item 7). With no proposals.json at the default
    # location it is a clean ran-but-failed (exit 1), not a crash.
    rc = main(["explain", "ADV-0001", "--proposals", "does-not-exist.json"])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "not implemented" not in err
    assert "no proposal" not in err  # the file itself is missing, not the id


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch.
    rc = main(["--version", "run", "--pins", "p", "--advisories", "a",
               "--changelogs", "c", "--out", "o"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def test_run_parser_required_flags_and_defaults(tmp_path):
    args = build_parser().parse_args(
        [
            "run",
            "--pins",
            str(tmp_path / "pins.toml"),
            "--advisories",
            str(tmp_path / "adv"),
            "--changelogs",
            str(tmp_path / "chg"),
            "--out",
            str(tmp_path / "proposals.json"),
        ]
    )
    assert args.command == "run"
    assert args.pins == str(tmp_path / "pins.toml")
    assert args.advisories == str(tmp_path / "adv")
    assert args.changelogs == str(tmp_path / "chg")
    assert args.out == str(tmp_path / "proposals.json")
    assert args.min_severity == DEFAULT_MIN_SEVERITY


def test_run_parser_min_severity_choice(tmp_path):
    args = build_parser().parse_args(
        [
            "run",
            "--pins",
            "p",
            "--advisories",
            "a",
            "--changelogs",
            "c",
            "--out",
            "o",
            "--min-severity",
            "high",
        ]
    )
    assert args.min_severity == "high"


def test_run_parser_min_severity_rejects_unknown(tmp_path):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(
            [
                "run",
                "--pins",
                "p",
                "--advisories",
                "a",
                "--changelogs",
                "c",
                "--out",
                "o",
                "--min-severity",
                "apocalyptic",
            ]
        )
    assert exc.value.code == 2


def test_run_parser_requires_out(tmp_path):
    # ``--out`` is required per the documented interface.
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(
            [
                "run",
                "--pins",
                "p",
                "--advisories",
                "a",
                "--changelogs",
                "c",
            ]
        )
    assert exc.value.code == 2


def test_explain_parser_takes_positional_id():
    args = build_parser().parse_args(["explain", "ADV-0001"])
    assert args.command == "explain"
    assert args.proposal_id == "ADV-0001"


def test_explain_parser_requires_id():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["explain"])
    assert exc.value.code == 2


def test_severity_levels_are_ordered_and_disjoint():
    # The scaffold vocabulary is a fixed, ordered, non-empty list.
    assert len(SEVERITY_LEVELS) >= 2
    assert len(set(SEVERITY_LEVELS)) == len(SEVERITY_LEVELS)
    # The documented default must be one of the accepted levels.
    assert DEFAULT_MIN_SEVERITY in SEVERITY_LEVELS
