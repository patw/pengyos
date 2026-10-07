"""Tests for UEFI/OVMF firmware support.

Covers the firmware *locator* (``locate_ovmf`` + :class:`OvmfFirmware`), the
UEFI *wiring* inside ``build_argv`` (split pflash drives vs combined ``-bios``),
and the kernel/UEFI mutual-exclusion rule. No real firmware, no real QEMU, no
network: candidate firmware files are created under ``tmp_path`` and passed via
the ``search=`` / ``ovmf=`` parameters or a monkeypatched environment.
"""

import pytest

from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify.argv import (
    DEFAULT_SERIAL_LOG,
    OvmfFirmware,
    OvmfNotFound,
    build_argv,
    locate_ovmf,
)


def has_pair(got, flag, value):
    idx = got.index(flag)
    assert got[idx + 1] == value, (got, flag, value)


def has_drive(got, value):
    """Assert some `-drive` flag is immediately followed by ``value`` (any
    occurrence, since a UEFI argv carries several `-drive` tokens)."""
    for i, tok in enumerate(got):
        if tok == "-drive" and i + 1 < len(got) and got[i + 1] == value:
            return
    raise AssertionError(f"no -drive with value {value!r} in {got}")


# ==========================================================================
# locate_ovmf — resolution order
# ==========================================================================


def test_locate_explicit_path_wins(tmp_path, monkeypatch):
    """An explicit path is used as-is and no env/candidates are consulted."""
    code = tmp_path / "mycode.fd"
    code.write_bytes(b"x")
    monkeypatch.setenv("PENGYOS_OVMF", "/should/not/be/tried.fd")
    fw = locate_ovmf(str(code))
    assert fw.code == str(code)
    assert fw.vars is None  # no sibling VARS file -> combined


def test_locate_explicit_missing_path_raises(tmp_path):
    with pytest.raises(OvmfNotFound) as exc:
        locate_ovmf(str(tmp_path / "nope.fd"))
    # The attempted list carries the explicit path for an actionable message.
    assert exc.value.attempted == [str(tmp_path / "nope.fd")]


def test_locate_env_pengyos_ovmf_takes_priority(tmp_path):
    hi = tmp_path / "hi.fd"
    lo = tmp_path / "lo.fd"
    hi.write_bytes(b"x")
    lo.write_bytes(b"x")
    fw = locate_ovmf(
        env={"PENGYOS_OVMF": str(hi), "OVMF": str(lo)},
        search=(),  # empty search: only env can succeed
    )
    assert fw.code == str(hi)


def test_locate_env_ovmf_used_when_pengyos_unset(tmp_path):
    lo = tmp_path / "lo.fd"
    lo.write_bytes(b"x")
    fw = locate_ovmf(env={"OVMF": str(lo)}, search=())
    assert fw.code == str(lo)


def test_locate_ignores_blank_env(tmp_path):
    """A blank/whitespace env var is skipped in favour of the candidate search."""
    cand = tmp_path / "cand.fd"
    cand.write_bytes(b"x")
    fw = locate_ovmf(
        env={"PENGYOS_OVMF": "   ", "OVMF": ""},
        search=(str(cand),),
    )
    assert fw.code == str(cand)


def test_locate_env_nonexistent_falls_through_to_search(tmp_path):
    """A set-but-missing env path is skipped, not fatal."""
    cand = tmp_path / "cand.fd"
    cand.write_bytes(b"x")
    fw = locate_ovmf(env={"OVMF": str(tmp_path / "missing.fd")}, search=(str(cand),))
    assert fw.code == str(cand)


def test_locate_search_first_existing_wins(tmp_path):
    a = tmp_path / "a.fd"
    b = tmp_path / "b.fd"
    b.write_bytes(b"x")  # only the second exists
    missing = tmp_path / "m.fd"
    fw = locate_ovmf(search=(str(missing), str(b)))
    assert fw.code == str(b)


