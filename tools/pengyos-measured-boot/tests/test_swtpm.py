"""Tests for the swtpm runner abstraction.

No real ``swtpm`` is invoked: the subprocess layer is replaced with a fake
``ProcRunner`` in every case, so these tests need no root, hardware, or network.
"""

from __future__ import annotations

import hashlib

import pytest

from pengyos_measured_boot.errors import PengyOSMeasuredBootError
from pengyos_measured_boot.swtpm import (
    SwtpmBootConfig,
    SwtpmRunner,
    build_boot_argv,
    build_read_pcrs_argv,
    require_tool,
)


def _state_hash(image: str) -> str:
    return hashlib.sha256(image.encode("utf-8")).hexdigest()[:16]


def test_build_boot_argv_golden() -> None:
    config = SwtpmBootConfig(image="pengyos.raw")
    assert build_boot_argv(config) == [
        "swtpm",
        "boot",
        "--image",
        "pengyos.raw",
        "--tpm2-state",
        f".swtpm/swtpm-state-{_state_hash('pengyos.raw')}",
        "--pcr-algo",
        "sha256",
    ]


def test_build_boot_argv_uses_explicit_state() -> None:
    config = SwtpmBootConfig(image="img.raw", tpm2_state="/tmp/st")
    assert build_boot_argv(config) == [
        "swtpm",
        "boot",
        "--image",
        "img.raw",
        "--tpm2-state",
        "/tmp/st",
        "--pcr-algo",
        "sha256",
    ]


def test_build_boot_argv_respects_configured_tools() -> None:
    config = SwtpmBootConfig(
        image="img.raw", boot_tool="swtpm2", read_tool="tpm2_getpcrs2", pcr_algo="sha384"
    )
    assert build_boot_argv(config) == [
        "swtpm2",
        "boot",
        "--image",
        "img.raw",
        "--tpm2-state",
        f".swtpm/swtpm-state-{_state_hash('img.raw')}",
        "--pcr-algo",
        "sha384",
    ]


def test_build_read_pcrs_argv_golden() -> None:
    config = SwtpmBootConfig(image="img.raw")
    assert build_read_pcrs_argv(config) == [
        "tpm2_getpcrs",
        "--output",
        "-",
        "--format",
        "json",
        "--pcr-list",
        "0,1,2,3,4,5,6,7,8,11,12,16",
    ]


def test_boot_argv_is_deterministic() -> None:
    # Same config -> identical argv (reproducible boot).
    assert build_boot_argv(SwtpmBootConfig(image="img.raw")) == build_boot_argv(
        SwtpmBootConfig(image="img.raw")
    )


def _recording_runner(
    returncode: int, stdout: str = "", stderr: str = ""
) -> tuple:
    """Return a fake ProcRunner that records argvs and returns a fixed result."""

    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        from pengyos_measured_boot.swtpm import ProcResult

        return ProcResult(returncode, stdout, stderr)

    return runner, calls


def test_runner_uses_injected_proc_runner_no_real_swtpm() -> None:
    fake, calls = _recording_runner(0, stdout='{"pcr-7": {}}')
    runner = SwtpmRunner(SwtpmBootConfig(image="img.raw"), proc_runner=fake)

    result = runner.boot_and_read_pcrs()

    assert result.stdout == '{"pcr-7": {}}'
    # Two invocations: boot, then read.
    assert len(calls) == 2
    assert calls[0][0] == "swtpm"
    assert calls[1][0] == "tpm2_getpcrs"


def test_boot_only_runs_boot_step() -> None:
    fake, calls = _recording_runner(0)
    runner = SwtpmRunner(SwtpmBootConfig(image="img.raw"), proc_runner=fake)

    runner.boot()

    assert len(calls) == 1
    assert calls[0] == build_boot_argv(SwtpmBootConfig(image="img.raw"))


def test_read_only_runs_read_step() -> None:
    fake, calls = _recording_runner(0, stdout="{}")
    runner = SwtpmRunner(SwtpmBootConfig(image="img.raw"), proc_runner=fake)

    runner.read_pcrs()

    assert len(calls) == 1
    assert calls[0] == build_read_pcrs_argv(SwtpmBootConfig(image="img.raw"))


def test_boot_failure_raises_domain_error() -> None:
    fake, _ = _recording_runner(1, stderr="swtpm: boom")
    runner = SwtpmRunner(SwtpmBootConfig(image="img.raw"), proc_runner=fake)

    with pytest.raises(PengyOSMeasuredBootError) as exc:
        runner.boot()
    assert "boot failed" in str(exc.value)
    assert "boom" in str(exc.value)


def test_read_failure_raises_domain_error() -> None:
    fake, _ = _recording_runner(1, stderr="tpm2: no device")
    runner = SwtpmRunner(SwtpmBootConfig(image="img.raw"), proc_runner=fake)

    with pytest.raises(PengyOSMeasuredBootError) as exc:
        runner.read_pcrs()
    assert "failed to read PCRs" in str(exc.value)


def test_default_proc_runner_invokes_real_subprocess() -> None:
    # With no injected runner the SwtpmRunner falls back to the real subprocess
    # layer. ``echo`` is always available, so this exercises the default path
    # without any external tool, root, or network.
    runner = SwtpmRunner(SwtpmBootConfig(image="img.raw"))
    result = runner._proc_runner(["echo", "hello"])
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_require_tool_missing_raises() -> None:
    # A tool that does not exist on PATH must be reported as an actionable error.
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        require_tool("definitely-not-a-real-tool-xyz")
    assert "not found on PATH" in str(exc.value)


def test_require_tool_present_is_silent_on_real_binary() -> None:
    import shutil

    # pytest is virtually always installed; guard so the test is meaningful
    # without asserting on a specific tool's presence.
    tool = "pytest" if shutil.which("pytest") else "python3"
    require_tool(tool)  # must not raise
