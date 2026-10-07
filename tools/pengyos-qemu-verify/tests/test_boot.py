"""Tests for the headless boot engine.

Covers, without a real QEMU, network, root or display:

* readiness detection (``detect_readiness``) against committed serial-log
  fixtures — the ``pengyos login:`` sample and a kernel-panic sample that
  never reaches login — plus multi-marker semantics and evidence capture;
* incremental serial-log streaming (``read_log_incremental``);
* the full :func:`boot_image` lifecycle driven against a tiny *fake*
  ``qemu-system-x86_64`` Python script that parses its own ``-serial file:``
  argument and appends realistic serial output: success (login prompt), the
  spec's timeout case (a fake that sleeps forever — must yield ``timeout``,
  not hang or crash), early QEMU death, missing QEMU binary, UEFI VARS
  copy-out, and swtpm start/attach/teardown (fake swtpm from item 4).
"""

import json
import os
import re
import stat
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify import cli
from pengyos_qemu_verify import assertions as assertions_mod
from pengyos_qemu_verify.boot import (
    MAX_EVIDENCE_CHARS,
    BootResult,
    ImageNotFound,
    boot_image,
    detect_readiness,
    find_qemu_bin,
    read_log_incremental,
)
from pengyos_qemu_verify.tpm import SwtpmStartError
from pengyos_qemu_verify.tpm_state import TpmStateError

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Minimal fake swtpm (same idea as tests/test_tpm.py's FAKE_READY): open the
# --server/--ctrl unix sockets it is told about, then sleep until killed.
FAKE_SWTPM_READY = r'''#!/usr/bin/env python3
import os, socket, sys, time
args = sys.argv[1:]
paths = []
i = 0
while i < len(args):
    a = args[i]
    if a in ("--server", "--ctrl") and i + 1 < len(args):
        val = args[i + 1]
        if "path=" in val:
            paths.append(val.split("path=", 1)[1].split(",", 1)[0])
        i += 2
    else:
        i += 1
socks = []
for p in paths:
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    if os.path.exists(p):
        os.remove(p)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(p)
    s.listen(1)
    socks.append(s)
try:
    while True:
        time.sleep(0.2)
except Exception:
    pass
finally:
    for s in socks:
        s.close()
'''


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fake qemu executables
# ---------------------------------------------------------------------------

# A fake qemu-system-x86_64: parses its own argv for ``-serial file:<path>``
# and streams behaviour-specific lines to that log file (small sleeps between
# lines, like a real boot). Behaviours (FAKE_QEMU_BEHAVIOR):
#   login  — boots to "pengyos login: ", then sits there (handles SIGTERM
#            with a clean exit, like a guest power-off);
#   custom — streams FAKE_QEMU_SCRIPT (lines split on \x1e), then sits;
#   panic  — prints a kernel panic, never a login prompt, then sits;
#   hang   — prints one line, then sleeps forever, unresponsive to nothing
#            (the spec's "fake qemu that sleeps forever" timeout case);
#   die    — prints one line and exits 1 immediately.
FAKE_QEMU = r'''#!/usr/bin/env python3
import os, signal, sys, time

args = sys.argv[1:]
log_path = None
i = 0
while i < len(args):
    if args[i] == "-serial" and i + 1 < len(args) and args[i+1].startswith("file:"):
        log_path = args[i + 1][len("file:"):]
        i += 2
    else:
        i += 1
assert log_path, "fake qemu: no -serial file: argument given"

behavior = os.environ.get("FAKE_QEMU_BEHAVIOR", "login")
sleep_s = float(os.environ.get("FAKE_QEMU_SLEEP", "0.05"))
if behavior == "login":
    lines = [
        "OVMF 2026.02 (EDK II)\n",
        "Loading Boot0001: PengyOS\n",
        "[    0.000000] Linux version 6.6.0-pengyos\n",
        "[    1.445630] systemd[1]: Reached target Login Prompts.\n",
        "Welcome to PengyOS Core\n",
        "pengyos login: ",
    ]
    signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
elif behavior == "custom":
    script = os.environ.get("FAKE_QEMU_SCRIPT", "")
    lines = script.split("\x1e") if script else ["fake boot\n"]
elif behavior == "panic":
    lines = [
        "[    0.000000] Linux version 6.6.0-pengyos\n",
        "[  123.456789] Kernel panic - not syncing: VFS: Unable to mount root fs\n",
    ]
elif behavior == "hang":
    lines = ["[    0.000000] Linux version 6.6.0-pengyos\n"]
elif behavior == "die":
    lines = ["[    0.000000] Linux version 6.6.0-pengyos\n"]
else:
    raise SystemExit("unknown FAKE_QEMU_BEHAVIOR " + behavior)

fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
for line in lines:
    os.write(fd, line.encode())
    os.fsync(fd)
    time.sleep(sleep_s)
os.close(fd)
if behavior == "die":
    os._exit(1)
while True:
    time.sleep(1)
'''


def _write_exec(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def _make_image(tmp_path, name="disk.img"):
    img = tmp_path / name
    img.write_bytes(b"\x00" * 4096)
    return str(img)


def _has_pair(argv, flag, value):
    """True when adjacent elements [flag, value] appear in argv (element
    checks like `f'{flag} {value}' in argv` silently fail — argv is a list)."""
    return any(
        argv[i] == flag and argv[i + 1] == value for i in range(len(argv) - 1)
    )


def _boot(tmp_path, behavior, **kw):
    """Run boot_image against the fake qemu with the given behaviour."""
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    env = {"FAKE_QEMU_BEHAVIOR": behavior, "FAKE_QEMU_SLEEP": "0.03"}
    saved = {}
    for k, v in env.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        return boot_image(
            _make_image(tmp_path),
            kvm=False,
            poll_interval=0.02,
            timeout=kw.pop("timeout", 15.0),
            qemu_bin=qemu,
            **kw,
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ==========================================================================
# detect_readiness — readiness regex list vs serial log
# ==========================================================================


def test_detect_readiness_login_fixture_present():
    log = _read_fixture("serial_login_sample.log")
    ready, markers = detect_readiness(log, ("login:",))
    assert ready is True
    assert len(markers) == 1
    assert markers[0].matched is True
    # The marker is "pengyos login:"; the evidence line carries it.
    assert "pengyos login:" in markers[0].evidence


def test_detect_readiness_panic_fixture_absent():
    log = _read_fixture("serial_kernel_panic_sample.log")
    ready, markers = detect_readiness(log, ("login:",))
    assert ready is False
    assert markers[0].matched is False
    assert markers[0].evidence is None


def test_detect_readiness_multiple_markers_all_must_match():
    ready, markers = detect_readiness("step one ok\n", ("step one", "step two"))
    assert ready is False
    assert markers[0].matched is True
    assert markers[1].matched is False

    ready, markers = detect_readiness("step one ok\nstep two ok\n", ("step one", "step two"))
    assert ready is True
    assert all(m.matched for m in markers)


def test_detect_readiness_regex_is_real_regex():
    ready, markers = detect_readiness(
        "PengyOS Core ready\n", ("[Pp]engyOS Core ready$",)
    )
    assert ready is True
    assert "PengyOS Core ready" in markers[0].evidence


def test_detect_readiness_evidence_is_containing_line_truncated():
    long_line = "x" * 900 + " login: " + "y" * 900
    ready, markers = detect_readiness(long_line, ("login:",))
    assert ready is True
    assert markers[0].evidence is not None
    assert len(markers[0].evidence) <= MAX_EVIDENCE_CHARS
    # It is the *line* (the whole single line here, truncated), not just the match.
    assert markers[0].evidence.startswith("x" * 100)


def test_detect_readiness_empty_log_and_empty_patterns():
    ready, markers = detect_readiness("", ("login:",))
    assert ready is False
    assert markers[0].matched is False
    with pytest.raises(ValueError):
        detect_readiness("anything", ())
    with pytest.raises(re.error):
        detect_readiness("anything", ("(unclosed",))


# ==========================================================================
# read_log_incremental — streaming the -serial file: log
# ==========================================================================


def test_read_log_incremental(tmp_path):
    log = tmp_path / "serial.log"
    text, off = read_log_incremental(str(log), 0)
    assert text == "" and off == 0  # missing file is not an error yet

    log.write_text("part one\n")
    text, off = read_log_incremental(str(log), 0)
    assert text == "part one\n" and off == len("part one\n")

    text2, off2 = read_log_incremental(str(log), off)
    assert text2 == "" and off2 == off  # nothing new yet

    with open(log, "a") as fh:
        fh.write("part two\n")
    text2, off2 = read_log_incremental(str(log), off)
    assert text2 == "part two\n"
    assert off2 == len("part one\npart two\n")


def test_read_log_incremental_tolerates_bad_utf8(tmp_path):
    log = tmp_path / "serial.log"
    log.write_bytes(b"ok\n\xff\xfe\n")
    text, off = read_log_incremental(str(log), 0)
    assert text.startswith("ok\n")
    assert off == len(b"ok\n\xff\xfe\n")


# ==========================================================================
# boot_image — the lifecycle
# ==========================================================================


def test_boot_success_reaches_login(tmp_path):
    result = _boot(tmp_path, "login")
    assert isinstance(result, BootResult)
    assert result.ready is True
    assert result.timed_out is False
    assert result.error is None
    assert result.status == "ready"
    assert result.ok is True
    assert result.returncode is not None
    assert result.duration > 0
    assert result.duration < 15
    # The serial log lives in the workdir and captured the marker.
    assert result.serial_log.endswith("pengyos-serial.log")
    assert Path(result.serial_log).read_text(encoding="utf-8") .endswith("pengyos login: ")
    assert result.markers[0].matched is True
    assert "pengyos login:" in result.markers[0].evidence
    # The rendered argv is the golden q35 command line (no-KVM pinned).
    assert result.argv[0].endswith("fake-qemu")
    assert _has_pair(result.argv, "-cpu", "max")
    assert _has_pair(result.argv, "-serial", f"file:{result.serial_log}")


def test_boot_accels_flagged(tmp_path):
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    for kvm_pin, expected_accel, expected_tokens in (
        (True, "kvm", ["-enable-kvm", "-cpu", "host"]),
        (False, "tcg", ["-cpu", "max"]),
    ):
        os.environ["FAKE_QEMU_BEHAVIOR"] = "login"
        try:
            result = boot_image(
                _make_image(tmp_path),
                kvm=kvm_pin,
                poll_interval=0.02,
                timeout=15.0,
                qemu_bin=qemu,
            )
        finally:
            os.environ.pop("FAKE_QEMU_BEHAVIOR", None)
        assert result.accel == expected_accel
        for token in expected_tokens:
            assert token in result.argv
        if kvm_pin:
            assert "-enable-kvm" in result.argv
        else:
            assert "-enable-kvm" not in result.argv


def test_boot_never_hangs_past_timeout_sleeping_fake(tmp_path):
    """Spec case: a fake 'qemu' that sleeps forever yields FAIL with
    reason=timeout, not a crash — and never past --timeout."""
    result = _boot(tmp_path, "hang", timeout=1.0)
    t0 = time.monotonic()
    assert result.ready is False
    assert result.timed_out is True
    assert result.status == "timeout"
    assert result.ok is False
    assert result.returncode is not None  # captured exit after terminate/kill
    assert result.duration >= 1.0
    assert time.monotonic() - t0 < 5.0  # bounded wall time on our side too


def test_boot_panic_log_times_out(tmp_path):
    result = _boot(tmp_path, "panic", timeout=1.5)
    assert result.ready is False
    assert result.timed_out is True
    assert result.markers[0].matched is False
    # The log kept the evidence (kernel panic line), for item 6/7.
    log_text = Path(result.serial_log).read_text(encoding="utf-8")
    assert "Kernel panic" in log_text


def test_boot_qemu_dies_early(tmp_path):
    result = _boot(tmp_path, "die")
    assert result.ready is False
    assert result.timed_out is False
    assert result.status == "failed"
    assert result.returncode == 1  # the fake exits 1 immediately


def test_boot_missing_qemu_binary_is_a_result_not_a_crash(tmp_path):
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        timeout=5.0,
        qemu_bin=str(tmp_path / "no-such-qemu"),
    )
    assert result.ready is False
    assert result.error is not None
    assert "not found" in result.error
    assert result.returncode is None
    assert result.status == "failed"
    # The argv is still rendered, so a report can say what *would* have run.
    assert result.argv


def test_boot_missing_image_raises(tmp_path):
    with pytest.raises(ImageNotFound):
        boot_image(str(tmp_path / "missing.img"))
    with pytest.raises(ImageNotFound):
        boot_image("")


def test_boot_invalid_inputs_raise(tmp_path):
    img = _make_image(tmp_path)
    with pytest.raises(ValueError):
        boot_image(img, expect=())
    with pytest.raises(ValueError):
        boot_image(img, timeout=0)


def test_boot_multiple_expect_markers(tmp_path):
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    script = "init started\x1eall systems go\x1e"
    os.environ["FAKE_QEMU_BEHAVIOR"] = "custom"
    os.environ["FAKE_QEMU_SCRIPT"] = script
    try:
        result = boot_image(
            _make_image(tmp_path),
            kvm=False,
            poll_interval=0.02,
            timeout=15.0,
            qemu_bin=qemu,
            expect=("init started", "all systems go"),
        )
    finally:
        os.environ.pop("FAKE_QEMU_BEHAVIOR", None)
        os.environ.pop("FAKE_QEMU_SCRIPT", None)
    assert result.ready is True
    assert [m.matched for m in result.markers] == [True, True]

    # One marker missing -> not ready -> bounded timeout.
    os.environ["FAKE_QEMU_BEHAVIOR"] = "custom"
    os.environ["FAKE_QEMU_SCRIPT"] = "init started\x1e"
    try:
        result = boot_image(
            _make_image(tmp_path),
            kvm=False,
            poll_interval=0.02,
            timeout=1.0,
            qemu_bin=qemu,
            expect=("init started", "never appears"),
        )
    finally:
        os.environ.pop("FAKE_QEMU_BEHAVIOR", None)
        os.environ.pop("FAKE_QEMU_SCRIPT", None)
    assert result.ready is False
    assert result.timed_out is True
    assert [m.matched for m in result.markers] == [True, False]


def test_boot_uefi_combined_uses_bios(tmp_path):
    ovmf = tmp_path / "OVMF.fd"
    ovmf.write_bytes(b"\x01" * 1024)  # no _CODE -> combined blob
    result = _boot(tmp_path, "login", uefi=True, ovmf=str(ovmf))
    assert result.ready is True
    assert "-bios" in result.argv
    assert str(ovmf) in result.argv
    # No VARS copy for a combined blob.
    assert not (Path(result.serial_log).parent / "pengyos-ovmf-vars.fd").exists()


def test_boot_uefi_split_copies_vars_to_workdir(tmp_path):
    code = tmp_path / "OVMF_CODE.fd"
    code.write_bytes(b"C0DEC0DE" * 32)
    vars_orig = tmp_path / "OVMF_VARS.fd"
    vars_content = b"VARS" * 64
    vars_orig.write_bytes(vars_content)
    result = _boot(tmp_path, "login", uefi=True, ovmf=str(code))
    assert result.ready is True
    workdir = Path(result.serial_log).parent
    vars_copy = workdir / "pengyos-ovmf-vars.fd"
    assert vars_copy.exists()
    assert vars_copy.read_bytes() == vars_content
    # The original firmware VARS is untouched; the argv points at the copy.
    assert vars_orig.read_bytes() == vars_content
    # A -drive spec is ONE token after the flag: assert the spec elements.
    assert (
        f"if=pflash,format=raw,unit=1,file={vars_copy}" in result.argv
    )
    assert (
        f"if=pflash,format=raw,unit=0,readonly=on,file={code}" in result.argv
    )


def test_boot_uefi_no_firmware_raises(tmp_path):
    with pytest.raises(argv_mod.OvmfNotFound):
        _boot(tmp_path, "login", uefi=True, ovmf=str(tmp_path / "nope.fd"))


def test_boot_tpm_starts_attaches_tears_down(tmp_path):
    fake_swtpm = _write_exec(tmp_path, "fake-swtpm", FAKE_SWTPM_READY)
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=15.0,
        qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
        tpm=True,
        swtpm_bin=fake_swtpm,
    )
    assert result.ready is True
    workdir = Path(result.serial_log).parent
    sock = workdir / "tpm.sock"
    # The running swtpm datapath was attached to the argv...
    assert _has_pair(result.argv, "-chardev", f"socket,id=tpmchr,path={sock}")
    assert _has_pair(result.argv, "-tpmdev", "emulator,id=tpm0,chardev=tpmchr")
    # ...and swtpm is torn down before returning (socket removed).
    assert not sock.exists()


def test_boot_tpm_start_failure_raises(tmp_path):
    with pytest.raises(SwtpmStartError):
        boot_image(
            _make_image(tmp_path),
            kvm=False,
            timeout=5.0,
            qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
            tpm=True,
            swtpm_bin=_write_exec(tmp_path, "fake-swtpm", "#!/usr/bin/env python3\nimport sys\nsys.exit(3)\n"),
            poll_interval=0.02,
        )


def test_boot_default_workdir_is_a_kept_tempdir(tmp_path, monkeypatch):
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    os.environ["FAKE_QEMU_BEHAVIOR"] = "login"
    try:
        result = boot_image(
            _make_image(tmp_path),
            kvm=False,
            poll_interval=0.02,
            timeout=15.0,
            qemu_bin=qemu,
        )
    finally:
        os.environ.pop("FAKE_QEMU_BEHAVIOR", None)
    assert result.ready is True
    # No workdir given -> a temp dir was created and KEPT (evidence).
    assert Path(result.serial_log).is_file()
    assert "pengyos-verify-" in Path(result.serial_log).parent.name


def test_boot_result_to_dict_shape(tmp_path):
    result = _boot(tmp_path, "panic", timeout=1.0)
    d = result.to_dict()
    for key in (
        "image", "accel", "duration", "returncode",
        "ready", "timed_out", "serial_log", "markers", "checks",
        "error", "tpm_state", "tpm_state_method", "tpm_protocol", "result",
    ):
        assert key in d
    assert d["result"] == "timeout"
    assert d["accel"] == "tcg"
    assert d["markers"][0]["pattern"] == "login:"
    assert d["markers"][0]["matched"] is False
    assert isinstance(d["duration"], float)
    # The attached checks serialise in the spec's {name, status, evidence}
    # shape and mirror the attribute.
    assert d["checks"] == [c.to_dict() for c in result.checks]


def test_find_qemu_bin_returns_a_usable_name(tmp_path):
    name = find_qemu_bin()
    assert isinstance(name, str) and name
    assert name.endswith("qemu-system-x86_64") or os.path.exists(name)


# ==========================================================================
# CLI: the `boot` subcommand is wired (item 5)
# ==========================================================================


def test_cli_boot_success_exit_zero(tmp_path, monkeypatch, capsys):
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _write_exec(fake_dir, "qemu-system-x86_64", FAKE_QEMU)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ['PATH']}")
    os.environ["FAKE_QEMU_BEHAVIOR"] = "login"
    os.environ["FAKE_QEMU_SLEEP"] = "0.03"
    try:
        rc = cli.main(["boot", _make_image(tmp_path), "--timeout", "20"])
    finally:
        os.environ.pop("FAKE_QEMU_BEHAVIOR", None)
        os.environ.pop("FAKE_QEMU_SLEEP", None)
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "result:   ready" in out
    assert "login:" in out


