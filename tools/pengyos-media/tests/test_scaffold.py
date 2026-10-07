"""Smoke tests for the pengyos-media scaffold.

The scaffold provides the CLI surface that ties the three subcommands together:
package imports, the `plan` / `build` / `lint` subcommands with the spec option
surface, the exit-code convention, and the refusal behaviour (unknown / missing
options). All three subcommand bodies are now implemented (see
test_plan.py / test_build.py / test_build_cli.py / test_lint.py); this module smoke-tests that the surface still holds.

The `ExitCode.NOT_IMPLEMENTED` (3) convention and `NotYetImplementedError`
remain part of the documented exit-code contract for any not-yet-implemented
subcommand.
"""

import contextlib
import importlib
import io
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pengyos_media import __version__
from pengyos_media.cli import (
    EDITIONS,
    ExitCode,
    NotYetImplementedError,
    build_parser,
    main,
)

ROOT = Path(__file__).resolve().parent.parent


def _run(*argv: str) -> tuple[int | str, str, str]:
    """Run the CLI in-process, capturing stdout/stderr and the exit code.

    argparse failures raise SystemExit; normal returns come from main().
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def test_package_imports():
    assert isinstance(__version__, str)
    assert __version__
    # __main__ must import cleanly (python -m entry path).
    importlib.import_module("pengyos_media.__main__")


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
    mod_name, fn_name = meta["project"]["scripts"]["pengyos-media"].split(":")
    mod = importlib.import_module(mod_name)
    assert callable(getattr(mod, fn_name))


def test_exit_code_convention():
    assert ExitCode.OK == 0
    assert ExitCode.ERROR == 1
    assert ExitCode.USAGE == 2  # argparse's own exit code — must not collide
    assert ExitCode.NOT_IMPLEMENTED == 3


def test_editions_constant():
    assert EDITIONS == ("desktop", "core", "server")


def test_help_lists_subcommands():
    code, out, err = _run("--help")
    assert code == ExitCode.OK
    assert "pengyos-media" in out
    for cmd in ("plan", "build", "lint"):
        assert cmd in out


def test_help_shows_subcommand_options():
    plan, plan_out, _ = _run("plan", "--help")
    assert plan == ExitCode.OK
    for opt in ("--edition", "--release", "--out", "--json"):
        assert opt in plan_out, f"plan --help missing {opt}"

    build, build_out, _ = _run("build", "--help")
    assert build == ExitCode.OK
    for opt in ("--plan", "--dry-run", "--commit", "--runner"):
        assert opt in build_out, f"build --help missing {opt}"

    lint, lint_out, _ = _run("lint", "--help")
    assert lint == ExitCode.OK
    for opt in ("--plan", "--json"):
        assert opt in lint_out, f"lint --help missing {opt}"


def test_plan_requires_edition():
    code, _, err = _run("plan", "--release", "rel.json", "--out", "media.json")
    assert code == ExitCode.USAGE
    assert "edition" in err


def test_plan_requires_release():
    code, _, err = _run("plan", "--edition", "desktop", "--out", "media.json")
    assert code == ExitCode.USAGE
    assert "release" in err


def test_build_requires_plan():
    code, _, err = _run("build")
    assert code == ExitCode.USAGE
    assert "plan" in err


def test_unknown_option_is_usage_error():
    code, _, err = _run("plan", "--edition", "desktop", "--release", "rel.json",
                        "--out", "media.json", "--nope")
    assert code == ExitCode.USAGE
    assert "unrecognized" in err


def test_invalid_choice_is_usage_error():
    code, _, err = _run("plan", "--edition", "tablet", "--release", "rel.json",
                        "--out", "media.json")
    assert code == ExitCode.USAGE


def test_no_subcommand_is_usage_error():
    code, _, err = _run("--edition", "desktop")
    assert code == ExitCode.USAGE


def test_lint_is_implemented_not_skeleton(tmp_path):
    # `lint` now validates a plan: with a missing plan it is a hard error
    # (exit 1), not a NOT_IMPLEMENTED skeleton (exit 3). A valid plan passing is
    # covered in test_lint.py.
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = main(["lint", "--plan", "media.json"])
    assert code == ExitCode.ERROR
    assert "not implemented yet" not in err.getvalue()


def test_build_is_implemented_not_skeleton(tmp_path):
    # `build` now drives the plan through the runner: with a missing plan it is
    # a hard error (exit 1), not a NOT_IMPLEMENTED skeleton (exit 3).
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = main(["build", "--plan", "media.json"])
    assert code == ExitCode.ERROR
    assert "not implemented yet" not in err.getvalue()


def test_not_yet_implemented_error_shape():
    exc = NotYetImplementedError("plan", "the task")
    assert "plan" in str(exc) and "the task" in str(exc)


def test_version_flag():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        with pytest.raises(SystemExit) as e:
            main(["--version"])
    assert e.value.code == ExitCode.OK
    assert f"pengyos-media {__version__}" in out.getvalue()


def test_python_dash_m_entrypoint():
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_media", "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert "pengyos-media" in proc.stdout
    assert "plan" in proc.stdout and "build" in proc.stdout and "lint" in proc.stdout


def test_build_parser_exposes_subcommands():
    # Sanity that the parser wires the handlers the CLI dispatches to.
    parser = build_parser()
    plan_ns = parser.parse_args(
        ["plan", "--edition", "desktop", "--release", "rel.json", "--out", "m.json"]
    )
    assert plan_ns.command == "plan"
    assert plan_ns.edition == "desktop"
    assert plan_ns.release == "rel.json"
    assert plan_ns.out == "m.json"
    assert plan_ns.json is False

    build_ns = parser.parse_args(["build", "--plan", "m.json", "--dry-run"])
    assert build_ns.command == "build"
    assert build_ns.plan == "m.json"
    assert build_ns.dry_run is True
    assert build_ns.commit is False

    lint_ns = parser.parse_args(["lint", "--plan", "m.json", "--json"])
    assert lint_ns.command == "lint"
    assert lint_ns.plan == "m.json"
    assert lint_ns.json is True
