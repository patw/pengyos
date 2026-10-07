"""Golden + behaviour tests for the plan model and the ``plan`` subcommand.

Everything here is hermetic: the plan is *pure* and all device facts come from
an injected :class:`~pengyos_installer.plan.TargetProbe`. The only filesystem
touches are the read-only ``os.stat``/``/sys`` reads in
``collect_target_probe`` when the CLI path is exercised; the CLI happy path is
tested by monkeypatching that collector with a fixture probe so **no test ever
touches a disk**.

Covers (spec.md "Verification"):
* target sanity checks — block device, not the live device, >= 4 GiB;
* the ordered step list (all phases, in order);
* deterministic output (byte-identical plan for identical inputs);
* a golden for a sample target (exact ``to_dict``);
* the ``--json`` shape;
* the copy phase: source discovery (``None`` → running ``/``; tarball
  extension → ``tar``; else ``dir``) and the ``mount`` / ``rsync`` / ``tar``
  argv with the virtual-dir + target-device excludes;
* the CLI wiring: ``plan`` success, refusal (exit 1), and usage errors.
"""

import contextlib
import io
import json

import pytest

from pengyos_installer import plan as planmod
from pengyos_installer.cli import ExitCode, main
from pengyos_installer.plan import (
    COPY_SRC_KIND_DIR,
    COPY_SRC_KIND_TAR,
    ConfigError,
    ERR_INVALID_HOSTNAME,
    ERR_IS_LIVE_ESP,
    ERR_IS_LIVE_ROOT,
    ERR_MISSING_PARTUUID,
    ERR_NOT_BLOCK_DEVICE,
    ERR_TOO_SMALL,
    ESP_FS_LABEL,
    ESP_MOUNTPOINT,
    ESP_PART_NAME,
    ESP_START_OFFSET,
    ETC_FSTAB,
    ETC_HOSTNAME,
    FALLBACK_GRUB_CFG,
    GRUB_TARGET,
    GRUB_TIMEOUT,
    INSTALL_MARKER,
    MIN_TARGET_SIZE_BYTES,
    PHASES,
    ROOT_FS_LABEL,
    ROOT_PART_NAME,
    ESP_EFI_DIR,
    EFI_LOADER_PATH,
    GRUB_BOOT_DIR,
    InstallPlan,
    SanityResult,
    TargetProbe,
    VIRTUAL_DIR_EXCLUDES,
    WORK_DIR,
    BOOTLOADER_ID,
    build_plan,
    check_target,
    classify_rootfs_src,
    configure_file_steps,
    bootloader_steps,
    copy_steps,
    format_steps,
    overlaps_device,
    partuuid_steps,
    parse_partuuid,
    partition_steps,
    render_fstab,
    render_grub_cfg,
    render_hostname,
    render_installed_marker,
    render_plan_text,
    rsync_source_arg,
)

GiB = 1024**3


def phases_in_order(steps) -> list[str]:
    """The phase sequence of ``steps``, validated: every phase appears at
    least once and the sequence never goes backwards in :data:`PHASES`.

    A phase may span several steps (partition is four, format is two), so
    equality with ``PHASES`` itself is the wrong assertion — this is the
    ordered-subsequence check.
    """
    # accepts Step objects or the ``--json`` dict form
    seq = [s["phase"] if isinstance(s, dict) else s.phase for s in steps]
    assert set(seq) <= set(PHASES), f"unknown phase: {sorted(set(seq) - set(PHASES))}"
    assert set(PHASES) <= set(seq), f"missing phases: {sorted(set(PHASES) - set(seq))}"
    order = {ph: i for i, ph in enumerate(PHASES)}
    assert all(
        order[a] <= order[b] for a, b in zip(seq, seq[1:])
    ), f"phases out of order: {seq}"
    return seq


def good_probe(target="/dev/sdb", size=8 * GiB,
               root_device="/dev/sda", esp_device="/dev/sda1"):
    """A well-formed, installable target (>= 4 GiB, not the live device)."""
    return TargetProbe(
        path=target,
        is_block_device=True,
        size_bytes=size,
        root_device=root_device,
        esp_device=esp_device,
    )


# --- sanity: disk / overlap helpers ----------------------------------------

def test_overlaps_device_exact():
    assert overlaps_device("/dev/sdb", "/dev/sdb")
    assert not overlaps_device("/dev/sdb", "/dev/sdc")


def test_overlaps_device_disk_contains_partition():
    # Targeting the whole disk that hosts the live partition is an overlap.
    assert overlaps_device("/dev/sda", "/dev/sda1")
    assert overlaps_device("/dev/sda1", "/dev/sda")


def test_overlaps_device_no_false_positive_on_sibling_devices():
    # Numeric ids that merely share a prefix are NOT the same device.
    assert not overlaps_device("/dev/loop1", "/dev/loop0")
    assert not overlaps_device("/dev/sdb", "/dev/sda")


def test_overlaps_device_nvme_partition():
    # The 'p' separator used by NVMe partitions is recognised.
    assert overlaps_device("/dev/nvme0n1", "/dev/nvme0n1p1")
    assert overlaps_device("/dev/nvme0n1p1", "/dev/nvme0n1")


def test_overlaps_device_none_is_false():
    assert not overlaps_device("/dev/sdb", None)
    assert not overlaps_device("", "/dev/sdb")


# --- sanity: check_target --------------------------------------------------

def test_check_target_ok():
    res = check_target(good_probe())
    assert res.ok is True
    assert res.errors == ()
    assert res.size_bytes == 8 * GiB


def test_check_target_not_block_device():
    p = good_probe()
    res = check_target(TargetProbe(
        path=p.path, is_block_device=False, size_bytes=p.size_bytes,
        root_device=p.root_device, esp_device=p.esp_device))
    assert res.ok is False
    assert [e.code for e in res.errors] == [ERR_NOT_BLOCK_DEVICE]


def test_check_target_is_live_root_exact():
    res = check_target(good_probe(target="/dev/sda", root_device="/dev/sda"))
    assert res.ok is False
    assert ERR_IS_LIVE_ROOT in [e.code for e in res.errors]


def test_check_target_is_live_root_via_disk_overlap():
    # Target = the whole disk; live root lives on one of its partitions.
    res = check_target(good_probe(target="/dev/sda", root_device="/dev/sda1"))
    assert res.ok is False
    assert ERR_IS_LIVE_ROOT in [e.code for e in res.errors]


