"""Tests for the swtpm TPM2 support.

Two halves are covered, neither needing a real ``swtpm``, network, root or a
display:

* the *pure* argv builders — :func:`argv.tpm_qemu_args` (the QEMU
  ``-chardev``/``-tpmdev`` tokens) and :func:`tpm.build_swtpm_argv` (the
  ``swtpm socket ...`` command line) — asserted against golden strings, and
  :func:`argv.build_argv` asserted to compose the TPM column on top of every
  existing boot mode *without* perturbing the pre-TPM golden baseline;
* the :class:`tpm.Swtpm` process lifecycle (start / ready-wait / stop /
  context manager), driven against small *fake* swtpm executables that merely
  open the requested unix socket and sleep.
"""

import os
import socket
import stat

import pytest

from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify.argv import (
    TPM_CHARDEV_ID,
    TPM_DEV_ID,
    build_argv,
    tpm_qemu_args,
)
from pengyos_qemu_verify import tpm as tpm_mod
from pengyos_qemu_verify.tpm import (
    DEFAULT_QEMU_BIN,
    PROTOCOL_CONTROL,
    PROTOCOL_LEGACY,
    SWTPM_BIN,
    Swtpm,
    SwtpmError,
    SwtpmStartError,
    build_swtpm_argv,
    protocol_for_qemu_version,
    qemu_version_tuple,
    resolve_tpm_protocol,
)


# ---------------------------------------------------------------------------
# fake swtpm executables
# ---------------------------------------------------------------------------

# Opens the --server/--ctrl unix sockets it is told about, then sleeps forever
# (until killed). Exercises the real Swtpm lifecycle: spawn -> socket appears ->
# stop.
FAKE_READY = r'''#!/usr/bin/env python3
import sys, os, socket, time
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

# Exits immediately with a non-zero code and never opens a socket -> the
# ready-wait must surface SwtpmStartError (early death), not hang.
FAKE_DEAD = r'''#!/usr/bin/env python3
import sys
sys.exit(3)
'''

# Sleeps forever but never opens the requested socket -> the ready-wait must
# time out with SwtpmStartError (never-ready), not hang.
FAKE_NEVER = r'''#!/usr/bin/env python3
import time
try:
    while True:
        time.sleep(1.0)
except Exception:
    pass