def test_cli_boot_timeout_exit_one(tmp_path, monkeypatch, capsys):
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _write_exec(fake_dir, "qemu-system-x86_64", FAKE_QEMU)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ['PATH']}")
    os.environ["FAKE_QEMU_BEHAVIOR"] = "hang"
    os.environ["FAKE_QEMU_SLEEP"] = "0.03"
    try:
        rc = cli.main(["boot", _make_image(tmp_path), "--timeout", "1.0"])
    finally:
        os.environ.pop("FAKE_QEMU_BEHAVIOR", None)
        os.environ.pop("FAKE_QEMU_SLEEP", None)
    out = capsys.readouterr().out
    assert rc == cli.EXIT_FAIL
    assert "result:   timeout" in out


def test_cli_boot_missing_image_exit_one(tmp_path, capsys):
    rc = cli.main(["boot", str(tmp_path / "missing.img")])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_FAIL
    assert "boot failed" in err
    assert "not found" in err


def test_cli_boot_custom_expect_flag(tmp_path, monkeypatch, capsys):
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _write_exec(fake_dir, "qemu-system-x86_64", FAKE_QEMU)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ['PATH']}")
    os.environ["FAKE_QEMU_BEHAVIOR"] = "custom"
    os.environ["FAKE_QEMU_SCRIPT"] = "init started\x1e"
    os.environ["FAKE_QEMU_SLEEP"] = "0.03"
    try:
        rc = cli.main(
            [
                "boot", _make_image(tmp_path),
                "--timeout", "20",
                "--expect", "init started",
            ]
        )
    finally:
        for k in ("FAKE_QEMU_BEHAVIOR", "FAKE_QEMU_SCRIPT", "FAKE_QEMU_SLEEP"):
            os.environ.pop(k, None)
    out = capsys.readouterr().out
    # The custom marker satisfied readiness, but the default assertion set
    # (wired into the boot flow) still runs: the log has no ``login:``, so
    # the ``reached-login`` check fails and the exit code is 1 even though
    # the boot is "ready" (spec: exit 0 iff ready AND all checks pass).
    assert rc == cli.EXIT_FAIL
    assert "'init started' MATCH" in out
    assert "check:    reached-login FAIL" in out
    assert "check:    no-kernel-panic PASS" in out
    assert "result:   ready" in out


