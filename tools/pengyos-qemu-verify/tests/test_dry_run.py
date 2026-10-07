"""Tests for ``--dry-run`` / ``probe``.

Covers the pure render path shared by ``probe`` and ``boot --dry-run``: the
resolved QEMU argv is printed (byte-for-byte the same for both), and — the
whole point of the item — *no side effects* happen: no QEMU is launched, no
workdir / serial-log file is created, no OVMF VARS is copied, and no swtpm is
started. No real QEMU, network, root, display, or hardware is required.
"""

import os
import shlex
import stat

from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify import cli
from pengyos_qemu_verify.cli import EXIT_FAIL, EXIT_OK, main, render_argv


def _printed_argv(out: "str") -> "list[str]":
    """Parse the argv that ``_run_probe`` printed from captured stdout.

    ``_run_probe`` prints a leading ``#`` comment line then the argv joined
    with :func:`shlex.join`; filtering the comment and ``shlex.split``-ing the
    remainder round-trips the original argv.
    """
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert len(lines) == 1, f"expected exactly one command line, got: {out!r}"
    return shlex.split(lines[0])


def _pin_kvm(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(argv_mod, "kvm_available", lambda path=argv_mod.KVM_DEVICE: value)


# --- the printed argv matches the golden build_argv ------------------------


def test_render_argv_is_the_golden_argv(monkeypatch):
    """render_argv must return exactly what a real boot would launch with."""
    _pin_kvm(monkeypatch, True)
    args = cli.build_parser().parse_args(["boot", "d.img", "--kernel", "bz", "--ram", "256"])
    got = render_argv(args)
    assert got == argv_mod.build_argv("d.img", kernel="bz", ram=256, kvm=True)
    assert got[0] == argv_mod.DEFAULT_QEMU_BIN


def test_probe_prints_disk_boot_argv(capsys, monkeypatch):
    _pin_kvm(monkeypatch, False)
    rc = main(["probe", "disk.img"])
    got = _printed_argv(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert got == argv_mod.build_argv("disk.img", kvm=False)
    assert "-kernel" not in got
    assert "-append" not in got


def test_boot_dry_run_kernel_boot(capsys, monkeypatch):
    _pin_kvm(monkeypatch, True)
    rc = main(["boot", "rootfs.ext4", "--kernel", "bzImage", "--dry-run"])
    got = _printed_argv(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert got == argv_mod.build_argv("rootfs.ext4", kernel="bzImage", kvm=True)
    assert argv_mod.BARE_METAL_APPEND in got


def test_probe_and_boot_dry_run_agree_byte_for_byte(capsys, monkeypatch):
    """The two entry points share render_argv, so their output must be equal."""
    _pin_kvm(monkeypatch, True)
    rc1 = main(["probe", "d.img", "--kernel", "bzImage", "--ram", "1024"])
    a = _printed_argv(capsys.readouterr().out)
    rc2 = main(["boot", "d.img", "--kernel", "bzImage", "--ram", "1024", "--dry-run"])
    b = _printed_argv(capsys.readouterr().out)
    assert rc1 == EXIT_OK and rc2 == EXIT_OK
    assert a == b


# --- UEFI render (both firmware shapes) ------------------------------------


def test_boot_dry_run_uefi_split_firmware(tmp_path, capsys, monkeypatch):
    """A present split firmware (CODE + VARS sibling) renders two pflash drives."""
    code = tmp_path / "OVMF_CODE_4M.fd"
    code.write_bytes(b"\x00" * 16)
    vars_ = tmp_path / "OVMF_VARS_4M.fd"
    vars_.write_bytes(b"\x00" * 16)
    monkeypatch.setenv("PENGYOS_OVMF", str(code))
    monkeypatch.delenv("OVMF", raising=False)
    _pin_kvm(monkeypatch, False)

    rc = main(["boot", "img.img", "--uefi", "--dry-run"])
    got = _printed_argv(capsys.readouterr().out)
    assert rc == EXIT_OK
    expected = argv_mod.build_argv(
        "img.img", kvm=False, uefi=True,
        ovmf=argv_mod.OvmfFirmware(str(code), str(vars_)),
    )
    assert got == expected
    assert "-kernel" not in got
    # Both pflash units must appear.
    assert any("unit=0" in t for t in got)
    assert any("unit=1" in t for t in got)


def test_boot_dry_run_uefi_combined_firmware(tmp_path, capsys, monkeypatch):
    """A lone combined blob (no VARS sibling) renders via -bios."""
    blob = tmp_path / "OVMF.fd"
    blob.write_bytes(b"\x00" * 16)
    monkeypatch.setenv("PENGYOS_OVMF", str(blob))
    monkeypatch.delenv("OVMF", raising=False)
    _pin_kvm(monkeypatch, True)

    rc = main(["probe", "img.img", "--uefi"])
    got = _printed_argv(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert "-bios" in got
    assert got[got.index("-bios") + 1] == str(blob)
    assert not any("pflash" in t for t in got)


# --- the render-only firmware fallback (missing firmware) ------------------


def test_locate_for_render_returns_real_when_present(monkeypatch):
    monkeypatch.setattr(
        argv_mod, "locate_ovmf",
        lambda *a, **k: argv_mod.OvmfFirmware("/fw/CODE", "/fw/VARS"),
    )
    fw = cli._locate_for_render(argv_mod)
    assert isinstance(fw, argv_mod.OvmfFirmware)
    assert fw.vars == "/fw/VARS"


def test_locate_for_render_falls_back_to_exist_false(monkeypatch):
    """When no firmware is present the render falls back to a representative
    path (exist=False) instead of raising — dry-run never errors on a missing
    firmware."""
    def fake(path=None, *, env=None, search=None, exist=True):
        if exist:
            raise argv_mod.OvmfNotFound(["a", "b"])
        return argv_mod.OvmfFirmware("/usr/share/ovmf/OVMF_CODE.fd")

    monkeypatch.setattr(argv_mod, "locate_ovmf", fake)
    fw = cli._locate_for_render(argv_mod)
    assert isinstance(fw, argv_mod.OvmfFirmware)
    assert fw.code == "/usr/share/ovmf/OVMF_CODE.fd"


def test_boot_dry_run_uefi_missing_firmware_still_renders(monkeypatch, capsys):
    """CLI-level: with no firmware available a dry-run still prints an argv."""
    def fake(path=None, *, env=None, search=None, exist=True):
        if exist:
            raise argv_mod.OvmfNotFound(["x"])
        return argv_mod.OvmfFirmware("/usr/share/ovmf/OVMF_CODE.fd")

    monkeypatch.setattr(argv_mod, "locate_ovmf", fake)
    monkeypatch.delenv("PENGYOS_OVMF", raising=False)
    monkeypatch.delenv("OVMF", raising=False)
    _pin_kvm(monkeypatch, False)

    rc = main(["probe", "img.img", "--uefi"])
    got = _printed_argv(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert "-bios" in got
    assert got[got.index("-bios") + 1] == "/usr/share/ovmf/OVMF_CODE.fd"


# --- TPM render (placeholder socket, no running swtpm) ---------------------


def test_probe_tpm_renders_placeholder_socket(capsys, monkeypatch):
    _pin_kvm(monkeypatch, False)
    rc = main(["probe", "disk.img", "--tpm"])
    got = _printed_argv(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert "-chardev" in got
    assert got[got.index("-chardev") + 1] == "socket,id=tpmchr,path=tpm.sock"
    assert got[got.index("-tpmdev") + 1] == "emulator,id=tpm0,chardev=tpmchr"
    # The guest must actually get a TPM device (0.3.0 attached only the backend).
    assert got[got.index("-device", got.index("-tpmdev")) + 1] == "tpm-tis,tpmdev=tpm0"


# --- error handling ---------------------------------------------------------


def test_dry_run_uefi_and_kernel_conflict_is_a_failure(capsys, monkeypatch):
    """--uefi and --kernel are mutually exclusive; the dry-run reports it
    (exit 1) rather than crashing."""
    _pin_kvm(monkeypatch, False)
    rc = main(["boot", "d.img", "--uefi", "--kernel", "bz", "--dry-run"])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "could not resolve argv" in err


# --- output shape -----------------------------------------------------------


def test_dry_run_prints_comment_header_then_command(capsys, monkeypatch):
    _pin_kvm(monkeypatch, False)
    main(["probe", "d.img"])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0].startswith("# pengyos-qemu-verify")
    assert len(lines) == 2
    assert lines[1].startswith("qemu-system-x86_64")


# --- the flag is registered -------------------------------------------------


def test_dry_run_flag_registered_on_boot():
    args = cli.build_parser().parse_args(["boot", "d.img"])
    assert args.dry_run is False
    args2 = cli.build_parser().parse_args(["boot", "d.img", "--dry-run"])
    assert args2.dry_run is True


# --- NO SIDE EFFECTS (the crux of the item) --------------------------------


def test_no_side_effects(tmp_path, monkeypatch, capsys):
    """A dry-run must launch no QEMU and create no files or workdirs.

    A fake ``qemu-system-x86_64`` is placed on PATH that would write a
    sentinel file if it were ever launched. Running ``probe`` and
    ``boot --dry-run`` in an empty working directory must leave the sentinel,
    a serial log, and any per-boot workdir absent.
    """
    fakedir = tmp_path / "fakedir"
    fakedir.mkdir()
    fake = fakedir / "qemu-system-x86_64"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "open(os.environ['PENGYOS_SENTINEL'], 'w').write('launched')\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    sentinel = tmp_path / "qemu-was-launched"
    monkeypatch.setenv("PATH", str(fakedir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("PENGYOS_SENTINEL", str(sentinel))
    _pin_kvm(monkeypatch, False)

    cwd = tmp_path / "run"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    rc1 = main(["probe", "disk.img"])
    rc2 = main(["boot", "disk.img", "--dry-run"])
    assert rc1 == EXIT_OK and rc2 == EXIT_OK

    assert not sentinel.exists(), "QEMU was launched during a dry-run"
    assert not (cwd / "pengyos-serial.log").exists(), "a serial log file was created"
    assert not list(cwd.glob("pengyos-verify-*")), "a per-boot workdir was created"
    # And the only observable effect is the stdout text, nothing on disk.
    assert [p.name for p in cwd.iterdir()] == []
