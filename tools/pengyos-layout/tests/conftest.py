"""Shared fixtures for the pengyos_layout test suite.

All fixtures are pure in-memory data — no device, network, root or disk access.
The ``golden_plan`` fixture is the committed golden layout the ``plan`` command
will reproduce; every invariant test mutates a copy of it to fire exactly one
violation.
"""

import copy

import pytest

from pengyos_layout import (
    Fs,
    Options,
    Partition,
    Plan,
    Role,
    TargetProbe,
    Validator,
)
from pengyos_layout.plan import plan

# A 66 GiB target disk (1 MiB aligned).
DISK_BYTES = 66 << 30  # 70891364992

# Fixed layout sizes (all 1 MiB aligned). They sum exactly to DISK_BYTES:
#   256 MiB (esp) + 8 GiB (rootA) + 8 GiB (rootB) + 50944 MiB (data) = 66 GiB.
ESP_BYTES = 256 << 20  # 256 MiB
ROOT_BYTES = 8 << 30  # 8 GiB per slot
DATA_BYTES = (66 << 30) - (ESP_BYTES + 2 * ROOT_BYTES)  # remainder


def _make_probe(device="/dev/sda", total_size=DISK_BYTES, is_live=False):
    return TargetProbe(device=device, total_size=total_size, is_live=is_live)


def _make_partition(role, *, start, size, **kwargs):
    return Partition(role=role, size=size, start=start, **kwargs)


@pytest.fixture
def probe():
    return _make_probe()


@pytest.fixture
def options():
    return Options(luks=False, swap_size=0, edition="server")


@pytest.fixture
def golden_plan(probe, options):
    """The committed golden layout for the 66 GiB fixture probe.

    Laid out sequentially from a 1 MiB-aligned start:
      esp 256 MiB @ 0
      rootA 8 GiB  @ 256 MiB
      rootB 8 GiB  @ 8 GiB + 256 MiB
      data remainder @ 8 GiB + 512 MiB (sums exactly to the disk)
    """
    esp_start = 0
    root_a_start = ESP_BYTES
    root_b_start = ESP_BYTES + ROOT_BYTES
    data_start = ESP_BYTES + 2 * ROOT_BYTES

    return Plan(
        probe=probe,
        options=options,
        partitions=[
            _make_partition(
                Role.ESP,
                start=esp_start,
                size=ESP_BYTES,
                fs=Fs.VFAT,
                mount="/boot/efi",
                type_guid="C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
            ),
            _make_partition(
                Role.ROOT_A,
                start=root_a_start,
                size=ROOT_BYTES,
                fs=Fs.EXT4,
                mount="/",
                type_guid="0FC63DA8-8483-4772-8E79-3D69D8477DE4",
            ),
            _make_partition(
                Role.ROOT_B,
                start=root_b_start,
                size=ROOT_BYTES,
                fs=Fs.EXT4,
                mount="/mnt/b",
                type_guid="0FC63DA8-8483-4772-8E79-3D69D8477DE4",
            ),
            _make_partition(
                Role.DATA,
                start=data_start,
                size=DATA_BYTES,
                fs=Fs.BTRFS,
                mount="/data",
                type_guid="9FS2FC2B-66DA-4464-BF7D-6011622C226C",
            ),
        ],
    )


@pytest.fixture
def planned_golden(probe, options):
    """The golden layout the ``plan`` function derives for the 66 GiB probe.

    This is the committed golden the ``plan`` command must reproduce; tests
    assert the planner produces it byte-for-byte and that it is stable.
    """
    return plan(probe, options)


@pytest.fixture
def validator():
    return Validator()


@pytest.fixture
def valid_with_swap(golden_plan):
    """Golden plan plus a trailing, aligned, non-trivial swap slot (last).

    The disk is grown by exactly the swap size so the swap fits at the end
    without overflowing (the base golden plan fills its disk exactly).
    """
    swap = copy.deepcopy(golden_plan)
    swap_size = 512 << 20  # 512 MiB
    swap.probe.total_size += swap_size  # room for the swap at the end
    swap.partitions.append(
        Partition(
            role=Role.SWAP,
            size=swap_size,
            start=swap.partitions[-1].start + swap.partitions[-1].size,
            fs=Fs.SWAP,
            mount=None,
        )
    )
    return swap