# ==========================================================================
# Assertion-runner wiring
# ==========================================================================


def test_boot_result_carries_default_checks_on_ready_boot(tmp_path):
    result = _boot(tmp_path, "login")
    assert result.ready is True
    # The default set ran over the captured log and was attached.
    assert [c.name for c in result.checks] == ["reached-login", "no-kernel-panic"]
    assert all(c.passed for c in result.checks) is True
    assert result.checks_passed is True
    login_check = result.checks[0]
    assert login_check.evidence and "pengyos login:" in login_check.evidence
    # A passing absent check has no match line to point at.
    assert result.checks[1].evidence is None


def test_boot_result_checks_fail_on_panic(tmp_path):
    result = _boot(tmp_path, "panic", timeout=1.0)
    assert result.ready is False
    assert result.timed_out is True
    assert result.checks_passed is False
    by_name = {c.name: c for c in result.checks}
    assert by_name["reached-login"].passed is False
    assert by_name["reached-login"].evidence is None  # no login: to point at
    assert by_name["no-kernel-panic"].passed is False
    assert "Kernel panic" in by_name["no-kernel-panic"].evidence


def test_boot_image_custom_assertions_replace_the_default_set(tmp_path):
    checks = (
        assertions_mod.Assertion.present("saw-init", "init started"),
        assertions_mod.Assertion.absent("no-panic", "Kernel panic"),
    )
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    env = {"FAKE_QEMU_BEHAVIOR": "custom", "FAKE_QEMU_SCRIPT": "init started\x1e",
           "FAKE_QEMU_SLEEP": "0.03"}
    for k, v in env.items():
        os.environ[k] = v
    try:
        result = boot_image(
            _make_image(tmp_path),
            kvm=False,
            poll_interval=0.02,
            timeout=10.0,
            qemu_bin=qemu,
            expect=("init started",),
            assertions=checks,
        )
    finally:
        for k in env:
            os.environ.pop(k, None)
    assert result.ready is True
    assert [c.name for c in result.checks] == ["saw-init", "no-panic"]
    assert all(c.passed for c in result.checks) is True


