"""Tests for the Validator: runs all invariants, collects all violations."""

import copy

import pytest

from pengyos_layout import Role, ValidationError

from .conftest import ROOT_BYTES


def test_golden_plan_is_valid(golden_plan, validator):
    assert validator.is_valid(golden_plan) is True
    assert validator.violations(golden_plan) == []


def test_validate_raises_on_invalid(golden_plan, validator):
    plan = copy.deepcopy(golden_plan)
    plan.probe.is_live = True
    with pytest.raises(ValidationError) as excinfo:
        validator.validate(plan)
    assert any("live device" in m for m in excinfo.value.messages)


def test_collects_all_violations(golden_plan, validator):
    """A single plan violating two invariants reports both, not just the first."""
    plan = copy.deepcopy(golden_plan)
    # asymmetric A/B + overlapping partition
    plan.partitions[0].size = 128  # misaligned + tiny
    plan.partitions[1].size = ROOT_BYTES + (1 << 20)  # asymmetric

    viols = validator.violations(plan)
    joined = "\n".join(viols)
    assert "asymmetric" in joined
    assert "overlap" in joined
    assert len(viols) >= 2


def test_check_one_unknown_check_raises(golden_plan, validator):
    with pytest.raises(KeyError):
        validator.check_one("does_not_exist", golden_plan)


def test_validate_live_device_reports_and_is_invalid(golden_plan, validator):
    plan = copy.deepcopy(golden_plan)
    plan.probe.is_live = True
    assert validator.is_valid(plan) is False
    with pytest.raises(ValidationError) as excinfo:
        validator.validate(plan)
    assert any("live device" in m for m in excinfo.value.messages)


def test_validate_missing_esp_reports(golden_plan, validator):
    plan = copy.deepcopy(golden_plan)
    plan.partitions = [p for p in plan.partitions if p.role != Role.ESP]
    assert validator.is_valid(plan) is False
    with pytest.raises(ValidationError) as excinfo:
        validator.validate(plan)
    assert any("ESP" in m for m in excinfo.value.messages)


def test_valid_with_swap_is_valid(valid_with_swap, validator):
    """A golden plan plus a trailing swap slot is still fully valid."""
    assert validator.is_valid(valid_with_swap) is True
    swap = valid_with_swap.role(Role.SWAP)
    assert swap is not None
    assert swap.mount is None
    assert valid_with_swap.partitions[-1].role == Role.SWAP
