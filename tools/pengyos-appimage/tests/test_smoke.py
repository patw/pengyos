"""Smoke tests: the package, the CLI wiring, and the no-execution invariant.

The invariant: the tool never runs or unpacks an AppImage. It has no reason to
start a process at all, so the strongest test is to make every way of starting
one raise, run all three commands against the sample AppImage (whose AppRun
would write a poison file), and require that they succeed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_appimage import __version__, cli

POISON_PATH = Path("/tmp/PENGYOS_APPIMAGE_POISON")


def test_package_has_version():
    assert __version__.count(".") == 2


def test_parser_registers_exactly_three_subcommands():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert set(sub.choices) == {"inspect", "integrate", "check"}
    assert set(cli._HANDLERS) == {"inspect", "integrate", "check"}


def test_no_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == cli.EXIT_USAGE


def test_cli_version_flag_via_module():
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_appimage", "--version"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert proc.returncode == 0
    assert __version__ in proc.stdout


@pytest.fixture
def no_processes(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"tried to start a process: {args!r}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    for name in ("system", "execv", "execve", "execvp", "execvpe", "posix_spawn",
                 "posix_spawnp", "fork"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, refuse)


def test_no_command_starts_a_process(sample_appimage, tmp_path, no_processes, capsys):
    POISON_PATH.unlink(missing_ok=True)
    assert cli.main(["inspect", str(sample_appimage)]) == cli.EXIT_OK
    assert cli.main(["integrate", str(sample_appimage), "--dest", str(tmp_path)]) == cli.EXIT_OK
    assert cli.main(["integrate", "--remove", str(sample_appimage), "--dest", str(tmp_path)]) == cli.EXIT_OK
    assert cli.main(["check", str(sample_appimage), "--libdirs", str(tmp_path)]) == cli.EXIT_MISSING
    assert not POISON_PATH.exists()


def test_nothing_is_unpacked(sample_appimage, tmp_path, monkeypatch):
    # inspect and check write nothing anywhere; integrate writes only its
    # entry and icon under --dest.
    monkeypatch.chdir(tmp_path)
    cli.main(["inspect", str(sample_appimage)])
    cli.main(["check", str(sample_appimage), "--libdirs", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []
    dest = tmp_path / "share"
    cli.main(["integrate", str(sample_appimage), "--dest", str(dest)])
    written = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert written == [
        "applications/appimage-sample.desktop",
        "icons/hicolor/16x16/apps/appimage-sample.png",
    ]
