"""Tests for the deterministic build steps.

Covers :func:`build_steps` — the ordered, argv-per-step derivation that turns
the plan's layout into the build recipe (mksquashfs the live rootfs, assemble
the ESP/boot, xorriso the ISO), :func:`shell_quote` / :func:`argv_to_shell`
(shell-quoting), :func:`render_build_steps_text`, and the ``DISK_IMAGE``
dispatch. Each derivation has a mutation: change the plan's edition / live
rootfs name / compressor / boot-entry loader and assert the argv reflects it.
"""

import shlex

import pytest

from pengyos_media.model import (
    BootEntry,
    LiveRootfs,
    LiveRootfsFormat,
    MediumKind,
    MediumPlan,
    Installer,
    make_plan,
)
from pengyos_media.steps import (
    SQUASHFS_COMPRESSOR,
    BuildStep,
    argv_to_shell,
    build_steps,
    render_build_steps_text,
    shell_quote,
)

GOLDEN = make_plan("desktop", "2026.09.1")


# --------------------------------------------------------------------------- #
# shell quoting
# --------------------------------------------------------------------------- #
def test_shell_quote_wraps_in_single_quotes():
    assert shell_quote("mksquashfs") == "'mksquashfs'"


def test_shell_quote_escapes_embedded_single_quote():
    # The safe ANSI-C style: an embedded ' becomes ' \' '
    assert shell_quote("a'b") == "'a'\\''b'"


def test_shell_quote_empty_arg_is_real_empty_string():
    # '' round-trips to "" (a real empty arg), not nothing.
    assert shell_quote("") == "''"
    assert shlex.split(shell_quote("")) == [""]


def test_shell_quote_round_trips():
    argv = ["mksquashfs", "live/rootfs", "with space.sqfs", "-comp", "a'b"]
    assert shlex.split(argv_to_shell(argv)) == argv


def test_argv_to_shell_is_deterministic():
    argv = ["xorriso", "-o", "out.iso", "."]
    assert argv_to_shell(argv) == argv_to_shell(argv)


# --------------------------------------------------------------------------- #
# BuildStep model
# --------------------------------------------------------------------------- #
def test_build_step_to_dict_shape():
    step = BuildStep(name="mksquashfs", description="compress", argv=["mksquashfs", "a"])
    d = step.to_dict()
    assert set(d) == {"name", "description", "argv"}
    assert d["argv"] == "'mksquashfs' 'a'"


