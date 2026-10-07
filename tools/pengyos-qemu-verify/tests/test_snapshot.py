"""Artifact-safety tests: a verification boot must not mutate the image.

Regression guard for the 2026-09-14 finding. The harness attached every guest
disk read-write (``-drive file=IMAGE,if=virtio,format=raw``), and the guest
re-mounts the ext4 root r/w and writes (journal, logs, RNG seed), so **every
verification boot rewrote the artifact under test** — the published image went
``fa4423cb…`` → ``29cf366f…`` after our own boots and the ``sha256sum -c`` users
are told to run started failing.

The disk is now attached through a QEMU throwaway overlay (``snapshot=on``), so
the backing file is opened read-only and the guest's writes go nowhere.
``--no-snapshot`` is the deliberate write-through escape hatch for boots that
must persist guest state.

These tests are pure (no QEMU): they assert the rendered argv and the plumbing
through :func:`boot.boot_image`. The behavioural proof — hashing a real image
before and after a real boot — lives in ``test_integration.py`` behind
``PENGYOS_IT=1``, because it needs QEMU.
"""

import pytest

from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify import cli
from pengyos_qemu_verify.boot import boot_image

FAKE_FIRMWARE = "/usr/share/OVMF/OVMF_CODE.fd"


def _image(tmp_path, name="disk.img"):
    path = tmp_path / name
    path.write_bytes(b"\x00" * 4096)
    return str(path)


def _disk_drive(argv):
    """The single guest-disk ``-drive`` value in ``argv``."""
    drives = [a for a in argv if a.startswith("file=") and "if=virtio" in a]
    assert len(drives) == 1, drives
    return drives[0]


# --- argv rendering (all three boot modes) ----------------------------------


def test_seabios_disk_boot_snapshots_by_default(tmp_path):
    image = _image(tmp_path)
    argv = argv_mod.build_argv(image, kvm=False)
    assert _disk_drive(argv) == f"file={image},if=virtio,format=raw,snapshot=on"


def test_uefi_disk_boot_snapshots_by_default(tmp_path):
    image = _image(tmp_path)
    argv = argv_mod.build_argv(
        image, kvm=False, uefi=True, ovmf=argv_mod.OvmfFirmware(FAKE_FIRMWARE)
    )
    assert _disk_drive(argv) == f"file={image},if=virtio,format=raw,snapshot=on"


def test_bare_metal_boot_snapshots_by_default(tmp_path):
    image = _image(tmp_path)
    argv = argv_mod.build_argv(image, kvm=False, kernel="/boot/bzImage")
    assert _disk_drive(argv) == f"file={image},if=virtio,format=raw,snapshot=on"


@pytest.mark.parametrize("mode", ["seabios", "uefi", "kernel"])
def test_snapshot_can_be_turned_off(tmp_path, mode):
    image = _image(tmp_path)
    kwargs = {"kvm": False, "snapshot": False}
    if mode == "uefi":
        kwargs.update(uefi=True, ovmf=argv_mod.OvmfFirmware(FAKE_FIRMWARE))
    elif mode == "kernel":
        kwargs["kernel"] = "/boot/bzImage"
    argv = argv_mod.build_argv(image, **kwargs)
    assert _disk_drive(argv) == f"file={image},if=virtio,format=raw"


def test_snapshot_does_not_disturb_the_rest_of_the_argv(tmp_path):
    """Only the guest disk changes: the rest of the reference argv is byte-equal."""
    image = _image(tmp_path)
    snap = argv_mod.build_argv(image, kvm=False)
    plain = argv_mod.build_argv(image, kvm=False, snapshot=False)
    assert len(snap) == len(plain)
    for a, b in zip(snap, plain):
        if a == b:
            continue
        assert a == f"file={image},if=virtio,format=raw,snapshot=on"
        assert b == f"file={image},if=virtio,format=raw"


def test_readonly_pflash_code_is_never_snapshotted(tmp_path):
    """The firmware CODE pflash is already read-only; snapshot is a disk concept."""
    image = _image(tmp_path)
    argv = argv_mod.build_argv(
        image,
        kvm=False,
        uefi=True,
        ovmf=argv_mod.OvmfFirmware(FAKE_FIRMWARE, "/tmp/vars.fd"),
    )
    pflash = [a for a in argv if a.startswith("if=pflash")]
    assert pflash, "expected the split CODE/VARS pflash wiring"
    assert all("snapshot=on" not in a for a in pflash)


# --- boot_image plumbing ----------------------------------------------------


def _boot_without_qemu(tmp_path, **kwargs):
    """Run boot_image with a missing qemu binary: it builds the argv, fails to
    launch, and returns a BootResult — no QEMU needed."""
    return boot_image(
        _image(tmp_path),
        kvm=False,
        qemu_bin=str(tmp_path / "definitely-not-qemu"),
        **kwargs,
    )


def test_boot_image_defaults_to_snapshot(tmp_path):
    result = _boot_without_qemu(tmp_path)
    assert result.error and "not found" in result.error
    assert result.snapshot is True
    assert "snapshot=on" in _disk_drive(result.argv)


def test_boot_image_honours_snapshot_false(tmp_path):
    result = _boot_without_qemu(tmp_path, snapshot=False)
    assert result.snapshot is False
    assert "snapshot=on" not in _disk_drive(result.argv)


def test_boot_result_reports_snapshot_in_json(tmp_path):
    result = _boot_without_qemu(tmp_path)
    assert result.to_dict()["snapshot"] is True
    assert _boot_without_qemu(tmp_path, snapshot=False).to_dict()["snapshot"] is False


# --- CLI --------------------------------------------------------------------


def test_cli_boot_parser_has_no_snapshot_flag():
    args = cli.build_parser().parse_args(["boot", "d.img"])
    assert args.no_snapshot is False
    assert cli.build_parser().parse_args(["boot", "d.img", "--no-snapshot"]).no_snapshot is True


def test_cli_probe_snapshots_by_default(capsys):
    rc = cli.main(["probe", "d.img"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "file=d.img,if=virtio,format=raw,snapshot=on" in out


def test_cli_probe_no_snapshot_writes_through(capsys):
    rc = cli.main(["probe", "d.img", "--no-snapshot"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "file=d.img,if=virtio,format=raw" in out
    assert "snapshot=on" not in out


def test_cli_boot_dry_run_snapshots_by_default(tmp_path, capsys):
    image = _image(tmp_path)
    rc = cli.main(["boot", image, "--dry-run"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert f"file={image},if=virtio,format=raw,snapshot=on" in out


def test_cli_boot_dry_run_no_snapshot(tmp_path, capsys):
    image = _image(tmp_path)
    rc = cli.main(["boot", image, "--dry-run", "--no-snapshot"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "snapshot=on" not in out


def test_no_snapshot_flag_documented_in_help(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["boot", "--help"])
    help_text = capsys.readouterr().out
    assert "--no-snapshot" in help_text
    assert "throwaway overlay" in help_text