def test_boot_image_empty_assertions_run_no_checks(tmp_path):
    result = _boot(tmp_path, "login", assertions=[])
    assert result.ready is True
    assert result.checks == []
    assert result.checks_passed is True  # vacuous, like all_passed


def test_boot_image_invalid_assertion_pattern_raises_value_error_pre_flight(
    tmp_path,
):
    """An uncompilable check must surface *before* QEMU launches."""
    sentinel = tmp_path / "launched.marker"
    launcher = (
        "#!/bin/sh\n"
        f"touch {sentinel}\n"
        "sleep 60\n"
    )
    qemu = _write_exec(tmp_path, "fake-qemu", launcher)
    with pytest.raises(ValueError, match="does not compile"):
        boot_image(
            _make_image(tmp_path),
            kvm=False,
            timeout=2.0,
            qemu_bin=qemu,
            assertions=(assertions_mod.Assertion.present("bad", "("),),
        )
    assert not sentinel.exists()  # nothing was launched


def test_boot_image_non_assertion_entry_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="Assertion instances"):
        boot_image(
            _make_image(tmp_path),
            kvm=False,
            timeout=2.0,
            assertions=[{"name": "x", "status": "passed", "evidence": None}],
        )


def test_boot_checks_run_even_when_qemu_fails_to_launch(tmp_path):
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        timeout=2.0,
        qemu_bin=str(tmp_path / "definitely-not-a-qemu"),
    )
    assert result.ready is False
    assert result.error is not None
    # The checks still ran over the (empty) captured log: the present check
    # failed, the absent check passed vacuously.
    by_name = {c.name: c for c in result.checks}
    assert by_name["reached-login"].passed is False
    assert by_name["no-kernel-panic"].passed is True
    assert result.checks_passed is False