def test_check_target_is_live_esp():
    res = check_target(good_probe(target="/dev/sda1", esp_device="/dev/sda1"))
    assert res.ok is False
    assert ERR_IS_LIVE_ESP in [e.code for e in res.errors]


def test_check_target_esp_none_is_ignored():
    # No live ESP known (esp_device None) -> no ESP refusal.
    res = check_target(good_probe(esp_device=None, target="/dev/sdb"))
    assert res.ok is True


def test_check_target_too_small():
    res = check_target(good_probe(size=MIN_TARGET_SIZE_BYTES - 1))
    assert res.ok is False
    assert [e.code for e in res.errors] == [ERR_TOO_SMALL]


def test_check_target_exactly_minimum_is_ok():
    res = check_target(good_probe(size=MIN_TARGET_SIZE_BYTES))
    assert res.ok is True


def test_check_target_reports_all_failures_together():
    p = TargetProbe(path="/dev/sda", is_block_device=False,
                    size_bytes=1024, root_device="/dev/sda1",
                    esp_device="/dev/sda1")
    res = check_target(p)
    assert res.ok is False
    codes = [e.code for e in res.errors]
    assert ERR_NOT_BLOCK_DEVICE in codes
    assert ERR_IS_LIVE_ROOT in codes
    assert ERR_IS_LIVE_ESP in codes
    assert ERR_TOO_SMALL in codes
    # errors carry a human message referencing the code path
    assert all(e.message for e in res.errors)


def test_check_target_size_bytes_carried_even_when_ok():
    res = check_target(good_probe(size=5 * GiB))
    assert res.size_bytes == 5 * GiB


# --- build_plan: ordered step list -----------------------------------------

def test_build_plan_has_all_phases_in_order():
    p = build_plan(target="/dev/sdb", esp_size="512M",
                   hostname="laptop", probe=good_probe())
    seq = phases_in_order(p.steps)
    # several phases span steps: 1+4+2+2+6+3+1 = 19 total
    assert seq.count("partition") == 4
    assert seq.count("format") == 2
    assert seq.count("copy") == 2
    assert seq.count("configure") == 6
    assert seq.count("bootloader") == 3
    assert len(seq) == 19


def test_build_plan_sanity_propagated():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    assert p.sanity.ok is True
    p2 = build_plan(target="/dev/sdb", esp_size="512M",
                    probe=TargetProbe(path="/dev/sdb", is_block_device=True,
                                      size_bytes=1024))
    assert p2.sanity.ok is False


def test_esp_size_parametric_in_partition_argv():
    # <esp-size> is the ESP end offset; the root partition starts there.
    p = build_plan(target="/dev/sdb", esp_size="1G", probe=good_probe())
    parts = [s for s in p.steps if s.phase == "partition"]
    assert parts[1].argv == (
        "parted", "-s", "/dev/sdb", "mkpart", "ESP", "fat32", "1MiB", "1G")
    assert parts[3].argv == (
        "parted", "-s", "/dev/sdb", "mkpart", "root", "ext4", "1G", "100%")
    # the size must appear in no other step
    joined = " ".join(" ".join(s.argv) for s in p.steps if s.argv)
    assert joined.count("1G") == 2


def test_build_plan_default_rootfs_is_running_root():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    assert p.rootfs_src is None
    copies = [s for s in p.steps if s.phase == "copy"]
    # the rsync step copies the running root "/"
    assert copies[1].argv[0] == "rsync"
    assert copies[1].argv[-2:] == ("/", WORK_DIR)
    assert copies[1].description.startswith("rsync: copy the source rootfs (/)")


def test_build_plan_explicit_rootfs_src_tar():
    p = build_plan(target="/dev/sdb", esp_size="512M",
                   rootfs_src="/srv/rootfs.tar", probe=good_probe())
    assert p.rootfs_src == "/srv/rootfs.tar"
    copies = [s for s in p.steps if s.phase == "copy"]
    assert len(copies) == 2
    assert copies[1].argv == ("tar", "-xpf", "/srv/rootfs.tar", "-C", WORK_DIR)
    assert "rootfs tarball /srv/rootfs.tar" in copies[1].description


def test_build_plan_explicit_rootfs_src_dir():
    p = build_plan(target="/dev/sdb", esp_size="512M",
                   rootfs_src="/srv/rootfs", probe=good_probe())
    copies = [s for s in p.steps if s.phase == "copy"]
    assert copies[1].argv[0] == "rsync"
    # explicit directory is normalised to a single trailing slash
    assert copies[1].argv[-2:] == ("/srv/rootfs/", WORK_DIR)


def test_no_step_is_pending_any_more():
    # Every phase is now filled in; no step may still carry a "pending" note.
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    assert not any("pending" in (s.note or "") for s in p.steps)


def test_internal_steps_have_no_argv():
    # sanity and teardown are internal (argv=None, stable "internal:" note).
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    for phase in ("sanity", "teardown"):
        s = next(x for x in p.steps if x.phase == phase)
        assert s.argv is None
        assert s.note is not None and "internal" in s.note


def test_external_phases_carry_argv_not_pending_notes():
    # partition / format / copy / configure(reads) / bootloader are all real
    # argv steps now; none of them is a "pending" placeholder.
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    for s in p.steps:
        if s.phase in ("partition", "format", "copy", "bootloader"):
            assert s.argv is not None
            assert "pending" not in (s.note or "")


def test_build_plan_uses_collector_when_probe_omitted(monkeypatch):
    called = {}

    def fake_collect(target):
        called["target"] = target
        return good_probe(target)

    monkeypatch.setattr(planmod, "collect_target_probe", fake_collect)
    p = build_plan(target="/dev/nvme1n1", esp_size="512M")
    assert called["target"] == "/dev/nvme1n1"
    assert p.sanity.ok is True


# --- determinism -----------------------------------------------------------

def _sample_kwargs():
    return dict(target="/dev/sdb", esp_size="512M", hostname="laptop",
                rootfs_src=None, probe=good_probe())


def test_plan_json_is_deterministic():
    a = build_plan(**_sample_kwargs())
    b = build_plan(**_sample_kwargs())
    assert a.to_json() == b.to_json()
    assert a.to_dict() == b.to_dict()
    assert render_plan_text(a) == render_plan_text(b)


