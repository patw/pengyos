"""Tests for live root/ESP discovery from ``/proc/mounts``.

The installer's sanity re-check refuses installing over the running system by
comparing the target against the live root / ESP block devices carried on the
:class:`~pengyos_installer.plan.TargetProbe`. Until now the default
:func:`~pengyos_installer.plan.collect_target_probe` left those two fields
``None``, so the refusal only fired for a hand-injected probe. This module
exercises the new seam: :func:`~pengyos_installer.plan.parse_mounts` +
:func:`~pengyos_installer.plan.live_mount_devices` (pure, over an injected
mount-table string) and the wiring into :func:`~pengyos_installer.plan.collect_target_probe`
and, end-to-end, the CLI ``plan`` handler.

Every test is hermetic: the mount table is injected as a string (or a
function) and **no test reads the real ``/proc/mounts`` or touches a disk**.
"""

import contextlib
import io

from pengyos_installer import plan as planmod
from pengyos_installer.cli import ExitCode, main
from pengyos_installer.plan import (
    FAT_FSTYPES,
    ESP_MOUNTPOINTS,
    PROC_MOUNTS_PATH,
    MountEntry,
    TargetProbe,
    check_target,
    collect_target_probe,
    live_mount_devices,
    parse_mounts,
)

GiB = 1024**3

#: A representative, realistic ``/proc/mounts``: an NVMe system booted from a
#: partition (``/`` on ``/dev/nvme0n1p2``) with the ESP on ``/dev/nvme0n1p1``
#: (``/boot/efi``), plus a pile of pseudo-filesystems, an unrelated FAT USB
#: data partition, and a FAT loop mount — none of the latter are the live
#: root/ESP.
REALISTIC_MOUNTS = (
    "/dev/nvme0n1p2 / ext4 rw,relatime 0 0\n"
    "/dev/nvme0n1p1 /boot/efi vfat rw,relatime,fmask=0077,dmask=0077 0 0\n"
    "proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"
    "sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\n"
    "devtmpfs /dev devtmpfs rw,nosuid,relatime,size=4096k 0 0\n"
    "cgroup2 /sys/fs/cgroup cgroup2 rw,relatime 0 0\n"
    "tmpfs /tmp tmpfs rw,relatime,size=8192k 0 0\n"
    "none /sys/kernel/config configfs rw,relatime 0 0\n"
    "/dev/sdb1 /media/usb vfat rw,relatime 0 0\n"
    "/dev/loop0 /mnt/loopdata vfat rw,relatime 0 0\n"
)


# --- parse_mounts -----------------------------------------------------------

def test_parse_mounts_realistic_fields():
    entries = parse_mounts(REALISTIC_MOUNTS)
    by_mp = {e.mountpoint: e for e in entries}
    root = by_mp["/"]
    assert root.device == "/dev/nvme0n1p2"
    assert root.fstype == "ext4"
    assert root.options == "rw,relatime"
    esp = by_mp["/boot/efi"]
    assert esp.device == "/dev/nvme0n1p1"
    assert esp.fstype == "vfat"
    # pseudo-filesystem device names are carried through verbatim
    assert by_mp["/sys/fs/cgroup"].device == "cgroup2"
    assert by_mp["/sys/kernel/config"].device == "none"


def test_parse_mounts_keeps_every_nonblank_line():
    # 10 data lines in the fixture, none blank, all six-field.
    assert len(parse_mounts(REALISTIC_MOUNTS)) == 10


def test_parse_mounts_skips_blank_and_short_lines():
    text = (
        "/dev/sda1 / ext4 rw 0 0\n"
        "\n"
        "   \n"
        "justonetwo\n"
        "/dev/sda2 /boot/efi vfat rw 0 0\n"
    )
    entries = parse_mounts(text)
    assert [e.mountpoint for e in entries] == ["/", "/boot/efi"]


def test_parse_mounts_empty_is_empty():
    assert parse_mounts("") == ()
    assert parse_mounts("   \n\n") == ()


def test_parse_mounts_unescapes_octal_space_in_mountpoint():
    # The kernel renders a literal space in a mountpoint as the four chars \040.
    entries = parse_mounts("/dev/sda1 /mnt/my\\040disk vfat rw,relatime 0 0\n")
    assert entries[0].mountpoint == "/mnt/my disk"
    assert entries[0].device == "/dev/sda1"


def test_parse_mounts_unescapes_tab_newline_and_backslash():
    tab = parse_mounts("/dev/sda1 /a\\011b vfat rw 0 0\n")[0].mountpoint
    assert tab == "/a\tb"
    nl = parse_mounts("/dev/sda1 /a\\012b vfat rw 0 0\n")[0].mountpoint
    assert nl == "/a\nb"
    # An escaped backslash (\\) must survive as a single backslash, and must
    # not clobber a following \040 (the two-phase unescape handles this).
    bs = parse_mounts("/dev/sda1 /x\\\\ y vfat rw 0 0\n")[0].mountpoint
    assert bs == "/x\\"


