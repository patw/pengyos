"""Tests for the pengyos-measured-boot CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pengyos_measured_boot import __version__
from pengyos_measured_boot.cli import _parse_expect, main
from pengyos_measured_boot.errors import (
    CommandNotReady,
    PengyOSMeasuredBootError,
    UsageError,
)

POLICY_TOML = "policy.toml"
PCRS_JSON = "pcrs.json"
PCRS_TAMPERED_JSON = "pcrs-tampered.json"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_no_command_is_usage_error() -> None:
    # A subcommand is required.
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_run_without_image_is_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert exc.value.code == 2


def test_run_without_expect_is_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", "--image", "img"])
    assert exc.value.code == 2


def _fake_subprocess(stdout: str = "{}", returncode: int = 0):
    """Return a ``subprocess.run`` stand-in that records argvs and returns a fixed result.

    Distinguishes the boot step (``swtpm``) from the read step
    (``tpm2_getpcrs``) by argv[0], so the read step can supply the PCR JSON.
    """

    import subprocess

    calls: list[list[str]] = []

    def fake(argv, *args, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), returncode, stdout, "")

    return fake, calls


def test_run_success_end_to_end(
    capsys: pytest.CaptureFixture[str], monkeypatch: "pytest.MonkeyPatch"
) -> None:
    # The full run backend: boot, read, parse, and verify against --expect.
    pcrs = json.dumps({"pcr-7": {"sha256": format(0xDEADBEEF, "064x")}})
    fake, calls = _fake_subprocess(stdout=pcrs)
    monkeypatch.setattr("subprocess.run", fake)

    rc = main(["run", "--image", "img.raw", "--expect", "pcr7=0xdeadbeef"])
    assert rc == 0

    # Two invocations: boot, then read.
    assert [c[0] for c in calls] == ["swtpm", "tpm2_getpcrs"]

    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["image"] == "img.raw"
    assert result["required"] == [7]


def test_run_mismatch_fails_end_to_end(
    capsys: pytest.CaptureFixture[str], monkeypatch: "pytest.MonkeyPatch"
) -> None:
    # A boot that does not match --expect fails, naming the PCR (exit 2).
    pcrs = json.dumps({"pcr-7": {"sha256": format(0xCAFEBABE, "064x")}})
    fake, _ = _fake_subprocess(stdout=pcrs)
    monkeypatch.setattr("subprocess.run", fake)

    rc = main(["run", "--image", "img.raw", "--expect", "pcr7=0xdeadbeef"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "PCR 7" in err
    assert "deadbeef" in err
    assert "cafebabe" in err


def test_run_missing_pcr_fails_end_to_end(
    capsys: pytest.CaptureFixture[str], monkeypatch: "pytest.MonkeyPatch"
) -> None:
    # A required PCR that the boot did not measure fails (exit 2).
    fake, _ = _fake_subprocess(stdout=json.dumps({"pcr0": {"sha256": format(0, "064x")}}))
    monkeypatch.setattr("subprocess.run", fake)

    rc = main(["run", "--image", "img.raw", "--expect", "pcr7=0x1"])
    assert rc == 2
    assert "PCR 7" in capsys.readouterr().err


def test_run_boot_failure_fails_end_to_end(
    capsys: pytest.CaptureFixture[str], monkeypatch: "pytest.MonkeyPatch"
) -> None:
    # A non-zero boot step fails with a clean domain error (exit 2).
    def fake(argv, *args, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(list(argv), 1, "", "swtpm: boom")

    monkeypatch.setattr("subprocess.run", fake)
    rc = main(["run", "--image", "img.raw", "--expect", "pcr7=0x1"])
    assert rc == 2
    assert "boot failed" in capsys.readouterr().err


def test_run_json_out_writes_file(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    pcrs = json.dumps({"pcr-7": {"sha256": format(0xDEADBEEF, "064x")}})
    fake, _ = _fake_subprocess(stdout=pcrs)
    monkeypatch.setattr("subprocess.run", fake)

    out = tmp_path / "result.json"
    rc = main(
        ["run", "--image", "img.raw", "--expect", "pcr7=0xdeadbeef", "--json", str(out)]
    )
    assert rc == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is True


def test_verify_ok_on_good_pcrs(repo_root: Path, capsys) -> None:
    # The committed good PCRs match the committed policy -> exit 0, JSON out.
    rc = main(
        [
            "verify",
            "--pcrs",
            str(repo_root / PCRS_JSON),
            "--policy",
            str(repo_root / POLICY_TOML),
        ]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["required"] == [0, 7]


def test_verify_tampered_pcr_fails(repo_root: Path, capsys) -> None:
    # A tampered PCR fails verification, naming the PCR (exit code 2).
    rc = main(
        [
            "verify",
            "--pcrs",
            str(repo_root / PCRS_TAMPERED_JSON),
            "--policy",
            str(repo_root / POLICY_TOML),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "PCR 7" in err
    assert "deadbeef" in err
    assert "cafebabe" in err


def test_verify_missing_required_pcr_fails(
    repo_root: Path, tmp_path: Path, capsys
) -> None:
    # The policy requires pcr7, but the committed PCRs omit it -> fail.
    partial = tmp_path / "partial.json"
    partial.write_text('{"pcr0": "0000000000000000000000000000000000000000000000000000000000000000"}', encoding="utf-8")
    rc = main(
        [
            "verify",
            "--pcrs",
            str(partial),
            "--policy",
            str(repo_root / POLICY_TOML),
        ]
    )
    assert rc == 2
    assert "PCR 7" in capsys.readouterr().err


def test_verify_json_out_writes_file(
    repo_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "result.json"
    rc = main(
        [
            "verify",
            "--pcrs",
            str(repo_root / PCRS_JSON),
            "--policy",
            str(repo_root / POLICY_TOML),
            "--json",
            str(out),
        ]
    )
    assert rc == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is True


def test_verify_missing_policy_file_is_clean_error(capsys) -> None:
    # A missing policy file is a clean domain error (exit 2), not a traceback.
    rc = main(["verify", "--pcrs", "nope.json", "--policy", "nope.toml"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "not found" in err


def test_parse_expect_hex_and_decimal() -> None:
    parsed = _parse_expect(["pcr7=0xdeadbeef", "pcr0=10"])
    assert parsed == {7: 0xDEADBEEF, 0: 10}


def test_parse_expect_negative_hex_is_decimal_not_flag() -> None:
    # "0x" forces base-16 even though the value looks like a negative decimal.
    parsed = _parse_expect(["pcr4=-5"])
    assert parsed == {4: -5}


def test_parse_expect_malformed_missing_equals() -> None:
    with pytest.raises(UsageError):
        _parse_expect(["pcr7"])


def test_parse_expect_bad_index() -> None:
    with pytest.raises(UsageError):
        _parse_expect(["pcrX=0x1"])


def test_parse_expect_bad_value() -> None:
    with pytest.raises(UsageError):
        _parse_expect(["pcr7=notanumber"])


def test_parse_expect_unknown_key() -> None:
    with pytest.raises(UsageError):
        _parse_expect(["reg7=0x1"])


def test_command_not_ready_is_domain_error() -> None:
    assert issubclass(CommandNotReady, PengyOSMeasuredBootError)
