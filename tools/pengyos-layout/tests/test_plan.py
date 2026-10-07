"""Tests for the ``plan`` planner: derive the golden layout from probe + options.

The planner is the *layout as data* step: a target probe + edition/options ->
ordered partitions with sizes, filesystems, mountpoints, GPT type GUIDs and
deterministic per-partition GUIDs. Every assertion here is a property of the
layout contract (see ``spec.md`` / ``plan.py``):

* ``esp`` is fixed, ``rootA``/``rootB`` are byte-symmetric, ``data`` is the
  remainder;
* every partition is 1 MiB aligned, laid out sequentially in a fixed role order
  with the correct type GUID and a unique deterministic GUID;
* ``swap`` is appended last (never LUKS-wrapped); ``luks`` wraps root/data;
* the disk must be big enough or planning is refused.
"""

import copy
import json
import os
import pathlib

import pytest

from pengyos_layout import (
    Fs,
    LayoutError,
    Options,
    Role,
    TargetProbe,
    Validator,
)
from pengyos_layout.invariants import ALIGNMENT, DATA_MIN_BYTES, ROOT_MIN_BYTES
from pengyos_layout.plan import (
    ESP_BYTES,
    ROOT_BYTES,
    GUID_POOL,
    plan,
)

from .conftest import DATA_BYTES, DISK_BYTES, ROOT_BYTES as FIX_ROOT_BYTES

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _roles(plan):
    return [p.role for p in plan.partitions]


def _starts(plan):
    return [p.start for p in plan.partitions]


# --- golden reproduction ----------------------------------------------------
def test_plan_reproduces_committed_golden(planned_golden):
    """The planner reproduces the committed golden plan.json byte-for-byte."""
    with open(FIXTURES / "golden_66gib_plan.json", encoding="utf-8") as fh:
        golden = json.load(fh)
    assert planned_golden.to_dict() == golden


def test_plan_golden_sizes(planned_golden):
    """esp=256 MiB fixed, rootA=rootB=8 GiB symmetric, data = remainder."""
    esp = planned_golden.role(Role.ESP)
    root_a = planned_golden.role(Role.ROOT_A)
    root_b = planned_golden.role(Role.ROOT_B)
    data = planned_golden.role(Role.DATA)

    assert esp.size == ESP_BYTES == 256 << 20
    assert root_a.size == root_b.size == ROOT_BYTES == 8 << 30
    assert data.size == DATA_BYTES  # the fixture's own remainder
    assert planned_golden.total_size == DISK_BYTES  # fills the disk exactly


def test_plan_symmetric_roots(planned_golden):
    """rootA and rootB are byte-symmetric in size (the A/B contract)."""
    a = planned_golden.role(Role.ROOT_A)
    b = planned_golden.role(Role.ROOT_B)
    assert a is not None and b is not None
    assert a.size == b.size
    assert a.size == FIX_ROOT_BYTES


# --- determinism ------------------------------------------------------------
def test_plan_is_deterministic(probe, options):
    """The same probe + options always yields the identical plan."""
    assert plan(probe, options).to_dict() == plan(probe, options).to_dict()


def test_plan_guids_are_deterministic_from_pool(planned_golden):
    """Each unique GUID is the pool with a distinct low 32-bit counter."""
    guids = [p.guid for p in planned_golden.partitions]
    head = GUID_POOL[: -8]
    for i, guid in enumerate(guids):
        assert guid.startswith(head)
        assert int(guid[-8:], 16) == i


def test_plan_guids_unique(planned_golden):
    guids = [p.guid for p in planned_golden.partitions]
    assert len(guids) == len(set(guids))


# --- geometry ---------------------------------------------------------------
def test_plan_roles_in_fixed_order(planned_golden):
    assert _roles(planned_golden) == [Role.ESP, Role.ROOT_A, Role.ROOT_B, Role.DATA]


def test_plan_all_aligned(planned_golden):
    for p in planned_golden.partitions:
        assert p.start % ALIGNMENT == 0
        assert p.size % ALIGNMENT == 0


