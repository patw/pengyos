"""Tests for QEMU argv construction.

Golden-string coverage for the KVM/no-KVM × kernel/no-kernel matrix (the
UEFI/TPM columns of the full spec matrix land with items 3 and 4), plus the
``/dev/kvm`` capability probe and input validation. No real QEMU, no KVM,
no I/O required: the accelerator is pinned via the ``kvm`` parameter and the
device probe is exercised against temp files.
"""

import os

import pytest

from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify.argv import (
    BARE_METAL_APPEND,
    DEFAULT_QEMU_BIN,
    DEFAULT_SERIAL_LOG,
    build_argv,
    kvm_available,
)


def has_pair(got, flag, value):
    """Assert ``flag`` is present and immediately followed by ``value``."""
    idx = got.index(flag)
    assert got[idx + 1] == value, (got, flag, value)


# --- goldens: the KVM × kernel matrix ---------------------------------------

def test_golden_kvm_baremetal_matches_reference():
    """The KVM bare-metal argv must match the known-good reference argv
    (captured on the build host), plus the harness-mandated ``-no-reboot``."""
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


def test_golden_nokvm_baremetal_uses_cpu_max_without_enable_kvm():
    got = build_argv("rootfs.ext4", kernel="bzImage", serial_log="LOG", kvm=False)
    assert got == [
        "qemu-system-x86_64",
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-cpu", "max",
        "-kernel", "bzImage",
        "-drive", "file=rootfs.ext4,if=virtio,format=raw,snapshot=on",
        "-append", "root=/dev/vda rw console=ttyS0",
        "-display", "none",
        "-serial", "file:LOG",
        "-monitor", "none",
        "-no-reboot",
    ]
    assert "-enable-kvm" not in got
    assert "host" not in got  # -cpu must be max, not host, without KVM


def test_golden_kvm_disk_boot_without_kernel():
    """No --kernel ⇒ firmware/disk boot: the image is the virtio drive and no
    -kernel/-append is emitted (PengyOS images boot via their own ESP/GRUB2)."""
    got = build_argv("/images/pengyos-core-x86_64.img", serial_log="LOG", kvm=True)
    assert got == [
        "qemu-system-x86_64",
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-enable-kvm",
        "-cpu", "host",
        "-drive", "file=/images/pengyos-core-x86_64.img,if=virtio,format=raw,snapshot=on",
        "-display", "none",
        "-serial", "file:LOG",
        "-monitor", "none",
        "-no-reboot",
    ]
    assert "-kernel" not in got
    assert "-append" not in got


def test_golden_nokvm_disk_boot():
    got = build_argv("disk.img", kvm=False)
    assert got == [
        DEFAULT_QEMU_BIN,
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-cpu", "max",
        "-drive", "file=disk.img,if=virtio,format=raw,snapshot=on",
        "-display", "none",
        "-serial", f"file:{DEFAULT_SERIAL_LOG}",
        "-monitor", "none",
        "-no-reboot",
    ]


# --- accelerator selection ---------------------------------------------------


def test_kvm_pin_true_wins_over_probe(monkeypatch):
    """Explicit kvm=True must not consult /dev/kvm at all (CI pinning)."""
    def _boom(path=argv_mod.KVM_DEVICE):
        raise AssertionError("probe must not run when kvm is pinned")

    monkeypatch.setattr(argv_mod, "kvm_available", _boom)
    got = build_argv("d.img", kvm=True)
    assert "-enable-kvm" in got and "host" in got


def test_kvm_pin_false_wins_over_probe(monkeypatch):
    def _boom(path=argv_mod.KVM_DEVICE):
        raise AssertionError("probe must not run when kvm is pinned")

    monkeypatch.setattr(argv_mod, "kvm_available", _boom)
    got = build_argv("d.img", kvm=False)
    assert "-enable-kvm" not in got
    assert "max" in got


def test_kvm_none_probes_device(monkeypatch):
    calls = []

    def fake_probe(path=argv_mod.KVM_DEVICE):
        calls.append(path)
        return True

    monkeypatch.setattr(argv_mod, "kvm_available", fake_probe)
    got = build_argv("d.img")  # kvm=None → auto-detect
    assert calls == ["/dev/kvm"]
    assert "-enable-kvm" in got


# --- the /dev/kvm probe itself -----------------------------------------------


def test_kvm_available_true_for_writable_file(tmp_path):
    dev = tmp_path / "kvm"
    dev.write_bytes(b"")
    assert kvm_available(str(dev)) is True