def test_plan_changes_with_inputs():
    a = build_plan(**_sample_kwargs())
    b = build_plan(target="/dev/sdc", esp_size="1G", hostname="other",
                   probe=good_probe(target="/dev/sdc"))
    assert a.to_json() != b.to_json()
    assert "1G" in b.to_json() and "512M" not in b.to_json()
    assert "other" in b.to_json()


# --- golden ----------------------------------------------------------------

def test_golden_plan_dict_sample_target():
    p = build_plan(**_sample_kwargs())
    assert p.to_dict() == {
        "target": "/dev/sdb",
        "esp_size": "512M",
        "hostname": "laptop",
        "rootfs_src": None,
        "minimum_target_size_bytes": MIN_TARGET_SIZE_BYTES,
        "sanity": {
            "ok": True,
            "size_bytes": 8 * GiB,
            "errors": [],
        },
        "steps": [
            {
                "seq": 1,
                "phase": "sanity",
                "description": (
                    "Verify target /dev/sdb is a block device, is not the "
                    "live root/ESP device, and is at least 4 GiB"
                ),
                "argv": None,
                "note": "internal: performed by check_target() before any step runs",
            },
            {
                "seq": 2,
                "phase": "partition",
                "description": "parted: create a fresh GPT partition table",
                "argv": ["parted", "-s", "/dev/sdb", "mklabel", "gpt"],
                "note": None,
            },
            {
                "seq": 3,
                "phase": "partition",
                "description": (
                    "parted: create the EFI System Partition ESP "
                    "(fat32, 1MiB..512M)"
                ),
                "argv": [
                    "parted", "-s", "/dev/sdb", "mkpart", "ESP", "fat32",
                    "1MiB", "512M",
                ],
                "note": None,
            },
            {
                "seq": 4,
                "phase": "partition",
                "description": "parted: set the esp flag on partition 1",
                "argv": ["parted", "-s", "/dev/sdb", "set", "1", "esp", "on"],
                "note": None,
            },
            {
                "seq": 5,
                "phase": "partition",
                "description": (
                    "parted: create the root partition root (ext4, 512M..100%)"
                ),
                "argv": [
                    "parted", "-s", "/dev/sdb", "mkpart", "root", "ext4",
                    "512M", "100%",
                ],
                "note": None,
            },
            {
                "seq": 6,
                "phase": "format",
                "description": (
                    "mkfs.vfat: format /dev/sdb1 as FAT32 with label "
                    "PENGYOS-ESP"
                ),
                "argv": ["mkfs.vfat", "-F32", "-n", "PENGYOS-ESP", "/dev/sdb1"],
                "note": None,
            },
            {
                "seq": 7,
                "phase": "format",
                "description": (
                    "mkfs.ext4: format /dev/sdb2 as ext4 with label pengyos-root"
                ),
                "argv": ["mkfs.ext4", "-L", "pengyos-root", "-F", "/dev/sdb2"],
                "note": None,
            },
            {
                "seq": 8,
                "phase": "copy",
                "description": (
                    "mount: mount the root partition /dev/sdb2 at the work "
                    "dir /mnt/pengyos-install"
                ),
                "argv": ["mount", "/dev/sdb2", "/mnt/pengyos-install"],
                "note": None,
            },
            {
                "seq": 9,
                "phase": "copy",
                "description": (
                    "rsync: copy the source rootfs (/) into the work dir "
                    "/mnt/pengyos-install with -aHAX --numeric-ids, excluding "
                    "the virtual dirs and the target device"
                ),
                "argv": [
                    "rsync", "-aHAX", "--numeric-ids",
                    "--exclude=/proc", "--exclude=/sys", "--exclude=/dev",
                    "--exclude=/run", "--exclude=/mnt", "--exclude=/media",
                    "--exclude=/tmp", "--exclude=/dev/sdb",
                    "/", "/mnt/pengyos-install",
                ],
                "note": None,
            },
            {
                "seq": 10,
                "phase": "configure",
                "description": (
                    "blkid: read the PARTUUID of the ESP partition /dev/sdb1"
                ),
                "argv": ["blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb1"],
                "note": None,
            },
            {
                "seq": 11,
                "phase": "configure",
                "description": (
                    "blkid: read the PARTUUID of the root partition /dev/sdb2"
                ),
                "argv": ["blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb2"],
                "note": None,
            },
            {
                "seq": 12,
                "phase": "configure",
                "description": (
                    "write /etc/fstab (PARTUUID= lines for root and ESP, "
                    "from the blkid output)"
                ),
                "argv": None,
                "note": (
                    "internal: written at install time with render_fstab() "
                    "from the two blkid steps above"
                ),
            },
            {
                "seq": 13,
                "phase": "configure",
                "description": "write /etc/hostname (laptop)",
                "argv": None,
                "note": (
                    "internal: written at install time with render_hostname() "
                    "(one line)"
                ),
            },
            {
                "seq": 14,
                "phase": "configure",
                "description": "write the install marker /etc/pengyos/installed",
                "argv": None,
                "note": (
                    "internal: written at install time with "
                    "render_installed_marker()"
                ),
            },
            {
                "seq": 15,
                "phase": "configure",
                "description": (
                    "write the fallback GRUB config /boot/efi/EFI/BOOT/"
                    "grub.cfg on the ESP (root=PARTUUID=<root-partuuid>)"
                ),
                "argv": None,
                "note": (
                    "internal: written at install time with render_grub_cfg() "
                    "after the ESP is mounted (bootloader phase)"
                ),
            },
            {
                "seq": 16,
                "phase": "bootloader",
                "description": (
                    "mount: mount the ESP partition /dev/sdb1 at "
                    "/mnt/pengyos-install/boot/efi"
                ),
                "argv": ["mount", "/dev/sdb1", "/mnt/pengyos-install/boot/efi"],
                "note": None,
            },
            {
                "seq": 17,
                "phase": "bootloader",
                "description": (
                    "grub-install: install GRUB for x86_64-efi with "
                    "--bootloader-id=PengyOS --removable"
                ),
                "argv": [
                    "grub-install",
                    "--target=x86_64-efi",
                    "--efi-directory=/mnt/pengyos-install/boot/efi",
                    "--boot-directory=/mnt/pengyos-install/boot",
                    "--bootloader-id=PengyOS",
                    "--removable",
                ],
                "note": None,
            },
            {
                "seq": 18,
                "phase": "bootloader",
                "description": (
                    "efibootmgr: create the firmware boot entry pointing at "
                    "the removable loader (optional; the fallback path boots "
                    "without it)"
                ),
                "argv": [
                    "efibootmgr", "-c", "-d", "/dev/sdb", "-p", "1",
                    "-L", "PengyOS", "-l", "/EFI/BOOT/bootx64.efi",
                ],
                "note": (
                    "optional: best-effort NVRAM entry; a failure here does "
                    "not abort the install (the --removable fallback path "
                    "already boots)"
                ),
            },
            {
                "seq": 19,
                "phase": "teardown",
                "description": "sync, unmount ESP then root, print a summary",
                "argv": None,
                "note": "internal: performed during install execution",
            },
        ],
    }