def test_plan_starts_are_cumulative(planned_golden):
    # sequential, non-overlapping, contiguous from a 1 MiB-aligned start
    cursor = 0
    for p in planned_golden.partitions:
        assert p.start == cursor
        cursor += p.size
    assert cursor == planned_golden.total_size


def test_plan_type_guids_by_role(planned_golden):
    expected = {
        Role.ESP: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
        Role.ROOT_A: "0FC63DA8-8483-4772-8E79-3D69D8477DE4",
        Role.ROOT_B: "0FC63DA8-8483-4772-8E79-3D69D8477DE4",
        Role.DATA: "9FS2FC2B-66DA-4464-BF7D-6011622C226C",
    }
    for role, guid in expected.items():
        assert planned_golden.role(role).type_guid == guid


def test_plan_default_fs_by_role(planned_golden):
    assert planned_golden.role(Role.ESP).fs == Fs.VFAT
    assert planned_golden.role(Role.ROOT_A).fs == Fs.EXT4
    assert planned_golden.role(Role.ROOT_B).fs == Fs.EXT4
    assert planned_golden.role(Role.DATA).fs == Fs.BTRFS


def test_plan_mountpoints(planned_golden):
    assert planned_golden.role(Role.ESP).mount == "/boot/efi"
    assert planned_golden.role(Role.ROOT_A).mount == "/"
    assert planned_golden.role(Role.ROOT_B).mount == "/mnt/b"
    assert planned_golden.role(Role.DATA).mount == "/data"


# --- validity ---------------------------------------------------------------
def test_plan_planned_golden_is_valid(planned_golden):
    validator = Validator()
    assert validator.is_valid(planned_golden) is True
    assert validator.violations(planned_golden) == []


# --- swap -------------------------------------------------------------------
def test_plan_swap_appends_last(probe):
    opts = Options(luks=False, swap_size=512 << 20, edition="server")
    p = plan(probe, opts)
    assert _roles(p) == [Role.ESP, Role.ROOT_A, Role.ROOT_B, Role.DATA, Role.SWAP]
    swap = p.role(Role.SWAP)
    assert swap is not None
    assert swap.size == 512 << 20
    # swap sits right after data, and the whole thing still fits the disk
    assert swap.start == p.role(Role.DATA).start + p.role(Role.DATA).size
    assert p.total_size == probe.total_size


def test_plan_swap_size_zero_has_no_swap(probe):
    opts = Options(luks=False, swap_size=0, edition="server")
    p = plan(probe, opts)
    assert p.role(Role.SWAP) is None


def test_plan_swap_is_never_luks(probe):
    opts = Options(luks=True, swap_size=512 << 20, edition="server")
    p = plan(probe, opts)
    swap = p.role(Role.SWAP)
    assert swap is not None
    assert swap.luks is False
    assert swap.fs == Fs.SWAP
    assert swap.inner_fs is None


def test_plan_swap_rounded_down_to_alignment(probe):
    opts = Options(luks=False, swap_size=(100 << 20) + 1234, edition="server")
    p = plan(probe, opts)
    swap = p.role(Role.SWAP)
    assert swap is not None
    assert swap.size % ALIGNMENT == 0
    assert swap.size == ((100 << 20) + 1234) // ALIGNMENT * ALIGNMENT


def test_plan_with_swap_is_valid(probe):
    opts = Options(luks=False, swap_size=512 << 20, edition="server")
    validator = Validator()
    assert validator.is_valid(plan(probe, opts)) is True


# --- luks -------------------------------------------------------------------
def test_plan_luks_wraps_roots_and_data(probe):
    opts = Options(luks=True, swap_size=0, edition="server")
    p = plan(probe, opts)
    for role in (Role.ROOT_A, Role.ROOT_B, Role.DATA):
        slot = p.role(role)
        assert slot.luks is True
        assert slot.fs == Fs.LUKS2
        assert slot.inner_fs is not None  # real inner fs recorded


def test_plan_luks_swap_stays_plaintext(probe):
    """Under LUKS, roots/data are wrapped but swap (and the ESP) stay plaintext."""
    opts = Options(luks=True, swap_size=256 << 20, edition="server")
    p = plan(probe, opts)
    for slot in p.partitions:
        if slot.role in (Role.SWAP, Role.ESP):
            assert slot.luks is False
        else:
            assert slot.luks is True


