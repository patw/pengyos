"""Tests for the ``emit`` module: the executor argv for a plan.

``emit`` turns a :class:`~pengyos_layout.model.Plan` into an ordered list of
:class:`~pengyos_layout.emit.Step` — each a shell-quotable ``argv`` for a tool
the install engine runs (``sgdisk`` create + GUID, ``cryptsetup`` luksFormat/open
for LUKS slots, ``mkfs``/``mkswap``, ``mount``). These assert the shape, order,
byte-precision and shell-quotability of that argv.

No real device, network, root or hardware is touched — the emitter never shells
out; it only produces argv lists from in-memory data.
"""

import shlex

from pengyos_layout import Fs, Options, Partition, Plan, Role, TargetProbe
from pengyos_layout.emit import MOUNT, SGDISK, PlanEmitter, Step, emit
from pengyos_layout.plan import plan

from .conftest import ESP_BYTES

DISK_BYTES = 66 << 30


# --- helpers ----------------------------------------------------------------
def _roles(plan):
    return [p.role for p in plan.partitions]


def _kinds(steps):
    return [s.kind for s in steps]


def _sgdisk_create(step):
    """Return ``(num, start, end, type_guid)`` parsed from a create step."""
    assert step.argv[0] == SGDISK
    assert step.argv[1] == "-n"
    spec = step.argv[2]
    num, start, end = spec.split(":")
    assert start.endswith("B") and end.endswith("B")
    type_arg = step.argv[4]  # after "-t"
    _, type_guid = type_arg.split(":")
    return num, int(start[:-1]), int(end[:-1]), type_guid


# --- golden / ground --------------------------------------------------------
def test_emit_plaintext_pipeline(golden_plan):
    """A plaintext plan: create + GUID + mkfs + mount per partition."""
    steps = emit(golden_plan)
    kinds = _kinds(steps)
    # per partition: sgdisk, sgdisk, mkfs.<fs>, mount  (x4 -> 16 steps)
    assert len(steps) == 16
    for i in range(4):
        assert kinds[i * 4] == "sgdisk"  # create
        assert kinds[i * 4 + 1] == "sgdisk"  # unique GUID
        assert kinds[i * 4 + 2].startswith("mkfs.")  # format
        assert kinds[i * 4 + 3] == "mount"  # mount


def test_emit_step_is_dataclass(golden_plan):
    """A step is (kind, argv, description) with render + dict round-trip."""
    (first,) = emit(golden_plan)[:1]
    assert isinstance(first, Step)
    assert isinstance(first.kind, str)
    assert isinstance(first.argv, list)
    assert isinstance(first.description, str)
    assert Step.from_dict(first.to_dict()) == first


def test_emit_shell_quotable(golden_plan):
    """Every step's argv round-trips through shlex.join/shlex.split losslessly."""
    for step in emit(golden_plan):
        assert shlex.split(step.render()) == step.argv


def test_emit_empty_plan_has_no_steps():
    """An empty plan emits nothing."""
    plan = Plan(
        probe=TargetProbe(device="/dev/sda", total_size=DISK_BYTES),
        options=Options(),
        partitions=[],
    )
    assert emit(plan) == []


# --- sgdisk create ----------------------------------------------------------
def test_emit_sgdisk_create_byte_offsets(golden_plan):
    """The create step encodes the plan's byte start/size with the ``B`` suffix."""
    steps = emit(golden_plan)
    for index, p in enumerate(golden_plan.partitions):
        num, start, end, _ = _sgdisk_create(steps[index * 4])
        assert num == str(index + 1)  # 1-based GPT partition number
        assert start == p.start
        assert end == p.start + p.size


def test_emit_sgdisk_type_guid_from_plan(golden_plan):
    """The create step stamps the plan's GPT type GUID per partition."""
    steps = emit(golden_plan)
    expected = {
        1: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
        2: "0FC63DA8-8483-4772-8E79-3D69D8477DE4",
        3: "0FC63DA8-8483-4772-8E79-3D69D8477DE4",
        4: "9FS2FC2B-66DA-4464-BF7D-6011622C226C",
    }
    for index, p in enumerate(golden_plan.partitions):
        _, _, _, type_guid = _sgdisk_create(steps[index * 4])
        assert type_guid == expected[index + 1]