def test_golden_plan_json_is_valid_and_roundtrips():
    p = build_plan(**_sample_kwargs())
    raw = p.to_json()
    parsed = json.loads(raw)
    assert parsed == p.to_dict()
    # key order is stable and known
    assert list(parsed.keys()) == [
        "target", "esp_size", "hostname", "rootfs_src",
        "minimum_target_size_bytes", "sanity", "steps",
    ]


def test_golden_partition_steps_exact_argv():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    parts = [s for s in p.steps if s.phase == "partition"]
    assert [s.argv for s in parts] == [
        ("parted", "-s", "/dev/sdb", "mklabel", "gpt"),
        ("parted", "-s", "/dev/sdb", "mkpart", "ESP", "fat32", "1MiB", "512M"),
        ("parted", "-s", "/dev/sdb", "set", "1", "esp", "on"),
        ("parted", "-s", "/dev/sdb", "mkpart", "root", "ext4", "512M", "100%"),
    ]


def test_golden_format_steps_exact_argv():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    fmts = [s for s in p.steps if s.phase == "format"]
    assert [s.argv for s in fmts] == [
        ("mkfs.vfat", "-F32", "-n", "PENGYOS-ESP", "/dev/sdb1"),
        ("mkfs.ext4", "-L", "pengyos-root", "-F", "/dev/sdb2"),
    ]


def test_partition_labels_are_stable_constants():
    assert ESP_PART_NAME == "ESP"
    assert ROOT_PART_NAME == "root"
    assert ESP_FS_LABEL == "PENGYOS-ESP"
    assert ROOT_FS_LABEL == "pengyos-root"
    assert ESP_START_OFFSET == "1MiB"


def test_partition_steps_builder_direct():
    # The builder is a pure function of (target, esp-size) — golden on its own.
    steps = partition_steps("/dev/sdc", "256M")
    assert len(steps) == 4
    assert [s.phase for s in steps] == ["partition"] * 4
    assert [s.argv for s in steps] == [
        ("parted", "-s", "/dev/sdc", "mklabel", "gpt"),
        ("parted", "-s", "/dev/sdc", "mkpart", "ESP", "fat32", "1MiB", "256M"),
        ("parted", "-s", "/dev/sdc", "set", "1", "esp", "on"),
        ("parted", "-s", "/dev/sdc", "mkpart", "root", "ext4", "256M", "100%"),
    ]
    assert all(s.note is None for s in steps)
    assert all(s.description.startswith("parted:") for s in steps)


def test_format_steps_builder_direct():
    steps = format_steps("/dev/sdc")
    assert len(steps) == 2
    assert [s.phase for s in steps] == ["format"] * 2
    assert [s.argv for s in steps] == [
        ("mkfs.vfat", "-F32", "-n", "PENGYOS-ESP", "/dev/sdc1"),
        ("mkfs.ext4", "-L", "pengyos-root", "-F", "/dev/sdc2"),
    ]
    assert all(s.note is None for s in steps)


# --- copy: source discovery -------------------------------------------------

def test_classify_rootfs_src_table():
    # None (no --rootfs-src) is the running "/" -> directory kind.
    assert classify_rootfs_src(None) == COPY_SRC_KIND_DIR
    assert classify_rootfs_src("/srv/rootfs") == COPY_SRC_KIND_DIR
    assert classify_rootfs_src("/srv/rootfs/") == COPY_SRC_KIND_DIR
    # the extension must be a full suffix of the file name
    assert classify_rootfs_src("/srv/rootfs.tarball") == COPY_SRC_KIND_DIR
    assert classify_rootfs_src("/srv/rootfs.tar.bz") == COPY_SRC_KIND_DIR
    # recognised tarball extensions (case-insensitive)
    for name in (
        "/srv/rootfs.tar", "/srv/rootfs.tar.gz", "/srv/rootfs.tgz",
        "/srv/rootfs.tar.xz", "/srv/rootfs.txz", "/srv/rootfs.tar.bz2",
        "/srv/rootfs.tbz2", "/srv/ROOTFS.TAR",
    ):
        assert classify_rootfs_src(name) == COPY_SRC_KIND_TAR, name
    assert COPY_SRC_KIND_DIR == "dir"
    assert COPY_SRC_KIND_TAR == "tar"


def test_rsync_source_arg_normalisation():
    assert rsync_source_arg(None) == "/"
    assert rsync_source_arg("/") == "/"
    assert rsync_source_arg("/srv/rootfs") == "/srv/rootfs/"
    assert rsync_source_arg("/srv/rootfs/") == "/srv/rootfs/"
    assert rsync_source_arg("/srv/rootfs///") == "/srv/rootfs/"


def test_copy_steps_builder_default_running_root():
    steps = copy_steps("/dev/sdb", None)
    assert len(steps) == 2
    assert [s.phase for s in steps] == ["copy"] * 2
    assert [s.argv for s in steps] == [
        ("mount", "/dev/sdb2", "/mnt/pengyos-install"),
        (
            "rsync", "-aHAX", "--numeric-ids",
            "--exclude=/proc", "--exclude=/sys", "--exclude=/dev",
            "--exclude=/run", "--exclude=/mnt", "--exclude=/media",
            "--exclude=/tmp", "--exclude=/dev/sdb",
            "/", "/mnt/pengyos-install",
        ),
    ]
    assert all(s.note is None for s in steps)
    assert steps[1].description.startswith(
        "rsync: copy the source rootfs (/) into the work dir /mnt/pengyos-install")


def test_copy_steps_builder_explicit_dir():
    steps = copy_steps("/dev/sdc", "/srv/rootfs")
    assert len(steps) == 2
    assert steps[1].argv[0] == "rsync"
    # explicit directory normalised to a single trailing slash; the mount
    # step still targets <tgt>2
    assert steps[1].argv[-2:] == ("/srv/rootfs/", WORK_DIR)
    assert steps[0].argv == ("mount", "/dev/sdc2", WORK_DIR)