def test_plan_luks_never_wraps_esp(probe):
    """The ESP must stay FAT32 (firmware reads it before any key) even under LUKS.

    Regression: an early planner passed ``options.luks`` to every slot, which
    wrapped the ESP in luks2 and broke the ESP-is-FAT32 invariant.
    """
    opts = Options(luks=True, swap_size=0, edition="server")
    p = plan(probe, opts)
    esp = p.role(Role.ESP)
    assert esp is not None
    assert esp.luks is False
    assert esp.fs == Fs.VFAT
    # and the whole thing is still a valid layout
    assert Validator().is_valid(p) is True


def test_plan_luks_keeps_symmetric_roots(probe):
    opts = Options(luks=True, swap_size=0, edition="server")
    p = plan(probe, opts)
    assert p.role(Role.ROOT_A).size == p.role(Role.ROOT_B).size


# --- disk sizing ------------------------------------------------------------
def test_plan_data_is_remainder(probe):
    """Data is exactly (disk - esp - 2*root), floored to alignment."""
    p = plan(probe, Options(luks=False, swap_size=0))
    expected = DISK_BYTES - ESP_BYTES - 2 * ROOT_BYTES
    assert p.role(Role.DATA).size == expected


def test_plan_larger_disk_grows_data_not_roots():
    """Roots stay fixed/symmetric; a bigger disk gives data the extra room."""
    small = plan(TargetProbe(device="/dev/sda", total_size=66 << 30), Options())
    big = plan(TargetProbe(device="/dev/sda", total_size=128 << 30), Options())
    assert small.role(Role.ROOT_A).size == big.role(Role.ROOT_A).size == ROOT_BYTES
    assert big.role(Role.DATA).size > small.role(Role.DATA).size
    # still symmetric
    assert big.role(Role.ROOT_A).size == big.role(Role.ROOT_B).size


def test_plan_min_disk_just_big_enough():
    """The smallest disk that fits ESP + 2 roots (4 GiB each) + data minimum."""
    total = ESP_BYTES + 2 * ROOT_BYTES + DATA_MIN_BYTES
    probe = TargetProbe(device="/dev/sda", total_size=total)
    p = plan(probe, Options())
    assert p.role(Role.ROOT_A).size == ROOT_BYTES
    assert p.role(Role.DATA).size == DATA_MIN_BYTES
    assert p.total_size == total


def test_plan_tiny_disk_too_small_for_roots_raises():
    """A disk that fits the ESP but not two roots + data refuses to plan."""
    total = ESP_BYTES + 2 * ROOT_BYTES + DATA_MIN_BYTES - ALIGNMENT
    probe = TargetProbe(device="/dev/sda", total_size=total)
    with pytest.raises(LayoutError):
        plan(probe, Options())


def test_plan_disk_smaller_than_esp_raises():
    probe = TargetProbe(device="/dev/sda", total_size=ESP_BYTES)
    with pytest.raises(LayoutError):
        plan(probe, Options())


def test_plan_disk_way_too_small_raises():
    probe = TargetProbe(device="/dev/sda", total_size=10 << 20)
    with pytest.raises(LayoutError):
        plan(probe, Options())


def test_plan_negative_size_raises():
    probe = TargetProbe(device="/dev/sda", total_size=-1)
    with pytest.raises(LayoutError):
        plan(probe, Options())


# --- options recording ------------------------------------------------------
def test_plan_records_options(planned_golden, options):
    assert planned_golden.options == options
    assert planned_golden.probe.device == "/dev/sda"


def test_plan_edition_flows_through():
    opts = Options(luks=False, swap_size=0, edition="desktop")
    probe = TargetProbe(device="/dev/sda", total_size=66 << 30)
    p = plan(probe, opts)
    assert p.options.edition == "desktop"


# --- serialisation roundtrip of a planned plan ------------------------------
def test_plan_planned_plan_roundtrips(planned_golden):
    restored = type(planned_golden).from_dict(planned_golden.to_dict())
    assert restored == planned_golden