def test_kvm_available_false_for_nonexistent_path(tmp_path):
    assert kvm_available(str(tmp_path / "nope")) is False


def test_kvm_available_false_for_unwritable_file(tmp_path):
    dev = tmp_path / "kvm"
    dev.write_bytes(b"")
    dev.chmod(0o444)
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root bypasses file permission bits")
        assert kvm_available(str(dev)) is False
    finally:
        dev.chmod(0o644)  # leave tmp_path clean for teardown


# --- input validation ---------------------------------------------------------


def test_ram_and_smp_are_rendered():
    got = build_argv("d.img", ram=1024, smp=4, kvm=True)
    has_pair(got, "-m", "1024")
    has_pair(got, "-smp", "4")


def test_ram_is_coerced_from_str():
    got = build_argv("d.img", ram="256", kvm=True)
    has_pair(got, "-m", "256")


@pytest.mark.parametrize("bad_ram", [0, -1, "0", -512])
def test_ram_must_be_positive(bad_ram):
    with pytest.raises(ValueError):
        build_argv("d.img", ram=bad_ram)


@pytest.mark.parametrize("bad_smp", [0, -2, "0"])
def test_smp_must_be_positive(bad_smp):
    with pytest.raises(ValueError):
        build_argv("d.img", smp=bad_smp)


@pytest.mark.parametrize("bad", [None, "x"])
def test_ram_and_smp_must_be_integers(bad):
    with pytest.raises(ValueError):
        build_argv("d.img", ram=bad)
    with pytest.raises(ValueError):
        build_argv("d.img", smp=bad)


def test_empty_image_rejected():
    with pytest.raises(ValueError):
        build_argv("")


def test_empty_qemu_bin_rejected():
    with pytest.raises(ValueError):
        build_argv("d.img", qemu_bin="")


def test_empty_serial_log_rejected():
    with pytest.raises(ValueError):
        build_argv("d.img", serial_log="")


def test_custom_qemu_bin_and_serial_log():
    got = build_argv("d.img", qemu_bin="/opt/qemu/bin/qemu-system-x86_64",
                     serial_log="/tmp/log.txt")
    assert got[0] == "/opt/qemu/bin/qemu-system-x86_64"
    has_pair(got, "-serial", "file:/tmp/log.txt")


# --- fixed-contract invariants -------------------------------------------------


def test_append_is_the_documented_golden_string():
    """The exact -append string is a fixed contract (recorded in spec.md)."""
    assert BARE_METAL_APPEND == "root=/dev/vda rw console=ttyS0"


@pytest.mark.parametrize("kvm", [True, False])
@pytest.mark.parametrize("kernel", ["bzImage", None])
def test_headless_tail_always_present(kvm, kernel):
    got = build_argv("d.img", kernel=kernel, kvm=kvm)
    # The headless tail is always present, in this order (7 tokens).
    assert got[-7:] == [
        "-display", "none",
        "-serial", f"file:{DEFAULT_SERIAL_LOG}",
        "-monitor", "none",
        "-no-reboot",
    ]
    # q35 machine type is always fixed.
    assert got[1:3] == ["-M", "q35"]


def test_image_existence_is_not_checked():
    """build_argv is a pure string builder: probe/--dry-run must work for
    not-yet-built images, so existence checks belong to the boot subtask."""
    got = build_argv("does/not/exist.img", kvm=False)
    assert got[0] == DEFAULT_QEMU_BIN


# --- host port forwarding (--hostfwd) ---------------------------------------

def test_no_hostfwd_emits_no_nic():
    """Without hostfwd the argv keeps QEMU's implicit default NIC (no -nic)."""
    got = build_argv("img", kvm=False)
    assert "-nic" not in got


def test_hostfwd_emits_nic_user_spec():
    got = build_argv("img", kvm=False, hostfwd=["tcp::2222-:22"])
    has_pair(got, "-nic", "user,hostfwd=tcp::2222-:22")
    assert got.index("-nic") < got.index("-display")


def test_hostfwd_multiple_specs_combine():
    got = build_argv("img", kvm=False, hostfwd=["tcp::2222-:22", "tcp::8080-:80"])
    has_pair(got, "-nic", "user,hostfwd=tcp::2222-:22,hostfwd=tcp::8080-:80")


def test_hostfwd_empty_spec_rejected():
    with pytest.raises(ValueError):
        build_argv("img", kvm=False, hostfwd=[""])
