"""Tests for the layout data model (partitions, probe, options, plan)."""

import copy

import pytest

from pengyos_layout import (
    Fs,
    LayoutError,
    Options,
    Partition,
    Plan,
    Role,
    TargetProbe,
)

from .conftest import ROOT_BYTES


def test_target_probe_roundtrip():
    probe = TargetProbe(device="/dev/sda", total_size=123456, is_live=False)
    assert TargetProbe.from_dict(probe.to_dict()) == probe


def test_options_roundtrip():
    opts = Options(luks=True, swap_size=1 << 30, edition="desktop")
    assert Options.from_dict(opts.to_dict()) == opts


def test_partition_defaults_role_name():
    p = Partition(role=Role.ROOT_A, size=1 << 20)
    assert p.name == "rootA"
    assert p.role == Role.ROOT_A


def test_partition_luks_sets_public_fs_and_inner():
    """A LUKS root slot: public fs is luks2, inner fs is the role default."""
    p = Partition(role=Role.ROOT_A, size=1 << 20, luks=True)
    assert p.fs == Fs.LUKS2
    assert p.inner_fs == Fs.EXT4


def test_partition_luks_swap_keeps_swap_fs():
    """A LUKS swap slot stays swap (swap is never wrapped)."""
    p = Partition(role=Role.SWAP, size=1 << 20, luks=True)
    assert p.fs == Fs.LUKS2
    assert p.inner_fs == Fs.SWAP


def test_partition_non_luks_luks2_fs_is_error():
    with pytest.raises(LayoutError):
        Partition(role=Role.DATA, size=1 << 20, luks=False, fs=Fs.LUKS2)


def test_partition_from_dict_normalizes_enums():
    p = Partition.from_dict(
        {
            "role": "rootA",
            "size": 100,
            "fs": "ext4",
            "luks": True,
            "start": 0,
        }
    )
    assert p.role == Role.ROOT_A
    assert p.fs == Fs.LUKS2
    assert p.inner_fs == Fs.EXT4
    assert p.size == 100


def test_partition_dict_roundtrip():
    p = Partition(
        role=Role.ESP,
        size=1 << 20,
        start=0,
        fs=Fs.VFAT,
        mount="/boot/efi",
        type_guid="GUID",
    )
    assert Partition.from_dict(p.to_dict()) == p


def test_plan_total_size_and_role_lookup(golden_plan):
    expected = sum(p.size for p in golden_plan.partitions)
    assert golden_plan.total_size == expected
    assert golden_plan.role(Role.ESP).mount == "/boot/efi"
    assert golden_plan.role(Role.SWAP) is None


def test_plan_root_slots_are_symmetric(golden_plan):
    slots = golden_plan.root_slots()
    assert [s.role for s in slots] == [Role.ROOT_A, Role.ROOT_B]
    assert slots[0].size == slots[1].size == ROOT_BYTES


def test_plan_dict_roundtrip(golden_plan):
    restored = Plan.from_dict(golden_plan.to_dict())
    assert restored == golden_plan


def test_probe_is_live_flag_default_false():
    assert TargetProbe(device="/dev/sda", total_size=1000).is_live is False