def test_boot_result_checks_passed_is_vacuously_true_for_manual_result():
    # A hand-built (pre-wiring-style) result with no checks stays sane.
    result = BootResult(image="x.img", ready=True)
    assert result.checks == []
    assert result.checks_passed is True
    assert "checks" in result.to_dict() and result.to_dict()["checks"] == []


# ==========================================================================
# TPM state provisioning wiring (the --tpm follow-up item)
# ==========================================================================

# A fake swtpm_setup: writes a marker state file into its --tpmstate dir=.
# (The provisioning policy itself is covered in tests/test_tpm_state.py; here
# it just stands in for a working provisioning tool in the boot flow.)
FAKE_SETUP = r'''#!/usr/bin/env python3
import os, sys
for i, a in enumerate(sys.argv[1:]):
    if a == "--tpmstate" and i + 1 < len(sys.argv[1:]):
        d = sys.argv[i + 2].split("dir=", 1)[1]
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "tpm2-00.permall"), "wb") as fh:
            fh.write(b"FAKESTATE" + b"\x00" * 16)
sys.exit(0)
'''


def test_boot_tpm_provisions_fixture_state_by_default(tmp_path):
    # No swtpm_setup forced in -> the committed fixture seeds the state dir
    # that Swtpm then runs with (--tpmstate dir=<workdir>/tpmstate).
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=15.0,
        qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
        tpm=True,
        swtpm_bin=_write_exec(tmp_path, "fake-swtpm", FAKE_SWTPM_READY),
        swtpm_setup_bin="",
    )
    assert result.ready is True
    # The per-boot state dir is the one Swtpm renders into --tpmstate dir=.
    assert result.tpm_state.endswith(os.path.join("tpmstate"))
    assert result.tpm_state_method == "fixture"
    # The committed fixture's state file was copied into the per-boot dir...
    state_files = list(Path(result.tpm_state).glob("tpm2-*"))
    assert len(state_files) == 1
    committed = next((FIXTURES / "tpmstate").glob("tpm2-*"))
    assert state_files[0].read_bytes() == committed.read_bytes()
    # ...and the swtpm datapath was still attached (item 4 unchanged).
    assert _has_pair(result.argv, "-tpmdev", "emulator,id=tpm0,chardev=tpmchr")


