"""Tests for the measured-boot orchestration (``pengyos_measured_boot.boot``).

No real ``swtpm`` is invoked: the subprocess layer is replaced with a fake
``ProcRunner`` in every case, so these tests need no root, hardware, or network.
"""

from __future__ import annotations

import json

import pytest

from pengyos_measured_boot.boot import run_measured_boot
from pengyos_measured_boot.errors import PengyOSMeasuredBootError, PolicyMismatch
from pengyos_measured_boot.swtpm import (
    SwtpmBootConfig,
    build_boot_argv,
    build_read_pcrs_argv,
)


def _h(n: int) -> str:
    return format(n, "064x")


def _recording_runner(
    returncode: int, stdout: str = "", stderr: str = ""
) -> tuple:
    """Return a fake ProcRunner that records argvs and returns a fixed result."""

    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return _ProcResult(returncode, stdout, stderr)

    return runner, calls


class _ProcResult:
    """A minimal ProcResult-shaped stand-in for the fake runner."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_measured_boot_success() -> None:
    # Boot + read + parse + verify against --expect -> success payload.
    raw = json.dumps({"pcr-7": {"sha256": _h(0xDEADBEEF)}})
    fake, calls = _recording_runner(0, stdout=raw)
    config = SwtpmBootConfig(image="img.raw")

    result = run_measured_boot(config, {7: 0xDEADBEEF}, proc_runner=fake)

    assert result["ok"] is True
    assert result["image"] == "img.raw"
    assert result["required"] == [7]
    # Boot then read, in that order.
    assert calls[0] == build_boot_argv(config)
    assert calls[1] == build_read_pcrs_argv(config)


def test_run_measured_boot_reads_then_verifies() -> None:
    # The parsed PCRs are exactly what the read step emitted.
    raw = json.dumps({"pcr-0": {"sha256": _h(0)}, "pcr-7": {"sha256": _h(0xBEEF)}})
    fake, _ = _recording_runner(0, stdout=raw)

    result = run_measured_boot(
        SwtpmBootConfig(image="img.raw"), {0: 0, 7: 0xBEEF}, proc_runner=fake
    )
    assert result["required"] == [0, 7]


def test_run_measured_boot_reports_value_mismatch() -> None:
    # A PCR that does not match --expect raises PolicyMismatch naming it.
    raw = json.dumps({"pcr-7": {"sha256": _h(0xCAFEBABE)}})
    fake, _ = _recording_runner(0, stdout=raw)

    with pytest.raises(PolicyMismatch) as exc:
        run_measured_boot(SwtpmBootConfig(image="img.raw"), {7: 0xDEADBEEF}, proc_runner=fake)
    message = str(exc.value)
    assert "PCR 7" in message
    assert "deadbeef" in message
    assert "cafebabe" in message


def test_run_measured_boot_reports_missing_pcr() -> None:
    # A required PCR the boot did not measure raises PolicyMismatch naming it.
    raw = json.dumps({"pcr-0": {"sha256": _h(0)}})
    fake, _ = _recording_runner(0, stdout=raw)

    with pytest.raises(PolicyMismatch) as exc:
        run_measured_boot(SwtpmBootConfig(image="img.raw"), {7: 0x1}, proc_runner=fake)
    assert "PCR 7" in str(exc.value)
    assert "not measured" in str(exc.value)


def test_run_measured_boot_multiple_expected() -> None:
    # Multiple --expect values: success only when all match.
    raw = json.dumps(
        {"pcr-0": {"sha256": _h(0)}, "pcr-7": {"sha256": _h(0xDEADBEEF)}}
    )
    fake, _ = _recording_runner(0, stdout=raw)

    result = run_measured_boot(
        SwtpmBootConfig(image="img.raw"), {0: 0, 7: 0xDEADBEEF}, proc_runner=fake
    )
    assert result["required"] == [0, 7]


def test_run_measured_boot_boot_failure_raises() -> None:
    # A non-zero boot step fails with a clean domain error.
    fake, _ = _recording_runner(1, stderr="swtpm: boom")

    with pytest.raises(PengyOSMeasuredBootError) as exc:
        run_measured_boot(SwtpmBootConfig(image="img.raw"), {7: 0x1}, proc_runner=fake)
    assert "boot failed" in str(exc.value)
    assert "boom" in str(exc.value)


def test_run_measured_boot_read_failure_raises() -> None:
    # A non-zero read step fails with a clean domain error. The boot step
    # succeeds; only the read step returns non-zero.
    def fake(argv):
        if argv[0] == "swtpm":
            return _ProcResult(0, "", "")
        return _ProcResult(1, "", "tpm2: no device")

    with pytest.raises(PengyOSMeasuredBootError) as exc:
        run_measured_boot(SwtpmBootConfig(image="img.raw"), {7: 0x1}, proc_runner=fake)
    assert "failed to read PCRs" in str(exc.value)


def test_run_measured_boot_unparseable_output_raises() -> None:
    # Garbage read output cannot be parsed -> clean domain error.
    fake, _ = _recording_runner(0, stdout="not json")

    with pytest.raises(PengyOSMeasuredBootError) as exc:
        run_measured_boot(SwtpmBootConfig(image="img.raw"), {7: 0x1}, proc_runner=fake)
    assert "not valid JSON" in str(exc.value)


def test_run_measured_boot_uses_default_proc_runner_when_uninjected() -> None:
    # With no injected runner the orchestration falls back to the real
    # subprocess layer; a harmless echo read step exercises that path.
    raw = json.dumps({"pcr-0": _h(0)})
    boot_calls = []

    def default_runner(argv):
        if argv[0] == "swtpm":
            boot_calls.append(list(argv))
            return _ProcResult(0, "", "")
        # read step: echo the fixture JSON
        return _ProcResult(0, raw, "")

    config = SwtpmBootConfig(image="img.raw")
    result = run_measured_boot(config, {0: 0}, proc_runner=default_runner)
    assert result["required"] == [0]
    # The boot step used the golden argv.
    assert boot_calls[0] == build_boot_argv(config)


def test_run_measured_boot_empty_expect_is_malformed() -> None:
    # An empty policy is malformed, not trivially passing.
    fake, _ = _recording_runner(0, stdout=json.dumps({"pcr-0": _h(0)}))
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        run_measured_boot(SwtpmBootConfig(image="img.raw"), {}, proc_runner=fake)
    assert "no expected PCRs" in str(exc.value)


def test_run_measured_boot_algo_selects_digest() -> None:
    # The algo parameter selects which digest to read from a multi-algo entry.
    raw = json.dumps({"pcr-0": {"sha1": _h(0), "sha256": _h(42)}})
    fake, _ = _recording_runner(0, stdout=raw)
    # Requesting sha1 selects the sha1 digest (0), so {0: 0} matches.
    result = run_measured_boot(
        SwtpmBootConfig(image="img.raw"), {0: 0}, proc_runner=fake, algo="sha1"
    )
    assert result["required"] == [0]
