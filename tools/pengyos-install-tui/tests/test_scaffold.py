"""Package and CLI wiring: imports, version, the console script, argparse."""

import contextlib
import importlib
import io
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pengyos_install_tui import __version__
from pengyos_install_tui.cli import ExitCode, build_parser, main

ROOT = Path(__file__).resolve().parent.parent


def test_package_imports():
    assert isinstance(__version__, str) and __version__
    importlib.import_module("pengyos_install_tui.__main__")
    for mod in ("session", "screen", "plain", "keys", "install"):
        importlib.import_module(f"pengyos_install_tui.{mod}")


def test_version_matches_pyproject():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == meta["project"]["version"]


def test_pyproject_stdlib_only():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["dependencies"] == []
    assert meta["project"]["optional-dependencies"]["test"] == ["pytest>=7"]


def test_console_script_target_exists():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mod_name, fn_name = meta["project"]["scripts"]["pengyos-install-tui"].split(":")
    assert callable(getattr(importlib.import_module(mod_name), fn_name))


def test_exit_code_convention():
    assert (ExitCode.OK, ExitCode.ERROR, ExitCode.USAGE) == (0, 1, 2)


def test_install_options():
    ns = build_parser().parse_args(["install"])
    assert (ns.command, ns.payload, ns.plain, ns.reboot) == ("install", None, False, False)
    ns = build_parser().parse_args(["install", "--payload", "/p", "--plain", "--reboot"])
    assert (ns.payload, ns.plain, ns.reboot) == ("/p", True, True)


def test_help_shows_install_options():
    code, out, _ = _run("install", "--help")
    assert code == ExitCode.OK
    for opt in ("--payload", "--plain", "--reboot"):
        assert opt in out


def test_removed_answer_set_commands_are_gone():
    for cmd in ("run", "auto"):
        code, _, err = _run(cmd)
        assert code == ExitCode.USAGE and "invalid choice" in err


def test_missing_command_is_usage_error():
    code, _, err = _run()
    assert code == ExitCode.USAGE and ("COMMAND" in err or "required" in err)


def test_plain_flag_reaches_the_installer(monkeypatch):
    seen = {}
    import pengyos_install_tui.install as inst

    monkeypatch.setattr(inst, "run_install", lambda **kw: seen.update(kw) or 7)
    assert main(["install", "--plain", "--payload", "/p"]) == 7
    assert seen == {"payload": "/p", "plain": True, "reboot": False}
    main(["install", "--reboot"])
    assert seen["plain"] is None and seen["reboot"] is True


def test_version_flag():
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == ExitCode.OK
    assert f"pengyos-install-tui {__version__}" in out.getvalue()


def test_python_dash_m_entrypoint():
    proc = subprocess.run([sys.executable, "-m", "pengyos_install_tui", "--help"],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "install" in proc.stdout


def _run(*argv: str) -> tuple[int | str, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()