def test_boot_tpm_provisions_with_swtpm_setup(tmp_path):
    fake_setup = _write_exec(tmp_path, "fake-swtpm-setup", FAKE_SETUP)
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=15.0,
        qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
        tpm=True,
        swtpm_bin=_write_exec(tmp_path, "fake-swtpm", FAKE_SWTPM_READY),
        swtpm_setup_bin=fake_setup,
    )
    assert result.ready is True
    assert result.tpm_state_method == "swtpm_setup"
    permall = Path(result.tpm_state) / "tpm2-00.permall"
    assert permall.read_bytes().startswith(b"FAKESTATE")


def test_boot_tpm_existing_state_is_reused(tmp_path):
    # A pre-seeded state dir (e.g. a reused workdir) is kept, never
    # re-provisioned over.
    workdir = tmp_path / "wd"
    state_dir = workdir / "tpmstate"  # Swtpm's default per-boot state dir
    state_dir.mkdir(parents=True)
    marker = b"REUSED-STATE"
    (state_dir / "tpm2-00.permall").write_bytes(marker)
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=15.0,
        workdir=str(workdir),
        qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
        tpm=True,
        swtpm_bin=_write_exec(tmp_path, "fake-swtpm", FAKE_SWTPM_READY),
        swtpm_setup_bin="",
    )
    assert result.ready is True
    assert result.tpm_state_method == "existing"
    assert result.tpm_state == str(state_dir.resolve())
    assert (state_dir / "tpm2-00.permall").read_bytes() == marker


def test_boot_tpm_provision_failure_is_preflight(tmp_path):
    # No swtpm_setup AND no usable fixture -> TpmStateError raised BEFORE a
    # boot starts. A read-only `qemu_bin --version` probe is expected
    # (TPM auto-protocol detection runs it pre-flight to pick the swtpm
    # wiring); a real *boot* is not. So the fake qemu writes the sentinel
    # only when actually launched to boot (it sees a boot argv, not
    # --version) — the sentinel means "a boot was attempted", not "the
    # binary was ever exec'd".
    sentinel = tmp_path / "qemu-ran"
    fake_qemu = tmp_path / "sentinel-qemu"
    fake_qemu.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('QEMU emulator version 10.2.1 (fake)')\n"
        "    sys.exit(0)\n"
        f"open({str(sentinel)!r}, 'w').write('ran')\n"
        "import time\ntime.sleep(60)\n"
    )
    fake_qemu.chmod(fake_qemu.stat().st_mode | 0o111)
    with pytest.raises(TpmStateError):
        boot_image(
            _make_image(tmp_path),
            kvm=False,
            timeout=5.0,
            qemu_bin=str(fake_qemu),
            tpm=True,
            swtpm_bin=_write_exec(tmp_path, "fake-swtpm", FAKE_SWTPM_READY),
            swtpm_setup_bin="",
            tpm_state_fixture=str(tmp_path / "missing-fixture"),
        )
    assert not sentinel.exists()  # no boot was ever started
    time.sleep(0.05)
    assert not sentinel.exists()  # not even a racing late boot


def test_boot_result_to_dict_has_tpm_state_fields(tmp_path):
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=15.0,
        qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
        tpm=True,
        swtpm_bin=_write_exec(tmp_path, "fake-swtpm", FAKE_SWTPM_READY),
        swtpm_setup_bin="",
    )
    d = result.to_dict()
    assert d["tpm_state"] == result.tpm_state
    assert d["tpm_state_method"] == "fixture"
    # A non-TPM boot leaves both empty (never present as noise).
    plain = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=15.0,
        qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
    )
    d2 = plain.to_dict()
    assert d2["tpm_state"] == "" and d2["tpm_state_method"] == ""


