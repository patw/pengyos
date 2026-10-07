"""Each invariant has exactly one mutation that fires it.

The "each invariant has a firing test" contract (see spec.md) is grounded here:
for every check in :data:`~pengyos_layout.invariants.CHECKS` there is a test that
mutates a copy of the golden plan to trigger it, and a positive test that the
golden plan passes every check.
"""

import copy

import pytest

from pengyos_layout import Fs, Partition, Role, TargetProbe
from pengyos_layout.invariants import CHECKS, ESP_MAX_MB, ROOT_MIN_BYTES

from .conftest import ESP_BYTES, ROOT_BYTES


def _fire(golden_plan, mutate):
    plan = copy.deepcopy(golden_plan)
    mutate(plan)
    return plan


@pytest.mark.parametrize("name", [n for n, _ in CHECKS])
def test_golden_passes_every_check(golden_plan, validator, name):
    """The golden plan is valid: no check reports a violation."""
    assert validator.check_one(name, golden_plan) is None


FIRE_MUTATIONS = {
    "live_device": lambda p: setattr(p.probe, "is_live", True),
    "alignment": lambda p: setattr(
        p.partitions[0], "start", ESP_BYTES + 1  # not 1 MiB aligned
    ),
    "esp_present": lambda p: p.partitions.remove(p.role(Role.ESP)),
    "esp_fs": lambda p: setattr(p.role(Role.ESP), "fs", Fs.EXT4),
    "esp_within_bounds": lambda p: setattr(
        p.role(Role.ESP), "start", (ESP_MAX_MB << 20)
    ),
    "esp_min_size": lambda p: setattr(
        p.role(Role.ESP), "size", (50 << 20)  # below ESP_MIN_BYTES (100 MiB)
    ),
    "root_symmetric": lambda p: setattr(
        p.role(Role.ROOT_A), "size", ROOT_BYTES + (1 << 20)
    ),
    "root_min_size": lambda p: setattr(
        p.role(Role.ROOT_A), "size", (ROOT_MIN_BYTES - (1 << 20))
    ),
    "data_present_min": lambda p: p.partitions.remove(p.role(Role.DATA)),
    "swap_min_size": lambda p: p.partitions.append(
        Partition(
            role=Role.SWAP,
            size=(1 << 20) - 1,  # below SWAP_MIN_BYTES
            start=p.partitions[-1].start + p.partitions[-1].size,
        )
    ),
    "no_overlap": lambda p: setattr(
        p.partitions[1], "start", p.partitions[0].start  # overlaps ESP
    ),
    "fits_disk": lambda p: setattr(
        p.probe, "total_size", p.total_size - (1 << 20)
    ),
}


@pytest.mark.parametrize("name", list(FIRE_MUTATIONS.keys()))
def test_each_invariant_has_a_firing_mutation(golden_plan, validator, name):
    """Mutating the golden plan to violate ``name`` produces a violation."""
    plan = _fire(golden_plan, FIRE_MUTATIONS[name])
    assert validator.check_one(name, plan) is not None


def test_swap_min_size_positive_no_swap(golden_plan, validator):
    """No swap slot present: the swap check is satisfied (None)."""
    assert validator.check_one("swap_min_size", golden_plan) is None
