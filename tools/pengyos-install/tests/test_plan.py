"""Tests for the pure install-plan renderer.

``plan`` turns the validated ``(release, layout)`` inputs into an ordered,
deterministic :class:`~pengyos_install.models.Plan` — every step a
shell-quotable argv. The plan is the artifact the ``install`` engine executes and
the same artifact the updater consumes, so it is golden-testable: for a fixed
(release, layout) triple the rendered plan is byte-stable.

The fixtures (``release_json``, ``layout_json``) live in ``conftest.py``.
"""

from __future__ import annotations

import io

import pytest

from pengyos_install import cli
from pengyos_install.models import Layout, Release, ValidationError
from pengyos_install.plan import PlanBuilder, render_plan
from pengyos_install.runner import FakeRunner

# The golden install plan for the conftest fixtures, rendered as a shell script.
# This pins the exact ordered argv of every step — the plan is golden.
GOLDEN_SHELL = (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "\n"
    "# 1. create the gpt partition table on /dev/sda\n"
    "parted /dev/sda --script mklabel gpt\n"
    "# 2. create the ESP partition\n"
    "parted /dev/sda --script mkpart esp fat16 0% 100%\n"
    "# 3. create the a root partition\n"
    "parted /dev/sda --script mkpart a ext4 0% 100%\n"
    "# 4. create the b root partition\n"
    "parted /dev/sda --script mkpart b ext4 0% 100%\n"
    "# 5. create the data partition\n"
    "parted /dev/sda --script mkpart data btrfs 0% 100%\n"
    "# 6. format the ESP as FAT\n"
    "mkfs.fat -n EFI /dev/sda1\n"
    "# 7. format the a root slot as ext4\n"
    "mkfs.ext4 -L pengyos-a /dev/sda2\n"
    "# 8. format the b root slot as ext4\n"
    "mkfs.ext4 -L pengyos-b /dev/sda3\n"
    "# 9. format the data partition as btrfs\n"
    "mkfs.btrfs -L data /dev/sda4\n"
    "# 10. write rootfs to the inactive slot b\n"
    "dd if=rootfs.squashfs of=/dev/sda3 status=progress conv=fsync\n"
    "# 11. commit the rootfs to the active slot a\n"
    "dd if=rootfs.squashfs of=/dev/sda2 status=progress conv=fsync\n"
    "# 12. write the UKI to the ESP EFI boot path\n"
    "dd if=boot/EFI/boot/bootx64.efi of=/dev/sda1/EFI/BOOT/bootx64.efi "
    "status=progress conv=fsync\n"
    "# 13. install the systemd-boot loader entries on the ESP\n"
    "install-bootctl /dev/sda1 --dir-only\n"
    "# 14. create the data subvolume /data\n"
    "btrfs subvolume create /dev/sda4/data\n"
    "# 15. create the data subvolume /data/home\n"
    "btrfs subvolume create /dev/sda4/data/home\n"
    "# 16. create the data subvolume /data/var/log\n"
    "btrfs subvolume create /dev/sda4/data/var/log\n"
    "# 17. first-boot provisioning (fstab / hostname / users / network)\n"
    "pengyos-install provision-plan --layout layout.json --slot a "
    "--config install.toml\n"
)


def _first_slot_name(layout):
    """The slot the fixture release marks active ('a')."""
    return "a"


# --------------------------------------------------------------------------- #
# Ordering / structure
# --------------------------------------------------------------------------- #
def test_plan_is_golden_for_fixture(release_json, layout_json):
    release = Release.from_json(release_json)
    layout = Layout.from_json(layout_json)
    plan = render_plan(release, layout)
    assert plan.to_shell_script() == GOLDEN_SHELL


def test_plan_has_expected_phase_count(release_json, layout_json):
    """partition(5) + fs(4) + slot(2) + uki(1) + loader(1) + data(3) + provision(1)."""
    plan = render_plan(
        Release.from_json(release_json), Layout.from_json(layout_json)
    )
    assert len(plan) == 17


