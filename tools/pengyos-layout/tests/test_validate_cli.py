"""End-to-end tests for the ``validate`` CLI subcommand.

The ``validate`` subcommand reads a plan JSON, runs
:class:`~pengyos_layout.validator.Validator`, and reports the result:

* exit ``0`` + ``OK`` for a valid layout;
* exit ``1`` + every violation for an invalid one (too-small disk, overlapping
  or misaligned partitions, asymmetric A/B slots, a missing/non-VAT32 ESP, the
  live boot device);
* exit ``2`` on a usage/IO error (missing file, malformed JSON).

No real device, network, root or hardware is touched — plans are in-memory data
or JSON written to a temp file.
"""

import copy
import json

import pytest

from pengyos_layout import Role
from pengyos_layout.cli import main

from .conftest import ESP_BYTES, ROOT_BYTES


def _write_plan(tmp_path, plan_dict, name="plan.json"):
    path = tmp_path / name
    path.write_text(json.dumps(plan_dict))
    return str(path)


def _fire(golden_plan, mutate):
    plan = copy.deepcopy(golden_plan)
    mutate(plan)
    return plan


# --- valid ------------------------------------------------------------------
def test_cli_validate_valid_returns_0(golden_plan, tmp_path, capsys):
    """A valid plan is accepted: exit 0 and an ``OK`` line."""
    path = _write_plan(tmp_path, golden_plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "valid layout" in out


def test_cli_validate_valid_json(golden_plan, tmp_path, capsys):
    """``--json`` on a valid plan emits ``{"valid": true, "violations": []}``."""
    path = _write_plan(tmp_path, golden_plan.to_dict())
    rc = main(["validate", "--plan", path, "--json"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"valid": True, "violations": []}


# --- each rejection the subtask names ---------------------------------------
def test_cli_validate_too_small_disk_returns_1(golden_plan, tmp_path, capsys):
    """A layout that exceeds the target disk is refused (exit 1)."""
    plan = _fire(golden_plan, lambda p: setattr(p.probe, "total_size", 1))
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 1
    err = capsys.readouterr().err
    assert "INVALID" in err
    assert "exceeds disk" in err


def test_cli_validate_overlap_returns_1(golden_plan, tmp_path, capsys):
    """Overlapping partitions are refused (exit 1)."""
    plan = _fire(
        golden_plan,
        lambda p: setattr(p.partitions[1], "start", p.partitions[0].start),
    )
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 1
    assert "overlap" in capsys.readouterr().err


def test_cli_validate_misaligned_returns_1(golden_plan, tmp_path, capsys):
    """A non-1 MiB-aligned start/size is refused (exit 1)."""
    plan = _fire(
        golden_plan,
        lambda p: setattr(p.partitions[0], "start", ESP_BYTES + 1),
    )
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 1
    assert "aligned" in capsys.readouterr().err


def test_cli_validate_asymmetric_roots_returns_1(golden_plan, tmp_path, capsys):
    """Asymmetric A/B slots are refused (exit 1)."""
    plan = _fire(
        golden_plan,
        lambda p: setattr(p.role(Role.ROOT_A), "size", ROOT_BYTES + (1 << 20)),
    )
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 1
    assert "asymmetric" in capsys.readouterr().err


def test_cli_validate_missing_esp_returns_1(golden_plan, tmp_path, capsys):
    """A layout without an ESP is refused (exit 1)."""
    plan = _fire(
        golden_plan,
        lambda p: p.partitions.remove(p.role(Role.ESP)),
    )
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 1
    assert "ESP" in capsys.readouterr().err


def test_cli_validate_live_device_returns_1(golden_plan, tmp_path, capsys):
    """Planning against the live boot device is refused (exit 1)."""
    plan = _fire(golden_plan, lambda p: setattr(p.probe, "is_live", True))
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 1
    assert "live device" in capsys.readouterr().err


def test_cli_validate_reports_all_violations_at_once(golden_plan, tmp_path, capsys):
    """A plan violating several invariants lists every one (exit 1)."""
    plan = _fire(golden_plan, lambda p: setattr(p.probe, "is_live", True))
    plan = _fire(
        plan,
        lambda p: setattr(p.role(Role.ROOT_A), "size", ROOT_BYTES + (1 << 20)),
    )
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path])
    assert rc == 1
    err = capsys.readouterr().err
    assert "live device" in err
    assert "asymmetric" in err


def test_cli_validate_json_reports_violations(golden_plan, tmp_path, capsys):
    """``--json`` on an invalid plan emits ``{"valid": false, [...]}``."""
    plan = _fire(
        golden_plan,
        lambda p: setattr(p.role(Role.ROOT_A), "size", ROOT_BYTES + (1 << 20)),
    )
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["validate", "--plan", path, "--json"])
    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False
    assert len(result["violations"]) >= 1
    assert any("asymmetric" in m for m in result["violations"])


# --- usage / IO errors ------------------------------------------------------
def test_cli_validate_missing_file_returns_2(capsys):
    """A missing plan file is a usage/IO error (exit 2)."""
    rc = main(["validate", "--plan", "/no/such/plan.json"])
    assert rc == 2
    assert "not found" in capsys.readouterr().err.lower()


def test_cli_validate_malformed_json_returns_2(tmp_path, capsys):
    """A malformed plan file is a usage/IO error (exit 2)."""
    path = tmp_path / "broken.json"
    path.write_text("{ not valid json")
    rc = main(["validate", "--plan", str(path)])
    assert rc == 2
    assert "cannot parse" in capsys.readouterr().err.lower()