def test_locate_search_no_match_raises_with_attempted(tmp_path):
    m1 = tmp_path / "x1.fd"
    m2 = tmp_path / "x2.fd"
    with pytest.raises(OvmfNotFound) as exc:
        locate_ovmf(search=(str(m1), str(m2)))
    assert exc.value.attempted == [str(m1), str(m2)]
    assert "OVMF" in str(exc.value)  # message is actionable


def test_locate_default_candidates_uses_real_paths(monkeypatch):
    """With no arg/env and default candidates, a real /usr/share path is found
    only if present on the host; otherwise OvmfNotFound. Either way the attempt
    list must be the full DEFAULT_OVMF_CANDIDATES tuple."""
    monkeypatch.delenv("PENGYOS_OVMF", raising=False)
    monkeypatch.delenv("OVMF", raising=False)
    try:
        fw = locate_ovmf()
        assert fw.code in argv_mod.DEFAULT_OVMF_CANDIDATES
    except OvmfNotFound as exc:
        assert exc.attempted == list(argv_mod.DEFAULT_OVMF_CANDIDATES)


# ==========================================================================
# locate_ovmf — split vs combined detection
# ==========================================================================


def test_locate_split_pairs_sibling_vars(tmp_path):
    code = tmp_path / "OVMF_CODE.fd"
    vars_ = tmp_path / "OVMF_VARS.fd"
    code.write_bytes(b"C")
    vars_.write_bytes(b"V")
    fw = locate_ovmf(search=(str(code),))
    assert fw.code == str(code)
    assert fw.vars == str(vars_)
    assert fw.combined is False


def test_locate_split_pairs_4m_sibling_vars(tmp_path):
    code = tmp_path / "OVMF_CODE_4M.fd"
    vars_ = tmp_path / "OVMF_VARS_4M.fd"
    code.write_bytes(b"C")
    vars_.write_bytes(b"V")
    fw = locate_ovmf(search=(str(code),))
    assert fw.vars == str(vars_)
    assert fw.combined is False


def test_locate_lone_combined_is_combined(tmp_path):
    """A lone OVMF.fd (no VARS sibling) is a combined blob -> wired via -bios."""
    code = tmp_path / "OVMF.fd"
    code.write_bytes(b"C")
    fw = locate_ovmf(search=(str(code),))
    assert fw.vars is None
    assert fw.combined is True


def test_locate_lone_4m_is_combined(tmp_path):
    """OVMF_CODE_4M.fd with *no* VARS sibling on disk is treated as combined."""
    code = tmp_path / "OVMF_CODE_4M.fd"
    code.write_bytes(b"C")  # no vars sibling present
    fw = locate_ovmf(search=(str(code),))
    assert fw.combined is True  # no sibling -> treated as combined


def test_locate_exist_false_does_not_check_disk(tmp_path):
    """exist=False returns the path as-is (argv rendering of pending installs)."""
    fake = str(tmp_path / "not/built/OVMF.fd")
    fw = locate_ovmf(fake, exist=False)
    assert fw.code == fake
    assert fw.vars is None
    assert fw.combined is True


def test_locate_exist_false_search_returns_first(tmp_path):
    fw = locate_ovmf(exist=False, search=(str(tmp_path / "a.fd"), str(tmp_path / "b.fd")))
    assert fw.code == str(tmp_path / "a.fd")


# ==========================================================================
# OvmfFirmware value object
# ==========================================================================


def test_firmware_combined_property():
    assert OvmfFirmware("c.fd").combined is True
    assert OvmfFirmware("c.fd", "v.fd").combined is False


def test_firmware_empty_code_rejected():
    with pytest.raises(ValueError):
        OvmfFirmware("")


# ==========================================================================
# build_argv — UEFI wiring (golden strings)
# ==========================================================================