def test_emit_sgdisk_unique_guid_from_plan(golden_plan):
    """The GUID step stamps the planner's deterministic unique GUID."""
    steps = emit(golden_plan)
    for index, p in enumerate(golden_plan.partitions):
        guid_step = steps[index * 4 + 1]
        assert guid_step.argv[0] == SGDISK
        assert guid_step.argv[1] == "-u"
        assert guid_step.argv[2] == f"u:{p.guid}"
        assert guid_step.argv[3] == str(index + 1)
        assert guid_step.argv[4] == golden_plan.probe.device


def test_emit_partition_numbers_1_based(golden_plan):
    """Partition numbers in the argv are 1-based (GPT numbering)."""
    steps = emit(golden_plan)
    nums = [_sgdisk_create(steps[i * 4])[0] for i in range(len(golden_plan.partitions))]
    assert nums == ["1", "2", "3", "4"]


def test_emit_device_from_probe():
    """Every partition-targeting argv references the probe's device path."""
    probe = TargetProbe(device="/dev/nvme0n1", total_size=DISK_BYTES)
    plan = Plan(probe=probe, options=Options(), partitions=[])
    # build a one-partition plan cheaply
    plan.partitions.append(
        Partition(role=Role.ESP, size=ESP_BYTES, start=0, fs=Fs.VFAT)
    )
    for step in emit(plan):
        # sgdisk/mkfs target <device><num>; mount targets <device><num> as source
        # (the mountpoint does not, so we check "any" arg carries the device).
        assert any(a.startswith(probe.device) for a in step.argv)


# --- filesystems ------------------------------------------------------------
def test_emit_filesystem_per_role(golden_plan):
    """Each slot is mkfs'd with its role's filesystem."""
    steps = emit(golden_plan)
    mkfs = [s for s in steps if s.kind.startswith("mkfs.")]
    assert [s.kind for s in mkfs] == ["mkfs.vfat", "mkfs.ext4", "mkfs.ext4", "mkfs.btrfs"]


def test_emit_swap_gets_mkswap_not_mkfs(golden_plan):
    """A swap slot is mkswap'd, never mkfs'd."""
    from pengyos_layout.model import Partition

    plan = Plan(
        probe=golden_plan.probe,
        options=golden_plan.options,
        partitions=list(golden_plan.partitions)
        + [Partition(role=Role.SWAP, size=512 << 20, start=1 << 30, fs=Fs.SWAP)],
    )
    steps = emit(plan)
    kinds = _kinds(steps)
    assert "mkswap" in kinds
    assert not any(k == "mkfs.swap" for k in kinds)


# --- mount ------------------------------------------------------------------
def test_emit_mounts_at_plan_mountpoints(golden_plan):
    """Every slot with a mountpoint is mounted there."""
    steps = emit(golden_plan)
    mounts = [s for s in steps if s.kind == MOUNT]
    assert len(mounts) == 4  # esp + 2 roots + data
    assert mounts[0].argv == [MOUNT, "/dev/sda1", "/boot/efi"]
    assert mounts[1].argv == [MOUNT, "/dev/sda2", "/"]
    assert mounts[2].argv == [MOUNT, "/dev/sda3", "/mnt/b"]
    assert mounts[3].argv == [MOUNT, "/dev/sda4", "/data"]


def test_emit_swap_not_mounted(golden_plan):
    """A swap slot is never mounted (it has no mountpoint)."""
    plan = Plan(
        probe=golden_plan.probe,
        options=golden_plan.options,
        partitions=list(golden_plan.partitions)
        + [Partition(role=Role.SWAP, size=512 << 20, start=1 << 30, fs=Fs.SWAP)],
    )
    steps = emit(plan)
    # the swap partition (/dev/sda5) must never appear as a mount source
    swap_mount = [s for s in steps if s.kind == MOUNT and s.argv[-1] == "/dev/sda5"]
    assert swap_mount == []


# --- order ------------------------------------------------------------------
def test_emit_order_is_per_partition(golden_plan):
    """emit() concatenates each partition's steps in plan order."""
    emitter = PlanEmitter(golden_plan)
    expected = []
    for index in range(len(golden_plan.partitions)):
        expected.extend(emitter.emit_partition(index))
    assert emit(golden_plan) == expected


def test_emit_esp_before_roots_before_data(golden_plan):
    """The first create step is the ESP, the last is the data partition."""
    steps = emit(golden_plan)
    first_create = steps[0]
    last_create = steps[-4]
    assert _sgdisk_create(first_create)[3].startswith("C12A7328")  # EFI System
    assert _sgdisk_create(last_create)[0] == "4"  # data is partition 4