def test_copy_steps_builder_tar():
    steps = copy_steps("/dev/sdc", "/srv/rootfs.tar.gz")
    assert len(steps) == 2
    assert steps[0].argv == ("mount", "/dev/sdc2", WORK_DIR)
    assert steps[1].argv == (
        "tar", "-xpf", "/srv/rootfs.tar.gz", "-C", "/mnt/pengyos-install")
    assert "rootfs tarball /srv/rootfs.tar.gz" in steps[1].description
    # a tar source must not produce an rsync step
    assert not any(s.argv[0] == "rsync" for s in steps)


def test_copy_steps_excludes_are_virtual_dirs_plus_target_in_order():
    steps = copy_steps("/dev/nvme0n1", None)
    argv = steps[1].argv
    excludes = tuple(a for a in argv if a.startswith("--exclude="))
    assert excludes == (
        tuple(f"--exclude={d}" for d in VIRTUAL_DIR_EXCLUDES)
        + ("--exclude=/dev/nvme0n1",)
    )


def test_copy_constants_are_stable():
    assert WORK_DIR == "/mnt/pengyos-install"
    assert VIRTUAL_DIR_EXCLUDES == (
        "/proc", "/sys", "/dev", "/run", "/mnt", "/media", "/tmp")


# --- plan: copy phase integration -------------------------------------------

def test_plan_copy_phase_default_rsync_golden():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    copies = [s for s in p.steps if s.phase == "copy"]
    assert [s.argv for s in copies] == [
        ("mount", "/dev/sdb2", WORK_DIR),
        (
            "rsync", "-aHAX", "--numeric-ids",
            "--exclude=/proc", "--exclude=/sys", "--exclude=/dev",
            "--exclude=/run", "--exclude=/mnt", "--exclude=/media",
            "--exclude=/tmp", "--exclude=/dev/sdb",
            "/", WORK_DIR,
        ),
    ]
    assert all(s.note is None for s in copies)


def test_plan_copy_phase_tar_source():
    p = build_plan(target="/dev/sdb", esp_size="512M",
                   rootfs_src="/srv/rootfs.tar", probe=good_probe())
    copies = [s for s in p.steps if s.phase == "copy"]
    assert copies[1].argv == ("tar", "-xpf", "/srv/rootfs.tar", "-C", WORK_DIR)
    # no rsync anywhere in the plan for a tar source
    assert "rsync" not in p.to_json()


def test_plan_tar_source_is_deterministic():
    kw = dict(target="/dev/sdb", esp_size="512M",
              rootfs_src="/srv/rootfs.tar.gz", probe=good_probe())
    a, b = build_plan(**kw), build_plan(**kw)
    assert a.to_json() == b.to_json()
    assert render_plan_text(a) == render_plan_text(b)


# --- rendering -------------------------------------------------------------

def test_render_plan_text_shape():
    p = build_plan(**_sample_kwargs())
    text = render_plan_text(p)
    lines = text.splitlines()
    assert lines[0] == "PengyOS install plan"
    assert "  target:   /dev/sdb" in text
    assert "  esp-size: 512M" in text
    assert "  hostname: laptop" in text
    assert "  rootfs:   / (the running root)" in text
    # numbered steps, all phases present in order (19 steps)
    assert "  1. [sanity]" in text
    assert "  19. [teardown]" in text
    seqs = [
        int(ln.lstrip().split(".", 1)[0])
        for ln in lines
        if ln.lstrip()[:1].isdigit() and ". [" in ln
    ]
    assert seqs == list(range(1, 20))
    # the real argv renders as shell-quoted command lines
    assert "       $ parted -s /dev/sdb mklabel gpt" in text
    assert ("       $ parted -s /dev/sdb mkpart ESP fat32 1MiB 512M") in text
    assert ("       $ parted -s /dev/sdb set 1 esp on") in text
    assert ("       $ parted -s /dev/sdb mkpart root ext4 512M 100%") in text
    assert ("       $ mkfs.vfat -F32 -n PENGYOS-ESP /dev/sdb1") in text
    assert ("       $ mkfs.ext4 -L pengyos-root -F /dev/sdb2") in text
    assert ("       $ mount /dev/sdb2 /mnt/pengyos-install") in text
    assert (
        "       $ rsync -aHAX --numeric-ids --exclude=/proc --exclude=/sys "
        "--exclude=/dev --exclude=/run --exclude=/mnt --exclude=/media "
        "--exclude=/tmp --exclude=/dev/sdb / /mnt/pengyos-install"
    ) in text
    # the configure phase renders the blkid reads; the file writes render
    # as internal notes (no $ line)
    assert ("       $ blkid -s PARTUUID -o value /dev/sdb1") in text
    assert ("       $ blkid -s PARTUUID -o value /dev/sdb2") in text
    assert "       (internal: written at install time with render_fstab()" in text
    # the bootloader phase renders mount ESP + grub-install + efibootmgr
    assert (
        "       $ mount /dev/sdb1 /mnt/pengyos-install/boot/efi"
    ) in text
    assert (
        "       $ grub-install --target=x86_64-efi "
        "--efi-directory=/mnt/pengyos-install/boot/efi "
        "--boot-directory=/mnt/pengyos-install/boot "
        "--bootloader-id=PengyOS --removable"
    ) in text
    assert (
        "       $ efibootmgr -c -d /dev/sdb -p 1 -L PengyOS "
        "-l /EFI/BOOT/bootx64.efi"
    ) in text
    assert "(optional: best-effort NVRAM entry" in text
    # 4 parted + 2 mkfs + 1 mount(root) + 1 rsync + 2 blkid
    # + 1 mount(ESP) + 1 grub-install + 1 efibootmgr = 13 argv steps
    assert text.count("       $ ") == 13


# --- configure: blkid steps + PARTUUID parsing ------------------------------

#: Fixture PARTUUIDs (deterministic; never from real hardware).
ROOT_UUID = "12345678-90ab-cdef-1234-567890abcdef"
ESP_UUID = "fedcba98-7654-abcd-ef01-23456789abcd"


def test_partuuid_steps_builder_direct():
    steps = partuuid_steps("/dev/sdc")
    assert len(steps) == 2
    assert [s.phase for s in steps] == ["configure"] * 2
    assert [s.argv for s in steps] == [
        ("blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdc1"),
        ("blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdc2"),
    ]
    assert all(s.note is None for s in steps)


