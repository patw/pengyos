"""CLI-level tests for ``build``.

Exercises the ``build`` subcommand end-to-end through :func:`main`: the
side-effect-free default dry run, the ``--plan`` error paths, and a real
``--commit`` driven by a fake recorder command on ``PATH`` (so the whole commit
path runs without any of the real ``xorriso``/``mksquashfs``/``install`` tools).
"""

import contextlib
import io
import os
import stat
from pathlib import Path

from pengyos_media.cli import ExitCode, main

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _write_plan(tmp_path: Path) -> Path:
    """Emit a golden plan file and return its path."""
    out = tmp_path / "media.json"
    code = main(
        ["plan", "--edition", "desktop",
         "--release", str(FIXTURES / "release-desktop.json"),
         "--out", str(out)]
    )
    assert code == ExitCode.OK
    return out


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:  # argparse failures
            code = e.code or ExitCode.USAGE
    return code, out.getvalue(), err.getvalue()


def test_build_default_is_side_effect_free_dry_run(tmp_path):
    plan = _write_plan(tmp_path)
    code, out, err = _run("build", "--plan", str(plan))
    assert code == ExitCode.OK
    # A dry run: renders the argv, runs nothing.
    assert "DRY-RUN" in out
    assert "xorriso" in out
    assert "desktop-install.iso" in out
    # Nothing was written to the plan's directory.
    assert list(tmp_path.iterdir()) == [plan]


def test_build_explicit_dry_run_flag(tmp_path):
    plan = _write_plan(tmp_path)
    code, out, _ = _run("build", "--plan", str(plan), "--dry-run")
    assert code == ExitCode.OK
    assert "DRY-RUN" in out


def test_build_missing_plan_is_error():
    code, _, err = _run("build", "--plan", "/no/such/media.json")
    assert code == ExitCode.ERROR
    assert "cannot read plan" in err


def test_build_bad_plan_json_is_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _, err = _run("build", "--plan", str(bad))
    assert code == ExitCode.ERROR
    assert "cannot read plan" in err


def test_build_commit_with_fake_recorder(tmp_path, monkeypatch):
    # A fake recorder that logs the argv it is handed and exits 0, placed on
    # PATH so --runner can find it. --runner CMD wraps each step as
    # ``CMD <step argv>``, so the recorder sees the step argv (not its own path).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = bin_dir / "recorder.sh"
    log = tmp_path / "recorder.log"
    recorder.write_text(
        "#!/bin/sh\necho \"$@\" >> " + str(log) + "\nexit 0\n", encoding="utf-8"
    )
    recorder.chmod(recorder.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    plan = _write_plan(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    code, out, err = _run(
        "build", "--plan", str(plan), "--commit", "--runner", str(recorder)
    )
    assert code == ExitCode.OK, err
    # The commit rendered the committed summary.
    assert "committed" in out
    # The recorder saw each planned step's argv (the real tools were never run).
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "mksquashfs" in text
    assert "live/rootfs" in text
    assert "xorriso" in text
    assert "desktop-install.iso" in text


def test_build_commit_reports_failure(tmp_path, monkeypatch):
    # A recorder that fails on the last step -> build exits ERROR.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = bin_dir / "failer.sh"
    recorder.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    recorder.chmod(recorder.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    plan = _write_plan(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    code, _, err = _run(
        "build", "--plan", str(plan), "--commit", "--runner", str(recorder)
    )
    assert code == ExitCode.ERROR
    assert "exited 3" in err


def test_build_commit_missing_tool_is_error(tmp_path, monkeypatch):
    # --runner points at a command that does not exist -> BuildError (exit 1).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    plan = _write_plan(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    code, _, err = _run(
        "build", "--plan", str(plan), "--commit",
        "--runner", str(bin_dir / "no-such-tool-xyz"),
    )
    assert code == ExitCode.ERROR
    assert "not on PATH" in err


def test_build_commit_dry_run_ignores_runner(tmp_path, monkeypatch):
    # In a dry run the runner is never invoked, so a recorder that would write
    # nothing proves the runner was not called.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = bin_dir / "recorder.sh"
    log = tmp_path / "should-not-exist.log"
    recorder.write_text(
        "#!/bin/sh\necho \"$@\" >> " + str(log) + "\nexit 0\n", encoding="utf-8"
    )
    recorder.chmod(recorder.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    plan = _write_plan(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    code, out, _ = _run(
        "build", "--plan", str(plan), "--dry-run", "--runner", str(recorder)
    )
    assert code == ExitCode.OK
    assert "DRY-RUN" in out
    # The recorder never ran in dry-run mode.
    assert not log.exists()