def test_cli_boot_tpm_prints_tpmstate_line(tmp_path, monkeypatch, capsys):
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _write_exec(fake_dir, "qemu-system-x86_64", FAKE_QEMU)
    # Hermetic fake swtpm on PATH (the CLI has no --swtpm flag — boot_image
    # resolves the default 'swtpm' name via PATH).
    _write_exec(fake_dir, "swtpm", FAKE_SWTPM_READY)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ['PATH']}")
    os.environ["FAKE_QEMU_BEHAVIOR"] = "login"
    os.environ["FAKE_QEMU_SLEEP"] = "0.03"
    try:
        # On a host without swtpm_setup the committed fixture seeds the
        # state; where swtpm_setup is installed it provisions via that —
        # either way the summary line carries the method + state dir.
        rc = cli.main(["boot", _make_image(tmp_path), "--tpm", "--timeout", "20"])
    finally:
        os.environ.pop("FAKE_QEMU_BEHAVIOR", None)
        os.environ.pop("FAKE_QEMU_SLEEP", None)
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    line = next(l for l in out.splitlines() if l.startswith("tpmstate: "))
    # The line is `tpmstate: <dir> (<method>, <protocol>)` for a --tpm boot:
    # the provisioning method (fixture/swtpm_setup/existing) plus the resolved
    # QEMU<->swtpm wiring protocol (control/legacy).
    import re as _re
    m = _re.match(
        r"^tpmstate: (\S+) \((existing|swtpm_setup|fixture), "
        r"(control|legacy)\)$", line,
    )
    assert m, f"unexpected tpmstate line format: {line!r}"
    state_dir = m.group(1)
    assert os.path.isdir(state_dir)
    assert any(n.startswith("tpm2-") for n in os.listdir(state_dir))


def test_cli_boot_tpm_state_preflight_failure_writes_json(tmp_path, monkeypatch, capsys):
    # A --tpm boot where no TPM state can be provisioned (no swtpm_setup on
    # PATH and no usable fixture) is a *pre-flight* failure: the CLI must not
    # crash with an unhandled traceback but report it — exit 1, a "boot
    # failed" note on stderr, and both requested reports written (honouring
    # the "always writes JSON" acceptance criterion). QEMU is never launched
    # (provisioning happens before the swtpm process starts).
    import pengyos_qemu_verify.tpm_state as tpm_state_mod

    # 1. No swtpm_setup on PATH (restrict PATH to an empty temp bin dir).
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    # 2. No usable committed fixture: force the locator to raise.
    def _raise_fixture(*_a, **_k):
        raise tpm_state_mod.TpmStateError(
            "no committed TPM state fixture available (test)"
        )
    monkeypatch.setattr(tpm_state_mod, "default_fixture_dir", _raise_fixture)

    json_out = tmp_path / "result.json"
    junit_out = tmp_path / "result.xml"
    rc = cli.main([
        "boot", _make_image(tmp_path), "--tpm",
        "--json", str(json_out), "--junit", str(junit_out),
    ])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_FAIL
    assert "boot failed" in err
    assert "fixture" in err

    # The JSON report was written and parses, with an overall "fail" verdict
    # and the pre-flight error recorded.
    assert json_out.is_file()
    data = json.loads(json_out.read_text())
    assert data["result"] == "fail"
    assert data["error"]
    assert "fixture" in data["error"]

    # The JUnit report was written and is well-formed (parseable back).
    assert junit_out.is_file()
    root = ET.parse(junit_out).getroot()
    assert root.tag == "testsuites"


# ==========================================================================
# QEMU-version TPM protocol selection (the PENGYOS_IT real-TPM-boot fix)
# ==========================================================================

# Same behaviour as FAKE_SWTPM_READY, but first dumps its own argv to
# $SWPTM_ARGV_DUMP so a test can assert which wiring the boot rendered.
FAKE_SWPTM_DUMP = r'''#!/usr/bin/env python3
import os, socket, sys, time
dump = os.environ.get("SWPTM_ARGV_DUMP")
if dump:
    with open(dump, "w") as fh:
        fh.write("\n".join(sys.argv))
args = sys.argv[1:]
paths = []
i = 0
while i < len(args):
    a = args[i]
    if a in ("--server", "--ctrl") and i + 1 < len(args):
        val = args[i + 1]
        if "path=" in val:
            paths.append(val.split("path=", 1)[1].split(",", 1)[0])
        i += 2
    else:
        i += 1
socks = []
for p in paths:
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    if os.path.exists(p):
        os.remove(p)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(p)
    s.listen(1)
    socks.append(s)
try:
    while True:
        time.sleep(0.2)
except Exception:
    pass
finally:
    for s in socks:
        s.close()
'''


def _boot_tpm_dumping(tmp_path, dump_path, **kw):
    """A --tpm boot whose fake swtpm records the argv it was started with."""
    os.environ["SWPTM_ARGV_DUMP"] = str(dump_path)
    try:
        result = boot_image(
            _make_image(tmp_path),
            kvm=False,
            poll_interval=0.02,
            timeout=15.0,
            qemu_bin=_write_exec(tmp_path, "fake-qemu", FAKE_QEMU),
            tpm=True,
            swtpm_bin=_write_exec(tmp_path, "fake-swtpm", FAKE_SWPTM_DUMP),
            swtpm_setup_bin="",
            **kw,
        )
    finally:
        os.environ.pop("SWPTM_ARGV_DUMP", None)
    started_argv = (dump_path.read_text().splitlines() if dump_path.exists() else [])
    return result, started_argv