def test_plan_starts_with_partition_table(release_json, layout_json):
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    first = plan[0]
    assert list(first.argv[:2]) == ["parted", "/dev/sda"]
    assert "mklabel" in first.argv
    assert "gpt" in first.argv


def test_plan_phase_order(release_json, layout_json):
    """The seven phases appear, in order, as contiguous runs of their tools."""
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    argv0 = [s.argv[0] for s in plan]

    # partition phase: all the parted calls come first
    parted_idx = [i for i, a in enumerate(argv0) if a == "parted"]
    mkfs_idx = [i for i, a in enumerate(argv0) if a == "mkfs.fat"]
    dd_idx = [i for i, a in enumerate(argv0) if a == "dd"]
    install_idx = [i for i, a in enumerate(argv0) if a == "install-bootctl"]
    btrfs_idx = [i for i, a in enumerate(argv0) if a == "btrfs"]
    provision_idx = [i for i, a in enumerate(argv0) if a == "pengyos-install"]

    # every partition step precedes every fs step precedes the dd writes ...
    assert max(parted_idx) < min(mkfs_idx)
    assert max(mkfs_idx) < min(dd_idx)
    assert max(dd_idx) < min(install_idx)
    assert min(install_idx) < min(btrfs_idx)
    # ... and the provision step is last.
    assert provision_idx == [len(plan) - 1]


def test_provision_step_is_last(release_json, layout_json):
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    last = plan[-1]
    assert last.argv[0] == "pengyos-install"
    assert last.argv[1] == "provision-plan"
    assert last.argv[-1] == "install.toml"


# --------------------------------------------------------------------------- #
# Atomic write: inactive slot first, then commit the active slot
# --------------------------------------------------------------------------- #
def test_write_is_atomic_inactive_then_active(release_json, layout_json):
    """The rootfs is written to the INACTIVE slot, then committed to the active."""
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    # The two rootfs writes carry `if=rootfs.squashfs` (the UKI write does not).
    rootfs_writes = [s for s in plan if s.argv[1] == "if=rootfs.squashfs"]
    assert len(rootfs_writes) == 2

    write, commit = rootfs_writes[0], rootfs_writes[1]
    # write goes to the inactive slot (b), commit goes to the active slot (a)
    assert write.argv[2] == "of=/dev/sda3"
    assert commit.argv[2] == "of=/dev/sda2"
    # the write always carries conv=fsync for durability
    assert "conv=fsync" in write.argv


def test_write_targets_the_inactive_slot(release_json, layout_json):
    """The release names slot 'a' active; the write must hit the other slot first."""
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    write = next(s for s in plan if s.argv[1] == "if=rootfs.squashfs")
    assert write.argv[2] == "of=/dev/sda3"  # slot b is inactive when a is active


def test_atomic_write_reversed_when_active_slot_is_b(release_json, layout_json):
    """If the release names slot 'b' active, the write targets slot 'a' first."""
    text = release_json.replace('"slot": "a"', '"slot": "b"')
    plan = render_plan(Release.from_json(text), Layout.from_json(layout_json))
    write = next(s for s in plan if s.argv[1] == "if=rootfs.squashfs")
    assert write.argv[2] == "of=/dev/sda2"  # slot a is inactive when b is active


# --------------------------------------------------------------------------- #
# UKI / loader / data phases
# --------------------------------------------------------------------------- #
def test_uki_written_to_efi_boot_path(release_json, layout_json):
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    uki = next(s for s in plan if "bootx64.efi" in " ".join(s.argv))
    assert uki.argv[1] == "if=boot/EFI/boot/bootx64.efi"
    assert uki.argv[2] == "of=/dev/sda1/EFI/BOOT/bootx64.efi"


def test_loader_installed_on_esp(release_json, layout_json):
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    loader = next(s for s in plan if s.argv[0] == "install-bootctl")
    assert loader.argv[1] == "/dev/sda1"


