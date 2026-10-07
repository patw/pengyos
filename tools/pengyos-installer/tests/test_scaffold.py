"""Smoke tests for the pengyos-installer scaffold.

The scaffold provides the CLI skeleton: package imports, the `plan` /
`install` subcommands with the spec option surface, exit-code convention, and
refusal behaviour (unknown options / missing target). ``plan`` is
golden-tested in ``test_plan.py`` and ``install`` execution (the injectable
runner, ``--yes`` gate, teardown) is covered in ``test_install.py`` — the
scaffold test here only asserts that ``install`` *refuses without --yes*
(exit 1, before touching any disk or runner), keeping it hermetic. The
``ExitCode.NOT_IMPLEMENTED`` (3) convention and ``NotYetImplementedError``
remain part of the documented exit-code contract for any not-yet-implemented
subcommand that a later task might introduce.
"""

import contextlib
import importlib
import io
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import pengyos_installer
from pengyos_installer import __version__
from pengyos_installer.cli import (
    BOOTLOADER_ID,
    DEFAULT_ESP_SIZE,
    MIN_TARGET_SIZE_BYTES,
    ExitCode,
    NotYetImplementedError,
    build_parser,
    main,
)

ROOT = Path(__file__).resolve().parent.parent


def test_package_imports():
    assert isinstance(__version__, str)
    assert __version__
    # __main__ must import cleanly (python -m entry path).
    importlib.import_module("pengyos_installer.__main__")


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
    mod_name, fn_name = meta["project"]["scripts"]["pengyos-installer"].split(":")
    mod = importlib.import_module(mod_name)
    assert callable(getattr(mod, fn_name))


def test_exit_code_convention():
    assert ExitCode.OK == 0
    assert ExitCode.ERROR == 1
    assert ExitCode.USAGE == 2  # argparse's own exit code — must not collide
    assert ExitCode.NOT_IMPLEMENTED == 3


def test_min_constants():
    assert DEFAULT_ESP_SIZE == "512M"
    assert MIN_TARGET_SIZE_BYTES == 4 * 1024**3
    assert BOOTLOADER_ID == "PengyOS"


def test_help_lists_subcommands():
    code, out, err = _run("--help")
    assert code == ExitCode.OK
    assert "pengyos-installer" in out
    assert "plan" in out and "install" in out


def test_help_shows_shared_options():
    for cmd in ("plan", "install"):
        code, out, err = _run(cmd, "--help")
        assert code == ExitCode.OK
        for opt in ("--target", "--esp-size", "--hostname", "--rootfs-src", "--json"):
            assert opt in out, f"{cmd} --help missing {opt}"
    code, out, err = _run("install", "--help")
    assert "--yes" in out
    code, out, err = _run("plan", "--help")
    assert "--yes" not in out


def test_subcommand_defaults():
    ns = build_parser().parse_args(["plan", "--target", "/dev/sdb"])
    assert ns.command == "plan"
    assert ns.target == "/dev/sdb"
    assert ns.esp_size == DEFAULT_ESP_SIZE
    assert ns.hostname is None
    assert ns.rootfs_src is None
    assert ns.json is False
    ns = build_parser().parse_args(
        ["install", "--target", "/dev/sdb", "--esp-size", "1G",
         "--hostname", "laptop", "--rootfs-src", "/srv/rootfs.tar",
         "--json", "--yes"]
    )
    assert ns.command == "install"
    assert ns.esp_size == "1G"
    assert ns.hostname == "laptop"
    assert ns.rootfs_src == "/srv/rootfs.tar"
    assert ns.json is True
    assert ns.yes is True


def test_missing_target_is_usage_error():
    code, out, err = _run("plan")
    assert code == ExitCode.USAGE
    assert "target" in err


def test_unknown_option_is_usage_error():
    code, out, err = _run("plan", "--target", "/dev/sdb", "--nope")
    assert code == ExitCode.USAGE
    assert "unrecognized" in err


def test_no_subcommand_is_usage_error():
    code, out, err = _run("--target", "/dev/sdb")
    assert code == ExitCode.USAGE


def _run(*argv: str) -> tuple[int | str, str, str]:
    """Run the CLI in-process, capturing stdout/stderr and the exit code.
    argparse failures raise SystemExit; normal returns come from main()."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def test_install_refuses_without_yes():
    # install is implemented; without --yes it refuses (exit 1) before it
    # would build a runner, collect a probe, or touch any disk.
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = main(["install", "--target", "/dev/sdb"])
    assert code == ExitCode.ERROR
    assert "--yes" in err.getvalue()


def test_plan_is_not_the_not_implemented_placeholder():
    # ``plan`` is implemented; it must not report "not implemented". A bogus
    # target now produces a sanity refusal (exit 1), not the exit-3 placeholder.
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = main(["plan", "--target", "/dev/definitely-not-a-real-disk-xyz"])
    assert code != ExitCode.NOT_IMPLEMENTED
    assert "not implemented yet" not in err.getvalue()


def test_install_not_reported_as_not_implemented():
    # install is no longer the exit-3 placeholder: a refusal without --yes is
    # an ordinary error (exit 1), not a not-implemented pointer.
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = main(["install", "--target", "/dev/sdb"])
    assert code != ExitCode.NOT_IMPLEMENTED
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
    assert f"pengyos-installer {__version__}" in out.getvalue()


def test_python_dash_m_entrypoint():
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_installer", "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert "pengyos-installer" in proc.stdout