_DUMP_NAME = "swtpm-cmdline.txt"


def test_boot_tpm_auto_protocol_is_control_for_unparseable_qemu(tmp_path):
    """The fake qemu has no parseable --version, so 'auto' falls back to the
    modern control wiring — the one that does not deadlock a QEMU >= 5.2.
    The QEMU-side argv (chardev/tpmdev) is identical under both protocols;
    only the swtpm command line differs."""
    dump = tmp_path / _DUMP_NAME
    result, started_argv = _boot_tpm_dumping(tmp_path, dump)
    assert result.ready is True
    assert result.tpm_protocol == "control"
    assert "--ctrl" in started_argv
    assert started_argv[started_argv.index("--ctrl") + 1].endswith(
        f"path={result.serial_log.rsplit('/', 1)[0]}/tpm.sock"
    )
    assert "--server" not in started_argv
    # The QEMU argv still carries the emulator wiring.
    assert _has_pair(result.argv, "-tpmdev", "emulator,id=tpm0,chardev=tpmchr")


def test_boot_tpm_explicit_legacy_protocol_renders_server(tmp_path):
    """An explicit legacy choice (pre-5.2 QEMU) renders the --server
    datapath plus the separate --ctrl control channel."""
    dump = tmp_path / _DUMP_NAME
    result, started_argv = _boot_tpm_dumping(
        tmp_path, dump, tpm_protocol="legacy",
    )
    assert result.ready is True
    assert result.tpm_protocol == "legacy"
    assert "--server" in started_argv
    assert started_argv[started_argv.index("--server") + 1].endswith(
        f"path={result.serial_log.rsplit('/', 1)[0]}/tpm.sock"
    )
    # use_ctrl=True is the legacy default: a separate --ctrl is present.
    assert "--ctrl" in started_argv


def test_boot_tpm_bad_protocol_is_preflight_value_error(tmp_path):
    """A typo'd protocol must raise ValueError before the workdir exists and
    before QEMU/swtpm start — the CLI's 'always writes JSON' path covers it."""
    workdir = tmp_path / "wd"
    sentinel = tmp_path / "qemu-ran"
    fake_qemu = tmp_path / "sentinel-qemu"
    fake_qemu.write_text(
        "#!/usr/bin/env python3\n"
        f"open({str(sentinel)!r}, 'w').write('ran')\n"
        "import time\ntime.sleep(60)\n"
    )
    fake_qemu.chmod(fake_qemu.stat().st_mode | 0o111)
    with pytest.raises(ValueError):
        boot_image(
            _make_image(tmp_path),
            kvm=False,
            timeout=5.0,
            workdir=str(workdir),
            qemu_bin=str(fake_qemu),
            tpm=True,
            swtpm_bin=_write_exec(tmp_path, "fake-swtpm", FAKE_SWTPM_READY),
            swtpm_setup_bin="",
            tpm_protocol="bogus",
        )
    assert not workdir.exists()  # nothing was prepared
    assert not sentinel.exists()  # QEMU was never launched


def test_boot_without_tpm_reports_empty_protocol(tmp_path):
    result = _boot(tmp_path, "login")
    assert result.tpm_protocol == ""
    assert result.to_dict()["tpm_protocol"] == ""

# ---------------------------------------------------------------------------
# settle: keep reading the log after readiness
# ---------------------------------------------------------------------------


def test_boot_settle_captures_what_follows_the_login_prompt(tmp_path):
    """The readiness marker is the login prompt; the login banner and the shell
    prompt land after it. A check that has to prove the login produced a working
    shell can only see them if the harness keeps listening."""
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    # The prompt is written one fake-sleep after the login line.
    os.environ["FAKE_QEMU_BEHAVIOR"] = "custom"
    os.environ["FAKE_QEMU_SCRIPT"] = "pengyos-core login: root (automatic login)\x1e# "
    os.environ["FAKE_QEMU_SLEEP"] = "0.4"
    try:
        settled = boot_image(
            _make_image(tmp_path), kvm=False, poll_interval=0.02, timeout=15.0,
            qemu_bin=qemu, settle=1.5,
        )
        plain = boot_image(
            _make_image(tmp_path), kvm=False, poll_interval=0.02, timeout=15.0,
            qemu_bin=qemu, settle=0.0,
        )
    finally:
        for key in ("FAKE_QEMU_BEHAVIOR", "FAKE_QEMU_SCRIPT", "FAKE_QEMU_SLEEP"):
            os.environ.pop(key, None)

    assert settled.ready is True and plain.ready is True
    settled_log = Path(settled.serial_log).read_text(encoding="utf-8", errors="replace")
    plain_log = Path(plain.serial_log).read_text(encoding="utf-8", errors="replace")
    assert "# " in settled_log  # what the matrix's autologin check needs
    assert "# " not in plain_log
    assert settled.duration > plain.duration  # the window is paid for, honestly


def test_boot_rejects_a_negative_settle(tmp_path):
    with pytest.raises(ValueError, match="settle must not be negative"):
        _boot(tmp_path, "login", settle=-1.0)