def test_golden_kvm_uefi_split_pflash():
    """KVM + UEFI split firmware: two pflash drives, firmware BEFORE the disk,
    no -kernel/-append (the kernel lives on the image's ESP/GRUB2)."""
    got = build_argv(
        "/img/pengyos-core.img",
        kvm=True,
        uefi=True,
        ovmf=OvmfFirmware("/ovmf/OVMF_CODE.fd", "/tmp/v/OVMF_VARS.fd"),
        serial_log="LOG",
    )
    assert got == [
        "qemu-system-x86_64",
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-enable-kvm",
        "-cpu", "host",
        "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=/ovmf/OVMF_CODE.fd",
        "-drive", "if=pflash,format=raw,unit=1,file=/tmp/v/OVMF_VARS.fd",
        "-drive", "file=/img/pengyos-core.img,if=virtio,format=raw,snapshot=on",
        "-display", "none",
        "-serial", "file:LOG",
        "-monitor", "none",
        "-no-reboot",
    ]
    assert "-kernel" not in got
    assert "-append" not in got
    assert "-bios" not in got


def test_golden_nokvm_uefi_split_pflash():
    got = build_argv(
        "d.img", kvm=False, uefi=True,
        ovmf=OvmfFirmware("CODE.fd", "VARS.fd"), serial_log="LOG",
    )
    assert got == [
        "qemu-system-x86_64",
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-cpu", "max",
        "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=CODE.fd",
        "-drive", "if=pflash,format=raw,unit=1,file=VARS.fd",
        "-drive", "file=d.img,if=virtio,format=raw,snapshot=on",
        "-display", "none",
        "-serial", "file:LOG",
        "-monitor", "none",
        "-no-reboot",
    ]


def test_golden_kvm_uefi_combined_bios():
    """Combined single blob firmware -> -bios (not pflash)."""
    got = build_argv(
        "d.img", kvm=True, uefi=True, ovmf=OvmfFirmware("/ovmf/OVMF.fd"), serial_log="LOG",
    )
    assert got == [
        "qemu-system-x86_64",
        "-M", "q35",
        "-m", "512",
        "-smp", "2",
        "-enable-kvm",
        "-cpu", "host",
        "-bios", "/ovmf/OVMF.fd",
        "-drive", "file=d.img,if=virtio,format=raw,snapshot=on",
        "-display", "none",
        "-serial", "file:LOG",
        "-monitor", "none",
        "-no-reboot",
    ]
    assert "-drive" in got  # the boot disk is still attached
    # the -bios firmware section precedes the boot-disk virtio drive
    assert got.index("-bios") < got.index("file=d.img,if=virtio,format=raw,snapshot=on")


def test_uefi_firmware_placed_before_boot_disk():
    """The firmware section must precede the image's virtio drive."""
    got = build_argv(
        "d.img", kvm=False, uefi=True,
        ovmf=OvmfFirmware("CODE.fd", "VARS.fd"),
    )
    fw_idx = got.index("if=pflash,format=raw,unit=0,readonly=on,file=CODE.fd")
    disk_idx = got.index("file=d.img,if=virtio,format=raw,snapshot=on")
    assert fw_idx < disk_idx


def test_uefi_keeps_headless_tail():
    got = build_argv("d.img", kvm=False, uefi=True, ovmf=OvmfFirmware("C.fd", "V.fd"))
    assert got[-7:] == [
        "-display", "none",
        "-serial", f"file:{DEFAULT_SERIAL_LOG}",
        "-monitor", "none",
        "-no-reboot",
    ]


def test_ovmf_vars_override_on_firmware_object():
    got = build_argv(
        "d.img", kvm=False, uefi=True,
        ovmf=OvmfFirmware("CODE.fd", "/default/v.fd"),
        ovmf_vars="/writable/copy.fd",
    )
    has_drive(got, "if=pflash,format=raw,unit=1,file=/writable/copy.fd")