def test_build_step_is_frozen():
    import dataclasses

    step = BuildStep(name="m", description="d", argv=["m"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        step.name = "x"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# ISO: the three steps, in order, with golden argv
# --------------------------------------------------------------------------- #
def test_iso_build_steps_are_three_in_order():
    steps = build_steps(GOLDEN)
    assert [s.name for s in steps] == ["mksquashfs", "assemble-esp-boot", "xorriso"]


def test_iso_mksquashfs_argv_golden():
    (mksquashfs, *_rest) = build_steps(GOLDEN)
    assert mksquashfs.argv == [
        "mksquashfs",
        "live/rootfs",
        "live/pengyos-live.sqfs",
        "-comp",
        SQUASHFS_COMPRESSOR,
        "-vol_id",
        "PENGYOS_LIVE",
        "-noappend",
    ]


def test_iso_esp_boot_argv_golden():
    (_mksquashfs, esp_boot, *_rest) = build_steps(GOLDEN)
    assert esp_boot.argv == [
        "install", "-D", "-m", "0755", "boot/bootx64.efi", "EFI/BOOT/bootx64.efi",
        "install", "-D", "-m", "0644", "assets/loader.conf", "EFI/BOOT/loader.conf",
        "install", "-D", "-m", "0755", "assets/installer", "boot/pengyos-install",
        "install", "-D", "-m", "0644", "assets/vmlinuz", "boot/vmlinuz",
        "install", "-D", "-m", "0644", "assets/initrd.img", "boot/initrd.img",
    ]


def test_iso_xorriso_argv_golden():
    xorriso = build_steps(GOLDEN)[-1]
    assert xorriso.argv == [
        "xorriso", "-as", "mkisofs", "-r", "-J", "-b", "EFI/BOOT/bootx64.efi",
        "-isohybrid-gpt-basdat", "-iso-version", "20220101-350000",
        "-volid", "PENGYOS_LIVE", "-o", "desktop-install.iso", ".",
    ]


def test_iso_xorriso_is_hybrid():
    (*_start, xorriso) = build_steps(GOLDEN)
    # Hybrid MBR+GPT so any firmware finds a boot path (the "dark arts" note).
    assert "-isohybrid-gpt-basdat" in xorriso.argv


def test_build_steps_are_deterministic():
    assert build_steps(GOLDEN) == build_steps(GOLDEN)


# --------------------------------------------------------------------------- #
# Derivation mutations — the argv reflects the plan
# --------------------------------------------------------------------------- #
def test_edition_flows_into_iso_name():
    plan = make_plan("core", "2026.09.1")
    (*_start, xorriso) = build_steps(plan)
    assert "core-install.iso" in xorriso.argv
    assert "desktop-install.iso" not in xorriso.argv


def test_live_rootfs_name_flows_into_mksquashfs():
    plan = make_plan("desktop", "2026.09.1", live_rootfs=LiveRootfs(name="my.sqfs"))
    (mksquashfs, *_rest) = build_steps(plan)
    assert "live/my.sqfs" in mksquashfs.argv


def test_mksquashfs_targets_staged_live_rootfs():
    (mksquashfs, *_rest) = build_steps(GOLDEN)
    assert mksquashfs.argv[1] == "live/rootfs"


def test_boot_entry_loader_flows_into_staged_path():
    # A custom loader path is staged where it lives inside the ISO.
    plan = make_plan("desktop", "2026.09.1", boot_entry=BootEntry(loader="/EFI/BOOT/pendrive.efi"))
    steps = build_steps(plan)
    esp_boot, xorriso = steps[1], steps[-1]
    assert "EFI/BOOT/pendrive.efi" in esp_boot.argv
    assert "EFI/BOOT/pendrive.efi" in xorriso.argv


def test_installer_binary_flows_into_esp_boot():
    plan = make_plan("desktop", "2026.09.1", installer=Installer(binary="setup"))
    esp_boot = build_steps(plan)[1]
    assert "boot/setup" in esp_boot.argv


def test_compressor_is_zstd():
    (mksquashfs, *_rest) = build_steps(GOLDEN)
    assert SQUASHFS_COMPRESSOR == "zstd"
    assert "-comp" in mksquashfs.argv
    assert SQUASHFS_COMPRESSOR in mksquashfs.argv


# --------------------------------------------------------------------------- #
# disk-image dispatch
# --------------------------------------------------------------------------- #
def test_disk_image_dispatches_the_eight_disk_image_steps():
    # Disk-image-correct: partition + format the ESP, attach a loop, create the
    # ESP dirs, build the squashfs, lay the loader on the ESP partition, write
    # the squashfs onto the live partition, detach the loop. (Not the ISO's
    # mksquashfs/assemble/xorriso.)
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    steps = build_steps(plan)
    assert [s.name for s in steps] == [
        "mkfs-disk", "losetup", "mkfs-esp", "mmd-esp", "mksquashfs",
        "assemble-esp", "write-live", "losetup-detach",
    ]
    # It is a disk image — never an ISO: no xorriso, no ISO-tree staging.
    assert "xorriso" not in {s.name for s in steps}


def test_disk_image_mkfs_argv_shapes_image():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    (mkfs, *_rest) = build_steps(plan)
    assert mkfs.argv[0] == "truncate"
    assert "server-install.img" in mkfs.argv
    # An EFI System Partition (GPT type C12A7328...) is partitioned first — the
    # GUID is the partition-type spec (``<n>:<GUID>``), so it is a substring of
    # an arg rather than a standalone element.
    joined = " ".join(mkfs.argv)
    assert "C12A7328-F81F-11D2-BA4B-00AA00BC3331" in joined


# --------------------------------------------------------------------------- #
# disk-image, disk-image-correct
#
# The whole point of this group: the disk image is NOT an ISO tree. The squashfs
# is written *onto the live partition* (dd conv=notrunc) and the loader is laid
# *on the ESP partition* (mtools mcopy -i) — never staged into a directory tree.
# --------------------------------------------------------------------------- #
def test_disk_image_mkfs_disk_partitions_both_guids():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    (mkfs, *_rest) = build_steps(plan)
    # truncate to the fixed size, then two sgdisk partition ops.
    assert mkfs.argv[0] == "truncate"
    assert "--size" in mkfs.argv and "512M" in mkfs.argv
    joined = " ".join(mkfs.argv)
    # ESP (partition 1) + live partition (partition 2), both GUIDs present.
    assert "C12A7328-F81F-11D2-BA4B-00AA00BC3331" in joined
    assert "0FC6DADA-F63A-11D0-A536-F80D18B59718" in joined


def test_disk_image_mkfs_esp_formats_fat32_on_esp_partition():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    steps = build_steps(plan)
    mkfs_esp = next(s for s in steps if s.name == "mkfs-esp")
    assert mkfs_esp.argv == ["mkfs.fat", "-n", "BOOT", "-F", "32", "<loop>-p1"]
    # It runs after the loop is attached (losetup) and before the squashfs.
    assert steps.index(mkfs_esp) > steps.index(next(s for s in steps if s.name == "losetup"))
    assert steps.index(mkfs_esp) < steps.index(next(s for s in steps if s.name == "mksquashfs"))


def test_disk_image_live_partition_is_raw_not_formatted():
    # The live partition (2) is left raw — it *is* the squashfs. Only the ESP is
    # formatted, so there is exactly one mkfs.fat and it targets <loop>-p1.
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    steps = build_steps(plan)
    fat_steps = [s for s in steps if s.argv and s.argv[0] == "mkfs.fat"]
    assert len(fat_steps) == 1
    assert fat_steps[0].argv[-1] == "<loop>-p1"


def test_disk_image_mmd_esp_creates_dirs_before_mcopy():
    # mtools mcopy does not create parent directories, and mmd creates one level
    # at a time: the ESP dir tree is created (level by level) before the loader
    # is copied.
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    steps = build_steps(plan)
    mmd = next(s for s in steps if s.name == "mmd-esp")
    assert mmd.argv[0] == "mmd"
    assert mmd.argv[1:3] == ["-i", "<loop>-p1"]
    # The loader's ancestor dirs, level by level from the root, then the boot dir.
    created = [a for a in mmd.argv if a.startswith("::")]
    assert created == ["::/EFI", "::/EFI/BOOT", "::/boot"]
    # mmd-esp runs after the ESP is formatted and before the loader is copied.
    assert steps.index(mmd) > steps.index(next(s for s in steps if s.name == "mkfs-esp"))
    assert steps.index(mmd) < steps.index(next(s for s in steps if s.name == "assemble-esp"))


def test_disk_image_mmd_esp_nested_loader_dirs():
    # A deeper loader path is created level by level (parents first).
    plan = make_plan(
        "server", "2026.09.1", kind=MediumKind.DISK_IMAGE,
        boot_entry=BootEntry(loader="/EFI/BOOT/extra/bootx64.efi"),
    )
    mmd = build_steps(plan)[3]
    assert [a for a in mmd.argv if a.startswith("::")] == [
        "::/EFI", "::/EFI/BOOT", "::/EFI/BOOT/extra", "::/boot",
    ]


def test_disk_image_mmd_esp_dedupes_overlapping_boot_dir():
    # A loader already under the boot dir does not create /boot twice.
    plan = make_plan(
        "server", "2026.09.1", kind=MediumKind.DISK_IMAGE,
        boot_entry=BootEntry(loader="/boot/x64/bootx64.efi"),
    )
    mmd = build_steps(plan)[3]
    created = [a for a in mmd.argv if a.startswith("::")]
    assert created.count("::/boot") == 1
    assert created == ["::/boot", "::/boot/x64"]


def test_disk_image_losetup_attaches_with_partitions():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    (_mkfs, losetup, *_rest) = build_steps(plan)
    assert losetup.name == "losetup"
    assert losetup.argv == ["losetup", "-f", "--show", "-P", "server-install.img"]


def test_disk_image_assemble_esp_uses_mcopy_not_install():
    # Disk-image-correct: the loader is laid on the ESP partition with mtools
    # mcopy -i (writes straight onto the partition) — NOT `install -D` staging
    # into a directory tree (the ISO approach).
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    esp = next(s for s in build_steps(plan) if s.name == "assemble-esp")
    assert esp.name == "assemble-esp"
    assert esp.argv[0] == "mcopy"
    assert "-i" in esp.argv
    # One mcopy per staged file, each targeting the ESP partition (1): the number
    # of `mcopy` commands equals the number of `<loop>-p1` targets.
    assert esp.argv.count("mcopy") == esp.argv.count("<loop>-p1")
    assert esp.argv.count("<loop>-p1") == 5
    # No ISO-tree staging: no `install` command anywhere.
    assert "install" not in esp.argv


def test_disk_image_assemble_esp_lays_loader_at_esp_path():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    esp = next(s for s in build_steps(plan) if s.name == "assemble-esp")
    joined = " ".join(esp.argv)
    # The loader lands at its real path inside the ESP (leading slash stripped).
    assert "::/EFI/BOOT/bootx64.efi" in joined
    # The boot menu sits next to the loader.
    assert "::/EFI/BOOT/loader.conf" in joined


def test_disk_image_assemble_esp_installer_and_kernels():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    esp = next(s for s in build_steps(plan) if s.name == "assemble-esp")
    joined = " ".join(esp.argv)
    assert "::/boot/pengyos-install" in joined
    assert "::/boot/vmlinuz" in joined
    assert "::/boot/initrd.img" in joined


def test_disk_image_write_live_writes_squashfs_to_live_partition():
    # Disk-image-correct: the squashfs is dd'd *onto the live partition*
    # (partition 2) with conv=notrunc — the partition *is* the squashfs.
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    write_live = next(s for s in build_steps(plan) if s.name == "write-live")
    assert write_live.name == "write-live"
    assert write_live.argv[0] == "dd"
    assert "of=<loop>-p2" in write_live.argv
    assert "conv=notrunc" in write_live.argv
    # Not the ESP partition — the live partition.
    assert "<loop>-p1" not in " ".join(write_live.argv)


def test_disk_image_write_live_targets_staged_squashfs():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    write_live = next(s for s in build_steps(plan) if s.name == "write-live")
    assert "if=live/pengyos-live.sqfs" in write_live.argv


def test_disk_image_live_rootfs_name_flows_into_write_live():
    plan = make_plan(
        "server", "2026.09.1", kind=MediumKind.DISK_IMAGE,
        live_rootfs=LiveRootfs(name="my.sqfs"),
    )
    write_live = next(s for s in build_steps(plan) if s.name == "write-live")
    assert "if=live/my.sqfs" in write_live.argv


def test_disk_image_losetup_detach():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    last = build_steps(plan)[-1]
    assert last.name == "losetup-detach"
    assert last.argv == ["losetup", "-d", "<loop>"]


def test_disk_image_edition_flows_into_img_name():
    plan = make_plan("core", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    (mkfs, *_rest) = build_steps(plan)
    assert "core-install.img" in mkfs.argv
    assert "server-install.img" not in " ".join(mkfs.argv)


def test_disk_image_loader_path_flows_into_esp():
    plan = make_plan(
        "server", "2026.09.1", kind=MediumKind.DISK_IMAGE,
        boot_entry=BootEntry(loader="/EFI/BOOT/pendrive.efi"),
    )
    esp = next(s for s in build_steps(plan) if s.name == "assemble-esp")
    assert "::/EFI/BOOT/pendrive.efi" in " ".join(esp.argv)


def test_disk_image_is_deterministic():
    plan = make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE)
    assert build_steps(plan) == build_steps(plan)


def test_unsupported_kind_raises():
    plan = MediumPlan(
        kind="floppy",  # type: ignore[arg-type]
        edition="desktop",
        release=make_plan("desktop", "1").release,
        os_release=make_plan("desktop", "1").os_release,
    )
    with pytest.raises(ValueError):
        build_steps(plan)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def test_render_build_steps_text_names_every_step():
    text = render_build_steps_text(build_steps(GOLDEN))
    for name in ("mksquashfs", "assemble-esp-boot", "xorriso"):
        assert name in text


def test_render_build_steps_text_includes_argv():
    text = render_build_steps_text(build_steps(GOLDEN))
    assert "desktop-install.iso" in text
    assert "mksquashfs" in text


def test_render_build_steps_text_counts_steps():
    text = render_build_steps_text(build_steps(GOLDEN))
    assert "build steps (3):" in text


def test_render_build_steps_text_names_disk_image_steps():
    text = render_build_steps_text(build_steps(GOLDEN))
    for name in ("mksquashfs", "assemble-esp-boot", "xorriso"):
        assert name in text
    # The disk image renders its own steps too.
    disk = render_build_steps_text(
        build_steps(make_plan("server", "2026.09.1", kind=MediumKind.DISK_IMAGE))
    )
    for name in ("mkfs-disk", "losetup", "mkfs-esp", "mmd-esp", "assemble-esp", "write-live"):
        assert name in disk
    assert "build steps (8):" in disk