def test_data_subvolumes_created(release_json, layout_json):
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    subvols = [s for s in plan if s.argv[0] == "btrfs"]
    assert len(subvols) == 3
    created = {s.argv[-1] for s in subvols}
    assert created == {"/dev/sda4/data", "/dev/sda4/data/home", "/dev/sda4/data/var/log"}


def test_fs_phases_format_each_partition(release_json, layout_json):
    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    fs = [s for s in plan if s.argv[0].startswith("mkfs")]
    assert len(fs) == 4
    formats = {s.argv[0] for s in fs}
    assert formats == {"mkfs.fat", "mkfs.ext4", "mkfs.ext4", "mkfs.btrfs"}


# --------------------------------------------------------------------------- #
# Determinism / golden
# --------------------------------------------------------------------------- #
def test_plan_is_deterministic(release_json, layout_json):
    a = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    b = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    assert a.to_json() == b.to_json()
    assert a.to_shell_script() == b.to_shell_script()


def test_plan_roundtrips_through_json(release_json, layout_json):
    from pengyos_install.models import Plan

    plan = render_plan(Release.from_json(release_json), Layout.from_json(layout_json))
    again = Plan.from_json(plan.to_json())
    assert again == plan


# --------------------------------------------------------------------------- #
# Validation / cross-checks
# --------------------------------------------------------------------------- #
def test_release_slot_cross_checked_against_layout(release_json, layout_json):
    """A release whose slot is not declared must raise, not silently plan."""
    text = release_json.replace('"slot": "a"', '"slot": "z"')
    with pytest.raises(ValidationError):
        render_plan(Release.from_json(text), Layout.from_json(layout_json))


def test_builder_add_accepts_argv_and_step():
    builder = PlanBuilder()
    builder.add(["a", "b"], description="first")
    from pengyos_install.models import Step

    builder.add(Step.from_argv(["c"], description="second"))
    plan = builder.plan()
    assert plan[0].argv == ("a", "b")
    assert plan[1].argv == ("c",)


# --------------------------------------------------------------------------- #
# CLI: plan renders; install/provision-plan are not yet implemented
# --------------------------------------------------------------------------- #
def _run(argv, *, runner=None):
    out = io.StringIO()
    err = io.StringIO()
    code = cli.main(argv, out=out, err=err, runner=runner)
    return code, out.getvalue(), err.getvalue()


def test_cli_plan_json(release_json, layout_json, tmp_path):
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text(release_json, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    code, out, _ = _run(
        ["plan", "--release", str(rel), "--layout", str(lay), "--json"]
    )
    assert code == 0
    assert '"argv"' in out
    assert out.count('"argv"') == 17


def test_cli_plan_shell_script(release_json, layout_json, tmp_path):
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text(release_json, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    code, out, _ = _run(
        ["plan", "--release", str(rel), "--layout", str(lay)]
    )
    assert code == 0
    assert out.startswith("#!/usr/bin/env bash")
    # the provision step references the layout path the CLI was given
    assert f"--layout {lay}" in out
    assert "--slot a --config install.toml" in out


def test_cli_plan_reports_bad_release_cleanly(tmp_path, layout_json):
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text("{not json", encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    code, _, err = _run(["plan", "--release", str(rel), "--layout", str(lay)])
    assert code == 2  # user-facing validation error
    assert "error:" in err


def test_cli_install_refuses_without_yes():
    # install refuses the destructive write without --yes (exit 2).
    code, _, err = _run(["install", "--release", "r", "--layout", "l"])
    assert code == 2
    assert "refusing" in err.lower()


def test_cli_install_runs_with_yes_via_injected_runner(tmp_path, release_json, layout_json):
    # With --yes the plan executes through the injected runner (no root/hardware).
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text(release_json, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    fake = FakeRunner()
    code, out, _ = _run(
        ["install", "--release", str(rel), "--layout", str(lay), "--yes"],
        runner=fake,
    )
    assert code == 0
    assert fake.calls[0][:2] == ["parted", "/dev/sda"]
    assert fake.calls[-1] == ["sync"]  # teardown sync on success
    assert "installed" in out