def test_plan_configure_phase_shape():
    p = build_plan(target="/dev/sdb", esp_size="512M",
                   hostname="laptop", probe=good_probe())
    cfgs = [s for s in p.steps if s.phase == "configure"]
    assert len(cfgs) == 6
    # the first two are the blkid reads (argv), ESP then root
    assert cfgs[0].argv == (
        "blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb1")
    assert cfgs[1].argv == (
        "blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb2")
    # the four file writes are internal: no argv, a stable internal note,
    # and — unlike the old placeholder — no longer 'pending'
    for s in cfgs[2:]:
        assert s.argv is None
        assert s.note is not None and s.note.startswith("internal:")
        assert "pending" not in s.note
    assert not any("pending" in (s.note or "") for s in cfgs)
    # the hostname write step names the planned hostname
    assert cfgs[2 + 1].description == "write /etc/hostname (laptop)"


def test_plan_configure_phase_default_hostname_note():
    # without --hostname the step names the live hostname (stable string;
    # the live value is read at install time, not plan time)
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    cfgs = [s for s in p.steps if s.phase == "configure"]
    assert cfgs[3].description == (
        "write /etc/hostname (the live system's hostname)")
    # the two plans differ only where the hostname feeds in
    a = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    b = build_plan(target="/dev/sdb", esp_size="512M", hostname="laptop",
                   probe=good_probe())
    assert a.to_json() != b.to_json()


def test_configure_file_steps_builder_direct():
    steps = configure_file_steps("laptop", "/dev/sdc")
    assert len(steps) == 4
    assert [s.phase for s in steps] == ["configure"] * 4
    assert all(s.argv is None for s in steps)
    assert [s.description for s in steps] == [
        "write /etc/fstab (PARTUUID= lines for root and ESP, from the blkid output)",
        "write /etc/hostname (laptop)",
        "write the install marker /etc/pengyos/installed",
        "write the fallback GRUB config /boot/efi/EFI/BOOT/grub.cfg on the ESP (root=PARTUUID=<root-partuuid>)",
    ]
    assert all(s.note.startswith("internal:") for s in steps)


def test_parse_partuuid_fixture_output():
    # fixture blkid -s PARTUUID -o value output: one token, trailing newline
    assert parse_partuuid(f"{ROOT_UUID}\n", "/dev/sdb1") == ROOT_UUID
    # surrounding whitespace is tolerated around the single token
    assert parse_partuuid(f"  {ESP_UUID} \n", "/dev/sdb2") == ESP_UUID


def test_parse_partuuid_empty_is_hard_error():
    with pytest.raises(ConfigError) as ei:
        parse_partuuid("", "/dev/sdb1")
    assert ei.value.code == ERR_MISSING_PARTUUID
    assert "/dev/sdb1" in str(ei.value)


def test_parse_partuuid_blank_output_is_hard_error():
    with pytest.raises(ConfigError) as ei:
        parse_partuuid("\n  \n", "/dev/sdb2")
    assert ei.value.code == ERR_MISSING_PARTUUID


def test_parse_partuuid_multiple_tokens_is_hard_error():
    # blkid -o value never emits two tokens; anything else is unusable
    with pytest.raises(ConfigError) as ei:
        parse_partuuid(f"{ROOT_UUID} {ESP_UUID}", "/dev/sdb1")
    assert ei.value.code == ERR_MISSING_PARTUUID


# --- configure: rendered file contents (golden) -----------------------------

def test_render_fstab_golden():
    assert render_fstab(ROOT_UUID, ESP_UUID) == (
        "# /etc/fstab - generated by pengyos-installer (do not edit by hand)\n"
        f"PARTUUID={ROOT_UUID} / ext4 defaults 0 1\n"
        f"PARTUUID={ESP_UUID} /boot/efi vfat umask=0077 0 1\n"
    )
    # the root entry comes before the ESP entry (stable order)
    fstab = render_fstab(ROOT_UUID, ESP_UUID)
    assert fstab.index(f"PARTUUID={ROOT_UUID} / ext4") \
        < fstab.index(f"PARTUUID={ESP_UUID} /boot/efi")


def test_render_fstab_missing_uuid_is_hard_error():
    with pytest.raises(ConfigError) as ei:
        render_fstab("", ESP_UUID)
    assert ei.value.code == ERR_MISSING_PARTUUID
    with pytest.raises(ConfigError):
        render_fstab(ROOT_UUID, "")


def test_render_fstab_constants_are_stable():
    assert ETC_FSTAB == "/etc/fstab"
    assert ETC_HOSTNAME == "/etc/hostname"
    assert INSTALL_MARKER == "/etc/pengyos/installed"
    assert ESP_MOUNTPOINT == "/boot/efi"
    assert FALLBACK_GRUB_CFG == "EFI/BOOT/grub.cfg"


def test_render_hostname_golden():
    assert render_hostname("laptop") == "laptop\n"
    # surrounding whitespace is stripped; exactly one trailing newline
    assert render_hostname("  laptop \n") == "laptop\n"


def test_render_hostname_empty_is_hard_error():
    with pytest.raises(ConfigError) as ei:
        render_hostname("   ")
    assert ei.value.code == ERR_INVALID_HOSTNAME


def test_render_installed_marker_golden():
    assert render_installed_marker("laptop", "/dev/sdb") == (
        "installed-by: pengyos-installer\n"
        "hostname: laptop\n"
        "target: /dev/sdb\n"
    )


def test_render_installed_marker_empty_hostname_is_hard_error():
    with pytest.raises(ConfigError) as ei:
        render_installed_marker("", "/dev/sdb")
    assert ei.value.code == ERR_INVALID_HOSTNAME


def test_render_grub_cfg_golden():
    assert render_grub_cfg(ROOT_UUID) == (
        "# generated by pengyos-installer (fallback EFI boot path, EFI/BOOT)\n"
        "set default=0\n"
        "set timeout=5\n"
        "\n"
        f"search --no-floppy --set=root --partuuid {ROOT_UUID}\n"
        "menuentry 'PengyOS' {\n"
        f"    linux /vmlinuz root=PARTUUID={ROOT_UUID} ro quiet\n"
        "    initrd /initrd.img\n"
        "}\n"
    )
    # the spec's 'root=PARTUUID=<root>' wiring is present
    assert f"root=PARTUUID={ROOT_UUID}" in render_grub_cfg(ROOT_UUID)
    # the custom timeout is honoured
    assert "set timeout=10" in render_grub_cfg(ROOT_UUID, timeout=10)
    assert GRUB_TIMEOUT == 5


