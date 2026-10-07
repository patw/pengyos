"""Tests for the ``pengyos-layout`` command-line interface.

The CLI is the shell-facing layer over the planner. These exercise the
``plan`` subcommand end-to-end: read a probe, derive a golden layout, write it
to disk or stdout, and refuse invalid input with a non-zero exit code. The
``validate`` / ``emit`` subcommands are not yet implemented and must report that
honestly.

No real device, network, root or hardware is touched — probes are JSON fixtures
and plans are plain data.
"""

import json
import os
import subprocess
import sys

import pytest

import pengyos_layout
from pengyos_layout.cli import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISK_BYTES = 66 << 30


@pytest.fixture
def probe_file(tmp_path):
    """A 66 GiB non-live probe written to a temp JSON file."""
    path = tmp_path / "probe.json"
    path.write_text(
        json.dumps(
            {
                "device": "/dev/sda",
                "total_size": DISK_BYTES,
                "sector_size": 512,
                "is_live": False,
            }
        )
    )
    return str(path)


def _roles(plan_dict):
    return [p["role"] for p in plan_dict["partitions"]]


def _total(plan_dict):
    return sum(p["size"] for p in plan_dict["partitions"])


def test_cli_plan_to_stdout(capsys, probe_file):
    """``plan`` with no --out writes the golden plan to stdout as JSON."""
    rc = main(["plan", "--target", probe_file])
    assert rc == 0
    out = capsys.readouterr().out
    plan = json.loads(out)  # parses -> it is valid JSON
    assert _roles(plan) == ["esp", "rootA", "rootB", "data"]
    assert plan["options"]["edition"] == "server"


def test_cli_plan_writes_file(tmp_path, probe_file):
    """``plan --out`` writes the golden plan to the given file."""
    out = tmp_path / "plan.json"
    rc = main(["plan", "--target", probe_file, "--out", str(out)])
    assert rc == 0
    assert out.exists()
    plan = json.loads(out.read_text())
    # fills the disk exactly
    assert _total(plan) == DISK_BYTES


def test_cli_plan_matches_planner(probe_file):
    """The CLI plan equals the planner's output for the same probe/options."""
    import contextlib
    import io

    from pengyos_layout import Options, TargetProbe
    from pengyos_layout.plan import plan

    probe = TargetProbe(device="/dev/sda", total_size=DISK_BYTES, is_live=False)
    expected = plan(probe, Options()).to_dict()

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["plan", "--target", probe_file])
    assert rc == 0
    got = json.loads(buf.getvalue())
    assert got == expected


def test_cli_plan_luks_keeps_esp_vfat(capsys, probe_file):
    """``--luks`` wraps roots/data but leaves the ESP FAT32 (valid layout)."""
    rc = main(["plan", "--target", probe_file, "--luks"])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    esp = next(p for p in plan["partitions"] if p["role"] == "esp")
    assert esp["fs"] == "vfat"
    assert esp["luks"] is False
    root_a = next(p for p in plan["partitions"] if p["role"] == "rootA")
    assert root_a["luks"] is True


def test_cli_plan_swap_carved_from_data(capsys, probe_file):
    """A swap slot is carved from data so the total still fills the disk."""
    swap = 512 << 20
    rc = main(["plan", "--target", probe_file, "--swap-size", str(swap)])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert _roles(plan) == ["esp", "rootA", "rootB", "data", "swap"]
    swap_slot = next(p for p in plan["partitions"] if p["role"] == "swap")
    assert swap_slot["size"] == swap
    # swap fits within the fixed disk; data shrank to make room
    assert _total(plan) == DISK_BYTES


def test_cli_plan_too_small_disk_returns_1(capsys, tmp_path):
    """A disk too small for the layout is refused with exit code 1."""
    tiny = tmp_path / "tiny.json"
    tiny.write_text(json.dumps({"device": "/dev/sda", "total_size": 10 << 20}))
    rc = main(["plan", "--target", str(tiny)])
    assert rc == 1
    assert "too small" in capsys.readouterr().err.lower()


def test_cli_plan_live_device_returns_1(capsys, tmp_path):
    """Planning against the live boot device is refused (exit 1)."""
    live = tmp_path / "live.json"
    live.write_text(
        json.dumps(
            {"device": "/dev/sda", "total_size": DISK_BYTES, "is_live": True}
        )
    )
    rc = main(["plan", "--target", str(live)])
    assert rc == 1
    assert "live device" in capsys.readouterr().err.lower()


def test_cli_validate_missing_plan_arg_exits_2(capsys):
    """A required --plan argument missing is a usage error (exit 2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["validate"])
    assert excinfo.value.code == 2


def test_cli_emit_now_implemented(capsys, tmp_path):
    """``emit`` is now wired: it produces executor argv for a valid plan."""
    import json

    from pengyos_layout import Options, TargetProbe
    from pengyos_layout.plan import plan

    probe = TargetProbe(device="/dev/sda", total_size=66 << 30, is_live=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan(probe, Options()).to_dict()))
    rc = main(["emit", "--plan", str(plan_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "not implemented" not in out.lower()
    assert "sgdisk -n 1:0B:268435456B" in out


def test_cli_no_command_prints_help(capsys):
    """With no subcommand, the CLI prints help and exits 0."""
    rc = main([])
    assert rc == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_cli_version_flag():
    """``--version`` prints the package version."""
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO_ROOT, "src"))
    out = subprocess.run(
        [sys.executable, "-m", "pengyos_layout", "--version"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    assert pengyos_layout.__version__ in out.stdout


def test_cli_missing_target_arg_exits_2(capsys):
    """A required --target argument missing is a usage error (exit 2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["plan"])
    assert excinfo.value.code == 2
