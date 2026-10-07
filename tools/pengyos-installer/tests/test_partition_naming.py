"""Partition-name handling for NVMe-style devices.

A target whose device name ends in a digit names its partitions
``<tgt>p<n>`` rather than ``<tgt><n>`` (the kernel rule, for NVMe / loop /
md / mmcblk). ``plan.partition_device`` is the single source of every
partition device name in the plan and in the install execution; this module
golden-tests that the ``p`` separator is applied — and only applied — where it
belongs, across the partition/format/copy/configure/bootloader argv, and that
install execution still matches the plan's (now ``p``-suffixed) ``blkid``
steps.

Everything is pure / driven through the recording :class:`FakeRunner` — no
test touches a disk.
"""

import pytest

from pengyos_installer import install as install_mod
from pengyos_installer.install import CommandResult, execute_install
from pengyos_installer.plan import (
    ESP_EFI_DIR,
    TargetProbe,
    WORK_DIR,
    build_plan,
    partition_device,
)

GiB = 1024**3

# Deterministic fixture PARTUUIDs (never from real hardware).
ROOT_UUID = "11111111-2222-3333-4444-555555555555"
ESP_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def good_probe(target, size=8 * GiB,
               root_device="/dev/sda", esp_device="/dev/sda1"):
    return TargetProbe(path=target, is_block_device=True, size_bytes=size,
                       root_device=root_device, esp_device=esp_device)


# --- partition_device: the naming rule -------------------------------------

@pytest.mark.parametrize(
    "target, index, expected",
    [
        # plain disks (name ends in a letter) take the bare number
        ("/dev/sda", 1, "/dev/sda1"),
        ("/dev/sdb", 2, "/dev/sdb2"),
        ("/dev/vda", 1, "/dev/vda1"),
        ("/dev/disk/by-id/foo-bar", 1, "/dev/disk/by-id/foo-bar1"),
        # names that end in a digit take the p-suffixed form
        ("/dev/nvme0n1", 1, "/dev/nvme0n1p1"),
        ("/dev/nvme0n1", 2, "/dev/nvme0n1p2"),
        ("/dev/nvme10n1", 1, "/dev/nvme10n1p1"),
        ("/dev/loop0", 1, "/dev/loop0p1"),
        ("/dev/loop3", 2, "/dev/loop3p2"),
        ("/dev/mmcblk0", 1, "/dev/mmcblk0p1"),
        ("/dev/md0", 2, "/dev/md0p2"),
    ],
)
def test_partition_device_naming(target, index, expected):
    assert partition_device(target, index) == expected


def test_partition_device_strips_trailing_slash_for_the_rule():
    # a stray trailing slash on the target must not defeat the digit rule
    assert partition_device("/dev/nvme0n1/", 1) == "/dev/nvme0n1/p1"


def test_partition_device_empty_name_is_not_p_suffixed():
    # an empty / root-only path has no name, so no separator
    assert partition_device("", 1) == "1"


def test_partition_device_matches_overlaps_device_prefix_rule():
    # the p-suffixed partition is recognised as a partition of the disk
    from pengyos_installer.plan import overlaps_device
    assert overlaps_device("/dev/nvme0n1", "/dev/nvme0n1p1")
    assert overlaps_device("/dev/nvme0n1p2", "/dev/nvme0n1")
    assert overlaps_device("/dev/loop0", "/dev/loop0p2")


# --- the plan carries p-suffixed names for an NVMe target -------------------

def _steps_in_phase(plan, phase):
    return [s for s in plan.steps if s.phase == phase]


def test_plan_nvme_format_steps_use_p_names():
    plan = build_plan(target="/dev/nvme0n1", esp_size="512M",
                      probe=good_probe("/dev/nvme0n1"))
    fmt = _steps_in_phase(plan, "format")
    assert fmt[0].argv == ("mkfs.vfat", "-F32", "-n", "PENGYOS-ESP",
                           "/dev/nvme0n1p1")
    assert fmt[1].argv == ("mkfs.ext4", "-L", "pengyos-root", "-F",
                           "/dev/nvme0n1p2")
    # the descriptions carry the real device name too
    assert "/dev/nvme0n1p1" in fmt[0].description
    assert "/dev/nvme0n1p2" in fmt[1].description


def test_plan_nvme_copy_mounts_p_root():
    plan = build_plan(target="/dev/nvme0n1", esp_size="512M",
                      probe=good_probe("/dev/nvme0n1"))
    mount = [s for s in _steps_in_phase(plan, "copy")
             if s.argv is not None and s.argv[0] == "mount"]
    assert mount[0].argv == ("mount", "/dev/nvme0n1p2", WORK_DIR)
    # the rsync exclude still targets the whole disk, not a partition
    rsync = [s for s in _steps_in_phase(plan, "copy")
             if s.argv is not None and s.argv[0] == "rsync"]
    assert ("--exclude=/dev/nvme0n1" in rsync[0].argv)