def test_parse_mounts_device_field_unescaped_too():
    # A (hypothetical) escaped space in the *device* field is unescaped just
    # like the mountpoint: \040 is a space within one whitespace-delimited field.
    entries = parse_mounts("/dev\\040sdX /mnt/x ext4 rw 0 0\n")
    assert entries[0].device == "/dev sdX"
    assert entries[0].mountpoint == "/mnt/x"


# --- live_mount_devices -----------------------------------------------------

def test_live_mount_devices_realistic():
    root, esp = live_mount_devices(parse_mounts(REALISTIC_MOUNTS))
    assert root == "/dev/nvme0n1p2"
    assert esp == "/dev/nvme0n1p1"


def test_live_mount_devices_ignores_fat_data_partitions():
    # A FAT USB drive and a FAT loop mount are NOT at an ESP mountpoint, so
    # they must not be mistaken for the ESP (a false ESP would spuriously
    # refuse valid targets on their disks).
    root, esp = live_mount_devices(parse_mounts(REALISTIC_MOUNTS))
    assert esp != "/dev/sdb1"
    assert esp != "/dev/loop0"


def test_live_mount_devices_no_block_root_is_none():
    # An overlay (chroot/container) / has no block-device root.
    text = "overlay / overlay rw,relatime 0 0\ntmpfs /tmp tmpfs rw 0 0\n"
    root, esp = live_mount_devices(parse_mounts(text))
    assert root is None
    assert esp is None


def test_live_mount_devices_no_esp_is_none():
    # Root present, ESP unmounted.
    text = "/dev/sda2 / ext4 rw 0 0\n"
    root, esp = live_mount_devices(parse_mounts(text))
    assert root == "/dev/sda2"
    assert esp is None


def test_live_mount_devices_esp_mountpoint_case_insensitive():
    text = "/dev/sda2 / ext4 rw 0 0\n/dev/sda1 /BOOT/EFI VFAT rw 0 0\n"
    root, esp = live_mount_devices(parse_mounts(text))
    assert root == "/dev/sda2"
    assert esp == "/dev/sda1"


def test_live_mount_devices_esp_at_efi_mountpoint():
    text = "/dev/sda2 / ext4 rw 0 0\n/dev/sda1 /efi vfat rw 0 0\n"
    root, esp = live_mount_devices(parse_mounts(text))
    assert esp == "/dev/sda1"


def test_live_mount_devices_non_fat_esp_mountpoint_ignored():
    # /boot/efi mounted as ext4 is not a FAT ESP -> no esp_device.
    text = "/dev/sda2 / ext4 rw 0 0\n/dev/sda1 /boot/efi ext4 rw 0 0\n"
    root, esp = live_mount_devices(parse_mounts(text))
    assert root == "/dev/sda2"
    assert esp is None


def test_live_mount_devices_pseudo_fstype_esp_ignored():
    # efivarfs is the UEFI variables filesystem, not the FAT ESP.
    text = ("/dev/sda2 / ext4 rw 0 0\n"
            "efivarfs /sys/firmware/efi/efivars efivarfs rw 0 0\n")
    root, esp = live_mount_devices(parse_mounts(text))
    assert root == "/dev/sda2"
    assert esp is None


def test_live_mount_devices_only_first_esp():
    # Two candidate ESPs -> the first wins, deterministically.
    text = ("/dev/sda1 /efi vfat rw 0 0\n"
            "/dev/sdb1 /boot/efi vfat rw 0 0\n")
    root, esp = live_mount_devices(parse_mounts(text))
    assert esp == "/dev/sda1"


def test_live_mount_devices_empty():
    assert live_mount_devices(()) == (None, None)
    assert live_mount_devices(parse_mounts("")) == (None, None)


def test_mountentry_is_frozen_and_carries_four_fields():
    e = MountEntry(device="/dev/sda1", mountpoint="/", fstype="ext4", options="rw")
    assert (e.device, e.mountpoint, e.fstype, e.options) == (
        "/dev/sda1", "/", "ext4", "rw",
    )
    with __import__("pytest").raises(Exception):
        e.device = "/dev/sdb1"  # frozen dataclass


def test_constants_are_sane():
    assert PROC_MOUNTS_PATH == "/proc/mounts"
    assert "vfat" in FAT_FSTYPES
    assert "/boot/efi" in ESP_MOUNTPOINTS and "/efi" in ESP_MOUNTPOINTS


# --- collect_target_probe wiring -------------------------------------------

def test_collect_target_probe_populates_live_devices():
    probe = collect_target_probe("/dev/sdz", read_mounts=lambda: REALISTIC_MOUNTS)
    # /dev/sdz does not exist, so is_block is False — but the mount-derived
    # live devices are still populated (they come from the mount table, not
    # from the target stat).
    assert probe.is_block_device is False
    assert probe.root_device == "/dev/nvme0n1p2"
    assert probe.esp_device == "/dev/nvme0n1p1"


def test_collect_target_probe_empty_mounts_gives_none_devices():
    probe = collect_target_probe("/dev/sdz", read_mounts=lambda: "")
    assert probe.root_device is None
    assert probe.esp_device is None