def test_ovmf_vars_override_on_plain_path(tmp_path):
    """A plain CODE path resolves to split when a sibling VARS exists;
    ovmf_vars can then redirect the VARS drive to a writable copy."""
    code = tmp_path / "OVMF_CODE.fd"
    sibling = tmp_path / "OVMF_VARS.fd"
    code.write_bytes(b"C")
    sibling.write_bytes(b"V")
    writable = tmp_path / "nvram-copy.fd"
    got = build_argv(
        "d.img", kvm=False, uefi=True,
        ovmf=str(code), ovmf_vars=str(writable),
    )
    has_drive(got, "if=pflash,format=raw,unit=0,readonly=on,file=%s" % code)
    has_drive(got, "if=pflash,format=raw,unit=1,file=%s" % writable)


def test_plain_combined_path_via_existing_file(tmp_path):
    """A plain path to a lone combined OVMF.fd (no sibling) -> -bios."""
    blob = tmp_path / "OVMF.fd"
    blob.write_bytes(b"C")
    got = build_argv("d.img", kvm=False, uefi=True, ovmf=str(blob))
    has_pair(got, "-bios", str(blob))
    assert "if=pflash" not in " ".join(got)


def test_oe_plain_missing_path_raises(tmp_path):
    """A plain (string) ovmf path that does not exist raises OvmfNotFound."""
    with pytest.raises(OvmfNotFound):
        build_argv("d.img", uefi=True, ovmf=str(tmp_path / "missing.fd"))


# --- kernel / UEFI mutual exclusion ---------------------------------------


def test_uefi_and_kernel_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_argv("d.img", kernel="bzImage", uefi=True,
                   ovmf=OvmfFirmware("C.fd", "V.fd"))


def test_kernel_without_uefi_is_unaffected():
    """The bare-metal path is unchanged when uefi is off (default)."""
    got = build_argv("d.img", kernel="bzImage", kvm=True)
    assert "-kernel" in got and "-bios" not in got


# --- default firmware lookup wiring ---------------------------------------


def test_default_ovmf_lookup_is_called(monkeypatch):
    """ovmf=None triggers locate_ovmf() for the default search."""
    calls = []

    def fake_locate(path=None, *, env=None, search=None, exist=True):
        calls.append(path)
        return OvmfFirmware("/fake/OVMF_CODE.fd", "/fake/OVMF_VARS.fd")

    monkeypatch.setattr(argv_mod, "locate_ovmf", fake_locate)
    got = build_argv("d.img", kvm=False, uefi=True)
    assert calls == [None]
    has_drive(got, "if=pflash,format=raw,unit=0,readonly=on,file=/fake/OVMF_CODE.fd")


def test_ovmf_not_found_propagates(monkeypatch):
    def fake_locate(path=None, **kw):
        raise OvmfNotFound(["/a", "/b"])

    monkeypatch.setattr(argv_mod, "locate_ovmf", fake_locate)
    with pytest.raises(OvmfNotFound):
        build_argv("d.img", kvm=False, uefi=True)


# ==========================================================================
# invariants: UEFI mode never emits bare-metal tokens; non-UEFI unchanged
# ==========================================================================


@pytest.mark.parametrize("kvm", [True, False])
def test_uefi_never_emits_kernel_or_append(kvm):
    got = build_argv("d.img", kvm=kvm, uefi=True, ovmf=OvmfFirmware("C.fd", "V.fd"))
    assert "-kernel" not in got
    assert "-append" not in got
    # firmware section present (either pflash or bios)
    assert ("-bios" in got) or any("if=pflash" in tok for tok in got)


@pytest.mark.parametrize("kvm", [True, False])
def test_non_uefi_disk_boot_unchanged(kvm):
    """Default (no uefi) disk boot still emits a plain virtio drive only."""
    got = build_argv("d.img", kvm=kvm)
    drives = [t for t in got if t.startswith("file=d.img")]
    assert drives == ["file=d.img,if=virtio,format=raw,snapshot=on"]
    assert "-bios" not in got
    assert not any("if=pflash" in t for t in got)


def test_oe_untouched_baseline_golden_still_valid():
    """Regression guard: the item-2 KVM bare-metal golden is byte-for-byte intact."""
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