def test_render_grub_cfg_missing_uuid_is_hard_error():
    with pytest.raises(ConfigError) as ei:
        render_grub_cfg("")
    assert ei.value.code == ERR_MISSING_PARTUUID


def test_configure_is_deterministic_and_input_sensitive():
    kw = dict(target="/dev/sdb", esp_size="512M", hostname="laptop",
              probe=good_probe())
    a, b = build_plan(**kw), build_plan(**kw)
    assert a.to_json() == b.to_json()
    c = build_plan(target="/dev/sdc", esp_size="512M", hostname="laptop",
                   probe=good_probe(target="/dev/sdc"))
    assert c.to_json() != a.to_json()
    # the target device appears in the blkid argv of the configure phase
    cfgs = [s for s in c.steps if s.phase == "configure"]
    assert cfgs[0].argv[-1] == "/dev/sdc1"
    assert cfgs[1].argv[-1] == "/dev/sdc2"


def test_render_plan_text_defaults_when_none():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    text = render_plan_text(p)
    assert "  hostname: (live)" in text
    assert "  rootfs:   / (the running root)" in text
    assert "  hostname: laptop" not in text
    assert "write /etc/hostname (the live system's hostname)" in text


def test_render_plan_text_shows_argv_when_present():
    # A plan carrying a real argv must render the shell-quoted command line.
    base = build_plan(**_sample_kwargs())
    steps = list(base.steps)
    steps[1] = type(steps[1])(phase="partition", description="mk gpt",
                              argv=("parted", "-s", "/dev/sdb", "mklabel", "gpt"))
    p = InstallPlan(target="/dev/sdb", esp_size="512M", hostname=None,
                    rootfs_src=None, min_target_size_bytes=MIN_TARGET_SIZE_BYTES,
                    sanity=base.sanity, steps=tuple(steps))
    text = render_plan_text(p)
    assert "$ parted -s /dev/sdb mklabel gpt" in text


# --- bootloader: mount ESP + grub-install + optional efibootmgr -------------

def test_bootloader_constants_are_stable():
    assert GRUB_TARGET == "x86_64-efi"
    assert BOOTLOADER_ID == "PengyOS"
    assert ESP_EFI_DIR == "/mnt/pengyos-install/boot/efi"
    assert GRUB_BOOT_DIR == "/mnt/pengyos-install/boot"
    assert EFI_LOADER_PATH == "/EFI/BOOT/bootx64.efi"
    # the ESP mount point sits under the work dir's /boot/efi
    assert ESP_EFI_DIR == WORK_DIR + "/boot/efi"
    assert GRUB_BOOT_DIR == WORK_DIR + "/boot"


def test_bootloader_steps_builder_direct():
    steps = bootloader_steps("/dev/sdc")
    assert len(steps) == 3
    assert [s.phase for s in steps] == ["bootloader"] * 3
    assert [s.argv for s in steps] == [
        ("mount", "/dev/sdc1", "/mnt/pengyos-install/boot/efi"),
        (
            "grub-install",
            "--target=x86_64-efi",
            "--efi-directory=/mnt/pengyos-install/boot/efi",
            "--boot-directory=/mnt/pengyos-install/boot",
            "--bootloader-id=PengyOS",
            "--removable",
        ),
        (
            "efibootmgr", "-c", "-d", "/dev/sdc", "-p", "1",
            "-L", "PengyOS", "-l", "/EFI/BOOT/bootx64.efi",
        ),
    ]
    # the mount + grub-install are plain commands; efibootmgr is optional
    assert steps[0].note is None
    assert steps[1].note is None
    assert steps[2].note is not None
    assert steps[2].note.startswith("optional:")
    # none of them is a "pending" placeholder anymore
    assert all("pending" not in (s.note or "") for s in steps)


def test_bootloader_grub_install_argv_flags():
    steps = bootloader_steps("/dev/sdb")
    grub = steps[1].argv
    # every documented flag is present exactly once
    assert grub[0] == "grub-install"
    assert grub.count("--removable") == 1
    assert "--target=x86_64-efi" in grub
    assert "--bootloader-id=PengyOS" in grub
    assert "--efi-directory=/mnt/pengyos-install/boot/efi" in grub
    assert "--boot-directory=/mnt/pengyos-install/boot" in grub


def test_bootloader_efibootmgr_points_at_removable_loader():
    steps = bootloader_steps("/dev/sdb")
    efi = steps[2].argv
    # -c = create, -d = disk, -p 1 = the ESP partition, -L label, -l loader
    assert efi == (
        "efibootmgr", "-c", "-d", "/dev/sdb", "-p", "1",
        "-L", BOOTLOADER_ID, "-l", EFI_LOADER_PATH,
    )
    # the loader it points at is exactly what grub-install --removable writes
    assert efi[-1] == "/EFI/BOOT/bootx64.efi"


def test_plan_bootloader_phase_shape():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    bl = [s for s in p.steps if s.phase == "bootloader"]
    assert len(bl) == 3
    # mount the ESP, then grub-install, then the optional efibootmgr
    assert bl[0].argv == ("mount", "/dev/sdb1", ESP_EFI_DIR)
    assert bl[1].argv[0] == "grub-install"
    assert bl[2].argv[0] == "efibootmgr"
    # the ESP mount step targets <tgt>1 and the work-dir boot/efi point
    assert bl[0].argv[-1] == WORK_DIR + "/boot/efi"
    # grub-install must run after the ESP is mounted (ordering within phase)
    order = [s.phase for s in p.steps]
    assert order.index("bootloader") <= order.index("teardown")


def test_plan_bootloader_efibootmgr_optional_note():
    p = build_plan(target="/dev/sdb", esp_size="512M", probe=good_probe())
    bl = [s for s in p.steps if s.phase == "bootloader"]
    assert bl[2].note is not None and bl[2].note.startswith("optional:")
    # the optional marker never appears on the required steps
    assert bl[0].note is None and bl[1].note is None
    # and never the word "pending" anywhere in the plan
    assert "pending" not in p.to_json()