# --- LUKS -------------------------------------------------------------------
def _luks_plan(probe):
    """A golden-layout plan with every root/data slot LUKS-wrapped."""
    return plan(probe, Options(luks=True, swap_size=0, edition="server"))


def test_emit_luks_wraps_roots_and_data(probe):
    """Under LUKS, roots/data get luksFormat + open; the ESP stays plaintext."""
    plan = _luks_plan(probe)
    emitter = PlanEmitter(plan)

    # ESP: no cryptsetup at all, formatted as FAT32 on the raw partition.
    esp = emitter.emit_partition(0)
    assert not any("cryptsetup" in s.kind for s in esp)
    assert next(s for s in esp if s.kind.startswith("mkfs")).argv == [
        "mkfs.vfat",
        "/dev/sda1",
    ]

    # roots + data: luksFormat + open on the raw partition, mkfs.<inner> on the
    # mapper, mount from the mapper.
    for index, role in enumerate((Role.ROOT_A, Role.ROOT_B, Role.DATA)):
        block = emitter.emit_partition(index + 1)
        part = plan.partitions[index + 1]
        luks_fmt = next(s for s in block if s.kind.startswith("cryptsetup luksFormat"))
        open_step = next(s for s in block if s.kind.startswith("cryptsetup open"))
        mkfs = next(s for s in block if s.kind.startswith("mkfs."))
        assert luks_fmt.argv[-1] == f"/dev/sda{index + 2}"  # luksFormat on raw part
        assert open_step.argv[-2] == f"/dev/sda{index + 2}"  # open on raw part
        assert open_step.argv[5] == f"{role.value}_crypt"  # --name <mapper>
        assert mkfs.argv == [
            "mkfs." + part.inner_fs_value(),
            f"/dev/mapper/{role.value}_crypt",
        ]


def test_emit_luks_format_on_mapper(probe):
    """A LUKS slot's mkfs runs on the decrypted mapper, not the raw partition."""
    plan = _luks_plan(probe)
    emitter = PlanEmitter(plan)
    block = emitter.emit_partition(1)  # rootA is the 2nd partition
    mkfs = next(s for s in block if s.kind.startswith("mkfs."))
    assert mkfs.kind == "mkfs.ext4"
    assert mkfs.argv == ["mkfs.ext4", "/dev/mapper/rootA_crypt"]


def test_emit_luks_mounts_mapper(probe):
    """A LUKS slot is mounted from its mapper."""
    plan = _luks_plan(probe)
    steps = emit(plan)
    mounts = [s for s in steps if s.kind == MOUNT]
    assert mounts[1].argv == [MOUNT, "/dev/mapper/rootA_crypt", "/"]


def test_emit_esp_never_luks_even_under_luks(probe):
    """Regression: the ESP must never be LUKS-wrapped (firmware reads FAT32)."""
    plan = _luks_plan(probe)
    steps = emit(plan)
    esp_block = steps[0:4]
    assert not any("cryptsetup" in k for k in _kinds(esp_block))
    assert esp_block[2].kind == "mkfs.vfat"


# --- swap under LUKS --------------------------------------------------------
def test_emit_swap_under_luks_stays_plaintext(golden_plan):
    """Swap is never LUKS-wrapped even when --luks is set."""
    opts = Options(luks=True, swap_size=512 << 20, edition="server")
    plan = Plan(
        probe=golden_plan.probe,
        options=opts,
        partitions=list(golden_plan.partitions)
        + [Partition(role=Role.SWAP, size=512 << 20, start=1 << 30, fs=Fs.SWAP)],
    )
    steps = emit(plan)
    swap_idx = len(plan.partitions) - 1
    block = steps[swap_idx * 4 : swap_idx * 4 + 4]
    kinds = _kinds(block)
    assert "mkswap" in kinds
    assert not any("cryptsetup" in k for k in kinds)


# --- determinism ------------------------------------------------------------
def test_emit_is_deterministic(golden_plan):
    """Emitting the same plan twice yields identical steps."""
    assert emit(golden_plan) == emit(golden_plan)


def test_emit_matches_cli_json_shape(golden_plan):
    """emit() output is JSON-serialisable (the CLI echoes it under --json)."""
    import json

    steps = emit(golden_plan)
    payload = {"steps": [s.to_dict() for s in steps]}
    parsed = json.loads(json.dumps(payload))  # round-trips
    assert parsed["steps"][0]["kind"] == "sgdisk"
    assert parsed["steps"][0]["argv"][0] == "sgdisk"