def test_plan_nvme_partuuid_steps_use_p_names():
    plan = build_plan(target="/dev/nvme0n1", esp_size="512M",
                      probe=good_probe("/dev/nvme0n1"))
    blkids = [s for s in _steps_in_phase(plan, "configure")
              if s.argv is not None and s.argv[0] == "blkid"]
    assert blkids[0].argv == ("blkid", "-s", "PARTUUID", "-o", "value",
                              "/dev/nvme0n1p1")
    assert blkids[1].argv == ("blkid", "-s", "PARTUUID", "-o", "value",
                              "/dev/nvme0n1p2")


def test_plan_nvme_bootloader_mounts_p_esp():
    plan = build_plan(target="/dev/nvme0n1", esp_size="512M",
                      probe=good_probe("/dev/nvme0n1"))
    bl = _steps_in_phase(plan, "bootloader")
    assert bl[0].argv == ("mount", "/dev/nvme0n1p1", ESP_EFI_DIR)
    # efibootmgr -d is the whole disk; -p is the partition *number*
    assert bl[2].argv == (
        "efibootmgr", "-c", "-d", "/dev/nvme0n1", "-p", "1",
        "-L", "PengyOS", "-l", "/EFI/BOOT/bootx64.efi",
    )


def test_plan_nvme_no_ambiguous_bare_number_name():
    # nowhere in an NVMe plan should the ambiguous `<tgt><n>` (no p) form
    # appear as a partition device: the whole disk keeps its bare path, but a
    # `1`/`2` glued straight onto it would be a different (nonexistent) node.
    plan = build_plan(target="/dev/nvme0n1", esp_size="512M",
                      probe=good_probe("/dev/nvme0n1"))
    for step in plan.steps:
        if step.argv is None:
            continue
        for arg in step.argv:
            if arg.startswith("/dev/nvme0n1") and arg not in ("/dev/nvme0n1",):
                assert arg.startswith("/dev/nvme0n1p"), arg


def test_plan_plain_disk_keeps_bare_number_names():
    # regression: a name not ending in a digit must NOT gain a p separator
    plan = build_plan(target="/dev/sdb", esp_size="512M",
                      probe=good_probe("/dev/sdb"))
    fmt = _steps_in_phase(plan, "format")
    assert fmt[0].argv[-1] == "/dev/sdb1"
    assert fmt[1].argv[-1] == "/dev/sdb2"
    blkids = [s for s in _steps_in_phase(plan, "configure")
              if s.argv is not None and s.argv[0] == "blkid"]
    assert blkids[0].argv[-1] == "/dev/sdb1"
    assert blkids[1].argv[-1] == "/dev/sdb2"


# --- install execution matches the p-suffixed blkid steps -------------------

class FakeRunner:
    """A recording runner that reports the fixture PARTUUID per device."""

    def __init__(self, *, fail=(), stdout=None):
        self.calls = []
        self.fail = set(fail)
        self.stdout = dict(stdout or {})

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv in self.fail or argv[0] in self.fail:
            return CommandResult(argv, 1, "", "boom")
        if argv[0] == "blkid" and argv not in self.stdout:
            uuid = ESP_UUID if argv[-1].endswith("1") else ROOT_UUID
            return CommandResult(argv, 0, f"{uuid}\n", "")
        out = self.stdout.get(argv, self.stdout.get(argv[0], ""))
        return CommandResult(argv, 0, out, "")


def test_execute_install_nvme_maps_partuuids_from_p_devices(tmp_path):
    """The executor's esp/root PARTUUID capture must line up with the plan's
    ``p``-suffixed ``blkid`` argv (both use ``partition_device``), so an NVMe
    install still captures the ESP on ``…p1`` and the root on ``…p2`` and
    writes correct fstab / grub.cfg."""
    plan = build_plan(target="/dev/nvme0n1", esp_size="512M",
                      hostname="nvme-box",
                      probe=good_probe("/dev/nvme0n1"))
    runner = FakeRunner()
    res = execute_install(plan, runner, work_root=str(tmp_path))
    assert res.ok is True

    # the blkid steps ran against the p-suffixed nodes
    blkid_args = [c[-1] for c in runner.calls if c and c[0] == "blkid"]
    assert blkid_args == ["/dev/nvme0n1p1", "/dev/nvme0n1p2"]

    # fstab: root first (…p2 → ROOT_UUID), then ESP (…p1 → ESP_UUID)
    fstab = res.files_written[str(tmp_path) + "/etc/fstab"]
    assert f"PARTUUID={ROOT_UUID} / ext4 defaults 0 1\n" in fstab
    assert f"PARTUUID={ESP_UUID} /boot/efi vfat umask=0077 0 1\n" in fstab

    # grub.cfg: root=PARTUUID=<root> from …p2
    grub = res.files_written[str(tmp_path) + "/boot/efi/EFI/BOOT/grub.cfg"]
    assert f"root=PARTUUID={ROOT_UUID} ro quiet" in grub
    assert f"--partuuid {ROOT_UUID}" in grub
