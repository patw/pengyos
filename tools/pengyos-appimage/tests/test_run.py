"""Tests for :mod:`pengyos_appimage.run` and the ``run`` CLI.

The central invariant under test: running an extracted AppImage NEVER executes
the embedded ``AppRun`` hook. Instead of launching the untrusted wrapper, the
real binary named by the payload's ``.desktop`` ``Exec`` line is launched —
inside a bubblewrap sandbox.

We prove the invariant two ways:

* Structurally — the extracted prefix carries a *poison* ``AppRun``; we spy on
  ``subprocess.run`` so any attempt to execute it (or the AppImage) raises. The
  resolved binary is the *real* payload binary, never ``AppRun``.
* End-to-end (integration, opt-in) — the committed fixture's real poison
  ``AppRun`` sits in the extracted prefix; the poison marker is never created.

The ``bwrap`` execution seam (:class:`BwrapTool`) is replaced with a fake that
records the argv and never shells out, so no real ``bwrap``, root, network, or
display is required.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pengyos_appimage import cli
from pengyos_appimage import desktop as desktop_mod
from pengyos_appimage import run as runner

# The poison marker the embedded AppRun would create if ever executed.
POISON_PATH = "/tmp/PENGYOS_APPIMAGE_POISON"

DESKTOP_CONTENT = (
    "[Desktop Entry]\n"
    "Version=1.0\n"
    "Type=Application\n"
    "Name=Sample App\n"
    "GenericName=Sample\n"
    "Exec=sample %F\n"
    "Icon=sample\n"
    "Terminal=false\n"
    "Categories=Utility;\n"
)

# A desktop whose Exec points at an absolute path inside the payload.
DESKTOP_ABS_EXEC = (
    "[Desktop Entry]\n"
    "Version=1.0\n"
    "Type=Application\n"
    "Name=Abs App\n"
    "Exec=/usr/bin/absapp --flag\n"
    "Icon=absapp\n"
)

# A desktop with no Exec key -> run must refuse (never falls back to AppRun).
DESKTOP_NO_EXEC = (
    "[Desktop Entry]\n"
    "Version=1.0\n"
    "Type=Application\n"
    "Name=No Exec App\n"
)

POISON_APPRUN = (
    "#!/bin/sh\n"
    f'echo "poisoned" > {repr(POISON_PATH)}\n'
    "exec ./usr/bin/sample \"$@\"\n"
)


class FakeBwrapTool(runner.BwrapTool):
    """A fake ``bwrap`` that records argv and never shells out."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def available(self) -> bool:
        return True

    def run(self, argv, capture=True):  # type: ignore[override]
        self.calls.append(list(argv))
        return SimpleNamespace(
            returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def _triple_in(argv: list[str], triple: tuple[str, str, str]) -> bool:
    """True when *triple* appears as three consecutive elements of *argv*."""
    for i in range(len(argv) - 2):
        if (argv[i], argv[i + 1], argv[i + 2]) == triple:
            return True
    return False


def _write_appdir(root: Path, desktop_content: str = DESKTOP_CONTENT) -> Path:
    """Materialise a canonical extracted AppImage prefix under *root*.

    The prefix carries a poison ``AppRun`` (the untrusted hook) and the real
    ``usr/bin/sample`` binary the ``.desktop`` ``Exec`` line points at.
    """
    (root / "usr" / "bin").mkdir(parents=True)

    poison = root / "AppRun"
    poison.write_text(POISON_APPRUN, encoding="utf-8")
    poison.chmod(0o755)

    (root / "usr" / "bin" / "sample").write_text("#!/bin/sh\necho sample\n")
    (root / "usr" / "bin" / "sample").chmod(0o755)
    (root / "sample.desktop").write_text(desktop_content, encoding="utf-8")
    return root


@pytest.fixture
def appdir(tmp_path) -> Path:
    return _write_appdir(tmp_path / "app")


def _tool(returncode: int = 0, stdout: str = "sample ran\n") -> FakeBwrapTool:
    return FakeBwrapTool(returncode=returncode, stdout=stdout)


def _run(appdir, tool=None, extra_args=None, sandbox=True):
    return runner.run_app(
        appdir,
        extra_args or [],
        sandbox=sandbox,
        tool=tool or _tool(),
    )


# --- _program_from_exec ----------------------------------------------------


def test_program_from_exec_takes_first_token():
    assert runner._program_from_exec("sample %F") == "sample"
    assert runner._program_from_exec("/usr/bin/absapp --flag") == "/usr/bin/absapp"
    assert runner._program_from_exec(None) is None
    assert runner._program_from_exec("") is None


# --- resolve_binary --------------------------------------------------------


def test_resolve_binary_bare_name(appdir):
    exec_value = desktop_mod.parse_desktop_file(appdir / "sample.desktop").main.get(
        "Exec"
    )
    resolved = runner.resolve_binary(appdir, exec_value)
    assert resolved == appdir / "usr" / "bin" / "sample"


def test_resolve_binary_absolute_path(tmp_path):
    (tmp_path / "usr" / "bin").mkdir(parents=True)
    (tmp_path / "usr" / "bin" / "absapp").write_text("#!/bin/sh\n")
    resolved = runner.resolve_binary(tmp_path, "/usr/bin/absapp")
    assert resolved == tmp_path / "usr" / "bin" / "absapp"


def test_resolve_binary_no_match(appdir):
    assert runner.resolve_binary(appdir, "does-not-exist") is None


def test_resolve_binary_no_exec(appdir):
    assert runner.resolve_binary(appdir, None) is None
    assert runner.resolve_binary(appdir, "") is None


# --- build_sandbox_argv (golden) -------------------------------------------


def test_build_sandbox_argv_golden(monkeypatch):
    # Deterministic: pretend /etc/resolv.conf is absent so the golden is stable.
    monkeypatch.setattr(runner.os.path, "exists", lambda _p: False)
    argv = runner.build_sandbox_argv(
        "/apps/sample", "/apps/sample/usr/bin/sample", ["--flag"]
    )
    assert argv == [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        "/apps/sample",
        "/apps/sample",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--dir",
        "/home",
        "--",
        "/apps/sample/usr/bin/sample",
        "--flag",
    ]


def test_build_sandbox_argv_binds_resolv_conf(monkeypatch):
    # When /etc/resolv.conf exists, it is bound read-only.
    monkeypatch.setattr(
        runner.os.path, "exists", lambda _p: _p == "/etc/resolv.conf"
    )
    argv = runner.build_sandbox_argv("/a", "/a/bin/x")
    assert _triple_in(argv, ("--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf"))


def test_build_sandbox_argv_no_resolv_when_absent(monkeypatch):
    monkeypatch.setattr(runner.os.path, "exists", lambda _p: False)
    argv = runner.build_sandbox_argv("/a", "/a/bin/x")
    assert "/etc/resolv.conf" not in argv


def test_build_sandbox_argv_extra_args_forwarded():
    argv = runner.build_sandbox_argv("/a", "/a/bin/x", ["--v", "5"])
    assert argv[-4:] == ["--", "/a/bin/x", "--v", "5"]


def test_build_sandbox_argv_extra_ro_binds(monkeypatch):
    monkeypatch.setattr(runner.os.path, "exists", lambda _p: False)
    argv = runner.build_sandbox_argv(
        "/a", "/a/bin/x", bind_roots={"/run/user/1000": "/run/user/1000"}
    )
    assert _triple_in(argv, ("--ro-bind", "/run/user/1000", "/run/user/1000"))


# --- run_app ---------------------------------------------------------------


def test_run_app_resolves_and_runs_real_binary(appdir):
    result = _run(appdir)
    assert result.program == "sample"
    assert result.binary == str(appdir / "usr" / "bin" / "sample")
    assert result.sandboxed is True
    assert result.returncode == 0
    assert result.stdout == "sample ran\n"
    # The launched argv runs the REAL binary, never the AppRun hook.
    run_cmd = result.argv[result.argv.index("--") + 1 :]
    assert run_cmd == [str(appdir / "usr" / "bin" / "sample")]


def test_run_app_forwards_extra_args(appdir):
    tool = FakeBwrapTool()
    result = _run(appdir, tool=tool, extra_args=["--flag"])
    argv = tool.calls[0]
    assert argv[-3:] == ["--", result.binary, "--flag"]


def test_run_app_absolute_exec_path(tmp_path):
    # An absolute Exec path (/usr/bin/absapp) is resolved under the payload root.
    appdir = _write_appdir(tmp_path / "app", DESKTOP_ABS_EXEC)
    (appdir / "usr" / "bin" / "absapp").write_text("#!/bin/sh\necho abs\n")
    (appdir / "usr" / "bin" / "absapp").chmod(0o755)
    tool = FakeBwrapTool()
    result = _run(appdir, tool=tool)
    assert result.program == "/usr/bin/absapp"
    assert result.binary == str(appdir / "usr" / "bin" / "absapp")
    run_cmd = result.argv[result.argv.index("--") + 1 :]
    assert run_cmd == [str(appdir / "usr" / "bin" / "absapp")]


def test_run_app_no_sandbox_execs_directly(appdir):
    tool = FakeBwrapTool()
    result = _run(appdir, tool=tool, sandbox=False)
    assert result.sandboxed is False
    # Direct exec: argv is just [binary, ...args], no bwrap prefix.
    assert result.argv[0] == str(appdir / "usr" / "bin" / "sample")
    assert "bwrap" not in result.argv
    assert any("insecure" in w for w in result.warnings)


def test_run_app_missing_exec_raises(appdir):
    (appdir / "sample.desktop").write_text(DESKTOP_NO_EXEC, encoding="utf-8")
    with pytest.raises(runner.RunError):
        _run(appdir)


def test_run_app_missing_binary_raises(appdir):
    (appdir / "sample.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=X\nExec=missing\n",
        encoding="utf-8",
    )
    with pytest.raises(runner.RunError):
        _run(appdir)


def test_run_app_not_a_directory_raises(tmp_path):
    with pytest.raises(runner.RunError):
        runner.run_app(tmp_path / "nope", tool=FakeBwrapTool())


def test_run_app_result_json_serialisable(appdir):
    result = _run(appdir)
    payload = json.dumps(result.to_dict())
    parsed = json.loads(payload)
    assert parsed["program"] == "sample"
    assert parsed["sandboxed"] is True


# --- poison invariant ------------------------------------------------------


def test_run_does_not_execute_poison_hook(appdir, monkeypatch):
    """The poison invariant: run never executes the embedded AppRun hook.

    The prefix carries a real poison ``AppRun``; ``subprocess.run`` is spied so
    any attempt to execute it (or the AppImage) raises. The launched argv must
    name the *real* payload binary, never ``AppRun``.
    """
    if Path(POISON_PATH).exists():
        Path(POISON_PATH).unlink()

    def spy_run(cmd, *args, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "AppRun" in joined or "sample-appimage" in joined:
            raise AssertionError(f"run executed the AppImage/AppRun: {cmd}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", spy_run)

    tool = FakeBwrapTool()
    try:
        result = _run(appdir, tool=tool)
    finally:
        if Path(POISON_PATH).exists():
            Path(POISON_PATH).unlink()

    assert result.sandboxed is True
    # The run command names the real binary, never AppRun.
    run_cmd = result.argv[result.argv.index("--") + 1 :]
    assert run_cmd == [str(appdir / "usr" / "bin" / "sample")]
    assert "AppRun" not in " ".join(tool.calls[0])
    assert not Path(POISON_PATH).exists()


# --- CLI -------------------------------------------------------------------


def test_cli_run_human(appdir, monkeypatch, capsys):
    monkeypatch.setattr(runner, "default_tool", lambda: _tool())
    rc = cli.main(["run", str(appdir)])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Program:" in out
    assert "sample" in out
    assert "bubblewrap" in out


def test_cli_run_no_sandbox(appdir, monkeypatch, capsys):
    # --no-sandbox execs the binary directly via subprocess.run; stub it so no
    # real binary is executed.
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    rc = cli.main(["run", str(appdir), "--no-sandbox"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "disabled" in out
    assert "insecure" in out


def test_cli_run_json(appdir, monkeypatch, capsys):
    monkeypatch.setattr(runner, "default_tool", lambda: _tool())
    rc = cli.main(["run", str(appdir), "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["program"] == "sample"
    assert payload["sandboxed"] is True


def test_cli_run_missing_exec_errors(appdir, monkeypatch, capsys):
    monkeypatch.setattr(runner, "default_tool", lambda: FakeBwrapTool())
    (appdir / "sample.desktop").write_text(DESKTOP_NO_EXEC, encoding="utf-8")
    rc = cli.main(["run", str(appdir)])
    assert rc == cli.EXIT_ERROR
    assert capsys.readouterr().err.strip()


def test_cli_run_mutually_exclusive_sandbox_flags(capsys):
    rc = cli.main(["run", "/tmp/app", "--sandbox", "--no-sandbox"])
    assert rc == cli.EXIT_USAGE
    assert capsys.readouterr().err.strip()


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT") or shutil.which("bwrap") is None,
    reason="PENGYOS_IT=1 and bwrap required for the real sandbox run",
)
def test_integration_real_bwrap_sandbox_runs_no_poison(tmp_path):
    """Real bwrap runs the app under a sandbox; the poison AppRun is never run.

    The extracted prefix carries a real poison ``AppRun``. Under the sandbox the
    real ``usr/bin/sample`` binary is launched (never ``AppRun``), so the poison
    marker is never created.
    """
    assert Path(POISON_PATH).exists() is False
    appdir = _write_appdir(tmp_path / "app")
    result = runner.run_app(appdir, sandbox=True)
    assert result.program == "sample"
    assert result.sandboxed is True
    assert result.returncode == 0
    assert not Path(POISON_PATH).exists()