def test_collect_target_probe_unreadable_mounts_degrades_not_raises():
    def boom():
        raise OSError("no such mount table")
    # A missing/unreadable mount table must degrade to None/None, not raise —
    # the installer stays usable and the other sanity checks still apply.
    probe = collect_target_probe("/dev/sdz", read_mounts=boom)
    assert probe.root_device is None
    assert probe.esp_device is None


def test_collect_target_probe_garbage_mounts_degrade_to_none():
    # Short/malformed lines are skipped by parse_mounts -> no devices found.
    probe = collect_target_probe("/dev/sdz", read_mounts=lambda: "a b c\n\n")
    assert probe.root_device is None
    assert probe.esp_device is None


def test_collect_target_probe_default_reader_is_module_seam(monkeypatch):
    # The default (no read_mounts kwarg) must resolve through the module-level
    # _read_mounts at call time — so monkeypatching that attribute is the seam
    # the CLI path (which passes no read_mounts) uses.
    monkeypatch.setattr(planmod, "_read_mounts", lambda: REALISTIC_MOUNTS)
    probe = collect_target_probe("/dev/sdz")
    assert probe.root_device == "/dev/nvme0n1p2"
    assert probe.esp_device == "/dev/nvme0n1p1"


# --- end-to-end: check_target now refuses over the running system ----------

def _probe_from_mounts(text, target, size=8 * GiB):
    p = collect_target_probe(target, read_mounts=lambda: text)
    # Force the block/size facts so the ONLY thing under test is the live
    # device comparison (the target path need not exist on the test host).
    return TargetProbe(
        path=target,
        is_block_device=True,
        size_bytes=size,
        root_device=p.root_device,
        esp_device=p.esp_device,
    )


def test_check_target_refuses_whole_live_disk():
    probe = _probe_from_mounts(REALISTIC_MOUNTS, "/dev/nvme0n1")
    res = check_target(probe)
    codes = {e.code for e in res.errors}
    assert res.ok is False
    # Targeting the whole disk that hosts the live root + ESP refuses on both.
    assert "is-live-root" in codes
    assert "is-live-esp" in codes


def test_check_target_refuses_a_live_partition():
    # Target = the live root partition itself.
    probe = _probe_from_mounts(REALISTIC_MOUNTS, "/dev/nvme0n1p2")
    res = check_target(probe)
    assert res.ok is False
    assert any(e.code == "is-live-root" for e in res.errors)


def test_check_target_allows_a_different_disk():
    # A disk unrelated to the live root/ESP passes the live-device checks.
    probe = _probe_from_mounts(REALISTIC_MOUNTS, "/dev/sdc")
    res = check_target(probe)
    assert res.ok is True
    assert res.errors == ()


def test_check_target_allows_fat_data_disk():
    # The FAT USB data partition's disk is NOT the live ESP, so targeting it
    # must NOT trigger an is-live-esp refusal.
    probe = _probe_from_mounts(REALISTIC_MOUNTS, "/dev/sdb")
    res = check_target(probe)
    assert res.ok is True


def test_check_target_refusal_needs_a_populated_probe():
    # With no live devices discovered (empty mount table) the live checks are
    # inert — a different disk is accepted, proving the refusal comes from the
    # discovered devices, not a hard refusal.
    probe = _probe_from_mounts("", "/dev/sdc")
    assert probe.root_device is None and probe.esp_device is None
    assert check_target(probe).ok is True


# --- end-to-end: the CLI plan handler --------------------------------------

def _cli_run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def test_cli_plan_refuses_live_disk_via_mount_table(monkeypatch):
    # The production wiring: `plan` calls build_plan() with no probe ->
    # collect_target_probe (the module default) -> _read_mounts (the seam) ->
    # live device discovery -> check_target refuses the running system.
    monkeypatch.setattr(planmod, "_read_mounts", lambda: REALISTIC_MOUNTS)
    code, out, err = _cli_run("plan", "--target", "/dev/nvme0n1")
    assert code == ExitCode.ERROR
    # The mount-derived live-device refusal fired end-to-end. (The target path
    # is not a real block device on the test host, so not-block-device is
    # also reported — check_target reports all failures together — but the
    # live-root/live-esp codes prove the mount table drove the refusal.)
    assert "[is-live-root]" in err
    assert "[is-live-esp]" in err
    assert out == ""  # no plan is emitted on refusal


def test_cli_plan_allows_unrelated_disk_via_mount_table(monkeypatch):
    # A disk unrelated to the live root/ESP is not refused for being live.
    # (It still can't fully plan here — the target isn't a real block device —
    # but the absence of the live-device codes is what is asserted.) Use a
    # device name that cannot be a real block device so the result is
    # independent of the test host's hardware.
    monkeypatch.setattr(planmod, "_read_mounts", lambda: REALISTIC_MOUNTS)
    code, out, err = _cli_run("plan", "--target", "/dev/pengyos-no-such-disk")
    assert code == ExitCode.ERROR  # refused, but for not-block-device
    assert "is-live-root" not in err
    assert "is-live-esp" not in err
    assert "[not-block-device]" in err