'''


def _write_fake(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


# ==========================================================================
# argv.tpm_qemu_args — the QEMU -chardev/-tpmdev tokens
# ==========================================================================


def test_tpm_qemu_args_golden():
    got = tpm_qemu_args("/run/tpm.sock")
    assert got == [
        "-chardev", "socket,id=tpmchr,path=/run/tpm.sock",
        "-tpmdev", "emulator,id=tpm0,chardev=tpmchr",
        "-device", "tpm-tis,tpmdev=tpm0",
    ]


def test_tpm_qemu_args_custom_ids():
    got = tpm_qemu_args(
        "/s", chardev_id="mychr", dev_id="mytpm",
    )
    assert got == [
        "-chardev", "socket,id=mychr,path=/s",
        "-tpmdev", "emulator,id=mytpm,chardev=mychr",
        "-device", "tpm-tis,tpmdev=mytpm",
    ]


def test_tpm_qemu_args_chardev_id_threaded_into_tpmdev():
    """The chardev id in -tpmdev must reference the -chardev id (self-consistent)."""
    got = tpm_qemu_args("/s", chardev_id="abc")
    chardev = got[got.index("-chardev") + 1]
    tpmdev = got[got.index("-tpmdev") + 1]
    assert "id=abc," in chardev
    assert "chardev=abc" in tpmdev


@pytest.mark.parametrize("socket_path", ["", None])
def test_tpm_qemu_args_empty_socket_rejected(socket_path):
    with pytest.raises(ValueError):
        tpm_qemu_args(socket_path)


def test_tpm_qemu_args_empty_ids_rejected():
    with pytest.raises(ValueError):
        tpm_qemu_args("/s", chardev_id="")
    with pytest.raises(ValueError):
        tpm_qemu_args("/s", dev_id="")


# ==========================================================================
# argv.build_argv — the TPM column composes onto every boot mode
# ==========================================================================


def test_build_argv_no_tpm_is_byte_identical_baseline():
    """Regression guard: omitting tpm_socket leaves the pre-TPM golden argv
    untouched (KVM bare-metal, the item-2 contract)."""
    got = build_argv("rootfs.ext4", kernel="bzImage", serial_log="LOG", kvm=True)
    assert got == [
        "qemu-system-x86_64",
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-enable-kvm",
        "-cpu", "host",
        "-kernel", "bzImage",
        "-drive", "file=rootfs.ext4,if=virtio,format=raw,snapshot=on",
        "-append", "root=/dev/vda rw console=ttyS0",
        "-display", "none",
        "-serial", "file:LOG",
        "-monitor", "none",
        "-no-reboot",
    ]
    assert "-chardev" not in got
    assert "-tpmdev" not in got


def test_tpm_tokens_placed_after_disk_before_tail():
    """The -chardev/-tpmdev section sits after the boot disk and immediately
    before the headless tail, so it composes with any disk/firmware section."""
    got = build_argv("d.img", kvm=False, tpm_socket="/run/tpm.sock")
    disk_idx = got.index("file=d.img,if=virtio,format=raw,snapshot=on")
    chr_idx = got.index("-chardev")
    tpm_idx = got.index("-tpmdev")
    disp_idx = got.index("-display")
    assert disk_idx < chr_idx < tpm_idx < disp_idx
    # -chardev, -tpmdev and the TPM -device are the only tokens between the disk and the tail
    assert got[chr_idx:disp_idx] == [
        "-chardev", "socket,id=tpmchr,path=/run/tpm.sock",
        "-tpmdev", "emulator,id=tpm0,chardev=tpmchr",
        "-device", "tpm-tis,tpmdev=tpm0",
    ]


def test_golden_seabios_tpm():
    got = build_argv(
        "d.img", kvm=False, tpm_socket="/run/tpm.sock", serial_log="LOG",
    )
    assert got == [
        "qemu-system-x86_64",
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-cpu", "max",
        "-drive", "file=d.img,if=virtio,format=raw,snapshot=on",
        "-chardev", "socket,id=tpmchr,path=/run/tpm.sock",
        "-tpmdev", "emulator,id=tpm0,chardev=tpmchr",
        "-device", "tpm-tis,tpmdev=tpm0",
        "-display", "none",
        "-serial", "file:LOG",
        "-monitor", "none",
        "-no-reboot",
    ]


def test_golden_kvm_baremetal_tpm():
    """TPM composes onto the bare-metal -kernel path."""
    got = build_argv(
        "rootfs.ext4", kernel="bzImage", kvm=True,
        tpm_socket="/run/tpm.sock", serial_log="LOG",
    )
    # -kernel section, then TPM, then tail — all present, in that order.
    assert got.index("-kernel") < got.index("-chardev") < got.index("-tpmdev") < got.index("-display")
    assert "-chardev" in got and "-tpmdev" in got
    assert got[got.index("-chardev") + 1] == "socket,id=tpmchr,path=/run/tpm.sock"
    assert got[got.index("-tpmdev") + 1] == "emulator,id=tpm0,chardev=tpmchr"


def test_golden_uefi_tpm():
    """TPM composes onto the UEFI/firmware-disk path (the PengyOS Core default)."""
    from pengyos_qemu_verify.argv import OvmfFirmware
    got = build_argv(
        "pengyos-core.img", kvm=True, uefi=True,
        ovmf=OvmfFirmware("CODE.fd", "VARS.fd"),
        tpm_socket="/run/tpm.sock", serial_log="LOG",
    )
    fw_idx = got.index("if=pflash,format=raw,unit=0,readonly=on,file=CODE.fd")
    disk_idx = got.index("file=pengyos-core.img,if=virtio,format=raw,snapshot=on")
    chr_idx = got.index("-chardev")
    disp_idx = got.index("-display")
    assert fw_idx < disk_idx < chr_idx < disp_idx
    assert got[chr_idx + 1] == "socket,id=tpmchr,path=/run/tpm.sock"
    assert "-kernel" not in got and "-append" not in got


@pytest.mark.parametrize("kvm", [True, False])
def test_tpm_custom_ids_through_build_argv(kvm):
    got = build_argv(
        "d.img", kvm=kvm, tpm_socket="/run/tpm.sock",
        tpm_chardev_id="c1", tpm_dev_id="t9",
    )
    assert "-chardev" in got and "-tpmdev" in got
    assert got[got.index("-chardev") + 1] == "socket,id=c1,path=/run/tpm.sock"
    assert got[got.index("-tpmdev") + 1] == "emulator,id=t9,chardev=c1"


@pytest.mark.parametrize("kvm", [True, False])
def test_tpm_defaults_match_constants(kvm):
    got = build_argv("d.img", kvm=kvm, tpm_socket="/run/tpm.sock")
    assert got[got.index("-chardev") + 1] == \
        f"socket,id={TPM_CHARDEV_ID},path=/run/tpm.sock"
    assert got[got.index("-tpmdev") + 1] == \
        f"emulator,id={TPM_DEV_ID},chardev={TPM_CHARDEV_ID}"


# ==========================================================================
# tpm.build_swtpm_argv — the swtpm socket command line
# ==========================================================================


def test_build_swtpm_argv_legacy_full_golden():
    """The pre-5.2 wiring: the QEMU-facing socket is the raw data channel
    (--server), with a separate optional --ctrl control socket."""
    got = build_swtpm_argv(
        "/run/tpm.sock", "/run/tpm.ctrl", "/run/state",
        swtpm_bin="swtpm", pidfile="/run/swtpm.pid",
        protocol=PROTOCOL_LEGACY,
    )
    assert got == [
        "swtpm", "socket", "--tpm2",
        "--server", "type=unixio,path=/run/tpm.sock",
        "--ctrl", "type=unixio,path=/run/tpm.ctrl",
        "--tpmstate", "dir=/run/state",
        "--pid", "file=/run/swtpm.pid",
    ]


def test_build_swtpm_argv_control_golden():
    """The QEMU >= 5.2 wiring (the default): the QEMU-facing socket IS the
    PTM control channel; swtpm gets --ctrl and NO --server (QEMU passes its
    own data fd via CMD_SET_DATAFD)."""
    got = build_swtpm_argv(
        "/run/tpm.sock",
        state_dir="/run/state",
        swtpm_bin="swtpm", pidfile="/run/swtpm.pid",
        protocol=PROTOCOL_CONTROL,
    )
    assert got == [
        "swtpm", "socket", "--tpm2",
        "--ctrl", "type=unixio,path=/run/tpm.sock",
        "--tpmstate", "dir=/run/state",
        "--pid", "file=/run/swtpm.pid",
    ]
    assert "--server" not in got


def test_build_swtpm_argv_default_is_control_protocol():
    """Every modern QEMU (>= 5.2, i.e. all releases since 2022) needs the
    control wiring, so it is the builder's default."""
    got = build_swtpm_argv("/run/tpm.sock")
    assert got == [
        "swtpm", "socket", "--tpm2",
        "--ctrl", "type=unixio,path=/run/tpm.sock",
    ]
    assert "--server" not in got
    assert "--tpmstate" not in got and "--pid" not in got


def test_build_swtpm_argv_control_rejects_second_ctrl():
    """Under the control protocol the QEMU-facing socket IS the control
    channel, so a separate ctrl_path is meaningless and must be rejected."""
    with pytest.raises(ValueError):
        build_swtpm_argv(
            "/run/tpm.sock", "/run/tpm.ctrl", protocol=PROTOCOL_CONTROL,
        )


def test_build_swtpm_argv_legacy_omits_ctrl_when_none():
    got = build_swtpm_argv("/run/tpm.sock", protocol=PROTOCOL_LEGACY)
    assert got == [
        "swtpm", "socket", "--tpm2",
        "--server", "type=unixio,path=/run/tpm.sock",
    ]
    assert "--ctrl" not in got


def test_build_swtpm_argv_extra_appended_verbatim():
    got = build_swtpm_argv(
        "/run/tpm.sock", extra=("--daemon", "--flags", "not-need-init"),
    )
    assert got[-3:] == ["--daemon", "--flags", "not-need-init"]


def test_build_swtpm_argv_uses_injected_bin():
    got = build_swtpm_argv("/s", swtpm_bin="/usr/local/bin/swtpm")
    assert got[0] == "/usr/local/bin/swtpm"


def test_build_swtpm_argv_rejects_empty_socket():
    with pytest.raises(ValueError):
        build_swtpm_argv("")


def test_build_swtpm_argv_rejects_empty_bin():
    with pytest.raises(ValueError):
        build_swtpm_argv("/s", swtpm_bin="")


def test_build_swtpm_argv_rejects_unknown_protocol():
    with pytest.raises(ValueError):
        build_swtpm_argv("/s", protocol="bogus")


# ==========================================================================
# QEMU-version protocol detection (the PENGYOS_IT real-TPM-boot fix)
# ==========================================================================

@pytest.fixture()
def fake_qemu(tmp_path):
    """Factory: write a fake QEMU script printing ``version_line`` on --version."""
    def _make(version_line: "str | None", name="fake-qemu") -> str:
        return _write_fake(tmp_path, name + ".py", _write_qemu_body(version_line))
    return _make


def _write_qemu_body(version_line: "str | None") -> str:
    head = "#!/usr/bin/env python3\nimport sys\n"
    if version_line is None:
        # Non-zero exit: an unparseable/unrunnable binary.
        return head + "sys.exit(1)\n"
    return head + f"print({version_line!r})\n"


def test_qemu_version_tuple_parses_debian_version(fake_qemu):
    qemu = fake_qemu(
        "QEMU emulator version 10.2.1 (Debian 1:10.2.1+ds-1ubuntu3.2)"
    )
    assert qemu_version_tuple(qemu) == (10, 2, 1)


def test_qemu_version_tuple_parses_bare_version(fake_qemu):
    qemu = fake_qemu("QEMU emulator version 4.2.0")
    assert qemu_version_tuple(qemu) == (4, 2, 0)


def test_qemu_version_tuple_two_part(fake_qemu):
    qemu = fake_qemu("QEMU emulator version 5.2")
    assert qemu_version_tuple(qemu) == (5, 2)


def test_qemu_version_tuple_no_version_string(fake_qemu):
    qemu = fake_qemu("gibberish output without a version")
    assert qemu_version_tuple(qemu) is None


def test_qemu_version_tuple_nonzero_exit_is_none(fake_qemu):
    qemu = fake_qemu(None)
    assert qemu_version_tuple(qemu) is None


def test_qemu_version_tuple_missing_binary_is_none():
    # Never raises: a missing binary is an unparseable version, full stop.
    assert qemu_version_tuple(str("/nonexistent/qemu-system-x86_64-xyz")) is None


def test_qemu_version_tuple_default_probes_path():
    """With binary=None the default QEMU name is probed on PATH; the result
    is either a real tuple (QEMU installed) or None (not installed) — the
    function must never raise either way."""
    assert DEFAULT_QEMU_BIN == "qemu-system-x86_64"
    got = qemu_version_tuple(None)
    assert got is None or (isinstance(got, tuple) and all(isinstance(p, int) for p in got))


@pytest.mark.parametrize("version,expected", [
    ((4, 2, 0), PROTOCOL_LEGACY),
    ((5, 1, 0), PROTOCOL_LEGACY),
    ((5, 1), PROTOCOL_LEGACY),
    ((5, 2), PROTOCOL_CONTROL),
    ((5, 2, 0), PROTOCOL_CONTROL),
    ((6, 0, 0), PROTOCOL_CONTROL),
    ((10, 2, 1), PROTOCOL_CONTROL),
])
def test_protocol_for_qemu_version_boundaries(version, expected):
    assert protocol_for_qemu_version(version) == expected


def test_protocol_for_qemu_version_unknown_defaults_to_control():
    """An undetectable version must pick the modern wiring: every QEMU
    release since 2022 is >= 5.2 (the legacy wiring deadlocks on it)."""
    assert protocol_for_qemu_version(None) == PROTOCOL_CONTROL


def test_resolve_tpm_protocol_auto_probes_binary(fake_qemu):
    old = fake_qemu("QEMU emulator version 4.2.0", name="qemu-old")
    new = fake_qemu("QEMU emulator version 9.1.50", name="qemu-new")
    assert resolve_tpm_protocol("auto", qemu_bin=old) == PROTOCOL_LEGACY
    assert resolve_tpm_protocol("auto", qemu_bin=new) == PROTOCOL_CONTROL


def test_resolve_tpm_protocol_auto_unknown_binary_defaults_to_control():
    assert resolve_tpm_protocol(
        "auto", qemu_bin="/nonexistent/qemu-xyz"
    ) == PROTOCOL_CONTROL


@pytest.mark.parametrize("p", [PROTOCOL_LEGACY, PROTOCOL_CONTROL])
def test_resolve_tpm_protocol_explicit_passes_through(p):
    # An explicit choice never probes anything (no binary needed).
    assert resolve_tpm_protocol(p, qemu_bin=None) == p


def test_resolve_tpm_protocol_bad_value_raises():
    with pytest.raises(ValueError):
        resolve_tpm_protocol("bogus", qemu_bin=None)


# ==========================================================================
# tpm.Swtpm — the protocol selects the rendered wiring
# ==========================================================================


def test_swtpm_default_protocol_is_control_and_no_separate_ctrl(tmp_path):
    t = Swtpm(str(tmp_path / "w"))
    assert t.protocol == PROTOCOL_CONTROL
    assert t.ctrl_path is None  # the QEMU-facing socket IS the control channel
    assert "--server" not in t.argv
    assert t.argv[t.argv.index("--ctrl") + 1] == f"type=unixio,path={t.socket_path}"


def test_swtpm_legacy_protocol_keeps_server_and_optional_ctrl(tmp_path):
    t = Swtpm(str(tmp_path / "w"), protocol=PROTOCOL_LEGACY)
    assert t.protocol == PROTOCOL_LEGACY
    assert t.ctrl_path is not None  # use_ctrl=True default under legacy
    assert t.argv[t.argv.index("--server") + 1] == f"type=unixio,path={t.socket_path}"
    assert t.argv[t.argv.index("--ctrl") + 1] == f"type=unixio,path={t.ctrl_path}"


def test_swtpm_rejects_unknown_protocol(tmp_path):
    with pytest.raises(ValueError):
        Swtpm(str(tmp_path / "w"), protocol="bogus")


# ==========================================================================
# tpm.Swtpm — process lifecycle against a fake swtpm
# ==========================================================================


def test_swtpm_start_open_socket_and_is_running(tmp_path):
    fake = _write_fake(tmp_path, "swtpm-ok.py", FAKE_READY)
    t = Swtpm(str(tmp_path / "w"), swtpm_bin=fake, ready_timeout=10.0)
    sock = t.start()
    try:
        assert sock == t.socket_path
        assert t.running is True
        assert os.path.exists(sock)
        # The socket is a *live* listener, not just a file: a connect must succeed.
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(sock)
        finally:
            client.close()
    finally:
        t.stop()


def test_swtpm_stop_kills_process_and_cleans_socket(tmp_path):
    fake = _write_fake(tmp_path, "swtpm-ok.py", FAKE_READY)
    t = Swtpm(str(tmp_path / "w"), swtpm_bin=fake, ready_timeout=10.0)
    t.start()
    assert os.path.exists(t.socket_path)
    t.stop()
    assert t.running is False
    assert not os.path.exists(t.socket_path)
    # stop is idempotent — a second call is a no-op, not an error.
    t.stop()
    assert t.running is False


def test_swtpm_context_manager_starts_and_stops(tmp_path):
    fake = _write_fake(tmp_path, "swtpm-ok.py", FAKE_READY)
    with Swtpm(str(tmp_path / "w"), swtpm_bin=fake, ready_timeout=10.0) as t:
        assert t.running is True
        assert os.path.exists(t.socket_path)
    assert t.running is False
    assert not os.path.exists(t.socket_path)


def test_swtpm_double_start_raises(tmp_path):
    fake = _write_fake(tmp_path, "swtpm-ok.py", FAKE_READY)
    t = Swtpm(str(tmp_path / "w"), swtpm_bin=fake, ready_timeout=10.0)
    t.start()
    try:
        with pytest.raises(SwtpmError):
            t.start()
    finally:
        t.stop()


def test_swtpm_missing_binary_raises(tmp_path):
    t = Swtpm(
        str(tmp_path / "w"),
        swtpm_bin=str(tmp_path / "does-not-exist-xyz"),
        ready_timeout=5.0,
    )
    with pytest.raises(SwtpmError):
        t.start()
    assert t.running is False


def test_swtpm_early_exit_raises_start_error(tmp_path):
    fake = _write_fake(tmp_path, "swtpm-dead.py", FAKE_DEAD)
    t = Swtpm(str(tmp_path / "w"), swtpm_bin=fake, ready_timeout=10.0)
    with pytest.raises(SwtpmStartError):
        t.start()
    # A failed start must have torn the process down, not left it running.
    assert t.running is False


def test_swtpm_never_ready_times_out(tmp_path):
    fake = _write_fake(tmp_path, "swtpm-never.py", FAKE_NEVER)
    t = Swtpm(
        str(tmp_path / "w"), swtpm_bin=fake,
        ready_timeout=0.4, poll_interval=0.02, stop_timeout=3.0,
    )
    with pytest.raises(SwtpmStartError):
        t.start()
    # On timeout the process is killed and the (absent) socket cleaned up.
    assert t.running is False
    assert not os.path.exists(t.socket_path)


def test_swtpm_argv_is_injected_fake(tmp_path):
    """The rendered swtpm argv uses the injected binary as argv[0]."""
    fake = _write_fake(tmp_path, "swtpm-ok.py", FAKE_READY)
    t = Swtpm(str(tmp_path / "w"), swtpm_bin=fake)
    assert t.argv[0] == fake
    assert t.argv[1] == "socket"
    # The default (control) wiring: the QEMU-facing socket is --ctrl.
    assert "--server" not in t.argv
    ctrl_val = t.argv[t.argv.index("--ctrl") + 1]
    assert ctrl_val == f"type=unixio,path={t.socket_path}"


def test_swtpm_no_ctrl_when_disabled(tmp_path):
    # use_ctrl only has meaning under the legacy wiring (a separate
    # control socket alongside the --server datapath).
    t = Swtpm(str(tmp_path / "w"), protocol=PROTOCOL_LEGACY, use_ctrl=False)
    assert t.ctrl_path is None
    assert "--ctrl" not in t.argv
    assert "--server" in t.argv


def test_swtpm_creates_state_dir(tmp_path):
    fake = _write_fake(tmp_path, "swtpm-ok.py", FAKE_READY)
    workdir = str(tmp_path / "w")
    t = Swtpm(workdir, swtpm_bin=fake, ready_timeout=10.0)
    t.start()
    try:
        assert os.path.isdir(t.state_dir)
    finally:
        t.stop()


def test_find_swtpm_bin_returns_str_or_none():
    """Never raises; returns a str path when present, else None."""
    got = tpm_mod.find_swtpm_bin()
    assert got is None or isinstance(got, str)
    # The constant default is the bare "swtpm".
    assert SWTPM_BIN == "swtpm"