def test_plan_bootloader_target_parametric():
    p = build_plan(target="/dev/nvme0n1", esp_size="512M",
                   probe=good_probe(target="/dev/nvme0n1"))
    bl = [s for s in p.steps if s.phase == "bootloader"]
    # the ESP partition is the p-suffixed form (the name ends in a digit),
    # and efibootmgr -d tracks the whole disk; -p is the partition *number*
    assert bl[0].argv[1] == "/dev/nvme0n1p1"
    assert bl[2].argv == (
        "efibootmgr", "-c", "-d", "/dev/nvme0n1", "-p", "1",
        "-L", "PengyOS", "-l", "/EFI/BOOT/bootx64.efi",
    )


def test_plan_bootloader_is_deterministic():
    kw = dict(target="/dev/sdb", esp_size="512M", hostname="laptop",
              probe=good_probe())
    a, b = build_plan(**kw), build_plan(**kw)
    assert a.to_json() == b.to_json()
    bl_a = [s for s in a.steps if s.phase == "bootloader"]
    bl_b = [s for s in b.steps if s.phase == "bootloader"]
    assert [s.argv for s in bl_a] == [s.argv for s in bl_b]


# --- CLI wiring: plan success / refusal / usage ----------------------------

def _cli_run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def test_cli_plan_success_prints_numbered_list(monkeypatch):
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    code, out, err = _cli_run("plan", "--target", "/dev/sdb")
    assert code == ExitCode.OK
    assert "PengyOS install plan" in out
    assert "  1. [sanity]" in out
    assert err == ""


def test_cli_plan_success_json(monkeypatch):
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    code, out, err = _cli_run("plan", "--target", "/dev/sdb", "--hostname",
                              "laptop", "--json")
    assert code == ExitCode.OK
    data = json.loads(out)
    assert data["target"] == "/dev/sdb"
    assert data["hostname"] == "laptop"
    assert data["sanity"]["ok"] is True
    phases_in_order(data["steps"])  # all phases, in order (multi-step phases)
    assert data["steps"][1]["argv"] == [
        "parted", "-s", "/dev/sdb", "mklabel", "gpt"]


def test_cli_plan_rootfs_src_tar_json(monkeypatch):
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    code, out, err = _cli_run("plan", "--target", "/dev/sdb", "--rootfs-src",
                              "/srv/rootfs.tar", "--json")
    assert code == ExitCode.OK
    data = json.loads(out)
    assert data["rootfs_src"] == "/srv/rootfs.tar"
    copies = [s for s in data["steps"] if s["phase"] == "copy"]
    assert copies[1]["argv"] == [
        "tar", "-xpf", "/srv/rootfs.tar", "-C", "/mnt/pengyos-install"]


def test_cli_plan_json_carries_configure_blkid_steps(monkeypatch):
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    code, out, err = _cli_run("plan", "--target", "/dev/sdb",
                              "--hostname", "laptop", "--json")
    assert code == ExitCode.OK
    data = json.loads(out)
    cfgs = [s for s in data["steps"] if s["phase"] == "configure"]
    assert len(cfgs) == 6
    assert cfgs[0]["argv"] == [
        "blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb1"]
    assert cfgs[1]["argv"] == [
        "blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb2"]
    assert cfgs[0]["note"] is None and cfgs[1]["note"] is None
    assert all(s["argv"] is None for s in cfgs[2:])
    assert all((s["note"] or "").startswith("internal:") for s in cfgs[2:])


def test_cli_plan_json_carries_bootloader_steps(monkeypatch):
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    code, out, err = _cli_run("plan", "--target", "/dev/sdb", "--json")
    assert code == ExitCode.OK
    data = json.loads(out)
    bl = [s for s in data["steps"] if s["phase"] == "bootloader"]
    assert len(bl) == 3
    assert bl[0]["argv"] == ["mount", "/dev/sdb1", "/mnt/pengyos-install/boot/efi"]
    assert bl[1]["argv"][0] == "grub-install"
    assert "--bootloader-id=PengyOS" in bl[1]["argv"]
    assert "--removable" in bl[1]["argv"]
    assert bl[1]["note"] is None
    assert bl[2]["argv"][0] == "efibootmgr"
    assert (bl[2]["note"] or "").startswith("optional:")
    phases_in_order(data["steps"])  # all phases, in order


def test_cli_plan_refusal_not_block_device(monkeypatch):
    def fake_collect(t):
        return TargetProbe(path=t, is_block_device=False, size_bytes=0)
    monkeypatch.setattr(planmod, "collect_target_probe", fake_collect)
    code, out, err = _cli_run("plan", "--target", "/dev/sdb")
    assert code == ExitCode.ERROR
    assert "refusing to plan an install for /dev/sdb" in err
    assert "[not-block-device]" in err
    assert out == ""


def test_cli_plan_refusal_too_small(monkeypatch):
    def fake_collect(t):
        return TargetProbe(path=t, is_block_device=True,
                           size_bytes=1000, root_device="/dev/sda")
    monkeypatch.setattr(planmod, "collect_target_probe", fake_collect)
    code, out, err = _cli_run("plan", "--target", "/dev/sdb")
    assert code == ExitCode.ERROR
    assert "[too-small]" in err


def test_cli_plan_refusal_live_device(monkeypatch):
    def fake_collect(t):
        return TargetProbe(path=t, is_block_device=True, size_bytes=8 * GiB,
                           root_device="/dev/sdb1")
    monkeypatch.setattr(planmod, "collect_target_probe", fake_collect)
    code, out, err = _cli_run("plan", "--target", "/dev/sdb")
    assert code == ExitCode.ERROR
    assert "[is-live-root]" in err


def test_cli_plan_missing_target_is_usage_error():
    code, out, err = _cli_run("plan")
    assert code == ExitCode.USAGE
    assert "target" in err


def test_cli_plan_unknown_option_is_usage_error():
    code, out, err = _cli_run("plan", "--target", "/dev/sdb", "--bogus")
    assert code == ExitCode.USAGE
    assert "unrecognized" in err


def test_install_refuses_without_yes_before_running():
    # install is implemented; without --yes it refuses (exit 1) before running
    # anything (no runner, no probe, no disk).
    code, out, err = _cli_run("install", "--target", "/dev/sdb")
    assert code == ExitCode.ERROR
    assert "--yes" in err
    assert out == ""


def test_install_plan_model_importable():
    # Guard the public API the follow-on tasks will lean on.
    assert issubclass(SanityResult, object)
    assert isinstance(MIN_TARGET_SIZE_BYTES, int)
    assert MIN_TARGET_SIZE_BYTES == 4 * GiB
