"""Tests for the injectable command runner.

Covers :class:`RunResult` (the shape of a step's outcome), :func:`default_runner`,
:func:`wrap_runner` (the ``--runner CMD`` prefix), and :class:`RecordingRunner`
(the side-effect-free stand-in the test suite injects so no real tool ever runs).

Everything here is hermetic: the only runner that touches a process is
:func:`default_runner`, and it is exercised against a no-op echo-style argv that
needs no special tool.
"""

from pengyos_media.runner import (
    RecordingRunner,
    RunResult,
    default_runner,
    wrap_runner,
)


# --------------------------------------------------------------------------- #
# RunResult
# --------------------------------------------------------------------------- #
def test_run_result_defaults_are_clean_exit():
    r = RunResult(argv=["mksquashfs", "a"])
    assert r.argv == ["mksquashfs", "a"]
    assert r.returncode == 0
    assert r.stdout == ""
    assert r.stderr == ""
    assert r.ok is True


def test_run_result_ok_reflects_returncode():
    assert RunResult(argv=["x"], returncode=0).ok is True
    assert RunResult(argv=["x"], returncode=1).ok is False
    assert RunResult(argv=["x"], returncode=130).ok is False


# --------------------------------------------------------------------------- #
# default_runner
# --------------------------------------------------------------------------- #
def test_default_runner_runs_and_captures(tmp_path):
    # A no-op command that exists everywhere: echo. Captured stdout is returned.
    result = default_runner(["echo", "hello-world"], cwd=str(tmp_path))
    assert result.argv == ["echo", "hello-world"]
    assert result.returncode == 0
    assert result.stdout.strip() == "hello-world"


def test_default_runner_returns_returncode_on_failure(tmp_path):
    # `false` exits non-zero; default_runner does NOT raise — it reports the code.
    result = default_runner(["false"], cwd=str(tmp_path))
    assert result.returncode != 0
    assert result.ok is False


def test_default_runner_passes_cwd(tmp_path):
    # cwd is honoured: a relative path resolves inside tmp_path.
    result = default_runner(["pwd"], cwd=str(tmp_path))
    assert result.returncode == 0
    assert result.stdout.strip().rstrip("/") == str(tmp_path).rstrip("/")


def test_default_runner_is_subprocess_like():
    # It is documented as a subprocess.run-like callable: the signature accepts
    # the kwargs build hands it (cwd). A plain callable without **kwargs would
    # raise TypeError here.
    result = default_runner(["true"], cwd=None)
    assert result.returncode == 0


# --------------------------------------------------------------------------- #
# wrap_runner  (the --runner CMD prefix)
# --------------------------------------------------------------------------- #
def test_wrap_runner_prefixes_command():
    rec = RecordingRunner()
    wrapped = wrap_runner("sudo", runner=rec)
    wrapped(["mksquashfs", "a", "b"])
    # The step argv is prefixed with the wrapped command.
    assert rec.calls == [["sudo", "mksquashfs", "a", "b"]]


def test_wrap_runner_forwards_cwd():
    seen = {}

    def spy(argv, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return RunResult(argv=list(argv))

    wrapped = wrap_runner("tracer", runner=spy)
    wrapped(["xorriso", "x"], cwd="/build")
    assert seen["cwd"] == "/build"


def test_wrap_runner_returns_full_argv_result():
    captured: dict = {}

    def spy(argv, **kwargs):
        captured["argv"] = list(argv)
        return RunResult(argv=list(argv))

    wrapped = wrap_runner("sudo", runner=spy)
    result = wrapped(["xorriso", "x"])
    assert result.argv == ["sudo", "xorriso", "x"]
    assert captured["argv"] == ["sudo", "xorriso", "x"]


# --------------------------------------------------------------------------- #
# RecordingRunner
# --------------------------------------------------------------------------- #
def test_recording_runner_records_every_call():
    rec = RecordingRunner()
    rec(["a"])
    rec(["b", "c"])
    assert rec.calls == [["a"], ["b", "c"]]


def test_recording_runner_returns_default_clean_result():
    rec = RecordingRunner()
    result = rec(["x"])
    assert result.ok is True
    assert result.returncode == 0


def test_recording_runner_result_for_overrides_all_subsequent():
    rec = RecordingRunner()
    rec.result_for(returncode=2, stderr="boom")
    first = rec(["x"])
    second = rec(["y"])
    assert first.returncode == 2 and second.returncode == 2
    assert "boom" in second.stderr


def test_recording_runner_ignores_extra_kwargs():
    # build hands the runner cwd=…; RecordingRunner must accept it.
    rec = RecordingRunner()
    result = rec(["x"], cwd="/build")
    assert result.ok is True
    assert rec.calls == [["x"]]
